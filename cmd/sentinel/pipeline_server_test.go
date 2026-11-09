package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestPipelineCreatesIncidentAndAlertWithReport(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "")
	t.Setenv("GROQ_API_KEY", "")
	cfg := defaultConfig()
	cfg.DBPath = filepath.Join(t.TempDir(), "sentinel.db")
	cfg.Heuristics.LLMThreshold = 0.40
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

	var lastResult IngestResult
	allAlertIDs := []string{}
	for _, event := range events {
		lastResult, err = service.IngestMessage(event)
		if err != nil {
			t.Fatalf("ingest: %v", err)
		}
		allAlertIDs = append(allAlertIDs, lastResult.AlertIDs...)
	}

	if lastResult.AssessmentID == nil {
		t.Fatalf("expected assessment id")
	}
	switch lastResult.Severity {
	case "tensao", "incendio":
	default:
		t.Fatalf("expected severe incident, got %s", lastResult.Severity)
	}
	if len(allAlertIDs) < 1 {
		t.Fatalf("expected at least one emitted alert id")
	}
	var alertTotal int
	if err := service.db.QueryRow("SELECT COUNT(*) FROM alerts").Scan(&alertTotal); err != nil {
		t.Fatalf("count alerts: %v", err)
	}
	if alertTotal < 1 {
		t.Fatalf("expected at least one alert in db")
	}

	markdown, payload, err := service.BuildDailyReport(lastResult.GroupID, "2026-04-18")
	if err != nil {
		t.Fatalf("build report: %v", err)
	}
	if !strings.Contains(markdown, "Incidentes potenciais") {
		t.Fatalf("report markdown missing incidents section")
	}
	critical, ok := payload["critical_incidents"].([]map[string]interface{})
	if !ok {
		t.Fatalf("expected critical_incidents list in payload")
	}
	if len(critical) < 1 {
		t.Fatalf("expected at least one critical incident in report")
	}
}

func TestHTTPHealthAndIngestEndpoint(t *testing.T) {
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
	var ingestBody map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&ingestBody); err != nil {
		t.Fatalf("decode ingest response: %v", err)
	}
	if toString(ingestBody["severity"]) != "normal" {
		t.Fatalf("expected normal severity, got %v", ingestBody["severity"])
	}
}
