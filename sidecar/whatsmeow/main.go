package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

type bridgeConfig struct {
	IngestURL     string
	AuthToken     string
	Platform      string
	StorePath     string
	MediaDir      string
	GroupsOnly    bool
	IgnoreFromMe  bool
	HTTPTimeout   time.Duration
	ReconnectWait time.Duration
	LogLevel      string
}

type ingestPayload struct {
	Platform          string         `json:"platform"`
	ExternalGroupID   string         `json:"external_group_id"`
	GroupName         string         `json:"group_name,omitempty"`
	ExternalUserID    string         `json:"external_user_id"`
	UserName          string         `json:"user_name,omitempty"`
	MessageType       string         `json:"message_type"`
	RawText           string         `json:"raw_text,omitempty"`
	TranscriptText    string         `json:"transcript_text,omitempty"`
	Language          string         `json:"language,omitempty"`
	ExternalMessageID string         `json:"external_message_id,omitempty"`
	ReceivedAt        string         `json:"received_at"`
	SentAt            string         `json:"sent_at,omitempty"`
	ReplyToMessageID  string         `json:"reply_to_message_id,omitempty"`
	QuotedMessageID   string         `json:"quoted_message_id,omitempty"`
	HasMedia          bool           `json:"has_media"`
	MediaType         string         `json:"media_type,omitempty"`
	MediaPath         string         `json:"media_path,omitempty"`
	Metadata          map[string]any `json:"metadata"`
}

type ingestResponse struct {
	MessageID string   `json:"message_id"`
	Severity  string   `json:"severity"`
	RiskScore float64  `json:"risk_score"`
	AlertIDs  []string `json:"alert_ids"`
}

type mediaInfo struct {
	Path     string
	Type     string
	Metadata map[string]any
}

type bridge struct {
	cfg          bridgeConfig
	logger       *slog.Logger
	httpClient   *http.Client
	client       *whatsmeow.Client
	shuttingDown atomic.Bool
	reconnecting atomic.Bool
}

func main() {
	cfg := loadConfig()
	logger := slog.New(
		slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: parseLogLevel(cfg.LogLevel)}),
	)
	if err := os.MkdirAll(filepath.Dir(cfg.StorePath), 0o755); err != nil {
		logger.Error("store_dir_create_failed", "error", err)
		os.Exit(1)
	}
	if err := os.MkdirAll(cfg.MediaDir, 0o755); err != nil {
		logger.Error("media_dir_create_failed", "error", err)
		os.Exit(1)
	}

	ctx := context.Background()
	storeLogger := waLog.Stdout("Database", strings.ToUpper(cfg.LogLevel), true)
	container, err := sqlstore.New(ctx, "sqlite3", sqliteDSN(cfg.StorePath), storeLogger)
	if err != nil {
		logger.Error("store_open_failed", "error", err, "path", cfg.StorePath)
		os.Exit(1)
	}
	defer func() {
		if closeErr := container.Close(); closeErr != nil {
			logger.Warn("store_close_failed", "error", closeErr)
		}
	}()

	deviceStore, err := container.GetFirstDevice(ctx)
	if err != nil {
		logger.Error("device_store_failed", "error", err)
		os.Exit(1)
	}
	clientLogger := waLog.Stdout("WhatsMeow", strings.ToUpper(cfg.LogLevel), true)
	client := whatsmeow.NewClient(deviceStore, clientLogger)
	bridge := &bridge{
		cfg:        cfg,
		logger:     logger,
		httpClient: &http.Client{Timeout: cfg.HTTPTimeout},
		client:     client,
	}
	client.AddEventHandler(bridge.handleEvent)

	if err := bridge.connect(ctx); err != nil {
		logger.Error("connect_failed", "error", err)
		os.Exit(1)
	}

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	<-signals

	bridge.shuttingDown.Store(true)
	client.Disconnect()
}

func loadConfig() bridgeConfig {
	return bridgeConfig{
		IngestURL:     envOrDefault("SENTINEL_INGEST_URL", "http://127.0.0.1:8080/ingest"),
		AuthToken:     os.Getenv("SENTINEL_AUTH_TOKEN"),
		Platform:      envOrDefault("SENTINEL_PLATFORM", "whatsapp"),
		StorePath:     envOrDefault("SENTINEL_WHATSMEOW_STORE", "./.store/whatsmeow.db"),
		MediaDir:      envOrDefault("SENTINEL_MEDIA_DIR", "./.media"),
		GroupsOnly:    envBool("SENTINEL_GROUPS_ONLY", true),
		IgnoreFromMe:  envBool("SENTINEL_IGNORE_FROM_ME", true),
		HTTPTimeout:   envDuration("SENTINEL_HTTP_TIMEOUT", 30*time.Second),
		ReconnectWait: envDuration("SENTINEL_RECONNECT_WAIT", 5*time.Second),
		LogLevel:      envOrDefault("LOG_LEVEL", "info"),
	}
}

