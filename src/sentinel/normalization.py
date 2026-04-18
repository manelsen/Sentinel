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
    if not text:
        return ""
    normalized = text
    for emoji, marker in EMOJI_MARKERS.items():
        normalized = normalized.replace(emoji, marker)
    normalized = normalized.replace("\u200b", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def contains_profanity(text: str) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in PROFANITY_WORDS)


def contains_direct_address(text: str) -> bool:
    return any(pattern.search(text) for pattern in DIRECT_ADDRESS_PATTERNS)


def detect_language(_text: str) -> str:
    return "pt-BR"


def token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0
