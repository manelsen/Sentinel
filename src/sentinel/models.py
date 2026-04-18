from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    OTHER = "other"


class Severity(StrEnum):
    NORMAL = "normal"
    ATENCAO = "atencao"
    TENSAO = "tensao"
    INCENDIO = "incendio"


class RecommendedAction(StrEnum):
    NONE = "none"
    MONITOR = "monitor"
    ALERT_MODERATOR = "alert_moderator"
    ALERT_MODERATOR_NOW = "alert_moderator_now"


class FeedbackType(StrEnum):
    CORRETO = "correto"
    EXAGERADO = "exagerado"
    INCORRETO = "incorreto"
    UTIL_IMPRECISO = "util apesar de impreciso"
    REVISADO = "revisado manualmente"


class IncomingMessage(BaseModel):
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
        if self.message_type == MessageType.TEXT and not self.raw_text:
            raise ValueError("raw_text e obrigatorio para message_type=text")
        if self.message_type == MessageType.AUDIO and not (self.media_path or self.transcript_text):
            raise ValueError("audio exige media_path ou transcript_text")
        return self


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    feedback_type: FeedbackType
    note: str | None = None
    reviewer_id: str | None = None


class DailyReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    date: str


class TranscriptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_text: str | None
    language: str | None
    confidence: float | None
    duration_seconds: float | None
    status: str
    error_message: str | None = None


class PromptBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_prompt: str
    request_payload: dict[str, Any]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    reason: str


class ClassificationResult(BaseModel):
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
    window_id: str
    group_id: str
    start_at: str
    end_at: str
    message_count: int
    distinct_user_count: int
    heuristic_risk_score: float
    heuristic_signals: dict[str, float]


class WindowSnapshotMessage(TypedDict):
    message_id: str
    timestamp: str
    author_name: str
    author_id: str
    text: str
    direct_attack_score: float
    profanity_score: float
    negativity_score: float


class WindowSnapshot(TypedDict):
    metadata: WindowSnapshotMetadata
    messages: list[WindowSnapshotMessage]


class IngestResult(TypedDict):
    message_id: str
    group_id: str
    user_id: str
    assessment_id: str | None
    alert_ids: list[str]
    severity: str
    risk_score: float


class CriticalIncident(TypedDict):
    incident_id: str
    severity: str
    participants: list[str]
    summary_short: str
    trigger_message_id: str | None
    review_status: str
    created_at: str


class DailyReportPayload(TypedDict):
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
    message_id: str
    author: str | None
    excerpt: str


class AlertPayload(TypedDict):
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
