"""Feature extraction and heuristic risk scoring for conversational windows."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .config import HeuristicConfig

NEGATIVE_TERMS = {
    "distorcendo",
    "mentira",
    "mentindo",
    "ridiculo",
    "ridículo",
    "nunca",
    "absurdo",
    "vergonha",
    "covarde",
    "incompetente",
}

SARCASM_TERMS = {"aham", "ta bom", "tá bom", "claro", "parabens", "parabéns", "kkk", "kkkk"}
IMPERATIVE_PATTERNS = [
    re.compile(r"^(para|pare|calma|escuta|le|lê|olha|fala|fica)\b", re.IGNORECASE),
]
DIRECT_ATTACK_PATTERNS = [
    re.compile(
        r"\b(voce|você|tu|vc)\b.{0,25}\b("
        r"nunca|sempre|mente|mente de novo|nao sabe|não sabe|distorce|distorcendo"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(idiota|burro|imbecil|babaca|otario|otário|palhaco|palhaço)\b", re.IGNORECASE),
]


@dataclass(slots=True)
class MessageFeatureSet:
    """Computed per-message features used in downstream window analysis."""

    caps_ratio: float
    exclamation_count: int
    question_count: int
    direct_attack_score: float
    profanity_score: float
    sarcasm_hint_score: float
    imperative_score: float
    reply_intensity_score: float
    negativity_score: float
    details: dict[str, float | int | str]


@dataclass(slots=True)
class WindowFeatureSet:
    """Aggregated window-level features and final heuristic risk score."""

    messages_per_minute: float
    reply_concentration_score: float
    dyadic_exchange_score: float
    participant_concentration_score: float
    escalation_velocity_score: float
    hostility_density_score: float
    sustained_back_and_forth_score: float
    audio_burst_score: float
    heuristic_risk_score: float
    detail_signals: dict[str, float]


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Clamp a division result into ``[0, 1]`` while handling zero denominator.

    Args:
        numerator: Numerator of the ratio.
        denominator: Denominator of the ratio.

    Returns:
        Bounded ratio between 0 and 1.
    """
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _as_float(value: object) -> float:
    """Coerce arbitrary scalar values into ``float``.

    Args:
        value: Input value from SQLite rows or computed metadata.

    Returns:
        Floating-point representation, using ``0.0`` for ``None``.
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _as_int(value: object) -> int:
    """Coerce arbitrary scalar values into ``int``.

    Args:
        value: Input value from SQLite rows or computed metadata.

    Returns:
        Integer representation, using ``0`` for ``None``.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(float(str(value)))


def _alpha_caps_ratio(text: str) -> float:
    """Compute uppercase ratio considering alphabetic characters only.

    Args:
        text: Message text.

    Returns:
        Ratio of uppercase letters among all alphabetic characters.
    """
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    uppercase = sum(1 for char in letters if char.isupper())
    return uppercase / len(letters)


def compute_message_features(
    text: str,
    *,
    reply_to_message_id: str | None = None,
    contains_profanity: bool = False,
    contains_direct_address: bool = False,
) -> MessageFeatureSet:
    """Compute lexical and interactional features for one message.

    Args:
        text: Normalized analysis text.
        reply_to_message_id: Referenced parent message ID when this message is a reply.
        contains_profanity: Whether profanity was detected during normalization.
        contains_direct_address: Whether direct addressing was detected.

    Returns:
        Structured per-message feature set.
    """
    lowered = text.casefold()
    exclamation_count = text.count("!")
    question_count = text.count("?")
    caps_ratio = _alpha_caps_ratio(text)
    direct_attack_hits = sum(1 for pattern in DIRECT_ATTACK_PATTERNS if pattern.search(text))
    profanity_score = 1.0 if contains_profanity else 0.0
    sarcasm_hint_score = 1.0 if any(term in lowered for term in SARCASM_TERMS) else 0.0
    imperative_score = 1.0 if any(pattern.search(text) for pattern in IMPERATIVE_PATTERNS) else 0.0
    negativity_hits = sum(1 for term in NEGATIVE_TERMS if term in lowered)
    negativity_score = min(1.0, negativity_hits / 2.0)
    if contains_direct_address and (negativity_score > 0 or profanity_score > 0):
        direct_attack_hits += 1
    direct_attack_score = min(1.0, direct_attack_hits / 2.0)
    reply_intensity_score = 1.0 if reply_to_message_id else 0.0
    details = {
        "caps_ratio": round(caps_ratio, 4),
        "exclamation_count": exclamation_count,
        "question_count": question_count,
        "contains_direct_address": int(contains_direct_address),
        "contains_profanity": int(contains_profanity),
    }
    return MessageFeatureSet(
        caps_ratio=round(caps_ratio, 4),
        exclamation_count=exclamation_count,
        question_count=question_count,
        direct_attack_score=round(direct_attack_score, 4),
        profanity_score=round(profanity_score, 4),
        sarcasm_hint_score=round(sarcasm_hint_score, 4),
        imperative_score=round(imperative_score, 4),
        reply_intensity_score=round(reply_intensity_score, 4),
        negativity_score=round(negativity_score, 4),
        details=details,
    )


