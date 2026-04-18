from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.config import AppConfig
from sentinel.prompts import build_prompt
from sentinel.providers import (
    CLASSIFICATION_RESPONSE_SCHEMA,
    GeminiStructuredClient,
    ProviderError,
    _extract_gemini_text,
    _normalize_groq_language,
    _parse_groq_transcription_payload,
    _parse_groq_transcription_response,
    build_gemini_client,
    build_transcriber,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "providers"


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class ProviderTests(unittest.TestCase):
    def test_extracts_gemini_text(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"conflict_present": false}'},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(_extract_gemini_text(cast(dict[str, object], payload)), '{"conflict_present": false}')

    def test_normalizes_groq_language_locale(self) -> None:
        self.assertEqual(_normalize_groq_language("pt-BR"), "pt")
        self.assertEqual(_normalize_groq_language("en_US"), "en")
        self.assertIsNone(_normalize_groq_language(None))

    def test_build_transcriber_uses_groq_when_key_exists(self) -> None:
        config = AppConfig()
        config.transcription.provider = "groq"
        with patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=False):
            transcriber = build_transcriber(config)
        self.assertEqual(transcriber.__class__.__name__, "GroqTranscriber")

    def test_build_gemini_client_requires_key(self) -> None:
        config = AppConfig()
        config.llm.provider = "gemini"
        with patch.dict(os.environ, {}, clear=True):
            client = build_gemini_client(config)
        self.assertIsNone(client)

    def test_gemini_client_parses_structured_json(self) -> None:
        prompt = build_prompt(
            {
                "metadata": {
                    "group_id": "grp_1",
                    "window_id": "win_1",
                    "start_at": "2026-04-18T16:00:00Z",
                    "end_at": "2026-04-18T16:01:00Z",
                    "message_count": 1,
                    "distinct_user_count": 1,
                    "heuristic_risk_score": 0.6,
                    "heuristic_signals": {"hostility_density_score": 0.7},
                },
                "messages": [
                    {
                        "message_id": "msg_1",
                        "timestamp": "2026-04-18T16:00:00Z",
                        "author_name": "Alice",
                        "author_id": "alice",
                        "text": "Voce esta distorcendo tudo.",
                        "direct_attack_score": 0.8,
                        "profanity_score": 0.0,
                        "negativity_score": 0.4,
                    }
                ],
            }
        )
        with patch("sentinel.providers._http_json") as mocked_http:
            mocked_http.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"conflict_present": true, "escalation_risk": 0.81, '
                                        '"severity": "incendio", "participants": ["Alice"], '
                                        '"trigger_message_id": "msg_1", "evidence": ['
                                        '{"message_id": "msg_1", "reason": "acusacao_direta"}], '
                                        '"summary_short": "Conflito forte", "summary_long": "Conflito forte e rapido", '
                                        '"recommended_action": "alert_moderator_now", "confidence": 0.88, '
                                        '"uncertainty_notes": ""}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            }
            client = GeminiStructuredClient(
                api_key="secret",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                model="gemini-2.5-flash",
                timeout_seconds=10,
            )
            parsed, raw = client.classify_json(prompt, CLASSIFICATION_RESPONSE_SCHEMA)
        self.assertEqual(parsed["severity"], "incendio")
        self.assertIn("alert_moderator_now", raw)

    def test_parses_groq_fixture_payload(self) -> None:
        fixture = _load_fixture("groq_transcription_fixture.json")
        parsed = _parse_groq_transcription_payload(
            cast(dict[str, object], fixture["response"]),
            fallback_language="pt-BR",
        )
        self.assertEqual(parsed.status, "completed")
        self.assertTrue(parsed.transcript_text)
        self.assertEqual(parsed.language, "Portuguese")
        self.assertIsNotNone(parsed.duration_seconds)

    def test_parses_groq_plaintext_response(self) -> None:
        parsed = _parse_groq_transcription_response("transcricao direta\n", fallback_language="pt-BR")
        self.assertEqual(parsed.status, "completed")
        self.assertEqual(parsed.transcript_text, "transcricao direta")
        self.assertEqual(parsed.language, "pt-BR")

    def test_records_groq_live_error_fixture(self) -> None:
        fixture = _load_fixture("groq_transcription_error_fixture.json")
        self.assertEqual(fixture["provider"], "groq")
        self.assertIn("403", str(fixture["captured_error"]))

    def test_extracts_gemini_text_from_fixture(self) -> None:
        fixture = _load_fixture("gemini_generate_content_fixture.json")
        response = cast(dict[str, object], fixture["response"])
        text = _extract_gemini_text(response)
        parsed = json.loads(text)
        self.assertIn("severity", parsed)
        self.assertIn(parsed["severity"], {"normal", "atencao", "tensao", "incendio"})

    def test_raises_when_gemini_fixture_has_no_candidates(self) -> None:
        fixture = _load_fixture("gemini_missing_candidates_fixture.json")
        response = cast(dict[str, object], fixture["response"])
        with self.assertRaises(ProviderError) as raised:
            _extract_gemini_text(response)
        self.assertEqual(str(raised.exception), fixture["expected_error"])

    def test_raises_when_gemini_fixture_has_no_parts(self) -> None:
        fixture = _load_fixture("gemini_missing_parts_fixture.json")
        response = cast(dict[str, object], fixture["response"])
        with self.assertRaises(ProviderError) as raised:
            _extract_gemini_text(response)
        self.assertEqual(str(raised.exception), fixture["expected_error"])

    def test_parses_groq_fixture_missing_text_as_failed(self) -> None:
        fixture = _load_fixture("groq_transcription_missing_text_fixture.json")
        parsed = _parse_groq_transcription_payload(
            cast(dict[str, object], fixture["response"]),
            fallback_language="pt-BR",
        )
        self.assertEqual(parsed.status, fixture["expected_status"])
        self.assertEqual(parsed.error_message, fixture["expected_error"])
        self.assertIsNone(parsed.transcript_text)

    def test_parses_groq_empty_response_fixture(self) -> None:
        fixture = _load_fixture("groq_transcription_empty_response_fixture.json")
        parsed = _parse_groq_transcription_response(
            str(fixture["response_body"]),
            fallback_language=str(fixture["fallback_language"]),
        )
        self.assertEqual(parsed.status, fixture["expected_status"])
        self.assertEqual(parsed.error_message, fixture["expected_error"])
        self.assertEqual(parsed.language, fixture["fallback_language"])


if __name__ == "__main__":
    unittest.main()
