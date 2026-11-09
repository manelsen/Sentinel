package main

import (
	"bytes"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/pelletier/go-toml/v2"
)

var (
	directAddressPattern = regexp.MustCompile(`(?i)\b(voce|você|vc|tu|vocês)\b`)
	profanityWords       = []string{"idiota", "burro", "imbecil", "palhaco", "palhaço", "merda", "porra", "caralho", "otario", "otário", "babaca"}
	negativeTerms        = []string{"distorcendo", "mentira", "mentindo", "ridiculo", "ridículo", "nunca", "absurdo", "vergonha", "covarde", "incompetente"}
	stopwords            = map[string]struct{}{
		"a": {}, "o": {}, "de": {}, "do": {}, "da": {}, "e": {}, "que": {}, "em": {}, "um": {}, "uma": {},
		"para": {}, "com": {}, "na": {}, "no": {}, "isso": {}, "essa": {}, "esse": {}, "mas": {}, "por": {}, "se": {},
		"eu": {}, "voce": {}, "você": {},
	}
)

const schemaSQL = `
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_group_id TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, external_group_id)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    raw_text TEXT,
    transcript_text TEXT,
    received_at TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    reply_to_message_id TEXT,
    has_media INTEGER NOT NULL,
    media_type TEXT,
    media_path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audio_transcriptions (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    transcript_text TEXT,
    language TEXT,
    confidence REAL,
    duration_seconds REAL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS normalized_messages (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    analysis_text TEXT NOT NULL,
    language TEXT,
    contains_profanity INTEGER NOT NULL,
    contains_direct_address INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS message_features (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    caps_ratio REAL NOT NULL,
    exclamation_count INTEGER NOT NULL,
    question_count INTEGER NOT NULL,
    direct_attack_score REAL NOT NULL,
    profanity_score REAL NOT NULL,
    negativity_score REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS incident_assessments (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    risk_score REAL NOT NULL,
    severity TEXT NOT NULL,
    conflict_present INTEGER NOT NULL,
    trigger_message_id TEXT,
    participants_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary_short TEXT NOT NULL,
    summary_long TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    incident_assessment_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    risk_score REAL NOT NULL,
    channel TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_assessment_id) REFERENCES incident_assessments(id)
);

CREATE TABLE IF NOT EXISTS moderator_feedback (
    id TEXT PRIMARY KEY,
    incident_assessment_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    feedback_note TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_assessment_id) REFERENCES incident_assessments(id)
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    report_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(group_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_incidents_group_time ON incident_assessments(group_id, created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_group_time ON alerts(group_id, created_at);
`

type Config struct {
	DBPath string
	Server struct {
		Host      string
		Port      int
		AuthToken string
	}
	Windows struct {
		ShortMinutes      int
		ShortMessageCount int
		ExpandedMinutes   int
		ExpandedMsgCount  int
	}
	Heuristics struct {
		LLMThreshold           float64
		HeuristicOnlyThreshold float64
		Weights                map[string]float64
	}
	Alerts struct {
		Channels        []string
		CooldownSeconds int
		MinimumSeverity string
	}
	Transcription struct {
		Provider  string
		Model     string
		BaseURL   string
		Timeout   time.Duration
		APIKeyEnv string
	}
	LLM struct {
		Provider  string
		Model     string
		BaseURL   string
		Timeout   time.Duration
		APIKeyEnv string
	}
}

type IncomingMessage struct {
	Platform          string                 `json:"platform"`
	ExternalGroupID   string                 `json:"external_group_id"`
	GroupName         string                 `json:"group_name"`
	ExternalUserID    string                 `json:"external_user_id"`
	UserName          string                 `json:"user_name"`
	MessageType       string                 `json:"message_type"`
	RawText           string                 `json:"raw_text"`
	TranscriptText    string                 `json:"transcript_text"`
	Language          string                 `json:"language"`
	ExternalMessageID string                 `json:"external_message_id"`
	ReceivedAt        string                 `json:"received_at"`
	SentAt            string                 `json:"sent_at"`
	ReplyToMessageID  *string                `json:"reply_to_message_id"`
	HasMedia          bool                   `json:"has_media"`
	MediaType         string                 `json:"media_type"`
	MediaPath         string                 `json:"media_path"`
	Metadata          map[string]interface{} `json:"metadata"`
}

type IngestResult struct {
	MessageID    string   `json:"message_id"`
	GroupID      string   `json:"group_id"`
	UserID       string   `json:"user_id"`
	AssessmentID *string  `json:"assessment_id"`
	AlertIDs     []string `json:"alert_ids"`
	Severity     string   `json:"severity"`
	RiskScore    float64  `json:"risk_score"`
}

type TranscriptionResult struct {
	TranscriptText  string
	Language        string
	Confidence      *float64
	DurationSeconds *float64
	Status          string
	ErrorMessage    string
}

type Classification struct {
	ConflictPresent   bool
	EscalationRisk    float64
	Severity          string
	Participants      []string
	TriggerMessageID  string
	Evidence          []map[string]string
	SummaryShort      string
	SummaryLong       string
	RecommendedAction string
	Confidence        float64
	UncertaintyNotes  string
}

type Service struct {
	cfg        Config
	db         *sql.DB
	httpClient *http.Client
}

func defaultConfig() Config {
	cfg := Config{}
	cfg.DBPath = "sentinel.db"
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = 8080
	cfg.Windows.ShortMinutes = 5
	cfg.Windows.ShortMessageCount = 20
	cfg.Windows.ExpandedMinutes = 15
	cfg.Windows.ExpandedMsgCount = 50
	cfg.Heuristics.LLMThreshold = 0.55
	cfg.Heuristics.HeuristicOnlyThreshold = 0.85
	cfg.Heuristics.Weights = map[string]float64{
		"hostility_density_score":         0.18,
		"dyadic_exchange_score":           0.16,
		"escalation_velocity_score":       0.14,
		"reply_concentration_score":       0.12,
		"participant_concentration_score": 0.10,
		"direct_attack_density":           0.10,
		"profanity_density":               0.08,
		"caps_intensity":                  0.06,
		"punctuation_aggression":          0.06,
	}
	cfg.Alerts.Channels = []string{"stdout"}
	cfg.Alerts.CooldownSeconds = 300
	cfg.Alerts.MinimumSeverity = "tensao"
	cfg.Transcription.Provider = "groq"
	cfg.Transcription.Model = "whisper-large-v3-turbo"
	cfg.Transcription.BaseURL = "https://api.groq.com/openai/v1"
	cfg.Transcription.Timeout = 120 * time.Second
	cfg.Transcription.APIKeyEnv = "GROQ_API_KEY"
	cfg.LLM.Provider = "gemini"
	cfg.LLM.Model = "gemini-2.5-flash"
	cfg.LLM.BaseURL = "https://generativelanguage.googleapis.com/v1beta"
	cfg.LLM.Timeout = 30 * time.Second
	cfg.LLM.APIKeyEnv = "GEMINI_API_KEY"
	return cfg
}

type tomlConfig struct {
	App struct {
		DBPath string `toml:"db_path"`
	} `toml:"app"`
	Server struct {
		Host      string `toml:"host"`
		Port      int    `toml:"port"`
		AuthToken string `toml:"auth_token"`
	} `toml:"server"`
	Windows struct {
		ShortMinutes      int `toml:"short_minutes"`
		ShortMessageCount int `toml:"short_message_count"`
		ExpandedMinutes   int `toml:"expanded_minutes"`
		ExpandedMsgCount  int `toml:"expanded_message_count"`
	} `toml:"windows"`
	Heuristics struct {
		LLMThreshold           float64            `toml:"llm_threshold"`
		HeuristicOnlyThreshold float64            `toml:"heuristic_only_threshold"`
		Weights                map[string]float64 `toml:"weights"`
	} `toml:"heuristics"`
	Alerts struct {
		Channels        []string `toml:"channels"`
		CooldownSeconds int      `toml:"cooldown_seconds"`
		MinimumSeverity string   `toml:"minimum_severity"`
	} `toml:"alerts"`
	Transcription struct {
		Provider  string `toml:"provider"`
		Model     string `toml:"model"`
		APIKeyEnv string `toml:"api_key_env"`
		BaseURL   string `toml:"base_url"`
		Timeout   int    `toml:"timeout_seconds"`
	} `toml:"transcription"`
	LLM struct {
		Provider  string `toml:"provider"`
		Model     string `toml:"model"`
		APIKeyEnv string `toml:"api_key_env"`
		BaseURL   string `toml:"base_url"`
		Timeout   int    `toml:"timeout_seconds"`
	} `toml:"llm"`
}

