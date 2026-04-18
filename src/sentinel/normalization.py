"""Text normalization and lightweight lexical signal detection utilities."""

from __future__ import annotations

import math
import re

EMOJI_MARKERS = {
    "😡": " [emoji_raiva] ",
    "🤡": " [emoji_deboche] ",
    "😂": " [emoji_riso] ",
    "🤣": " [emoji_riso] ",
    "😒": " [emoji_desdem] ",
}

PROFANITY_WORDS = {
    "idiota",
    "burro",
    "imbecil",
    "palhaco",
    "palhaço",
    "merda",
    "porra",
    "caralho",
    "otario",
    "otário",
    "babaca",
}

DIRECT_ADDRESS_PATTERNS = [
    re.compile(r"\b(voce|você|vc|tu|vocês)\b", re.IGNORECASE),
    re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+,", re.UNICODE),
]


def normalize_text(text: str | None) -> str:
    """Normalize user text while preserving aggression-relevant markers.

    Args:
        text: Raw input text.

    Returns:
        Whitespace-normalized text with supported emoji markers expanded.
    """
    if not text:
        return ""
    normalized = text
    for emoji, marker in EMOJI_MARKERS.items():
        normalized = normalized.replace(emoji, marker)
    normalized = normalized.replace("\u200b", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def contains_profanity(text: str) -> bool:
    """Detect whether text contains known profanity lexicon entries.

    Args:
        text: Normalized analysis text.

    Returns:
        ``True`` when at least one profanity token is found.
    """
    lowered = text.casefold()
    return any(word in lowered for word in PROFANITY_WORDS)


def contains_direct_address(text: str) -> bool:
    """Detect whether text directly addresses a participant.

    Args:
        text: Normalized analysis text.

    Returns:
        ``True`` when a direct-address pattern is matched.
    """
    return any(pattern.search(text) for pattern in DIRECT_ADDRESS_PATTERNS)


def detect_language(_text: str) -> str:
    """Return the language code used by the current normalization pipeline.

    Args:
        _text: Analysis text (currently unused placeholder).

    Returns:
        BCP-47 language code.
    """
    return "pt-BR"


def token_estimate(text: str) -> int:
    """Estimate token count using a conservative character-based heuristic.

    Args:
        text: Normalized analysis text.

    Returns:
        Estimated token count.
    """
    return max(1, math.ceil(len(text) / 4)) if text else 0