func (b *bridge) connect(ctx context.Context) error {
	if b.client.IsConnected() {
		return nil
	}
	if b.client.Store.ID == nil {
		qrChan, err := b.client.GetQRChannel(ctx)
		if err != nil {
			return fmt.Errorf("get qr channel: %w", err)
		}
		go b.consumeQRChannel(qrChan)
	}
	if err := b.client.Connect(); err != nil {
		return fmt.Errorf("client connect: %w", err)
	}
	return nil
}

func (b *bridge) consumeQRChannel(qrChan <-chan whatsmeow.QRChannelItem) {
	for item := range qrChan {
		switch item.Event {
		case whatsmeow.QRChannelEventCode:
			b.logger.Info("qr_code_ready", "timeout_seconds", item.Timeout.Seconds())
			qrterminal.GenerateHalfBlock(item.Code, qrterminal.L, os.Stdout)
		case "success":
			b.logger.Info("pairing_succeeded")
		case "timeout":
			b.logger.Warn("pairing_timed_out")
		case whatsmeow.QRChannelEventError:
			b.logger.Error("pairing_failed", "error", item.Error)
		default:
			b.logger.Warn("pairing_event", "event", item.Event)
		}
	}
}

func (b *bridge) handleEvent(raw any) {
	switch evt := raw.(type) {
	case *events.Message:
		if err := b.handleMessage(context.Background(), evt); err != nil {
			b.logger.Error("message_ingest_failed", "error", err, "chat", evt.Info.Chat.String(), "id", evt.Info.ID)
		}
	case *events.Connected:
		b.logger.Info("whatsmeow_connected")
	case *events.Disconnected:
		b.logger.Warn("whatsmeow_disconnected")
		b.scheduleReconnect()
	case *events.LoggedOut:
		b.logger.Error("whatsmeow_logged_out", "reason", evt.Reason)
	case *events.TemporaryBan:
		b.logger.Error("whatsmeow_temporary_ban", "details", evt.String())
	}
}

func (b *bridge) scheduleReconnect() {
	if b.shuttingDown.Load() {
		return
	}
	if !b.reconnecting.CompareAndSwap(false, true) {
		return
	}
	go func() {
		defer b.reconnecting.Store(false)
		time.Sleep(b.cfg.ReconnectWait)
		if b.shuttingDown.Load() || b.client.IsConnected() {
			return
		}
		if err := b.connect(context.Background()); err != nil {
			b.logger.Error("reconnect_failed", "error", err)
		} else {
			b.logger.Info("reconnect_attempted")
		}
	}()
}

func (b *bridge) handleMessage(ctx context.Context, evt *events.Message) error {
	if evt == nil || evt.Message == nil {
		return nil
	}
	if b.cfg.GroupsOnly && !evt.Info.IsGroup {
		return nil
	}
	if b.cfg.IgnoreFromMe && evt.Info.IsFromMe {
		return nil
	}

	payload, err := b.buildPayload(ctx, evt)
	if err != nil {
		return err
	}
	if payload == nil {
		return nil
	}

	response, err := b.postIngest(ctx, *payload)
	if err != nil {
		return err
	}
	b.logger.Info(
		"message_ingested",
		"chat", evt.Info.Chat.String(),
		"message_id", evt.Info.ID,
		"severity", response.Severity,
		"risk_score", response.RiskScore,
		"alert_ids", response.AlertIDs,
	)
	return nil
}

func (b *bridge) buildPayload(ctx context.Context, evt *events.Message) (*ingestPayload, error) {
	audioMessage := evt.Message.GetAudioMessage()
	rawText := extractText(evt.Message)
	if rawText == "" && audioMessage == nil {
		return nil, nil
	}

	messageType := "text"
	var media *mediaInfo
	var err error
	if audioMessage != nil {
		messageType = "audio"
		media, err = b.persistAudio(ctx, evt.Info.ID, audioMessage)
		if err != nil {
			return nil, fmt.Errorf("persist audio: %w", err)
		}
	}

	replyID := ""
	if contextInfo := extractContextInfo(evt.Message); contextInfo != nil {
		replyID = contextInfo.GetStanzaID()
	}

	senderID := evt.Info.Sender.String()
	if senderID == "" {
		senderID = evt.Info.Chat.String()
	}
	userName := evt.Info.PushName
	if userName == "" {
		userName = senderID
	}

	metadata := map[string]any{
		"source":       "whatsmeow-sidecar",
		"chat_jid":     evt.Info.Chat.String(),
		"sender_jid":   senderID,
		"is_group":     evt.Info.IsGroup,
		"is_from_me":   evt.Info.IsFromMe,
		"push_name":    evt.Info.PushName,
		"message_type": evt.Info.Type,
		"media_type":   evt.Info.MediaType,
	}
	if media != nil {
		for key, value := range media.Metadata {
			metadata[key] = value
		}
	}

	payload := &ingestPayload{
		Platform:          b.cfg.Platform,
		ExternalGroupID:   evt.Info.Chat.String(),
		GroupName:         evt.Info.Chat.String(),
		ExternalUserID:    senderID,
		UserName:          userName,
		MessageType:       messageType,
		RawText:           rawText,
		Language:          "pt-BR",
		ExternalMessageID: evt.Info.ID,
		ReceivedAt:        time.Now().UTC().Format(time.RFC3339),
		SentAt:            evt.Info.Timestamp.UTC().Format(time.RFC3339),
		ReplyToMessageID:  replyID,
		QuotedMessageID:   replyID,
		HasMedia:          media != nil && media.Path != "",
		Metadata:          metadata,
	}
	if media != nil {
		payload.MediaType = media.Type
		payload.MediaPath = media.Path
	}
	return payload, nil
}