func loadConfig(path string) (Config, error) {
	cfg := defaultConfig()
	if strings.TrimSpace(path) != "" {
		data, err := os.ReadFile(path)
		if err != nil {
			return cfg, err
		}
		var parsed tomlConfig
		if err := toml.Unmarshal(data, &parsed); err != nil {
			return cfg, err
		}
		if strings.TrimSpace(parsed.App.DBPath) != "" {
			cfg.DBPath = parsed.App.DBPath
		}
		if strings.TrimSpace(parsed.Server.Host) != "" {
			cfg.Server.Host = parsed.Server.Host
		}
		if parsed.Server.Port > 0 {
			cfg.Server.Port = parsed.Server.Port
		}
		if strings.TrimSpace(parsed.Server.AuthToken) != "" {
			cfg.Server.AuthToken = parsed.Server.AuthToken
		}
		if parsed.Windows.ShortMinutes > 0 {
			cfg.Windows.ShortMinutes = parsed.Windows.ShortMinutes
		}
		if parsed.Windows.ShortMessageCount > 0 {
			cfg.Windows.ShortMessageCount = parsed.Windows.ShortMessageCount
		}
		if parsed.Windows.ExpandedMinutes > 0 {
			cfg.Windows.ExpandedMinutes = parsed.Windows.ExpandedMinutes
		}
		if parsed.Windows.ExpandedMsgCount > 0 {
			cfg.Windows.ExpandedMsgCount = parsed.Windows.ExpandedMsgCount
		}
		if parsed.Heuristics.LLMThreshold > 0 {
			cfg.Heuristics.LLMThreshold = parsed.Heuristics.LLMThreshold
		}
		if parsed.Heuristics.HeuristicOnlyThreshold > 0 {
			cfg.Heuristics.HeuristicOnlyThreshold = parsed.Heuristics.HeuristicOnlyThreshold
		}
		if len(parsed.Heuristics.Weights) > 0 {
			cfg.Heuristics.Weights = parsed.Heuristics.Weights
		}
		if len(parsed.Alerts.Channels) > 0 {
			cfg.Alerts.Channels = parsed.Alerts.Channels
		}
		if parsed.Alerts.CooldownSeconds > 0 {
			cfg.Alerts.CooldownSeconds = parsed.Alerts.CooldownSeconds
		}
		if strings.TrimSpace(parsed.Alerts.MinimumSeverity) != "" {
			cfg.Alerts.MinimumSeverity = parsed.Alerts.MinimumSeverity
		}
		if strings.TrimSpace(parsed.Transcription.Provider) != "" {
			cfg.Transcription.Provider = parsed.Transcription.Provider
		}
		if strings.TrimSpace(parsed.Transcription.Model) != "" {
			cfg.Transcription.Model = parsed.Transcription.Model
		}
		if strings.TrimSpace(parsed.Transcription.APIKeyEnv) != "" {
			cfg.Transcription.APIKeyEnv = parsed.Transcription.APIKeyEnv
		}
		if strings.TrimSpace(parsed.Transcription.BaseURL) != "" {
			cfg.Transcription.BaseURL = parsed.Transcription.BaseURL
		}
		if parsed.Transcription.Timeout > 0 {
			cfg.Transcription.Timeout = time.Duration(parsed.Transcription.Timeout) * time.Second
		}
		if strings.TrimSpace(parsed.LLM.Provider) != "" {
			cfg.LLM.Provider = parsed.LLM.Provider
		}
		if strings.TrimSpace(parsed.LLM.Model) != "" {
			cfg.LLM.Model = parsed.LLM.Model
		}
		if strings.TrimSpace(parsed.LLM.APIKeyEnv) != "" {
			cfg.LLM.APIKeyEnv = parsed.LLM.APIKeyEnv
		}
		if strings.TrimSpace(parsed.LLM.BaseURL) != "" {
			cfg.LLM.BaseURL = parsed.LLM.BaseURL
		}
		if parsed.LLM.Timeout > 0 {
			cfg.LLM.Timeout = time.Duration(parsed.LLM.Timeout) * time.Second
		}
	}
	if err := applyEnvOverrides(&cfg); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func applyEnvOverrides(cfg *Config) error {
	if v := strings.TrimSpace(os.Getenv("SENTINEL_DB_PATH")); v != "" {
		cfg.DBPath = v
	}
	if v := strings.TrimSpace(os.Getenv("SENTINEL_SERVER_HOST")); v != "" {
		cfg.Server.Host = v
	}
	if v := strings.TrimSpace(os.Getenv("SENTINEL_SERVER_PORT")); v != "" {
		port, err := strconv.Atoi(v)
		if err != nil {
			return errors.New("SENTINEL_SERVER_PORT deve ser inteiro")
		}
		cfg.Server.Port = port
	}
	if v := strings.TrimSpace(os.Getenv("SENTINEL_AUTH_TOKEN")); v != "" {
		cfg.Server.AuthToken = v
	}
	return nil
}

func loadDotenv(path string, override bool) (int, error) {
	file, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return 0, nil
		}
		return 0, err
	}
	loaded := 0
	lines := strings.Split(string(file), "\n")
	for idx, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if strings.HasPrefix(trimmed, "export ") {
			trimmed = strings.TrimSpace(strings.TrimPrefix(trimmed, "export "))
		}
		key, value, ok := strings.Cut(trimmed, "=")
		if !ok {
			return loaded, fmt.Errorf("linha invalida em %s:%d", path, idx+1)
		}
		key = strings.TrimSpace(key)
		if key == "" {
			return loaded, fmt.Errorf("chave vazia em %s:%d", path, idx+1)
		}
		if !override {
			if _, exists := os.LookupEnv(key); exists {
				continue
			}
		}
		value = strings.TrimSpace(value)
		if i := strings.Index(value, " #"); i >= 0 {
			value = strings.TrimSpace(value[:i])
		}
		if len(value) >= 2 && ((value[0] == '\'' && value[len(value)-1] == '\'') || (value[0] == '"' && value[len(value)-1] == '"')) {
			value = value[1 : len(value)-1]
		}
		if err := os.Setenv(key, value); err != nil {
			return loaded, err
		}
		loaded++
	}
	return loaded, nil
}

func openDB(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite3", fmt.Sprintf("file:%s?_foreign_keys=on", path))
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	return db, nil
}

func initDB(db *sql.DB) error {
	_, err := db.Exec(schemaSQL)
	return err
}

func newService(cfg Config) (*Service, error) {
	db, err := openDB(cfg.DBPath)
	if err != nil {
		return nil, err
	}
	if err := initDB(db); err != nil {
		db.Close()
		return nil, err
	}
	return &Service{
		cfg:        cfg,
		db:         db,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func (s *Service) close() error {
	return s.db.Close()
}

func makeID(prefix string) string {
	buf := make([]byte, 6)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
	}
	return fmt.Sprintf("%s_%s", prefix, hex.EncodeToString(buf))
}

func isoNow() string {
	return time.Now().UTC().Format(time.RFC3339)
}

func parseTimestamp(value string) time.Time {
	if strings.TrimSpace(value) == "" {
		return time.Now().UTC()
	}
	t, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return time.Now().UTC()
	}
	return t.UTC()
}

func normalizeText(text string) string {
	replacements := map[string]string{
		"😡": " [emoji_raiva] ",
		"🤡": " [emoji_deboche] ",
		"😂": " [emoji_riso] ",
		"🤣": " [emoji_riso] ",
		"😒": " [emoji_desdem] ",
	}
	normalized := strings.ReplaceAll(text, "\u200b", " ")
	for k, v := range replacements {
		normalized = strings.ReplaceAll(normalized, k, v)
	}
	return strings.Join(strings.Fields(normalized), " ")
}

