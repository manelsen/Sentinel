"""Utilities for building and rendering moderation alerts."""

from __future__ import annotations

import json

from .models import AlertPayload, ClassificationResult, Severity
from .utils import isoformat


def build_alert_payload(
    *,
    alert_id: str,
    incident_id: str,
    group_id: str,
    group_name: str,
    result: ClassificationResult,
    risk_score: float,
    trigger_message_author: str | None,
    trigger_excerpt: str | None,
    created_at: str | None = None,
) -> AlertPayload:
    """Build the canonical alert payload persisted and emitted by Sentinel.

    Args:
        alert_id: Unique identifier for the alert record.
        incident_id: Identifier of the correlated incident assessment.
        group_id: Internal group identifier.
        group_name: Human-readable group name.
        result: Classification output used as alert source.
        risk_score: Final risk score associated with the incident.
        trigger_message_author: Author name for the trigger message, when known.
        trigger_excerpt: Text excerpt from the trigger message, when available.
        created_at: Optional explicit timestamp in ISO-8601 format.

    Returns:
        A normalized JSON-serializable alert payload.
    """
    signals = []
    if result.severity in {Severity.TENSAO, Severity.INCENDIO}:
        signals.append("escalada_relacional")
    if result.trigger_message_id:
        signals.append("mensagem_gatilho_identificada")
    if result.recommended_action.value == "alert_moderator_now":
        signals.append("prioridade_imediata")
    payload: AlertPayload = {
        "alert_id": alert_id,
        "incident_assessment_id": incident_id,
        "group_id": group_id,
        "group_name": group_name,
        "severity": result.severity.value,
        "risk_score": round(risk_score, 4),
        "created_at": created_at or isoformat(),
        "participants": result.participants,
        "trigger_message_id": result.trigger_message_id,
        "summary_short": result.summary_short,
        "signals": signals,
        "recommended_action": result.recommended_action.value,
        "evidence": [],
        "dashboard_url": None,
    }
    if result.trigger_message_id and trigger_excerpt:
        payload["evidence"].append(
            {
                "message_id": result.trigger_message_id,
                "author": trigger_message_author,
                "excerpt": trigger_excerpt[:280],
            }
        )
    return payload


def render_human_alert(payload: AlertPayload) -> str:
    """Render a concise human-readable alert block for terminal output.

    Args:
        payload: Structured alert payload.

    Returns:
        Multi-line string optimized for quick moderation triage.
    """
    title = str(payload["severity"]).upper().replace("ATENCAO", "ATENCAO")
    participants = ", ".join(payload["participants"]) if payload["participants"] else "desconhecido"
    signals = ", ".join(payload["signals"]) if payload["signals"] else "nenhum sinal listado"
    return (
        f"ALERTA: {title}\n"
        f"Grupo: {payload['group_name']}\n"
        f"Risco: {payload['risk_score']}\n"
        f"Participantes centrais: {participants}\n"
        f"Gatilho provavel: {payload['trigger_message_id']}\n"
        f"Sinais: {signals}\n"
        f"Resumo: {payload['summary_short']}\n"
        f"Acao sugerida: {payload['recommended_action']}\n"
    )


def render_machine_alert(payload: AlertPayload) -> str:
    """Serialize an alert payload as ASCII-safe JSON.

    Args:
        payload: Structured alert payload.

    Returns:
        JSON text representation of the payload.
    """
    return json.dumps(payload, ensure_ascii=True)
