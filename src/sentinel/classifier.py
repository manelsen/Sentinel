"""Structured classification orchestration and heuristic fallback logic."""

from __future__ import annotations

import json
import shlex
import subprocess

from .config import AppConfig
from .heuristics import heuristic_severity
from .models import (
    ClassificationResult,
    PromptBundle,
    RecommendedAction,
    Severity,
    WindowSnapshot,
    WindowSnapshotMessage,
)
from .prompts import PROMPT_VERSION, build_prompt
from .providers import CLASSIFICATION_RESPONSE_SCHEMA, build_gemini_client


class ClassificationError(ValueError):
    """Raised when a provider classification payload is invalid for a window."""

    pass


def validate_classification(payload: dict[str, object], window_message_ids: set[str]) -> ClassificationResult:
    """Validate and normalize a provider payload into ``ClassificationResult``.

    Args:
        payload: Parsed JSON payload returned by a classifier provider.
        window_message_ids: Set of message IDs that belong to the analyzed window.

    Returns:
        Validated classification model.

    Raises:
        ClassificationError: If schema validation fails or IDs are inconsistent.
    """
    try:
        result = ClassificationResult.model_validate(payload)
    except Exception as exc:
        raise ClassificationError(str(exc)) from exc
    if result.trigger_message_id and result.trigger_message_id not in window_message_ids:
        raise ClassificationError("trigger_message_id fora da janela")
    return result


