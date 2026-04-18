from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from .models import DailyReportPayload

STOPWORDS = {
    "a",
    "o",
    "de",
    "do",
    "da",
    "e",
    "que",
    "em",
    "um",
    "uma",
    "para",
    "com",
    "na",
    "no",
    "isso",
    "essa",
    "esse",
    "mas",
    "por",
    "se",
    "eu",
    "voce",
    "você",
}


def _extract_topics(texts: list[str]) -> list[str]:
    counter = Counter()
    for text in texts:
        for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", text.casefold()):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return [token for token, _ in counter.most_common(5)]


def generate_daily_report(
    connection: sqlite3.Connection, group_id: str, report_date: str
) -> tuple[str, DailyReportPayload]:
    day_start = f"{report_date}T00:00:00Z"
    day_end = f"{report_date}T23:59:59Z"

    message_rows = connection.execute(
        """
        SELECT
            m.id,
            m.message_type,
            m.sent_at,
            COALESCE(nm.analysis_text, m.raw_text, at.transcript_text, '') AS analysis_text
        FROM messages m
        LEFT JOIN normalized_messages nm ON nm.message_id = m.id
        LEFT JOIN audio_transcriptions at ON at.message_id = m.id
        WHERE m.group_id = ?
          AND COALESCE(m.sent_at, m.received_at) BETWEEN ? AND ?
        ORDER BY COALESCE(m.sent_at, m.received_at) ASC
        """,
        (group_id, day_start, day_end),
    ).fetchall()

    transcription_stats = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
            COALESCE(SUM(duration_seconds), 0) AS total_duration
        FROM audio_transcriptions at
        JOIN messages m ON m.id = at.message_id
        WHERE m.group_id = ?
          AND COALESCE(m.sent_at, m.received_at) BETWEEN ? AND ?
        """,
        (group_id, day_start, day_end),
    ).fetchone()

    incident_rows = connection.execute(
        """
        SELECT
            ia.id,
            ia.severity,
            ia.summary_short,
            ia.trigger_message_id,
            ia.participants_json,
            ia.created_at,
            COALESCE(mf.feedback_type, 'pendente') AS review_status
        FROM incident_assessments ia
        JOIN analysis_windows aw ON aw.id = ia.window_id
        LEFT JOIN moderator_feedback mf ON mf.incident_assessment_id = ia.id
        WHERE aw.group_id = ?
          AND ia.created_at BETWEEN ? AND ?
        ORDER BY ia.risk_score DESC, ia.created_at ASC
        """,
        (group_id, day_start, day_end),
    ).fetchall()

    attention_rows = [row for row in incident_rows if row["severity"] == "atencao"]
    critical_rows = [row for row in incident_rows if row["severity"] in {"tensao", "incendio"}]

    texts = [str(row["analysis_text"]) for row in message_rows if row["analysis_text"]]
    topics = _extract_topics(texts)
    audio_total = sum(1 for row in message_rows if row["message_type"] == "audio")

    authors_total = connection.execute(
        """
        SELECT COUNT(DISTINCT user_id) AS total
        FROM messages
        WHERE group_id = ?
          AND COALESCE(sent_at, received_at) BETWEEN ? AND ?
        """,
        (group_id, day_start, day_end),
    ).fetchone()["total"]

    payload: DailyReportPayload = {
        "group_id": group_id,
        "report_date": report_date,
        "message_total": len(message_rows),
        "author_total": authors_total,
        "audio_total": audio_total,
        "transcribed_minutes": round(float(transcription_stats["total_duration"] or 0.0) / 60.0, 2),
        "transcription_success_rate": round(
            (float(transcription_stats["completed"] or 0.0) / max(1.0, float(transcription_stats["total"] or 0.0))),
            4,
        ),
        "topics": topics,
        "critical_incidents": [
            {
                "incident_id": str(row["id"]),
                "severity": str(row["severity"]),
                "participants": json.loads(str(row["participants_json"])),
                "summary_short": str(row["summary_short"]),
                "trigger_message_id": str(row["trigger_message_id"]) if row["trigger_message_id"] else None,
                "review_status": str(row["review_status"]),
                "created_at": str(row["created_at"]),
            }
            for row in critical_rows
        ],
        "attention_incidents": [
            {
                "incident_id": str(row["id"]),
                "severity": str(row["severity"]),
                "participants": json.loads(str(row["participants_json"])),
                "summary_short": str(row["summary_short"]),
                "trigger_message_id": str(row["trigger_message_id"]) if row["trigger_message_id"] else None,
                "review_status": str(row["review_status"]),
                "created_at": str(row["created_at"]),
            }
            for row in attention_rows
        ],
    }

    lines = [
        f"# Relatorio Diario - {report_date}",
        "",
        f"- Grupo: `{group_id}`",
        f"- Total de mensagens: {payload['message_total']}",
        f"- Total de autores: {payload['author_total']}",
        f"- Total de audios: {payload['audio_total']}",
        f"- Minutos transcritos: {payload['transcribed_minutes']}",
        f"- Taxa de transcricao: {payload['transcription_success_rate']:.2%}",
        "",
        "## Topicos discutidos",
    ]
    if topics:
        lines.extend([f"- {topic}" for topic in topics])
    else:
        lines.append("- Nenhum topico identificavel")
    lines.extend(["", "## Incidentes potenciais"])
    if critical_rows:
        for row in critical_rows:
            participants = ", ".join(json.loads(row["participants_json"])) or "desconhecido"
            lines.append(
                f"- {row['created_at']} | {row['severity']} | {participants} | "
                f"{row['summary_short']} | status: {row['review_status']}"
            )
    else:
        lines.append("- Nenhum incidente de tensao/incendio no periodo")
    lines.extend(["", "## Janelas marcadas apenas como atencao"])
    if attention_rows:
        for row in attention_rows:
            participants = ", ".join(json.loads(row["participants_json"])) or "desconhecido"
            lines.append(
                f"- {row['created_at']} | {participants} | {row['summary_short']} | status: {row['review_status']}"
            )
    else:
        lines.append("- Nenhuma janela de atencao registrada")
    lines.extend(["", "## Resumo executivo"])
    if critical_rows:
        lines.append(
            f"- O dia teve {len(critical_rows)} incidente(s) materialmente relevante(s); "
            "moderacao deve revisar esses blocos primeiro."
        )
    else:
        lines.append("- Nao houve incidente de maior severidade registrado no dia.")
    report_markdown = "\n".join(lines)
    return report_markdown, payload
