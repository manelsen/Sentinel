package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func loadProviderFixture(t *testing.T, name string) map[string]interface{} {
	t.Helper()
	path := filepath.Join("testdata", "providers", name)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	var payload map[string]interface{}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatalf("decode fixture %s: %v", name, err)
	}
	return payload
}

func asMap(t *testing.T, value interface{}, label string) map[string]interface{} {
	t.Helper()
	out, ok := value.(map[string]interface{})
	if !ok {
		t.Fatalf("expected map for %s", label)
	}
	return out
}

func TestExtractsGeminiText(t *testing.T) {
	payload := map[string]interface{}{
		"candidates": []interface{}{
			map[string]interface{}{
				"content": map[string]interface{}{
					"parts": []interface{}{
						map[string]interface{}{"text": `{"conflict_present": false}`},
					},
				},
			},
		},
	}
	text, err := extractGeminiText(payload)
	if err != nil {
		t.Fatalf("extract gemini text: %v", err)
	}
	if text != `{"conflict_present": false}` {
		t.Fatalf("unexpected gemini text: %q", text)
	}
}

func TestNormalizesGroqLanguageLocale(t *testing.T) {
	if got := normalizeGroqLanguage("pt-BR"); got != "pt" {
		t.Fatalf("expected pt, got %q", got)
	}
	if got := normalizeGroqLanguage("en_US"); got != "en" {
		t.Fatalf("expected en, got %q", got)
	}
	if got := normalizeGroqLanguage(""); got != "" {
		t.Fatalf("expected empty normalization, got %q", got)
	}
}

func TestClassifyWithGeminiRequiresKey(t *testing.T) {
	cfg := defaultConfig()
	cfg.DBPath = filepath.Join(t.TempDir(), "sentinel.db")
	t.Setenv(cfg.LLM.APIKeyEnv, "")
	service, err := newService(cfg)
	if err != nil {
		t.Fatalf("new service: %v", err)
	}
	defer service.close()
	_, err = service.classifyWithGemini(
		[]windowRow{
			{
				MessageID:    "msg-1",
				UserID:       "alice",
				AuthorName:   "Alice",
				MessageType:  "text",
				SortTS:       parseTimestamp("2026-04-18T16:00:00Z"),
				AnalysisText: "Voce esta distorcendo tudo.",
			},
		},
		windowFeatures{
			Signals: map[string]float64{"hostility_density_score": 0.7},
			Risk:    0.6,
		},
	)
	if err == nil || !strings.Contains(err.Error(), "api key ausente") {
		t.Fatalf("expected missing api key error, got %v", err)
	}
}

func TestParsesGroqFixturePayload(t *testing.T) {
	fixture := loadProviderFixture(t, "groq_transcription_fixture.json")
	response := asMap(t, fixture["response"], "response")
	parsed := parseGroqTranscriptionPayload(response, "pt-BR")
	if parsed.Status != "completed" {
		t.Fatalf("expected completed status, got %s", parsed.Status)
	}
	if strings.TrimSpace(parsed.TranscriptText) == "" {
		t.Fatalf("expected transcript text")
	}
	if parsed.Language != "Portuguese" {
		t.Fatalf("expected Portuguese language, got %q", parsed.Language)
	}
	if parsed.DurationSeconds == nil {
		t.Fatalf("expected duration")
	}
}

func TestParsesGroqPlaintextResponse(t *testing.T) {
	parsed := parseGroqTranscriptionResponse("transcricao direta\n", "pt-BR")
	if parsed.Status != "completed" {
		t.Fatalf("expected completed status, got %s", parsed.Status)
	}
	if parsed.TranscriptText != "transcricao direta" {
		t.Fatalf("unexpected transcript: %q", parsed.TranscriptText)
	}
	if parsed.Language != "pt-BR" {
		t.Fatalf("unexpected language: %q", parsed.Language)
	}
}

func TestRecordsGroqLiveErrorFixture(t *testing.T) {
	fixture := loadProviderFixture(t, "groq_transcription_error_fixture.json")
	if got := asMap(t, fixture, "fixture")["provider"]; got != "groq" {
		t.Fatalf("unexpected provider: %v", got)
	}
	if !strings.Contains(toString(fixture["captured_error"]), "403") {
		t.Fatalf("expected 403 in captured error")
	}
}

func TestExtractsGeminiTextFromFixture(t *testing.T) {
	fixture := loadProviderFixture(t, "gemini_generate_content_fixture.json")
	response := asMap(t, fixture["response"], "response")
	text, err := extractGeminiText(response)
	if err != nil {
		t.Fatalf("extract gemini fixture text: %v", err)
	}
	var parsed map[string]interface{}
	if err := json.Unmarshal([]byte(text), &parsed); err != nil {
		t.Fatalf("decode gemini text: %v", err)
	}
	severity := toString(parsed["severity"])
	switch severity {
	case "normal", "atencao", "tensao", "incendio":
	default:
		t.Fatalf("unexpected severity %q", severity)
	}
}

func TestRaisesWhenGeminiFixtureHasNoCandidates(t *testing.T) {
	fixture := loadProviderFixture(t, "gemini_missing_candidates_fixture.json")
	response := asMap(t, fixture["response"], "response")
	_, err := extractGeminiText(response)
	if err == nil {
		t.Fatalf("expected error")
	}
	if err.Error() != toString(fixture["expected_error"]) {
		t.Fatalf("unexpected error: %q", err.Error())
	}
}

func TestRaisesWhenGeminiFixtureHasNoParts(t *testing.T) {
	fixture := loadProviderFixture(t, "gemini_missing_parts_fixture.json")
	response := asMap(t, fixture["response"], "response")
	_, err := extractGeminiText(response)
	if err == nil {
		t.Fatalf("expected error")
	}
	if err.Error() != toString(fixture["expected_error"]) {
		t.Fatalf("unexpected error: %q", err.Error())
	}
}

func TestParsesGroqFixtureMissingTextAsFailed(t *testing.T) {
	fixture := loadProviderFixture(t, "groq_transcription_missing_text_fixture.json")
	response := asMap(t, fixture["response"], "response")
	parsed := parseGroqTranscriptionPayload(response, "pt-BR")
	if parsed.Status != toString(fixture["expected_status"]) {
		t.Fatalf("unexpected status: %s", parsed.Status)
	}
	if parsed.ErrorMessage != toString(fixture["expected_error"]) {
		t.Fatalf("unexpected error: %s", parsed.ErrorMessage)
	}
	if parsed.TranscriptText != "" {
		t.Fatalf("expected empty transcript")
	}
}

func TestParsesGroqEmptyResponseFixture(t *testing.T) {
	fixture := loadProviderFixture(t, "groq_transcription_empty_response_fixture.json")
	parsed := parseGroqTranscriptionResponse(
		toString(fixture["response_body"]),
		toString(fixture["fallback_language"]),
	)
	if parsed.Status != toString(fixture["expected_status"]) {
		t.Fatalf("unexpected status: %s", parsed.Status)
	}
	if parsed.ErrorMessage != toString(fixture["expected_error"]) {
		t.Fatalf("unexpected error: %s", parsed.ErrorMessage)
	}
	if parsed.Language != toString(fixture["fallback_language"]) {
		t.Fatalf("unexpected language: %s", parsed.Language)
	}
}
