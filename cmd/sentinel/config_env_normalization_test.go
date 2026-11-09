package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadDotenvWithoutOverridingExistingValues(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	content := "GROQ_API_KEY=from-dotenv\nexport GEMINI_API_KEY='gemini-secret'\nSENTINEL_AUTH_TOKEN=abc123 # inline comment\n"
	if err := os.WriteFile(envPath, []byte(content), 0o644); err != nil {
		t.Fatalf("write env: %v", err)
	}
	t.Setenv("GROQ_API_KEY", "preexisting")
	loaded, err := loadDotenv(envPath, false)
	if err != nil {
		t.Fatalf("load dotenv: %v", err)
	}
	if loaded != 2 {
		t.Fatalf("expected 2 vars loaded, got %d", loaded)
	}
	if got := os.Getenv("GROQ_API_KEY"); got != "preexisting" {
		t.Fatalf("expected existing key, got %q", got)
	}
	if got := os.Getenv("GEMINI_API_KEY"); got != "gemini-secret" {
		t.Fatalf("unexpected GEMINI_API_KEY: %q", got)
	}
	if got := os.Getenv("SENTINEL_AUTH_TOKEN"); got != "abc123" {
		t.Fatalf("unexpected SENTINEL_AUTH_TOKEN: %q", got)
	}
}

func TestLoadDotenvMissingFileWithoutError(t *testing.T) {
	dir := t.TempDir()
	loaded, err := loadDotenv(filepath.Join(dir, ".env"), false)
	if err != nil {
		t.Fatalf("load dotenv should ignore missing file: %v", err)
	}
	if loaded != 0 {
		t.Fatalf("expected 0 vars loaded, got %d", loaded)
	}
}

func TestLoadDotenvRaisesForInvalidLine(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("INVALID-LINE\n"), 0o644); err != nil {
		t.Fatalf("write env: %v", err)
	}
	if _, err := loadDotenv(envPath, false); err == nil {
		t.Fatalf("expected error for invalid line")
	}
}

func TestLoadConfigAppliesEnvOverrides(t *testing.T) {
	dir := t.TempDir()
	configPath := filepath.Join(dir, "sentinel.toml")
	configBody := "[app]\n" +
		"db_path = \"from-file.db\"\n\n" +
		"[server]\n" +
		"host = \"127.0.0.1\"\n" +
		"port = 8080\n" +
		"auth_token = \"\"\n"
	if err := os.WriteFile(configPath, []byte(configBody), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	t.Setenv("SENTINEL_DB_PATH", "/data/sentinel.db")
	t.Setenv("SENTINEL_SERVER_HOST", "0.0.0.0")
	t.Setenv("SENTINEL_SERVER_PORT", "9090")
	t.Setenv("SENTINEL_AUTH_TOKEN", "token-from-env")
	cfg, err := loadConfig(configPath)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.DBPath != "/data/sentinel.db" {
		t.Fatalf("expected db path override, got %q", cfg.DBPath)
	}
	if cfg.Server.Host != "0.0.0.0" {
		t.Fatalf("expected host override, got %q", cfg.Server.Host)
	}
	if cfg.Server.Port != 9090 {
		t.Fatalf("expected port override, got %d", cfg.Server.Port)
	}
	if cfg.Server.AuthToken != "token-from-env" {
		t.Fatalf("expected auth override, got %q", cfg.Server.AuthToken)
	}
}

func TestLoadConfigRejectsInvalidEnvPort(t *testing.T) {
	t.Setenv("SENTINEL_SERVER_PORT", "not-a-number")
	if _, err := loadConfig(""); err == nil {
		t.Fatalf("expected invalid port error")
	}
}

func TestNormalizationPreservesAggressiveMarkers(t *testing.T) {
	text := "VOCE   esta  distorcendo tudo!!! 😡"
	normalized := normalizeText(text)
	if !strings.Contains(normalized, "VOCE") {
		t.Fatalf("normalized text must preserve VOCE token: %q", normalized)
	}
	if !strings.Contains(normalized, "!!!") {
		t.Fatalf("normalized text must preserve punctuation: %q", normalized)
	}
	if !strings.Contains(normalized, "[emoji_raiva]") {
		t.Fatalf("normalized text must tag angry emoji: %q", normalized)
	}
}

func TestDetectsProfanityAndDirectAddress(t *testing.T) {
	text := "Voce esta sendo um idiota"
	if !containsProfanity(text) {
		t.Fatalf("expected profanity detection")
	}
	if !containsDirectAddress(text) {
		t.Fatalf("expected direct address detection")
	}
	if tokenEstimate(text) <= 0 {
		t.Fatalf("expected positive token estimate")
	}
}
