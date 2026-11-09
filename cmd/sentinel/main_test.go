package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadDotenv(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	content := "GROQ_API_KEY=from-dotenv\nexport GEMINI_API_KEY='gemini-key'\nSENTINEL_AUTH_TOKEN=token # comment\n"
	if err := os.WriteFile(envPath, []byte(content), 0o644); err != nil {
		t.Fatalf("write env: %v", err)
	}
	_ = os.Unsetenv("GEMINI_API_KEY")
	_ = os.Unsetenv("SENTINEL_AUTH_TOKEN")
	t.Setenv("GROQ_API_KEY", "existing")
	loaded, err := loadDotenv(envPath, false)
	if err != nil {
		t.Fatalf("load dotenv: %v", err)
	}
	if loaded != 2 {
		t.Fatalf("expected 2 loaded vars, got %d", loaded)
	}
	if got := os.Getenv("GROQ_API_KEY"); got != "existing" {
		t.Fatalf("expected existing key, got %q", got)
	}
	if got := os.Getenv("GEMINI_API_KEY"); got != "gemini-key" {
		t.Fatalf("unexpected GEMINI_API_KEY: %q", got)
	}
}

func TestIngestCreatesIncidentAndAlert(t *testing.T) {
	t.Setenv("SENTINEL_AUTH_TOKEN", "")
	t.Setenv("GEMINI_API_KEY", "")
	t.Setenv("GROQ_API_KEY", "")
	cfg := defaultConfig()
	cfg.DBPath = filepath.Join(t.TempDir(), "sentinel.db")
	cfg.Heuristics.LLMThreshold = 0.40
	cfg.Alerts.MinimumSeverity = "atencao"
	service, err := newService(cfg)
	if err != nil {
		t.Fatalf("new service: %v", err)
	}
	defer service.close()

	base := IncomingMessage{
		Platform:        "demo",
		ExternalGroupID: "grupo-1",
		GroupName:       "Grupo Demo",
		MessageType:     "text",
	}
	events := []IncomingMessage{
		{
			Platform:        base.Platform,
			ExternalGroupID: base.ExternalGroupID,
			GroupName:       base.GroupName,
			ExternalUserID:  "alice",
			UserName:        "Alice",
			MessageType:     "text",
			RawText:         "Voce esta distorcendo tudo de novo!!!",
			SentAt:          "2026-04-18T16:00:01Z",
			ReceivedAt:      "2026-04-18T16:00:01Z",
		},
		{
			Platform:        base.Platform,
			ExternalGroupID: base.ExternalGroupID,
			GroupName:       base.GroupName,
			ExternalUserID:  "bruno",
			UserName:        "Bruno",
			MessageType:     "text",
			RawText:         "Nao, voce que nunca le antes de falar.",
			SentAt:          "2026-04-18T16:00:10Z",
			ReceivedAt:      "2026-04-18T16:00:10Z",
		},
		{
			Platform:        base.Platform,
			ExternalGroupID: base.ExternalGroupID,
			GroupName:       base.GroupName,
			ExternalUserID:  "alice",
			UserName:        "Alice",
			MessageType:     "text",
			RawText:         "Para de mentir, isso esta ridiculo!!!",
			SentAt:          "2026-04-18T16:00:20Z",
			ReceivedAt:      "2026-04-18T16:00:20Z",
		},
	}

	var result IngestResult
	for _, event := range events {
		result, err = service.IngestMessage(event)
		if err != nil {
			t.Fatalf("ingest: %v", err)
		}
	}
	if result.AssessmentID == nil {
		t.Fatalf("expected assessment id")
	}
	if result.Severity == "normal" {
		t.Fatalf("unexpected severity: %s", result.Severity)
	}
	var alertTotal int
	if err := service.db.QueryRow("SELECT COUNT(*) FROM alerts").Scan(&alertTotal); err != nil {
		t.Fatalf("count alerts: %v", err)
	}
	if alertTotal < 1 {
		t.Fatalf("expected at least one alert")
	}
}

func TestHTTPHealthAndIngest(t *testing.T) {
	t.Setenv("SENTINEL_AUTH_TOKEN", "")
	t.Setenv("GEMINI_API_KEY", "")
	cfg := defaultConfig()
	cfg.DBPath = filepath.Join(t.TempDir(), "sentinel.db")
	service, err := newService(cfg)
	if err != nil {
		t.Fatalf("new service: %v", err)
	}
	defer service.close()

	server := httptest.NewServer(newHTTPHandler(service, cfg))
	defer server.Close()

	resp, err := http.Get(server.URL + "/healthz")
	if err != nil {
		t.Fatalf("health request: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("unexpected health status: %d", resp.StatusCode)
	}

	payload := IncomingMessage{
		Platform:        "demo",
		ExternalGroupID: "grupo-1",
		GroupName:       "Grupo Demo",
		ExternalUserID:  "alice",
		UserName:        "Alice",
		MessageType:     "text",
		RawText:         "Voce esta distorcendo tudo de novo!!!",
		SentAt:          "2026-04-18T16:00:01Z",
		ReceivedAt:      "2026-04-18T16:00:01Z",
	}
	body, _ := json.Marshal(payload)
	resp, err = http.Post(server.URL+"/ingest", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("ingest request: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("unexpected ingest status: %d", resp.StatusCode)
	}
}