func containsProfanity(text string) bool {
	lower := strings.ToLower(text)
	for _, word := range profanityWords {
		if strings.Contains(lower, strings.ToLower(word)) {
			return true
		}
	}
	return false
}

func containsDirectAddress(text string) bool {
	return directAddressPattern.MatchString(text)
}

func alphaCapsRatio(text string) float64 {
	letters := 0
	uppercase := 0
	for _, r := range text {
		if ('A' <= r && r <= 'Z') || ('a' <= r && r <= 'z') || ('À' <= r && r <= 'ÿ') {
			letters++
			if strings.ToUpper(string(r)) == string(r) && strings.ToLower(string(r)) != string(r) {
				uppercase++
			}
		}
	}
	if letters == 0 {
		return 0
	}
	return float64(uppercase) / float64(letters)
}

type MessageFeature struct {
	CapsRatio        float64
	ExclamationCount int
	QuestionCount    int
	DirectAttack     float64
	Profanity        float64
	Negativity       float64
}

func computeMessageFeatures(text string, replyTo *string, hasProfanity bool, hasDirectAddress bool) MessageFeature {
	lower := strings.ToLower(text)
	negHits := 0
	for _, term := range negativeTerms {
		if strings.Contains(lower, strings.ToLower(term)) {
			negHits++
		}
	}
	negScore := minFloat(1, float64(negHits)/2.0)
	direct := 0.0
	if hasDirectAddress && (negScore > 0 || hasProfanity) {
		direct = 0.8
	}
	if strings.Contains(lower, "idiota") || strings.Contains(lower, "burro") || strings.Contains(lower, "imbecil") {
		direct = 1.0
	}
	return MessageFeature{
		CapsRatio:        round(alphaCapsRatio(text), 4),
		ExclamationCount: strings.Count(text, "!"),
		QuestionCount:    strings.Count(text, "?"),
		DirectAttack:     round(direct, 4),
		Profanity:        boolToFloat(hasProfanity),
		Negativity:       round(negScore, 4),
	}
}

func boolToFloat(v bool) float64 {
	if v {
		return 1
	}
	return 0
}

type windowRow struct {
	MessageID      string
	UserID         string
	AuthorName     string
	MessageType    string
	SortTS         time.Time
	AnalysisText   string
	ReplyTo        *string
	CapsRatio      float64
	ExclamationCnt int
	QuestionCnt    int
	DirectAttack   float64
	Profanity      float64
	Negativity     float64
}

type windowFeatures struct {
	Signals map[string]float64
	Risk    float64
}

type messageItem struct {
	messageType string
	text        string
}

func (s *Service) computeWindowFeatures(rows []windowRow) windowFeatures {
	if len(rows) == 0 {
		return windowFeatures{Signals: map[string]float64{}, Risk: 0}
	}
	start := rows[0].SortTS
	end := rows[len(rows)-1].SortTS
	durationMinutes := maxFloat(1.0, end.Sub(start).Minutes())
	messagesPerMinute := float64(len(rows)) / durationMinutes
	replyCount := 0
	userCounts := map[string]int{}
	aggressions := make([]float64, 0, len(rows))
	directTotal := 0.0
	profTotal := 0.0
	capsTotal := 0.0
	punctTotal := 0.0
	for _, r := range rows {
		if r.ReplyTo != nil && strings.TrimSpace(*r.ReplyTo) != "" {
			replyCount++
		}
		userCounts[r.UserID]++
		punct := minFloat(1.0, float64(r.ExclamationCnt+r.QuestionCnt)/6.0)
		aggr := minFloat(1.0, (r.DirectAttack*0.4)+(r.Profanity*0.3)+(r.Negativity*0.3))
		aggressions = append(aggressions, aggr)
		directTotal += r.DirectAttack
		profTotal += r.Profanity
		capsTotal += r.CapsRatio
		punctTotal += punct
	}
	replyConcentration := float64(replyCount) / float64(len(rows))
	sortedCounts := make([]int, 0, len(userCounts))
	for _, c := range userCounts {
		sortedCounts = append(sortedCounts, c)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(sortedCounts)))
	twoMost := 0
	for i, c := range sortedCounts {
		if i == 2 {
			break
		}
		twoMost += c
	}
	participantConcentration := float64(twoMost) / float64(len(rows))
	alternating := 0
	for i := 1; i < len(rows); i++ {
		if rows[i-1].UserID != rows[i].UserID {
			alternating++
		}
	}
	dyadicExchange := 0.0
	if len(rows) > 1 {
		dyadicExchange = float64(alternating) / float64(len(rows)-1)
	}
	third := (len(aggressions) + 2) / 3
	earlyAvg := average(aggressions[:third])
	lateAvg := average(aggressions[len(aggressions)-third:])
	escalationVelocity := clamp((lateAvg - earlyAvg + 0.25), 0, 1)
	hostilityDensity := average(aggressions)
	directDensity := directTotal / float64(len(rows))
	profanityDensity := profTotal / float64(len(rows))
	capsIntensity := capsTotal / float64(len(rows))
	punctAggression := punctTotal / float64(len(rows))
	signals := map[string]float64{
		"messages_per_minute":             round(messagesPerMinute, 4),
		"reply_concentration_score":       round(replyConcentration, 4),
		"dyadic_exchange_score":           round(dyadicExchange, 4),
		"participant_concentration_score": round(participantConcentration, 4),
		"escalation_velocity_score":       round(escalationVelocity, 4),
		"hostility_density_score":         round(hostilityDensity, 4),
		"direct_attack_density":           round(directDensity, 4),
		"profanity_density":               round(profanityDensity, 4),
		"caps_intensity":                  round(capsIntensity, 4),
		"punctuation_aggression":          round(punctAggression, 4),
	}
	risk := 0.0
	for key, weight := range s.cfg.Heuristics.Weights {
		risk += signals[key] * weight
	}
	return windowFeatures{
		Signals: signals,
		Risk:    round(clamp(risk, 0, 1), 4),
	}
}

func severityFromScore(score float64) string {
	if score < 0.35 {
		return "normal"
	}
	if score < 0.55 {
		return "atencao"
	}
	if score < 0.75 {
		return "tensao"
	}
	return "incendio"
}

func recommendedAction(severity string) string {
	switch severity {
	case "atencao":
		return "monitor"
	case "tensao":
		return "alert_moderator"
	case "incendio":
		return "alert_moderator_now"
	default:
		return "none"
	}
}

