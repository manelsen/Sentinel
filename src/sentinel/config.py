from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WEIGHTS = {
    "hostility_density_score": 0.18,
    "dyadic_exchange_score": 0.16,
    "escalation_velocity_score": 0.14,
    "reply_concentration_score": 0.12,
    "participant_concentration_score": 0.10,
    "direct_attack_density": 0.10,
    "profanity_density": 0.08,
    "caps_intensity": 0.06,
    "punctuation_aggression": 0.06,
}


@dataclass(slots=True)
class WindowConfig:
    short_minutes: int = 5
    short_message_count: int = 20
    expanded_minutes: int = 15
    expanded_message_count: int = 50


@dataclass(slots=True)
class HeuristicConfig:
    llm_threshold: float = 0.55
    heuristic_only_threshold: float = 0.85
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


@dataclass(slots=True)
class AlertConfig:
    channels: list[str] = field(default_factory=lambda: ["stdout"])
    cooldown_seconds: int = 300
    minimum_severity: str = "tensao"


@dataclass(slots=True)
class LLMConfig:
    provider: str = "fallback"
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: int = 30
    command: str = ""


@dataclass(slots=True)
class TranscriptionConfig:
    provider: str = "static"
    model: str = "whisper-large-v3-turbo"
    api_key_env: str = "GROQ_API_KEY"
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_seconds: int = 120
    response_format: str = "verbose_json"
    timestamp_granularities: list[str] = field(default_factory=lambda: ["segment"])


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    auth_token: str = ""


@dataclass(slots=True)
class AppConfig:
    db_path: str = "sentinel.db"
    windows: WindowConfig = field(default_factory=WindowConfig)
    heuristics: HeuristicConfig = field(default_factory=HeuristicConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def _merge_dataclass(instance: object, values: dict[str, object]) -> object:
    for key, value in values.items():
        if not hasattr(instance, key):
            continue
        current = getattr(instance, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge_dataclass(current, value)
        else:
            setattr(instance, key, value)
    return instance


def load_config(path: str | Path | None = None) -> AppConfig:
    config = AppConfig()
    if not path:
        return config
    content = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    app_values = content.get("app", {})
    if "db_path" in app_values:
        config.db_path = str(app_values["db_path"])
    _merge_dataclass(config.windows, content.get("windows", {}))
    _merge_dataclass(config.heuristics, content.get("heuristics", {}))
    _merge_dataclass(config.alerts, content.get("alerts", {}))
    _merge_dataclass(config.transcription, content.get("transcription", {}))
    _merge_dataclass(config.llm, content.get("llm", {}))
    _merge_dataclass(config.server, content.get("server", {}))
    return config
