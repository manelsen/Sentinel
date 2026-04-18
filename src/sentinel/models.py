"""Pydantic models and typed contracts shared across Sentinel layers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(StrEnum):
    """Supported ingested message categories."""

    TEXT = "text"
    AUDIO = "audio"
    OTHER = "other"


class Severity(StrEnum):
    """Incident severity levels used in assessments and alerts."""

    NORMAL = "normal"
    ATENCAO = "atencao"
    TENSAO = "tensao"
    INCENDIO = "incendio"


class RecommendedAction(StrEnum):
    """Recommended moderation action derived from a classification."""

    NONE = "none"
    MONITOR = "monitor"
    ALERT_MODERATOR = "alert_moderator"
    ALERT_MODERATOR_NOW = "alert_moderator_now"


class FeedbackType(StrEnum):
    """Feedback categories accepted from moderators."""

    CORRETO = "correto"
    EXAGERADO = "exagerado"
    INCORRETO = "incorreto"
    UTIL_IMPRECISO = "util apesar de impreciso"
    REVISADO = "revisado manualmente"


class IncomingMessage(BaseModel):
    """Canonical incoming event accepted by CLI and HTTP ingestion paths."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    platform: str
    external_group_id: str
    external_user_id: str
    message_type: MessageType
    group_name: str | None = None
    user_name: str | None = None
    raw_text: str | None = None
    transcript_text: str | None = None
    language: str | None = "pt-BR"
    external_message_id: str | None = None
    received_at: str | None = None
    sent_at: str | None = None
    reply_to_message_id: str | None = None
    quoted_message_id: str | None = None
    has_media: bool = False
    media_type: str | None = None
    media_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_message_payload(self) -> IncomingMessage:
        """Validate payload consistency according to message type.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If required fields are missing for the declared type.
        """
        if self.message_type == MessageType.TEXT and not self.raw_text:
            raise ValueError("raw_text e obrigatorio para message_type=text")
        if self.message_type == MessageType.AUDIO and not (self.media_path or self.transcript_text):
            raise ValueError("audio exige media_path ou transcript_text")
        return self


class FeedbackRequest(BaseModel):
    """Request body for moderator feedback registration endpoint."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    feedback_type: FeedbackType
    note: str | None = None
    reviewer_id: str | None = None


class DailyReportRequest(BaseModel):
    """Request body for daily report generation endpoint."""

    model_config = ConfigDict(extra="forbid")

    group_id: str
    date: str


class TranscriptionResult(BaseModel):
    """Structured output of audio transcription providers."""

    model_config = ConfigDict(extra="forbid")

    transcript_text: str | None
    language: str | None
    confidence: float | None
    duration_seconds: float | None
    status: str
    error_message: str | None = None


class PromptBundle(BaseModel):
    """Prompt package persisted for auditability of classification requests."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_prompt: str
    request_payload: dict[str, Any]


class EvidenceItem(BaseModel):
    """Evidence reference pointing to messages supporting a classification."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    reason: str


class ClassificationResult(BaseModel):
    """Normalized classifier output used for incident assessment persistence."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    conflict_present: bool
    escalation_risk: float = Field(ge=0.0, le=1.0)
    severity: Severity
    participants: list[str]
    trigger_message_id: str | None
    evidence: list[EvidenceItem]
    summary_short: str
    summary_long: str
    recommended_action: RecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_notes: str


class WindowSnapshotMetadata(TypedDict):
    """Typed metadata block attached to a classification window snapshot."""

    window_id: str
    group_id: str
    start_at: str
    end_at: str
    message_count: int
    distinct_user_count: int
    heuristic_risk_score: float
    heuristic_signals: dict[str, float]


class WindowSnapshotMessage(TypedDict):
    """Typed message representation included in a window snapshot."""

    message_id: str
    timestamp: str
    author_name: str
    author_id: str
    text: str
    direct_attack_score: float
    profanity_score: float
    negativity_score: float


class WindowSnapshot(TypedDict):
    """Typed snapshot payload sent to the structured classifier."""

    metadata: WindowSnapshotMetadata
    messages: list[WindowSnapshotMessage]


class IngestResult(TypedDict):
    """Return contract for ingestion operations."""

    message_id: str
    group_id: str
    user_id: str
    assessment_id: str | None
    alert_ids: list[str]
    severity: str
    risk_score: float


class CriticalIncident(TypedDict):
    """Incident representation used in daily report payloads."""

    incident_id: str
    severity: str
    participants: list[str]
    summary_short: str
    trigger_message_id: str | None
    review_status: str
    created_at: str


class DailyReportPayload(TypedDict):
    """Structured daily report payload persisted and returned by API/CLI."""

    group_id: str
    report_date: str
    message_total: int
    author_total: int
    audio_total: int
    transcribed_minutes: float
    transcription_success_rate: float
    topics: list[str]
    critical_incidents: list[CriticalIncident]
    attention_incidents: list[CriticalIncident]


class AlertEvidence(TypedDict):
    """Evidence excerpt included in alert payloads."""

    message_id: str
    author: str | None
    excerpt: str


class AlertPayload(TypedDict):
    """Canonical alert payload shape for persistence and emission."""

    alert_id: str
    incident_assessment_id: str
    group_id: str
    group_name: str
    severity: str
    risk_score: float
    created_at: str
    participants: list[str]
    trigger_message_id: str | None
    summary_short: str
    signals: list[str]
    recommended_action: str
    evidence: list[AlertEvidence]
    dashboard_url: str | None