def compute_window_features(
    message_rows: list[dict[str, object]],
    heuristic_config: HeuristicConfig,
) -> WindowFeatureSet:
    """Aggregate message features into window-level escalation signals.

    Args:
        message_rows: Chronologically ordered message rows with feature columns.
        heuristic_config: Threshold and weighting configuration.

    Returns:
        Window-level signals and the final heuristic risk score.
    """
    if not message_rows:
        empty_details = {
            "direct_attack_density": 0.0,
            "profanity_density": 0.0,
            "caps_intensity": 0.0,
            "punctuation_aggression": 0.0,
        }
        return WindowFeatureSet(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, empty_details)

    timestamps = [datetime.fromisoformat(str(row["sort_ts"]).replace("Z", "+00:00")) for row in message_rows]
    duration_seconds = max(60.0, (timestamps[-1] - timestamps[0]).total_seconds())
    messages_per_minute = len(message_rows) / (duration_seconds / 60.0)
    reply_messages = sum(1 for row in message_rows if row["reply_to_message_id"])
    reply_concentration = _safe_ratio(reply_messages, len(message_rows))

    user_counts = Counter(str(row["user_id"]) for row in message_rows)
    two_most_common = sum(count for _, count in user_counts.most_common(2))
    participant_concentration = _safe_ratio(two_most_common, len(message_rows))

    alternating_pairs = 0
    sustained_runs = 0
    longest_alternating_run = 1
    current_run = 1
    for previous, current in zip(message_rows, message_rows[1:], strict=False):
        prev_user = str(previous["user_id"])
        curr_user = str(current["user_id"])
        if prev_user != curr_user:
            alternating_pairs += 1
        if len({prev_user, curr_user}) == 2:
            current_run += 1
            longest_alternating_run = max(longest_alternating_run, current_run)
        else:
            current_run = 1
    sustained_runs = longest_alternating_run
    dyadic_exchange = _safe_ratio(alternating_pairs, max(1, len(message_rows) - 1))
    sustained_back_and_forth = _safe_ratio(sustained_runs - 1, len(message_rows))

    aggression_series = []
    direct_attack_total = 0.0
    profanity_total = 0.0
    caps_total = 0.0
    punctuation_total = 0.0
    audio_burst_hits = 0
    hostility_sum = 0.0

    for row in message_rows:
        direct_attack = _as_float(row["direct_attack_score"])
        profanity = _as_float(row["profanity_score"])
        negativity = _as_float(row["negativity_score"])
        caps_ratio = _as_float(row["caps_ratio"])
        punctuation = min(1.0, (_as_int(row["exclamation_count"]) + _as_int(row["question_count"])) / 6.0)
        aggression = min(1.0, (direct_attack * 0.4) + (profanity * 0.3) + (negativity * 0.3))
        aggression_series.append(aggression)
        direct_attack_total += direct_attack
        profanity_total += profanity
        caps_total += caps_ratio
        punctuation_total += punctuation
        hostility_sum += aggression
        if row["message_type"] == "audio" and _as_float(row["duration_seconds"]) >= 60:
            audio_burst_hits += 1

    third = max(1, math.ceil(len(aggression_series) / 3))
    early = aggression_series[:third]
    late = aggression_series[-third:]
    escalation_velocity = max(0.0, min(1.0, (sum(late) / len(late)) - (sum(early) / len(early)) + 0.25))
    hostility_density = _safe_ratio(hostility_sum, len(message_rows))
    direct_attack_density = _safe_ratio(direct_attack_total, len(message_rows))
    profanity_density = _safe_ratio(profanity_total, len(message_rows))
    caps_intensity = _safe_ratio(caps_total, len(message_rows))
    punctuation_aggression = _safe_ratio(punctuation_total, len(message_rows))
    audio_burst_score = _safe_ratio(audio_burst_hits, len(message_rows))

    detail_signals = {
        "direct_attack_density": round(direct_attack_density, 4),
        "profanity_density": round(profanity_density, 4),
        "caps_intensity": round(caps_intensity, 4),
        "punctuation_aggression": round(punctuation_aggression, 4),
    }

    score_inputs = {
        "hostility_density_score": hostility_density,
        "dyadic_exchange_score": dyadic_exchange,
        "escalation_velocity_score": escalation_velocity,
        "reply_concentration_score": reply_concentration,
        "participant_concentration_score": participant_concentration,
        **detail_signals,
    }
    heuristic_risk = 0.0
    for key, weight in heuristic_config.weights.items():
        heuristic_risk += score_inputs.get(key, 0.0) * weight
    heuristic_risk = round(max(0.0, min(1.0, heuristic_risk)), 4)

    return WindowFeatureSet(
        messages_per_minute=round(messages_per_minute, 4),
        reply_concentration_score=round(reply_concentration, 4),
        dyadic_exchange_score=round(dyadic_exchange, 4),
        participant_concentration_score=round(participant_concentration, 4),
        escalation_velocity_score=round(escalation_velocity, 4),
        hostility_density_score=round(hostility_density, 4),
        sustained_back_and_forth_score=round(sustained_back_and_forth, 4),
        audio_burst_score=round(audio_burst_score, 4),
        heuristic_risk_score=heuristic_risk,
        detail_signals=detail_signals,
    )


def heuristic_severity(score: float) -> str:
    """Map heuristic score into severity buckets.

    Args:
        score: Risk score between 0 and 1.

    Returns:
        Severity label in ``normal``, ``atencao``, ``tensao`` or ``incendio``.
    """
    if score < 0.35:
        return "normal"
    if score < 0.55:
        return "atencao"
    if score < 0.75:
        return "tensao"
    return "incendio"