func (s *Service) IngestMessage(event IncomingMessage) (IngestResult, error) {
	if err := validateIncoming(event); err != nil {
		return IngestResult{}, err
	}
	now := isoNow()
	if strings.TrimSpace(event.ReceivedAt) == "" {
		event.ReceivedAt = now
	}
	if strings.TrimSpace(event.SentAt) == "" {
		event.SentAt = event.ReceivedAt
	}
	if event.Metadata == nil {
		event.Metadata = map[string]interface{}{}
	}
	messageID := strings.TrimSpace(event.ExternalMessageID)
	if messageID == "" {
		messageID = makeID("msg")
	}

	tx, err := s.db.Begin()
	if err != nil {
		return IngestResult{}, err
	}
	defer tx.Rollback()

	groupID, err := upsertGroup(tx, event.Platform, event.ExternalGroupID, firstNonEmpty(event.GroupName, event.ExternalGroupID))
	if err != nil {
		return IngestResult{}, err
	}
	userID, err := upsertUser(tx, event.Platform, event.ExternalUserID, firstNonEmpty(event.UserName, event.ExternalUserID))
	if err != nil {
		return IngestResult{}, err
	}
	_, err = tx.Exec(`
INSERT INTO messages (
	id, group_id, user_id, message_type, raw_text, transcript_text, received_at, sent_at,
	reply_to_message_id, has_media, media_type, media_path, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		messageID, groupID, userID, event.MessageType, nullable(event.RawText), nil, event.ReceivedAt, event.SentAt,
		event.ReplyToMessageID, boolToInt(event.HasMedia || event.MessageType == "audio"), nullable(event.MediaType), nullable(event.MediaPath), now,
	)
	if err != nil {
		return IngestResult{}, err
	}

	transcription := TranscriptionResult{Status: "not_applicable", Language: firstNonEmpty(event.Language, "pt-BR")}
	if event.MessageType == "audio" {
		transcription = s.transcribeAudio(event)
		_, err = tx.Exec(`
INSERT INTO audio_transcriptions (
	id, message_id, provider, model, transcript_text, language, confidence, duration_seconds, status, error_message, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			makeID("tr"),
			messageID,
			choose(event.TranscriptText != "", "provided", s.cfg.Transcription.Provider),
			choose(event.TranscriptText != "", "provided-transcript", s.cfg.Transcription.Model),
			nullable(transcription.TranscriptText),
			nullable(transcription.Language),
			nullableFloat(transcription.Confidence),
			nullableFloat(transcription.DurationSeconds),
			transcription.Status,
			nullable(transcription.ErrorMessage),
			now,
		)
		if err != nil {
			return IngestResult{}, err
		}
	}

	analysisText := firstNonEmpty(event.RawText, transcription.TranscriptText)
	normalized := normalizeText(analysisText)
	hasProfanity := containsProfanity(normalized)
	hasDirect := containsDirectAddress(normalized)
	_, err = tx.Exec(`
INSERT INTO normalized_messages (
	id, message_id, analysis_text, language, contains_profanity, contains_direct_address, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		makeID("nmsg"),
		messageID,
		normalized,
		firstNonEmpty(event.Language, "pt-BR"),
		boolToInt(hasProfanity),
		boolToInt(hasDirect),
		now,
	)
	if err != nil {
		return IngestResult{}, err
	}
	features := computeMessageFeatures(normalized, event.ReplyToMessageID, hasProfanity, hasDirect)
	_, err = tx.Exec(`
INSERT INTO message_features (
	id, message_id, caps_ratio, exclamation_count, question_count, direct_attack_score, profanity_score, negativity_score, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		makeID("mf"),
		messageID,
		features.CapsRatio,
		features.ExclamationCount,
		features.QuestionCount,
		features.DirectAttack,
		features.Profanity,
		features.Negativity,
		now,
	)
	if err != nil {
		return IngestResult{}, err
	}

	rows, err := fetchWindowRows(tx, groupID, parseTimestamp(event.SentAt), s.cfg.Windows.ShortMinutes, s.cfg.Windows.ShortMessageCount)
	if err != nil {
		return IngestResult{}, err
	}
	window := s.computeWindowFeatures(rows)
	classification := s.fallbackClassification(rows, window)
	if window.Risk >= s.cfg.Heuristics.LLMThreshold {
		if llm, llmErr := s.classifyWithGemini(rows, window); llmErr == nil {
			classification = llm
		}
	}
	assessmentID := makeID("inc")
	participantsJSON, _ := json.Marshal(classification.Participants)
	evidenceJSON, _ := json.Marshal(classification.Evidence)
	_, err = tx.Exec(`
INSERT INTO incident_assessments (
	id, group_id, risk_score, severity, conflict_present, trigger_message_id, participants_json, evidence_json,
	summary_short, summary_long, recommended_action, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		assessmentID,
		groupID,
		classification.EscalationRisk,
		classification.Severity,
		boolToInt(classification.ConflictPresent),
		nullable(classification.TriggerMessageID),
		string(participantsJSON),
		string(evidenceJSON),
		classification.SummaryShort,
		classification.SummaryLong,
		classification.RecommendedAction,
		event.SentAt,
	)
	if err != nil {
		return IngestResult{}, err
	}

	alertIDs, err := s.maybeEmitAlerts(tx, groupID, firstNonEmpty(event.GroupName, event.ExternalGroupID), assessmentID, classification, event.SentAt)
	if err != nil {
		return IngestResult{}, err
	}

	if err := tx.Commit(); err != nil {
		return IngestResult{}, err
	}
	return IngestResult{
		MessageID:    messageID,
		GroupID:      groupID,
		UserID:       userID,
		AssessmentID: &assessmentID,
		AlertIDs:     alertIDs,
		Severity:     classification.Severity,
		RiskScore:    classification.EscalationRisk,
	}, nil
}

func validateIncoming(event IncomingMessage) error {
	if strings.TrimSpace(event.Platform) == "" {
		return errors.New("platform e obrigatorio")
	}
	if strings.TrimSpace(event.ExternalGroupID) == "" {
		return errors.New("external_group_id e obrigatorio")
	}
	if strings.TrimSpace(event.ExternalUserID) == "" {
		return errors.New("external_user_id e obrigatorio")
	}
	messageType := strings.TrimSpace(strings.ToLower(event.MessageType))
	if messageType != "text" && messageType != "audio" {
		return errors.New("message_type invalido")
	}
	if messageType == "text" && strings.TrimSpace(event.RawText) == "" {
		return errors.New("raw_text e obrigatorio para message_type=text")
	}
	if messageType == "audio" && strings.TrimSpace(event.MediaPath) == "" && strings.TrimSpace(event.TranscriptText) == "" {
		return errors.New("audio exige media_path ou transcript_text")
	}
	return nil
}

func upsertGroup(tx *sql.Tx, platform, externalID, name string) (string, error) {
	var id string
	err := tx.QueryRow("SELECT id FROM groups WHERE platform = ? AND external_group_id = ?", platform, externalID).Scan(&id)
	switch {
	case err == nil:
		_, err = tx.Exec("UPDATE groups SET display_name = ?, updated_at = ? WHERE id = ?", name, isoNow(), id)
		return id, err
	case errors.Is(err, sql.ErrNoRows):
		id = makeID("grp")
		_, err = tx.Exec("INSERT INTO groups (id, platform, external_group_id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
			id, platform, externalID, name, isoNow(), isoNow())
		return id, err
	default:
		return "", err
	}
}

func upsertUser(tx *sql.Tx, platform, externalID, name string) (string, error) {
	var id string
	err := tx.QueryRow("SELECT id FROM users WHERE platform = ? AND external_user_id = ?", platform, externalID).Scan(&id)
	switch {
	case err == nil:
		_, err = tx.Exec("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?", name, isoNow(), id)
		return id, err
	case errors.Is(err, sql.ErrNoRows):
		id = makeID("usr")
		_, err = tx.Exec("INSERT INTO users (id, platform, external_user_id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
			id, platform, externalID, name, isoNow(), isoNow())
		return id, err
	default:
		return "", err
	}
}

func fetchWindowRows(tx *sql.Tx, groupID string, end time.Time, minutes int, maxMessages int) ([]windowRow, error) {
	rows, err := tx.Query(`
SELECT
	m.id,
	m.user_id,
	COALESCE(u.display_name, m.user_id),
	m.message_type,
	COALESCE(nm.analysis_text, m.raw_text, m.transcript_text, ''),
	m.reply_to_message_id,
	COALESCE(m.sent_at, m.received_at),
	COALESCE(mf.caps_ratio, 0),
	COALESCE(mf.exclamation_count, 0),
	COALESCE(mf.question_count, 0),
	COALESCE(mf.direct_attack_score, 0),
	COALESCE(mf.profanity_score, 0),
	COALESCE(mf.negativity_score, 0)
FROM messages m
JOIN users u ON u.id = m.user_id
LEFT JOIN normalized_messages nm ON nm.message_id = m.id
LEFT JOIN message_features mf ON mf.message_id = m.id
WHERE m.group_id = ?
ORDER BY COALESCE(m.sent_at, m.received_at) DESC
LIMIT ?`, groupID, maxMessages)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cutoff := end.Add(-time.Duration(minutes) * time.Minute)
	out := make([]windowRow, 0, maxMessages)
	for rows.Next() {
		var row windowRow
		var ts string
		if err := rows.Scan(
			&row.MessageID,
			&row.UserID,
			&row.AuthorName,
			&row.MessageType,
			&row.AnalysisText,
			&row.ReplyTo,
			&ts,
			&row.CapsRatio,
			&row.ExclamationCnt,
			&row.QuestionCnt,
			&row.DirectAttack,
			&row.Profanity,
			&row.Negativity,
		); err != nil {
			return nil, err
		}
		row.SortTS = parseTimestamp(ts)
		if row.SortTS.Before(cutoff) || row.SortTS.After(end.Add(2*time.Second)) {
			continue
		}
		out = append(out, row)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].SortTS.Before(out[j].SortTS) })
	return out, rows.Err()
}

func (s *Service) fallbackClassification(rows []windowRow, wf windowFeatures) Classification {
	participants := uniqueParticipants(rows, 3)
	triggerID := ""
	best := -1.0
	for _, row := range rows {
		score := row.DirectAttack + row.Profanity + row.Negativity
		if score > best {
			best = score
			triggerID = row.MessageID
		}
	}
	risk := wf.Risk
	severity := severityFromScore(risk)
	dyadic := wf.Signals["dyadic_exchange_score"]
	direct := wf.Signals["direct_attack_density"]
	hostility := wf.Signals["hostility_density_score"]
	profanity := wf.Signals["profanity_density"]
	punct := wf.Signals["punctuation_aggression"]
	if len(rows) < 2 && severity == "atencao" && hostility < 0.55 && profanity == 0 {
		severity = "normal"
		risk = minFloat(risk, 0.34)
	}
	if severity == "atencao" {
		if direct >= 0.28 && dyadic >= 0.75 {
			severity = "tensao"
			risk = maxFloat(risk, 0.58)
		} else if hostility >= 0.40 && punct >= 0.35 {
			severity = "tensao"
			risk = maxFloat(risk, 0.56)
		}
	}
	if severity == "tensao" {
		if direct >= 0.50 && hostility >= 0.55 {
			severity = "incendio"
			risk = maxFloat(risk, 0.78)
		} else if hostility >= 0.50 && profanity >= 0.40 && dyadic >= 0.80 {
			severity = "incendio"
			risk = maxFloat(risk, 0.76)
		}
	}
	conflict := severity == "tensao" || severity == "incendio"
	evidence := []map[string]string{}
	if triggerID != "" {
		evidence = append(evidence, map[string]string{"message_id": triggerID, "reason": "heuristic_peak"})
	}
	return Classification{
		ConflictPresent:   conflict,
		EscalationRisk:    round(risk, 4),
		Severity:          severity,
		Participants:      participants,
		TriggerMessageID:  triggerID,
		Evidence:          evidence,
		SummaryShort:      fmt.Sprintf("Janela com risco %.2f e sinais de escalada detectados.", risk),
		SummaryLong:       fmt.Sprintf("Classificacao por heuristica local. Sinais: %s", mustJSON(wf.Signals)),
		RecommendedAction: recommendedAction(severity),
		Confidence:        chooseFloat(conflict, 0.58, 0.72),
		UncertaintyNotes:  "Classificacao sem LLM externo; usar como triagem inicial.",
	}
}

func (s *Service) classifyWithGemini(rows []windowRow, wf windowFeatures) (Classification, error) {
	if s.cfg.LLM.Provider != "gemini" {
		return Classification{}, errors.New("llm provider nao suportado")
	}
	apiKey := strings.TrimSpace(os.Getenv(s.cfg.LLM.APIKeyEnv))
	if apiKey == "" {
		return Classification{}, errors.New("gemini api key ausente")
	}
	lines := make([]string, 0, len(rows))
	for _, row := range rows {
		lines = append(lines, fmt.Sprintf("- %s | %s | %s", row.SortTS.Format(time.RFC3339), row.AuthorName, row.AnalysisText))
	}
	prompt := fmt.Sprintf(
		"Classifique risco relacional (normal/atencao/tensao/incendio) e responda JSON com campos: conflict_present, escalation_risk, severity, participants, trigger_message_id, evidence, summary_short, summary_long, recommended_action, confidence, uncertainty_notes.\nSinais: %s\nMensagens:\n%s",
		mustJSON(wf.Signals),
		strings.Join(lines, "\n"),
	)
	body := map[string]interface{}{
		"contents": []map[string]interface{}{
			{
				"role": "user",
				"parts": []map[string]string{
					{"text": prompt},
				},
			},
		},
		"generationConfig": map[string]interface{}{
			"responseMimeType": "application/json",
			"temperature":      0,
		},
	}
	data, _ := json.Marshal(body)
	req, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/models/%s:generateContent", strings.TrimRight(s.cfg.LLM.BaseURL, "/"), s.cfg.LLM.Model), bytes.NewReader(data))
	if err != nil {
		return Classification{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-goog-api-key", apiKey)
	client := &http.Client{Timeout: s.cfg.LLM.Timeout}
	resp, err := client.Do(req)
	if err != nil {
		return Classification{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return Classification{}, fmt.Errorf("gemini http %d", resp.StatusCode)
	}
	var payload map[string]interface{}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return Classification{}, err
	}
	text, err := extractGeminiText(payload)
	if err != nil {
		return Classification{}, err
	}
	var parsed map[string]interface{}
	if err := json.Unmarshal([]byte(text), &parsed); err != nil {
		return Classification{}, err
	}
	classification := Classification{
		ConflictPresent:   toBool(parsed["conflict_present"]),
		EscalationRisk:    clamp(toFloat(parsed["escalation_risk"]), 0, 1),
		Severity:          sanitizeSeverity(toString(parsed["severity"])),
		Participants:      toStringSlice(parsed["participants"]),
		TriggerMessageID:  toString(parsed["trigger_message_id"]),
		Evidence:          toEvidence(parsed["evidence"]),
		SummaryShort:      firstNonEmpty(toString(parsed["summary_short"]), "Classificacao gerada por LLM"),
		SummaryLong:       firstNonEmpty(toString(parsed["summary_long"]), "Sem detalhes adicionais."),
		RecommendedAction: firstNonEmpty(toString(parsed["recommended_action"]), recommendedAction(sanitizeSeverity(toString(parsed["severity"])))),
		Confidence:        clamp(toFloat(parsed["confidence"]), 0, 1),
		UncertaintyNotes:  toString(parsed["uncertainty_notes"]),
	}
	if classification.Severity == "" {
		return Classification{}, errors.New("severity ausente")
	}
	messageIDs := map[string]struct{}{}
	for _, row := range rows {
		messageIDs[row.MessageID] = struct{}{}
	}
	if classification.TriggerMessageID != "" {
		if _, ok := messageIDs[classification.TriggerMessageID]; !ok {
			return Classification{}, errors.New("trigger_message_id fora da janela")
		}
	}
	return classification, nil
}

func extractGeminiText(payload map[string]interface{}) (string, error) {
	candidates, ok := payload["candidates"].([]interface{})
	if !ok || len(candidates) == 0 {
		return "", errors.New("Gemini retornou sem candidates")
	}
	first, ok := candidates[0].(map[string]interface{})
	if !ok {
		return "", errors.New("gemini candidate invalido")
	}
	content, ok := first["content"].(map[string]interface{})
	if !ok {
		return "", errors.New("gemini content invalido")
	}
	parts, ok := content["parts"].([]interface{})
	if !ok || len(parts) == 0 {
		return "", errors.New("Gemini candidate sem parts")
	}
	var builder strings.Builder
	for _, part := range parts {
		if p, ok := part.(map[string]interface{}); ok {
			builder.WriteString(toString(p["text"]))
		}
	}
	text := strings.TrimSpace(builder.String())
	if text == "" {
		return "", errors.New("gemini sem texto")
	}
	return text, nil
}

func (s *Service) transcribeAudio(event IncomingMessage) TranscriptionResult {
	if strings.TrimSpace(event.TranscriptText) != "" {
		return TranscriptionResult{
			TranscriptText: event.TranscriptText,
			Language:       firstNonEmpty(event.Language, "pt-BR"),
			Status:         "completed",
		}
	}
	if s.cfg.Transcription.Provider != "groq" {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: "Transcriber provider nao configurado."}
	}
	apiKey := strings.TrimSpace(os.Getenv(s.cfg.Transcription.APIKeyEnv))
	if apiKey == "" {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: "Transcriber provider nao configurado."}
	}
	audioPath := strings.TrimSpace(event.MediaPath)
	if audioPath == "" {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: "Arquivo de audio nao encontrado"}
	}
	fileBytes, err := os.ReadFile(audioPath)
	if err != nil {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: fmt.Sprintf("Arquivo de audio nao encontrado: %s", audioPath)}
	}
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	_ = writer.WriteField("model", s.cfg.Transcription.Model)
	_ = writer.WriteField("response_format", "verbose_json")
	if lang := normalizeGroqLanguage(event.Language); lang != "" {
		_ = writer.WriteField("language", lang)
	}
	contentType := mime.TypeByExtension(filepath.Ext(audioPath))
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	part, err := writer.CreateFormFile("file", filepath.Base(audioPath))
	if err != nil {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: err.Error()}
	}
	_, _ = part.Write(fileBytes)
	_ = writer.Close()
	req, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/audio/transcriptions", strings.TrimRight(s.cfg.Transcription.BaseURL, "/")), body)
	if err != nil {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: err.Error()}
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("User-Agent", "Sentinel-Go/1.0")
	client := &http.Client{Timeout: s.cfg.Transcription.Timeout}
	resp, err := client.Do(req)
	if err != nil {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: fmt.Sprintf("Groq network error: %v", err)}
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return TranscriptionResult{Status: "failed", Language: event.Language, ErrorMessage: fmt.Sprintf("Groq HTTP %d: %s", resp.StatusCode, string(raw))}
	}
	return parseGroqTranscriptionResponse(string(raw), firstNonEmpty(event.Language, "pt-BR"))
}

func normalizeGroqLanguage(language string) string {
	lang := strings.TrimSpace(strings.ReplaceAll(language, "_", "-"))
	if lang == "" {
		return ""
	}
	return strings.ToLower(strings.Split(lang, "-")[0])
}

func parseGroqTranscriptionResponse(body string, fallbackLanguage string) TranscriptionResult {
	trimmed := strings.TrimSpace(body)
	if trimmed == "" {
		return TranscriptionResult{Status: "failed", Language: fallbackLanguage, ErrorMessage: "Groq returned empty response"}
	}
	var payload map[string]interface{}
	if err := json.Unmarshal([]byte(trimmed), &payload); err != nil {
		return TranscriptionResult{
			Status:         "completed",
			Language:       fallbackLanguage,
			TranscriptText: trimmed,
		}
	}
	return parseGroqTranscriptionPayload(payload, fallbackLanguage)
}

func parseGroqTranscriptionPayload(payload map[string]interface{}, fallbackLanguage string) TranscriptionResult {
	text := strings.TrimSpace(toString(payload["text"]))
	lang := firstNonEmpty(toString(payload["language"]), fallbackLanguage)
	segments, _ := payload["segments"].([]interface{})
	var duration float64
	var avgLogprob float64
	var logCount int
	for _, item := range segments {
		if seg, ok := item.(map[string]interface{}); ok {
			end := toFloat(seg["end"])
			if end > duration {
				duration = end
			}
			if _, exists := seg["avg_logprob"]; exists {
				avgLogprob += toFloat(seg["avg_logprob"])
				logCount++
			}
		}
	}
	var confidence *float64
	if logCount > 0 {
		c := clamp(1.0+(avgLogprob/float64(logCount))/5.0, 0, 1)
		c = round(c, 4)
		confidence = &c
	}
	var durPtr *float64
	if duration > 0 {
		d := round(duration, 3)
		durPtr = &d
	}
	if text == "" {
		return TranscriptionResult{
			Status:          "failed",
			Language:        lang,
			Confidence:      confidence,
			DurationSeconds: durPtr,
			ErrorMessage:    "Groq response had no text field",
		}
	}
	return TranscriptionResult{
		Status:          "completed",
		Language:        lang,
		TranscriptText:  text,
		Confidence:      confidence,
		DurationSeconds: durPtr,
	}
}

func (s *Service) maybeEmitAlerts(tx *sql.Tx, groupID, groupName, assessmentID string, classification Classification, createdAt string) ([]string, error) {
	if severityRank(classification.Severity) < severityRank(s.cfg.Alerts.MinimumSeverity) {
		return []string{}, nil
	}
	var lastCreated string
	var lastRisk float64
	err := tx.QueryRow("SELECT created_at, risk_score FROM alerts WHERE group_id = ? ORDER BY created_at DESC LIMIT 1", groupID).Scan(&lastCreated, &lastRisk)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	if err == nil {
		lastTS := parseTimestamp(lastCreated)
		if time.Since(lastTS) < time.Duration(s.cfg.Alerts.CooldownSeconds)*time.Second && (classification.EscalationRisk-lastRisk) <= 0.15 && classification.Severity != "incendio" {
			return []string{}, nil
		}
	}
	ids := make([]string, 0, len(s.cfg.Alerts.Channels))
	alertCreatedAt := firstNonEmpty(createdAt, isoNow())
	for _, channel := range s.cfg.Alerts.Channels {
		alertID := makeID("alt")
		payload := map[string]interface{}{
			"alert_id":               alertID,
			"incident_assessment_id": assessmentID,
			"group_id":               groupID,
			"group_name":             groupName,
			"severity":               classification.Severity,
			"risk_score":             classification.EscalationRisk,
			"created_at":             alertCreatedAt,
			"participants":           classification.Participants,
			"trigger_message_id":     nullable(classification.TriggerMessageID),
			"summary_short":          classification.SummaryShort,
			"recommended_action":     classification.RecommendedAction,
			"evidence":               classification.Evidence,
		}
		payloadJSON, _ := json.Marshal(payload)
		_, err := tx.Exec(
			"INSERT INTO alerts (id, incident_assessment_id, group_id, severity, risk_score, channel, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			alertID, assessmentID, groupID, classification.Severity, classification.EscalationRisk, channel, string(payloadJSON), alertCreatedAt,
		)
		if err != nil {
			return nil, err
		}
		if channel == "stdout" {
			fmt.Printf("ALERTA: %s\nGrupo: %s\nRisco: %.2f\nResumo: %s\n", strings.ToUpper(classification.Severity), groupName, classification.EscalationRisk, classification.SummaryShort)
			fmt.Println(string(payloadJSON))
		}
		ids = append(ids, alertID)
	}
	return ids, nil
}

func (s *Service) BuildDailyReport(groupID, reportDate string) (string, map[string]interface{}, error) {
	start := reportDate + "T00:00:00Z"
	end := reportDate + "T23:59:59Z"
	rows, err := s.db.Query(`
SELECT id, message_type, COALESCE(sent_at, received_at), COALESCE(raw_text, transcript_text, '')
FROM messages
WHERE group_id = ? AND COALESCE(sent_at, received_at) BETWEEN ? AND ?
ORDER BY COALESCE(sent_at, received_at) ASC`, groupID, start, end)
	if err != nil {
		return "", nil, err
	}
	defer rows.Close()
	messages := []messageItem{}
	audioTotal := 0
	for rows.Next() {
		var id, typ, ts, txt string
		if err := rows.Scan(&id, &typ, &ts, &txt); err != nil {
			return "", nil, err
		}
		messages = append(messages, messageItem{messageType: typ, text: txt})
		if typ == "audio" {
			audioTotal++
		}
	}
	var authorTotal int
	if err := s.db.QueryRow("SELECT COUNT(DISTINCT user_id) FROM messages WHERE group_id = ? AND COALESCE(sent_at, received_at) BETWEEN ? AND ?", groupID, start, end).Scan(&authorTotal); err != nil {
		return "", nil, err
	}
	incidentRows, err := s.db.Query(`
SELECT id, severity, summary_short, trigger_message_id, participants_json, created_at
FROM incident_assessments
WHERE group_id = ? AND created_at BETWEEN ? AND ?
ORDER BY risk_score DESC, created_at ASC`, groupID, start, end)
	if err != nil {
		return "", nil, err
	}
	defer incidentRows.Close()
	critical := []map[string]interface{}{}
	attention := []map[string]interface{}{}
	for incidentRows.Next() {
		var id, severity, summaryShort, triggerID, participantsJSON, createdAt string
		if err := incidentRows.Scan(&id, &severity, &summaryShort, &triggerID, &participantsJSON, &createdAt); err != nil {
			return "", nil, err
		}
		item := map[string]interface{}{
			"incident_id":        id,
			"severity":           severity,
			"participants":       mustJSONArray(participantsJSON),
			"summary_short":      summaryShort,
			"trigger_message_id": nullable(triggerID),
			"review_status":      "pendente",
			"created_at":         createdAt,
		}
		if severity == "atencao" {
			attention = append(attention, item)
		}
		if severity == "tensao" || severity == "incendio" {
			critical = append(critical, item)
		}
	}
	topics := extractTopics(messages)
	payload := map[string]interface{}{
		"group_id":                   groupID,
		"report_date":                reportDate,
		"message_total":              len(messages),
		"author_total":               authorTotal,
		"audio_total":                audioTotal,
		"transcribed_minutes":        0.0,
		"transcription_success_rate": 0.0,
		"topics":                     topics,
		"critical_incidents":         critical,
		"attention_incidents":        attention,
	}
	markdownLines := []string{
		fmt.Sprintf("# Relatorio Diario - %s", reportDate),
		"",
		fmt.Sprintf("- Grupo: `%s`", groupID),
		fmt.Sprintf("- Total de mensagens: %d", len(messages)),
		fmt.Sprintf("- Total de autores: %d", authorTotal),
		fmt.Sprintf("- Total de audios: %d", audioTotal),
		"",
		"## Topicos discutidos",
	}
	if len(topics) == 0 {
		markdownLines = append(markdownLines, "- Nenhum topico identificavel")
	} else {
		for _, topic := range topics {
			markdownLines = append(markdownLines, "- "+topic)
		}
	}
	markdownLines = append(markdownLines, "", "## Incidentes potenciais")
	if len(critical) == 0 {
		markdownLines = append(markdownLines, "- Nenhum incidente de tensao/incendio no periodo")
	} else {
		for _, item := range critical {
			markdownLines = append(markdownLines, fmt.Sprintf("- %v | %v | %v", item["created_at"], item["severity"], item["summary_short"]))
		}
	}
	markdown := strings.Join(markdownLines, "\n")
	payloadJSON, _ := json.Marshal(payload)
	_, err = s.db.Exec(`
INSERT INTO daily_reports (id, group_id, report_date, report_markdown, report_payload_json, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(group_id, report_date) DO UPDATE SET
	report_markdown=excluded.report_markdown,
	report_payload_json=excluded.report_payload_json,
	created_at=excluded.created_at`,
		makeID("rpt"), groupID, reportDate, markdown, string(payloadJSON), isoNow(),
	)
	if err != nil {
		return "", nil, err
	}
	return markdown, payload, nil
}

func extractTopics(messages []messageItem) []string {
	counts := map[string]int{}
	re := regexp.MustCompile(`[A-Za-zÀ-ÿ]{4,}`)
	for _, item := range messages {
		for _, token := range re.FindAllString(strings.ToLower(item.text), -1) {
			if _, stop := stopwords[token]; stop {
				continue
			}
			counts[token]++
		}
	}
	type topicCount struct {
		token string
		count int
	}
	items := make([]topicCount, 0, len(counts))
	for token, count := range counts {
		items = append(items, topicCount{token: token, count: count})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].count == items[j].count {
			return items[i].token < items[j].token
		}
		return items[i].count > items[j].count
	})
	out := []string{}
	for i, item := range items {
		if i >= 5 {
			break
		}
		out = append(out, item.token)
	}
	return out
}

func (s *Service) RecordFeedback(incidentID, feedbackType, note, reviewerID string) (string, error) {
	valid := map[string]struct{}{
		"correto":                  {},
		"exagerado":                {},
		"incorreto":                {},
		"util apesar de impreciso": {},
		"revisado manualmente":     {},
	}
	if _, ok := valid[feedbackType]; !ok {
		return "", fmt.Errorf("feedback_type invalido: %s", feedbackType)
	}
	id := makeID("fb")
	_, err := s.db.Exec(
		"INSERT INTO moderator_feedback (id, incident_assessment_id, feedback_type, feedback_note, reviewer_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		id, incidentID, feedbackType, nullable(note), nullable(reviewerID), isoNow(),
	)
	return id, err
}

func newHTTPHandler(s *Service, cfg Config) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/ingest", requirePostAuthorized(cfg, func(w http.ResponseWriter, r *http.Request) {
		var payload IncomingMessage
		if err := decodeJSONBody(r, &payload); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		result, err := s.IngestMessage(payload)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, result)
	}))
	mux.HandleFunc("/feedback", requirePostAuthorized(cfg, func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			IncidentID   string `json:"incident_id"`
			FeedbackType string `json:"feedback_type"`
			Note         string `json:"note"`
			ReviewerID   string `json:"reviewer_id"`
		}
		if err := decodeJSONBody(r, &payload); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		id, err := s.RecordFeedback(payload.IncidentID, payload.FeedbackType, payload.Note, payload.ReviewerID)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"feedback_id": id})
	}))
	mux.HandleFunc("/report-daily", requirePostAuthorized(cfg, func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			GroupID string `json:"group_id"`
			Date    string `json:"date"`
		}
		if err := decodeJSONBody(r, &payload); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		md, out, err := s.BuildDailyReport(payload.GroupID, payload.Date)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{"markdown": md, "payload": out})
	}))
	return mux
}

func requirePostAuthorized(cfg Config, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "not_found"})
			return
		}
		if !authorize(r, cfg.Server.AuthToken) {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next(w, r)
	}
}

func authorize(r *http.Request, token string) bool {
	token = strings.TrimSpace(token)
	if token == "" {
		return true
	}
	return r.Header.Get("Authorization") == "Bearer "+token
}

func decodeJSONBody(r *http.Request, dest interface{}) error {
	defer r.Body.Close()
	body, err := io.ReadAll(io.LimitReader(r.Body, 10<<20))
	if err != nil {
		return err
	}
	if len(body) == 0 {
		return errors.New("body vazio")
	}
	if err := json.Unmarshal(body, dest); err != nil {
		return errors.New("json invalido")
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, payload interface{}) {
	data, _ := json.Marshal(payload)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

func runServer(cfg Config) error {
	service, err := newService(cfg)
	if err != nil {
		return err
	}
	defer service.close()
	server := &http.Server{
		Addr:    fmt.Sprintf("%s:%d", cfg.Server.Host, cfg.Server.Port),
		Handler: newHTTPHandler(service, cfg),
	}
	return server.ListenAndServe()
}

func parseGlobal(args []string) (Config, string, []string, error) {
	cfg := defaultConfig()
	fs := flag.NewFlagSet("sentinel", flag.ContinueOnError)
	var configPath string
	var envFile string
	var noEnv bool
	fs.StringVar(&configPath, "config", "", "compatibility flag (ignored in Go rewrite)")
	fs.StringVar(&envFile, "env-file", ".env", "dotenv file path")
	fs.BoolVar(&noEnv, "no-env-file", false, "disable dotenv loading")
	if err := fs.Parse(args); err != nil {
		return cfg, "", nil, err
	}
	if !noEnv {
		if _, err := loadDotenv(envFile, false); err != nil {
			return cfg, "", nil, err
		}
	}
	loaded, err := loadConfig(configPath)
	if err != nil {
		return cfg, "", nil, err
	}
	cfg = loaded
	remaining := fs.Args()
	if len(remaining) == 0 {
		return cfg, "", nil, errors.New("comando obrigatorio")
	}
	return cfg, remaining[0], remaining[1:], nil
}

type cliCommandError struct {
	code int
	err  error
}

func (e *cliCommandError) Error() string {
	return e.err.Error()
}

func (e *cliCommandError) Unwrap() error {
	return e.err
}

func wrapCommandError(code int, err error) error {
	if err == nil {
		return nil
	}
	return &cliCommandError{code: code, err: err}
}

func commandExitCode(err error) int {
	var cmdErr *cliCommandError
	if errors.As(err, &cmdErr) {
		return cmdErr.code
	}
	return 1
}

func withService(cfg Config, fn func(*Service) error) error {
	service, err := newService(cfg)
	if err != nil {
		return err
	}
	defer service.close()
	return fn(service)
}

func runInitDBCommand(cfg Config, args []string) error {
	fs := flag.NewFlagSet("init-db", flag.ContinueOnError)
	dbPath := fs.String("db", cfg.DBPath, "db path")
	if err := fs.Parse(args); err != nil {
		return wrapCommandError(2, err)
	}
	db, err := openDB(*dbPath)
	if err != nil {
		return wrapCommandError(1, err)
	}
	defer db.Close()
	if err := initDB(db); err != nil {
		return wrapCommandError(1, err)
	}
	fmt.Println(*dbPath)
	return nil
}

func runServeCommand(cfg Config, args []string) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	dbPath := fs.String("db", cfg.DBPath, "db path")
	host := fs.String("host", cfg.Server.Host, "host")
	port := fs.Int("port", cfg.Server.Port, "port")
	if err := fs.Parse(args); err != nil {
		return wrapCommandError(2, err)
	}
	cfg.DBPath = *dbPath
	cfg.Server.Host = *host
	cfg.Server.Port = *port
	if err := runServer(cfg); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return wrapCommandError(1, err)
	}
	return nil
}

func runIngestCommand(cfg Config, args []string) error {
	fs := flag.NewFlagSet("ingest", flag.ContinueOnError)
	dbPath := fs.String("db", cfg.DBPath, "db path")
	eventFile := fs.String("event-file", "", "event json file")
	if err := fs.Parse(args); err != nil {
		return wrapCommandError(2, err)
	}
	if strings.TrimSpace(*eventFile) == "" {
		return wrapCommandError(2, errors.New("--event-file e obrigatorio"))
	}
	cfg.DBPath = *dbPath
	return withService(cfg, func(service *Service) error {
		raw, err := os.ReadFile(*eventFile)
		if err != nil {
			return wrapCommandError(1, err)
		}
		var payload IncomingMessage
		if err := json.Unmarshal(raw, &payload); err != nil {
			return wrapCommandError(2, err)
		}
		result, err := service.IngestMessage(payload)
		if err != nil {
			return wrapCommandError(2, err)
		}
		data, _ := json.Marshal(result)
		fmt.Println(string(data))
		return nil
	})
}

func runReportDailyCommand(cfg Config, args []string) error {
	fs := flag.NewFlagSet("report-daily", flag.ContinueOnError)
	dbPath := fs.String("db", cfg.DBPath, "db path")
	groupID := fs.String("group-id", "", "group id")
	date := fs.String("date", "", "date YYYY-MM-DD")
	if err := fs.Parse(args); err != nil {
		return wrapCommandError(2, err)
	}
	if *groupID == "" || *date == "" {
		return wrapCommandError(2, errors.New("--group-id e --date sao obrigatorios"))
	}
	cfg.DBPath = *dbPath
	return withService(cfg, func(service *Service) error {
		md, payload, err := service.BuildDailyReport(*groupID, *date)
		if err != nil {
			return wrapCommandError(2, err)
		}
		fmt.Println(md)
		data, _ := json.Marshal(payload)
		fmt.Println(string(data))
		return nil
	})
}

func runFeedbackCommand(cfg Config, args []string) error {
	fs := flag.NewFlagSet("feedback", flag.ContinueOnError)
	dbPath := fs.String("db", cfg.DBPath, "db path")
	incidentID := fs.String("incident-id", "", "incident id")
	feedbackType := fs.String("feedback-type", "", "feedback type")
	note := fs.String("note", "", "feedback note")
	reviewerID := fs.String("reviewer-id", "", "reviewer id")
	if err := fs.Parse(args); err != nil {
		return wrapCommandError(2, err)
	}
	if *incidentID == "" || *feedbackType == "" {
		return wrapCommandError(2, errors.New("--incident-id e --feedback-type sao obrigatorios"))
	}
	cfg.DBPath = *dbPath
	return withService(cfg, func(service *Service) error {
		id, err := service.RecordFeedback(*incidentID, *feedbackType, *note, *reviewerID)
		if err != nil {
			return wrapCommandError(2, err)
		}
		fmt.Println(id)
		return nil
	})
}

func executeCommand(cfg Config, cmd string, args []string) error {
	switch cmd {
	case "init-db":
		return runInitDBCommand(cfg, args)
	case "serve":
		return runServeCommand(cfg, args)
	case "ingest":
		return runIngestCommand(cfg, args)
	case "report-daily":
		return runReportDailyCommand(cfg, args)
	case "feedback":
		return runFeedbackCommand(cfg, args)
	default:
		return wrapCommandError(2, fmt.Errorf("comando desconhecido: %s", cmd))
	}
}

func main() {
	cfg, cmd, args, err := parseGlobal(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := executeCommand(cfg, cmd, args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(commandExitCode(err))
	}
}

func round(value float64, places int) float64 {
	pow := mathPow10(places)
	return float64(int(value*pow+chooseFloat(value >= 0, 0.5, -0.5))) / pow
}

func mathPow10(exp int) float64 {
	out := 1.0
	for i := 0; i < exp; i++ {
		out *= 10
	}
	return out
}

func clamp(v, min, max float64) float64 {
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

func average(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	total := 0.0
	for _, v := range values {
		total += v
	}
	return total / float64(len(values))
}

func tokenEstimate(text string) int {
	chars := len(strings.TrimSpace(text))
	if chars == 0 {
		return 0
	}
	return (chars + 3) / 4
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func choose[T any](cond bool, a, b T) T {
	if cond {
		return a
	}
	return b
}

func chooseFloat(cond bool, a, b float64) float64 {
	if cond {
		return a
	}
	return b
}

func nullable(value string) interface{} {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func nullableFloat(value *float64) interface{} {
	if value == nil {
		return nil
	}
	return *value
}

func boolToInt(v bool) int {
	if v {
		return 1
	}
	return 0
}

func severityRank(severity string) int {
	switch sanitizeSeverity(severity) {
	case "normal":
		return 0
	case "atencao":
		return 1
	case "tensao":
		return 2
	case "incendio":
		return 3
	default:
		return 0
	}
}

func sanitizeSeverity(severity string) string {
	switch strings.ToLower(strings.TrimSpace(severity)) {
	case "normal", "atencao", "tensao", "incendio":
		return strings.ToLower(strings.TrimSpace(severity))
	default:
		return "normal"
	}
}

func uniqueParticipants(rows []windowRow, max int) []string {
	seen := map[string]struct{}{}
	out := []string{}
	for _, row := range rows {
		name := firstNonEmpty(row.AuthorName, row.UserID)
		if _, ok := seen[name]; ok {
			continue
		}
		seen[name] = struct{}{}
		out = append(out, name)
		if len(out) >= max {
			break
		}
	}
	return out
}

func mustJSON(v interface{}) string {
	data, _ := json.Marshal(v)
	return string(data)
}

func mustJSONArray(v string) []string {
	if strings.TrimSpace(v) == "" {
		return []string{}
	}
	var out []string
	if err := json.Unmarshal([]byte(v), &out); err != nil {
		return []string{}
	}
	return out
}

func toString(v interface{}) string {
	switch value := v.(type) {
	case string:
		return value
	case fmt.Stringer:
		return value.String()
	case nil:
		return ""
	default:
		return fmt.Sprint(value)
	}
}

func toBool(v interface{}) bool {
	switch value := v.(type) {
	case bool:
		return value
	case string:
		return strings.EqualFold(value, "true")
	case float64:
		return value != 0
	default:
		return false
	}
}

func toFloat(v interface{}) float64 {
	switch value := v.(type) {
	case float64:
		return value
	case float32:
		return float64(value)
	case int:
		return float64(value)
	case int64:
		return float64(value)
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
		if err != nil {
			return 0
		}
		return f
	default:
		return 0
	}
}

func toStringSlice(v interface{}) []string {
	items, ok := v.([]interface{})
	if !ok {
		return []string{}
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if str := strings.TrimSpace(toString(item)); str != "" {
			out = append(out, str)
		}
	}
	return out
}

func toEvidence(v interface{}) []map[string]string {
	items, ok := v.([]interface{})
	if !ok {
		return []map[string]string{}
	}
	out := make([]map[string]string, 0, len(items))
	for _, item := range items {
		if obj, ok := item.(map[string]interface{}); ok {
			msgID := strings.TrimSpace(toString(obj["message_id"]))
			reason := strings.TrimSpace(toString(obj["reason"]))
			if msgID == "" {
				continue
			}
			out = append(out, map[string]string{
				"message_id": msgID,
				"reason":     firstNonEmpty(reason, "evidence"),
			})
		}
	}
	return out
}