func (b *bridge) persistAudio(ctx context.Context, messageID string, audioMessage *waE2E.AudioMessage) (*mediaInfo, error) {
	if audioMessage == nil {
		return nil, nil
	}
	extension := extensionForMIME(audioMessage.GetMimetype(), ".ogg")
	fileName := sanitizeFileName(messageID) + extension
	absolutePath, err := filepath.Abs(filepath.Join(b.cfg.MediaDir, fileName))
	if err != nil {
		return nil, fmt.Errorf("resolve media path: %w", err)
	}
	file, err := os.Create(absolutePath)
	if err != nil {
		return nil, fmt.Errorf("create media file: %w", err)
	}
	success := false
	defer func() {
		_ = file.Close()
		if !success {
			_ = os.Remove(absolutePath)
		}
	}()
	if err := b.client.DownloadToFile(ctx, audioMessage, file); err != nil {
		return nil, fmt.Errorf("download media: %w", err)
	}
	success = true
	return &mediaInfo{
		Path: absolutePath,
		Type: audioMessage.GetMimetype(),
		Metadata: map[string]any{
			"duration_seconds": audioMessage.GetSeconds(),
			"ptt":              audioMessage.GetPTT(),
			"mimetype":         audioMessage.GetMimetype(),
		},
	}, nil
}

func (b *bridge) postIngest(ctx context.Context, payload ingestPayload) (ingestResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return ingestResponse{}, fmt.Errorf("marshal payload: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, b.cfg.IngestURL, bytes.NewReader(body))
	if err != nil {
		return ingestResponse{}, fmt.Errorf("build request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	if b.cfg.AuthToken != "" {
		request.Header.Set("Authorization", "Bearer "+b.cfg.AuthToken)
	}
	response, err := b.httpClient.Do(request)
	if err != nil {
		return ingestResponse{}, fmt.Errorf("post ingest: %w", err)
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(response.Body)
	if err != nil {
		return ingestResponse{}, fmt.Errorf("read response: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ingestResponse{}, fmt.Errorf("sentinel ingest failed: http %d %s", response.StatusCode, string(responseBody))
	}
	var parsed ingestResponse
	if err := json.Unmarshal(responseBody, &parsed); err != nil {
		return ingestResponse{}, fmt.Errorf("decode response: %w", err)
	}
	return parsed, nil
}

func extractText(message *waE2E.Message) string {
	switch {
	case message == nil:
		return ""
	case message.GetConversation() != "":
		return message.GetConversation()
	case message.GetExtendedTextMessage() != nil:
		return message.GetExtendedTextMessage().GetText()
	case message.GetImageMessage() != nil:
		return message.GetImageMessage().GetCaption()
	case message.GetVideoMessage() != nil:
		return message.GetVideoMessage().GetCaption()
	case message.GetDocumentMessage() != nil:
		return message.GetDocumentMessage().GetCaption()
	default:
		return ""
	}
}

func extractContextInfo(message *waE2E.Message) *waE2E.ContextInfo {
	switch {
	case message == nil:
		return nil
	case message.GetExtendedTextMessage() != nil:
		return message.GetExtendedTextMessage().GetContextInfo()
	case message.GetImageMessage() != nil:
		return message.GetImageMessage().GetContextInfo()
	case message.GetVideoMessage() != nil:
		return message.GetVideoMessage().GetContextInfo()
	case message.GetAudioMessage() != nil:
		return message.GetAudioMessage().GetContextInfo()
	case message.GetDocumentMessage() != nil:
		return message.GetDocumentMessage().GetContextInfo()
	default:
		return nil
	}
}

func sqliteDSN(path string) string {
	return "file:" + path + "?_foreign_keys=on"
}

func extensionForMIME(mimeType string, fallback string) string {
	switch {
	case strings.Contains(mimeType, "ogg"):
		return ".ogg"
	case strings.Contains(mimeType, "mpeg"):
		return ".mp3"
	case strings.Contains(mimeType, "mp4"):
		return ".mp4"
	case strings.Contains(mimeType, "wav"):
		return ".wav"
	case strings.Contains(mimeType, "webm"):
		return ".webm"
	default:
		return fallback
	}
}

func sanitizeFileName(value string) string {
	replacer := strings.NewReplacer("/", "_", "\\", "_", ":", "_", " ", "_")
	return replacer.Replace(value)
}

func envOrDefault(key string, fallback string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	return value
}

func envBool(key string, fallback bool) bool {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	switch strings.ToLower(value) {
	case "1", "true", "yes", "y", "on":
		return true
	case "0", "false", "no", "n", "off":
		return false
	default:
		return fallback
	}
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func parseLogLevel(level string) slog.Level {
	switch strings.ToLower(level) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
