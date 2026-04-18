"""Configuration models and loader utilities for Sentinel runtime options."""

from __future__ import annotations

import os
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
    """Window sizing rules used for short and expanded analysis contexts."""

    short_minutes: int = 5
    short_message_count: int = 20
    expanded_minutes: int = 15
    expanded_message_count: int = 50


@dataclass(slots=True)
class HeuristicConfig:
    """Thresholds and weights used by local heuristic scoring."""

    llm_threshold: float = 0.55
    heuristic_only_threshold: float = 0.85
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


@dataclass(slots=True)
class AlertConfig:
    """Delivery and gating settings for alert emission."""

    channels: list[str] = field(default_factory=lambda: ["stdout"])
    cooldown_seconds: int = 300
    minimum_severity: str = "tensao"


@dataclass(slots=True)
class LLMConfig:
    """Structured classification provider settings."""

    provider: str = "fallback"
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: int = 30
    command: str = ""


@dataclass(slots=True)
class TranscriptionConfig:
    """Audio transcription provider settings."""

    provider: str = "static"
    model: str = "whisper-large-v3-turbo"
    api_key_env: str = "GROQ_API_KEY"
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_seconds: int = 120
    response_format: str = "verbose_json"
    timestamp_granularities: list[str] = field(default_factory=lambda: ["segment"])


@dataclass(slots=True)
class ServerConfig:
    """HTTP server bind and authorization settings."""

    host: str = "127.0.0.1"
    port: int = 8080
    auth_token: str = ""


@dataclass(slots=True)
class AppConfig:
    """Root configuration object used across Sentinel services."""

    db_path: str = "sentinel.db"
    windows: WindowConfig = field(default_factory=WindowConfig)
    heuristics: HeuristicConfig = field(default_factory=HeuristicConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def _merge_dataclass(instance: object, values: dict[str, object]) -> object:
    """Merge a mapping of values into a dataclass instance recursively.

    Args:
        instance: Dataclass object to be updated.
        values: Parsed configuration section to merge.

    Returns:
        The same instance after in-place updates.
    """
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
    """Load application configuration from a TOML file.

    Args:
        path: Optional path to a TOML config file. When omitted, defaults are used.

    Environment overrides:
        - ``SENTINEL_DB_PATH``
        - ``SENTINEL_SERVER_HOST``
        - ``SENTINEL_SERVER_PORT``
        - ``SENTINEL_AUTH_TOKEN``

    Returns:
        Fully populated application configuration.

    Raises:
        ValueError: If ``SENTINEL_SERVER_PORT`` is set but not an integer.
    """
    config = AppConfig()
    if path:
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

    env_db_path = os.getenv("SENTINEL_DB_PATH", "").strip()
    if env_db_path:
        config.db_path = env_db_path
    env_host = os.getenv("SENTINEL_SERVER_HOST", "").strip()
    if env_host:
        config.server.host = env_host
    env_port = os.getenv("SENTINEL_SERVER_PORT", "").strip()
    if env_port:
        try:
            config.server.port = int(env_port)
        except ValueError as exc:
            raise ValueError("SENTINEL_SERVER_PORT deve ser inteiro") from exc
    env_auth = os.getenv("SENTINEL_AUTH_TOKEN", "").strip()
    if env_auth:
        config.server.auth_token = env_auth
    return config
