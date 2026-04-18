from __future__ import annotations

import json
import sqlite3

from .alerts import build_alert_payload, render_human_alert, render_machine_alert
from .classifier import StructuredClassifier
from .config import AppConfig
from .db import connect, init_db
from .heuristics import compute_message_features, compute_window_features
from .models import (
    DailyReportPayload,
    FeedbackType,
    IncomingMessage,
    IngestResult,
    MessageType,
    Severity,
    TranscriptionResult,
    WindowSnapshot,
    WindowSnapshotMessage,
    WindowSnapshotMetadata,
)
from .normalization import contains_direct_address, contains_profanity, detect_language, normalize_text, token_estimate
from .providers import build_transcriber
from .reports import generate_daily_report
from .utils import isoformat, make_id, parse_timestamp


def _to_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


class SentinelService:
    def __init__(self, config: AppConfig, connection: sqlite3.Connection | None = None):
        self.config = config
        self.connection = connection or connect(config.db_path)
        init_db(self.connection)
        self.transcriber = build_transcriber(config)
        self.classifier = StructuredClassifier(config)

    def close(self) -> None:
        self.connection.close()

    def ingest_message(self, payload: IncomingMessage | dict[str, object]) -> IngestResult:
        event = payload if isinstance(payload, IncomingMessage) else IncomingMessage.model_validate(payload)
        sent_at = event.sent_at or event.received_at or isoformat()
        received_at = event.received_at or event.sent_at or isoformat()
        message_id = event.external_message_id or make_id("msg")
        group_id = self._upsert_group(event.platform, event.external_group_id, event.group_name)
        user_id = self._upsert_user(event.platform, event.external_user_id, event.user_name)

        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO messages (
                    id, group_id, user_id, external_message_id, message_type, raw_text, received_at, sent_at,
                    reply_to_message_id, quoted_message_id, has_media, media_type, media_path, ingest_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    group_id,
                    user_id,
                    event.external_message_id,
                    event.message_type.value,
                    event.raw_text,
                    received_at,
                    sent_at,
                    event.reply_to_message_id,
                    event.quoted_message_id,
                    int(event.has_media or event.message_type == MessageType.AUDIO),
                    event.media_type or (MessageType.AUDIO.value if event.message_type == MessageType.AUDIO else None),
                    event.media_path,
                    "persisted",
                    isoformat(),
                ),
            )

        transcription = self._transcribe_audio(message_id, event)
        analysis_source = event.raw_text or transcription.transcript_text or ""
        normalized_text = normalize_text(analysis_source)
        contains_bad_language = contains_profanity(normalized_text)
        direct_address = contains_direct_address(normalized_text)
        language = detect_language(normalized_text)

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO normalized_messages (
                    id, message_id, analysis_text, normalization_version, contains_profanity,
                    contains_direct_address, char_count, token_estimate, language, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("nmsg"),
                    message_id,
                    normalized_text,
                    "v1",
                    int(contains_bad_language),
                    int(direct_address),
                    len(normalized_text),
                    token_estimate(normalized_text),
                    language,
                    isoformat(),
                ),
            )
            self.connection.execute(
                """
                INSERT INTO message_search (message_id, group_id, user_id, analysis_text)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, group_id, user_id, normalized_text),
            )

        message_features = compute_message_features(
            normalized_text,
            reply_to_message_id=event.reply_to_message_id,
            contains_profanity=contains_bad_language,
            contains_direct_address=direct_address,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO message_features (
                    id, message_id, feature_version, caps_ratio, exclamation_count, question_count,
                    direct_attack_score, profanity_score, sarcasm_hint_score, imperative_score,
                    reply_intensity_score, negativity_score, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("mf"),
                    message_id,
                    "v1",
                    message_features.caps_ratio,
                    message_features.exclamation_count,
                    message_features.question_count,
                    message_features.direct_attack_score,
                    message_features.profanity_score,
                    message_features.sarcasm_hint_score,
                    message_features.imperative_score,
                    message_features.reply_intensity_score,
                    message_features.negativity_score,
                    json.dumps(message_features.details, ensure_ascii=True),
                    isoformat(),
                ),
            )

        short_rows = self._fetch_window_rows(
            group_id=group_id,
            end_timestamp=sent_at,
            minutes=self.config.windows.short_minutes,
            max_messages=self.config.windows.short_message_count,
        )
        short_window_id, short_features = self._store_window(
            group_id=group_id,
            rows=short_rows,
            window_type="hybrid_short",
            window_definition={
                "minutes": self.config.windows.short_minutes,
                "max_messages": self.config.windows.short_message_count,
            },
        )

        assessment_id = None
        alert_ids: list[str] = []
        severity = Severity.NORMAL.value
        risk_score = short_features.heuristic_risk_score

        if risk_score >= self.config.heuristics.llm_threshold:
            expanded_rows = self._fetch_window_rows(
                group_id=group_id,
                end_timestamp=sent_at,
                minutes=self.config.windows.expanded_minutes,
                max_messages=self.config.windows.expanded_message_count,
            )
            expanded_window_id, expanded_features = self._store_window(
                group_id=group_id,
                rows=expanded_rows,
                window_type="hybrid_expanded",
                window_definition={
                    "minutes": self.config.windows.expanded_minutes,
                    "max_messages": self.config.windows.expanded_message_count,
                },
            )
            window_snapshot = self._build_window_snapshot(
                expanded_window_id, group_id, expanded_rows, expanded_features
            )
            classification, llm_metadata = self.classifier.classify(window_snapshot)
            assessment_id = self._store_classification_and_assessment(
                expanded_window_id,
                classification,
                llm_metadata,
            )
            severity = classification.severity.value
            risk_score = classification.escalation_risk
            created_alerts = self._maybe_emit_alert(
                group_id=group_id,
                group_name=event.group_name or event.external_group_id,
                incident_assessment_id=assessment_id,
                classification=classification,
                risk_score=risk_score,
            )
            alert_ids.extend(created_alerts)
        elif risk_score >= self.config.heuristics.heuristic_only_threshold:
            classification, llm_metadata = self.classifier.classify(
                self._build_window_snapshot(short_window_id, group_id, short_rows, short_features)
            )
            assessment_id = self._store_classification_and_assessment(
                short_window_id,
                classification,
                llm_metadata,
            )
            severity = classification.severity.value
            risk_score = classification.escalation_risk
            created_alerts = self._maybe_emit_alert(
                group_id=group_id,
                group_name=event.group_name or event.external_group_id,
                incident_assessment_id=assessment_id,
                classification=classification,
                risk_score=risk_score,
            )
            alert_ids.extend(created_alerts)

        return {
            "message_id": message_id,
            "group_id": group_id,
            "user_id": user_id,
            "assessment_id": assessment_id,
            "alert_ids": alert_ids,
            "severity": severity,
            "risk_score": risk_score,
        }

    def record_feedback(
        self,
        incident_id: str,
        feedback_type: str,
        *,
        note: str | None = None,
        reviewer_id: str | None = None,
    ) -> str:
        if feedback_type not in {item.value for item in FeedbackType}:
            raise ValueError(f"feedback_type invalido: {feedback_type}")
        feedback_id = make_id("fb")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO moderator_feedback (
                    id, incident_assessment_id, feedback_type, feedback_note, reviewer_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, incident_id, feedback_type, note, reviewer_id, isoformat()),
            )
        return feedback_id

    def build_daily_report(self, group_id: str, report_date: str) -> tuple[str, DailyReportPayload]:
        group_exists = self.connection.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group_exists:
            raise ValueError(f"group_id nao encontrado: {group_id}")
        markdown, payload = generate_daily_report(self.connection, group_id, report_date)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO daily_reports (id, group_id, report_date, report_markdown, report_payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, report_date) DO UPDATE SET
                    report_markdown = excluded.report_markdown,
                    report_payload_json = excluded.report_payload_json,
                    created_at = excluded.created_at
                """,
                (
                    make_id("rpt"),
                    group_id,
                    report_date,
                    markdown,
                    json.dumps(payload, ensure_ascii=True),
                    isoformat(),
                ),
            )
        return markdown, payload

    def _upsert_group(self, platform: str, external_group_id: str, display_name: str | None) -> str:
        existing = self.connection.execute(
            "SELECT id FROM groups WHERE platform = ? AND external_group_id = ?",
            (platform, external_group_id),
        ).fetchone()
        if existing:
            group_id = str(existing["id"])
            self.connection.execute(
                "UPDATE groups SET display_name = COALESCE(?, display_name), updated_at = ? WHERE id = ?",
                (display_name, isoformat(), group_id),
            )
            return group_id
        group_id = make_id("grp")
        self.connection.execute(
            """
            INSERT INTO groups (id, platform, external_group_id, display_name, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (group_id, platform, external_group_id, display_name, isoformat(), isoformat()),
        )
        return group_id

    def _upsert_user(self, platform: str, external_user_id: str, display_name: str | None) -> str:
        existing = self.connection.execute(
            "SELECT id FROM users WHERE platform = ? AND external_user_id = ?",
            (platform, external_user_id),
        ).fetchone()
        if existing:
            user_id = str(existing["id"])
            self.connection.execute(
                "UPDATE users SET display_name = COALESCE(?, display_name), last_seen_at = ? WHERE id = ?",
                (display_name, isoformat(), user_id),
            )
            return user_id
        user_id = make_id("usr")
        self.connection.execute(
            """
            INSERT INTO users (id, platform, external_user_id, display_name, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, platform, external_user_id, display_name, isoformat(), isoformat()),
        )
        return user_id

    def _transcribe_audio(self, message_id: str, event: IncomingMessage) -> TranscriptionResult:
        if event.message_type != MessageType.AUDIO:
            return TranscriptionResult(
                transcript_text=None,
                language=event.language,
                confidence=None,
                duration_seconds=None,
                status="not_applicable",
            )
        started_at = isoformat()
        if event.transcript_text:
            result = TranscriptionResult(
                transcript_text=event.transcript_text,
                language=event.language,
                confidence=0.8,
                duration_seconds=float(event.metadata.get("duration_seconds", 0.0)),
                status="completed",
            )
        else:
            result = self.transcriber.transcribe(event.media_path or "", event.language)
            if result.duration_seconds is None and event.metadata.get("duration_seconds") is not None:
                result.duration_seconds = float(event.metadata.get("duration_seconds", 0.0))
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO audio_transcriptions (
                    id, message_id, provider, model, transcript_text, language, confidence,
                    duration_seconds, status, error_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("tr"),
                    message_id,
                    self.config.transcription.provider if not event.transcript_text else "static",
                    self.config.transcription.model if not event.transcript_text else "provided-transcript",
                    result.transcript_text,
                    result.language,
                    result.confidence,
                    result.duration_seconds,
                    result.status,
                    result.error_message,
                    started_at,
                    isoformat(),
                ),
            )
        return result

    def _fetch_window_rows(
        self,
        *,
        group_id: str,
        end_timestamp: str,
        minutes: int,
        max_messages: int,
    ) -> list[dict[str, object]]:
        end_dt = parse_timestamp(end_timestamp)
        assert end_dt is not None
        start_dt = end_dt.timestamp() - (minutes * 60)
        rows = self.connection.execute(
            """
            SELECT *
            FROM (
                SELECT
                    m.id AS message_id,
                    m.group_id,
                    m.user_id,
                    u.display_name AS author_name,
                    m.message_type,
                    m.reply_to_message_id,
                    COALESCE(m.sent_at, m.received_at) AS sort_ts,
                    COALESCE(nm.analysis_text, m.raw_text, at.transcript_text, '') AS analysis_text,
                    mf.caps_ratio,
                    mf.exclamation_count,
                    mf.question_count,
                    mf.direct_attack_score,
                    mf.profanity_score,
                    mf.sarcasm_hint_score,
                    mf.imperative_score,
                    mf.reply_intensity_score,
                    mf.negativity_score,
                    at.duration_seconds
                FROM messages m
                JOIN users u ON u.id = m.user_id
                LEFT JOIN normalized_messages nm ON nm.message_id = m.id
                LEFT JOIN audio_transcriptions at ON at.message_id = m.id
                LEFT JOIN message_features mf ON mf.message_id = m.id
                WHERE m.group_id = ?
                  AND strftime('%s', COALESCE(m.sent_at, m.received_at)) >= ?
                  AND strftime('%s', COALESCE(m.sent_at, m.received_at)) <= strftime('%s', ?)
                ORDER BY COALESCE(m.sent_at, m.received_at) DESC
                LIMIT ?
            ) recent
            ORDER BY sort_ts ASC
            """,
            (group_id, int(start_dt), end_timestamp, max_messages),
        ).fetchall()
        return [dict(row) for row in rows]

    def _store_window(
        self,
        *,
        group_id: str,
        rows: list[dict[str, object]],
        window_type: str,
        window_definition: dict[str, object],
    ):
        window_id = make_id("win")
        if rows:
            start_at = str(rows[0]["sort_ts"])
            end_at = str(rows[-1]["sort_ts"])
        else:
            start_at = end_at = isoformat()
        user_count = len({row["user_id"] for row in rows})
        features = compute_window_features(rows, self.config.heuristics)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO analysis_windows (
                    id, group_id, window_type, window_start_at, window_end_at, message_count,
                    distinct_user_count, window_definition_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    window_id,
                    group_id,
                    window_type,
                    start_at,
                    end_at,
                    len(rows),
                    user_count,
                    json.dumps(window_definition, ensure_ascii=True),
                    isoformat(),
                ),
            )
            for row in rows:
                self.connection.execute(
                    "INSERT INTO window_messages (window_id, message_id) VALUES (?, ?)",
                    (window_id, row["message_id"]),
                )
            self.connection.execute(
                """
                INSERT INTO window_features (
                    id, window_id, feature_version, messages_per_minute, reply_concentration_score,
                    dyadic_exchange_score, participant_concentration_score, escalation_velocity_score,
                    hostility_density_score, sustained_back_and_forth_score, audio_burst_score,
                    heuristic_risk_score, feature_details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("wf"),
                    window_id,
                    "v1",
                    features.messages_per_minute,
                    features.reply_concentration_score,
                    features.dyadic_exchange_score,
                    features.participant_concentration_score,
                    features.escalation_velocity_score,
                    features.hostility_density_score,
                    features.sustained_back_and_forth_score,
                    features.audio_burst_score,
                    features.heuristic_risk_score,
                    json.dumps(features.detail_signals, ensure_ascii=True),
                    isoformat(),
                ),
            )
        return window_id, features

    def _build_window_snapshot(
        self,
        window_id: str,
        group_id: str,
        rows: list[dict[str, object]],
        features,
    ) -> WindowSnapshot:
        metadata: WindowSnapshotMetadata = {
            "window_id": window_id,
            "group_id": group_id,
            "start_at": str(rows[0]["sort_ts"]) if rows else isoformat(),
            "end_at": str(rows[-1]["sort_ts"]) if rows else isoformat(),
            "message_count": len(rows),
            "distinct_user_count": len({row["user_id"] for row in rows}),
            "heuristic_risk_score": features.heuristic_risk_score,
            "heuristic_signals": {
                "messages_per_minute": features.messages_per_minute,
                "reply_concentration_score": features.reply_concentration_score,
                "dyadic_exchange_score": features.dyadic_exchange_score,
                "participant_concentration_score": features.participant_concentration_score,
                "escalation_velocity_score": features.escalation_velocity_score,
                "hostility_density_score": features.hostility_density_score,
                **features.detail_signals,
            },
        }
        messages: list[WindowSnapshotMessage] = [
            {
                "message_id": str(row["message_id"]),
                "timestamp": str(row["sort_ts"]),
                "author_name": str(row["author_name"] or row["user_id"]),
                "author_id": str(row["user_id"]),
                "text": str(row["analysis_text"]),
                "direct_attack_score": _to_float(row["direct_attack_score"]),
                "profanity_score": _to_float(row["profanity_score"]),
                "negativity_score": _to_float(row["negativity_score"]),
            }
            for row in rows
        ]
        snapshot: WindowSnapshot = {"metadata": metadata, "messages": messages}
        return snapshot

    def _store_classification_and_assessment(
        self,
        window_id: str,
        classification,
        llm_metadata: dict[str, object],
    ) -> str:
        llm_id = make_id("llm")
        incident_id = make_id("inc")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO llm_classifications (
                    id, window_id, provider, model, prompt_version, request_payload_json, response_raw_text,
                    response_json, parse_status, classification_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    llm_id,
                    window_id,
                    llm_metadata["provider"],
                    llm_metadata["model"],
                    llm_metadata["prompt_version"],
                    llm_metadata["request_payload_json"],
                    llm_metadata["response_raw_text"],
                    llm_metadata["response_json"],
                    llm_metadata["parse_status"],
                    llm_metadata["classification_status"],
                    isoformat(),
                ),
            )
            self.connection.execute(
                """
                INSERT INTO incident_assessments (
                    id, window_id, llm_classification_id, risk_score, severity, conflict_present,
                    trigger_message_id, participants_json, evidence_json, summary_short, summary_long,
                    recommended_action, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    window_id,
                    llm_id,
                    classification.escalation_risk,
                    classification.severity.value,
                    int(classification.conflict_present),
                    classification.trigger_message_id,
                    json.dumps(classification.participants, ensure_ascii=True),
                    json.dumps([item.model_dump(mode="json") for item in classification.evidence], ensure_ascii=True),
                    classification.summary_short,
                    classification.summary_long,
                    classification.recommended_action.value,
                    isoformat(),
                ),
            )
        return incident_id

    def _maybe_emit_alert(
        self,
        *,
        group_id: str,
        group_name: str,
        incident_assessment_id: str,
        classification,
        risk_score: float,
    ) -> list[str]:
        if not self._severity_meets_threshold(classification.severity.value):
            return []
        if not self._passes_cooldown(group_id, classification.severity.value, risk_score):
            return []
        trigger_message = None
        if classification.trigger_message_id:
            trigger_message = self.connection.execute(
                """
                SELECT
                    u.display_name AS author_name,
                    COALESCE(nm.analysis_text, m.raw_text, at.transcript_text, '') AS text
                FROM messages m
                JOIN users u ON u.id = m.user_id
                LEFT JOIN normalized_messages nm ON nm.message_id = m.id
                LEFT JOIN audio_transcriptions at ON at.message_id = m.id
                WHERE m.id = ?
                """,
                (classification.trigger_message_id,),
            ).fetchone()
        alert_ids = []
        for channel in self.config.alerts.channels:
            alert_id = make_id("alt")
            payload = build_alert_payload(
                alert_id=alert_id,
                incident_id=incident_assessment_id,
                group_id=group_id,
                group_name=group_name,
                result=classification,
                risk_score=risk_score,
                trigger_message_author=trigger_message["author_name"] if trigger_message else None,
                trigger_excerpt=trigger_message["text"] if trigger_message else None,
            )
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO alerts (
                        id, incident_assessment_id, alert_channel, alert_status, alert_payload_json, sent_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert_id,
                        incident_assessment_id,
                        channel,
                        "sent",
                        json.dumps(payload, ensure_ascii=True),
                        isoformat(),
                        isoformat(),
                    ),
                )
            if channel == "stdout":
                print(render_human_alert(payload))
                print(render_machine_alert(payload))
            alert_ids.append(alert_id)
        return alert_ids

    def _severity_meets_threshold(self, severity: str) -> bool:
        ordered = {"normal": 0, "atencao": 1, "tensao": 2, "incendio": 3}
        return ordered[severity] >= ordered[self.config.alerts.minimum_severity]

    def _passes_cooldown(self, group_id: str, severity: str, risk_score: float) -> bool:
        last_alert = self.connection.execute(
            """
            SELECT a.created_at, ia.risk_score, ia.severity
            FROM alerts a
            JOIN incident_assessments ia ON ia.id = a.incident_assessment_id
            JOIN analysis_windows aw ON aw.id = ia.window_id
            WHERE aw.group_id = ?
            ORDER BY a.created_at DESC
            LIMIT 1
            """,
            (group_id,),
        ).fetchone()
        if not last_alert:
            return True
        current_ts = parse_timestamp(isoformat())
        previous_ts = parse_timestamp(str(last_alert["created_at"]))
        if current_ts is None or previous_ts is None:
            return True
        delta = (current_ts - previous_ts).total_seconds()
        if delta >= self.config.alerts.cooldown_seconds:
            return True
        previous_risk = float(last_alert["risk_score"] or 0.0)
        return risk_score - previous_risk > 0.15 or severity == "incendio"