class StructuredClassifier:
    """Classify window snapshots using Gemini, command mode, or fallback heuristics."""

    def __init__(self, config: AppConfig):
        """Initialize classifier strategy from runtime configuration.

        Args:
            config: Application configuration with provider settings.
        """
        self.config = config
        self.gemini_client = build_gemini_client(config)

    def classify(
        self,
        window_snapshot: WindowSnapshot,
    ) -> tuple[ClassificationResult, dict[str, object]]:
        """Classify one analysis window and return result plus persistence metadata.

        Args:
            window_snapshot: Window payload containing metadata and ordered messages.

        Returns:
            Tuple where the first element is the normalized classification and the
            second element contains provider metadata stored in SQLite.
        """
        prompt = build_prompt(window_snapshot)
        window_message_ids = {item["message_id"] for item in window_snapshot["messages"]}

        if self.gemini_client:
            try:
                result, metadata = self._classify_with_gemini(prompt, window_message_ids)
                return result, metadata
            except Exception as exc:  # pragma: no cover - fallback path is still tested via service
                fallback, metadata = self._fallback_classification(window_snapshot, prompt)
                metadata["classification_status"] = f"gemini_failed:{exc.__class__.__name__}"
                return fallback, metadata

        if self.config.llm.command.strip():
            try:
                result, metadata = self._classify_with_command(prompt, window_message_ids)
                return result, metadata
            except Exception as exc:  # pragma: no cover - fallback path is still tested via service
                fallback, metadata = self._fallback_classification(window_snapshot, prompt)
                metadata["classification_status"] = f"command_failed:{exc.__class__.__name__}"
                return fallback, metadata

        return self._fallback_classification(window_snapshot, prompt)

    def _classify_with_gemini(
        self,
        prompt: PromptBundle,
        window_message_ids: set[str],
    ) -> tuple[ClassificationResult, dict[str, object]]:
        """Run structured classification through Gemini with JSON schema output.

        Args:
            prompt: Prompt bundle generated from the window snapshot.
            window_message_ids: IDs expected to appear in classification references.

        Returns:
            Validated classification and metadata to persist provider exchange.
        """
        assert self.gemini_client is not None
        parsed, raw_output = self.gemini_client.classify_json(prompt, CLASSIFICATION_RESPONSE_SCHEMA)
        result = validate_classification(parsed, window_message_ids)
        metadata: dict[str, object] = {
            "provider": "gemini",
            "model": self.config.llm.model,
            "prompt_version": PROMPT_VERSION,
            "request_payload_json": json.dumps(prompt.request_payload, ensure_ascii=True),
            "response_raw_text": raw_output,
            "response_json": json.dumps(parsed, ensure_ascii=True),
            "parse_status": "parsed",
            "classification_status": "ok",
        }
        return result, metadata

    def _classify_with_command(
        self,
        prompt: PromptBundle,
        window_message_ids: set[str],
    ) -> tuple[ClassificationResult, dict[str, object]]:
        """Run classification via user-provided command protocol over stdin/stdout.

        Args:
            prompt: Prompt bundle generated from the window snapshot.
            window_message_ids: IDs expected to appear in classification references.

        Returns:
            Validated classification and metadata to persist provider exchange.

        Raises:
            subprocess.CalledProcessError: If the configured command exits non-zero.
            json.JSONDecodeError: If stdout is not valid JSON.
            ClassificationError: If payload validation fails.
        """
        command = shlex.split(self.config.llm.command)
        payload = json.dumps(
            {
                "system_prompt": prompt.system_prompt,
                "user_prompt": prompt.user_prompt,
                "request_payload": prompt.request_payload,
            },
            ensure_ascii=True,
        )
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            check=True,
        )
        raw_output = completed.stdout.strip()
        parsed = json.loads(raw_output)
        result = validate_classification(parsed, window_message_ids)
        metadata: dict[str, object] = {
            "provider": self.config.llm.provider,
            "model": self.config.llm.model,
            "prompt_version": PROMPT_VERSION,
            "request_payload_json": json.dumps(prompt.request_payload, ensure_ascii=True),
            "response_raw_text": raw_output,
            "response_json": json.dumps(parsed, ensure_ascii=True),
            "parse_status": "parsed",
            "classification_status": "ok",
        }
        return result, metadata

    def _fallback_classification(
        self,
        window_snapshot: WindowSnapshot,
        prompt: PromptBundle,
    ) -> tuple[ClassificationResult, dict[str, object]]:
        """Derive a conservative classification using local heuristic signals only.

        Args:
            window_snapshot: Window payload containing metadata and ordered messages.
            prompt: Prompt bundle persisted for traceability even in fallback mode.

        Returns:
            Heuristic classification and synthetic provider metadata.
        """
        messages = window_snapshot["messages"]
        metadata = window_snapshot["metadata"]
        participants = []
        seen = set()
        for item in messages:
            author_name = str(item["author_name"])
            if author_name not in seen:
                seen.add(author_name)
                participants.append(author_name)
            if len(participants) == 3:
                break
        risk = float(metadata["heuristic_risk_score"])
        signals = metadata["heuristic_signals"]
        severity_text = heuristic_severity(risk)
        if (
            severity_text == "atencao"
            and float(signals.get("dyadic_exchange_score", 0.0)) >= 0.6
            and float(signals.get("hostility_density_score", 0.0)) >= 0.35
            and float(signals.get("direct_attack_density", 0.0)) >= 0.3
        ):
            severity_text = "tensao"
            risk = max(risk, 0.58)
        if (
            severity_text == "tensao"
            and float(signals.get("dyadic_exchange_score", 0.0)) >= 0.75
            and float(signals.get("hostility_density_score", 0.0)) >= 0.55
            and float(signals.get("direct_attack_density", 0.0)) >= 0.45
        ):
            severity_text = "incendio"
            risk = max(risk, 0.78)
        severity = Severity(severity_text)
        recommended_action = RecommendedAction.NONE
        if severity == Severity.ATENCAO:
            recommended_action = RecommendedAction.MONITOR
        elif severity == Severity.TENSAO:
            recommended_action = RecommendedAction.ALERT_MODERATOR
        elif severity == Severity.INCENDIO:
            recommended_action = RecommendedAction.ALERT_MODERATOR_NOW
        evidence = []
        trigger_message_id = None
        most_aggressive: tuple[float, WindowSnapshotMessage] | None = None
        for item in messages:
            score = (
                float(item["direct_attack_score"]) + float(item["profanity_score"]) + float(item["negativity_score"])
            )
            if most_aggressive is None or score > most_aggressive[0]:
                most_aggressive = (score, item)
        if most_aggressive and most_aggressive[1]:
            trigger_message_id = str(most_aggressive[1]["message_id"])
            evidence.append({"message_id": trigger_message_id, "reason": "heuristic_peak"})
        conflict_present = severity in {Severity.TENSAO, Severity.INCENDIO}
        result = ClassificationResult(
            conflict_present=conflict_present,
            escalation_risk=risk,
            severity=severity,
            participants=participants,
            trigger_message_id=trigger_message_id,
            evidence=evidence,
            summary_short=f"Janela com risco {risk:.2f} e sinais de escalada relacional detectados por heuristica.",
            summary_long=(
                "Classificacao derivada por fallback heuristico. "
                f"Sinais: {json.dumps(metadata['heuristic_signals'], ensure_ascii=True)}."
            ),
            recommended_action=recommended_action,
            confidence=0.55 if conflict_present else 0.7,
            uncertainty_notes="Classificacao sem LLM externo; usar como triagem inicial.",
        )
        metadata_out: dict[str, object] = {
            "provider": "fallback",
            "model": "heuristic-fallback",
            "prompt_version": PROMPT_VERSION,
            "request_payload_json": json.dumps(prompt.request_payload, ensure_ascii=True),
            "response_raw_text": result.model_dump_json(),
            "response_json": result.model_dump_json(),
            "parse_status": "parsed",
            "classification_status": "fallback_heuristic",
        }
        return result, metadata_out
