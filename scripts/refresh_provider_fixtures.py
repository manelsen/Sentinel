from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "providers"


def build_demo_window() -> dict[str, object]:
    return {
        "metadata": {
            "group_id": "grp_fixture",
            "window_id": "win_fixture",
            "start_at": "2026-04-18T16:00:00Z",
            "end_at": "2026-04-18T16:02:00Z",
            "message_count": 4,
            "distinct_user_count": 2,
            "heuristic_risk_score": 0.72,
            "heuristic_signals": {
                "hostility_density_score": 0.68,
                "dyadic_exchange_score": 0.84,
                "reply_concentration_score": 0.76,
                "escalation_velocity_score": 0.71,
            },
        },
        "messages": [
            {
                "message_id": "msg_fixture_1",
                "timestamp": "2026-04-18T16:00:10Z",
                "author_name": "Alice",
                "author_id": "alice",
                "text": "Voce esta distorcendo tudo de novo.",
                "direct_attack_score": 0.81,
                "profanity_score": 0.0,
                "negativity_score": 0.4,
            },
            {
                "message_id": "msg_fixture_2",
                "timestamp": "2026-04-18T16:00:25Z",
                "author_name": "Bruno",
                "author_id": "bruno",
                "text": "Nao, voce que nao le antes de responder.",
                "direct_attack_score": 0.73,
                "profanity_score": 0.0,
                "negativity_score": 0.35,
            },
            {
                "message_id": "msg_fixture_3",
                "timestamp": "2026-04-18T16:01:05Z",
                "author_name": "Alice",
                "author_id": "alice",
                "text": "Para de inventar coisa, isso esta ridiculo.",
                "direct_attack_score": 0.78,
                "profanity_score": 0.0,
                "negativity_score": 0.47,
            },
            {
                "message_id": "msg_fixture_4",
                "timestamp": "2026-04-18T16:01:45Z",
                "author_name": "Bruno",
                "author_id": "bruno",
                "text": "Entao mostra onde eu falei isso.",
                "direct_attack_score": 0.31,
                "profanity_score": 0.0,
                "negativity_score": 0.16,
            },
        ],
    }


def write_demo_wav(path: Path, *, sample_rate: int = 16000, seconds: float = 1.0) -> None:
    frame_count = int(sample_rate * seconds)
    amplitude = 12000
    frequency = 440.0
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            value = int(amplitude * math.sin(2.0 * math.pi * frequency * (index / sample_rate)))
            frames.extend(value.to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(frames)


def capture_groq_fixture(groq_api_key: str) -> dict[str, object]:
    from sentinel.providers import (
        GroqTranscriber,
        ProviderError,
        _decode_json_object,
        _multipart_body,
        _normalize_groq_language,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = Path(temp_dir) / "fixture.wav"
        write_demo_wav(wav_path)
        transcriber = GroqTranscriber(
            api_key=groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            model="whisper-large-v3-turbo",
            timeout_seconds=120,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
        file_bytes = wav_path.read_bytes()
        body, boundary = _multipart_body(
            fields={
                "model": transcriber.model,
                "response_format": transcriber.response_format,
                "language": _normalize_groq_language("pt-BR") or "pt",
                "timestamp_granularities[]#0": "segment",
            },
            file_field="file",
            file_name=wav_path.name,
            file_bytes=file_bytes,
            file_content_type="audio/wav",
        )
        request = urllib.request.Request(
            transcriber.transcription_url,
            data=body,
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Sentinel/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=transcriber.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"Groq HTTP {exc.code}: {body_text[:400]}") from exc
        parsed = _decode_json_object(response_body, url=transcriber.transcription_url)
    return {
        "provider": "groq",
        "captured_from_model": transcriber.model,
        "input": {
            "audio_kind": "generated_sine_wav",
            "duration_seconds": 1.0,
            "language": "pt-BR",
            "response_format": transcriber.response_format,
        },
        "response": parsed,
    }


def capture_gemini_fixture(gemini_api_key: str) -> dict[str, object]:
    from sentinel.prompts import build_prompt
    from sentinel.providers import CLASSIFICATION_RESPONSE_SCHEMA, GeminiStructuredClient, _http_json

    prompt = build_prompt(build_demo_window())
    client = GeminiStructuredClient(
        api_key=gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash",
        timeout_seconds=30,
    )
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": f"{prompt.system_prompt.strip()}\n\n{prompt.user_prompt.strip()}"}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": CLASSIFICATION_RESPONSE_SCHEMA,
            "temperature": 0,
        },
    }
    response = _http_json(
        url=client.generate_content_url,
        payload=payload,
        headers={"x-goog-api-key": gemini_api_key},
        timeout=client.timeout_seconds,
    )
    return {
        "provider": "gemini",
        "captured_from_model": client.model,
        "input": {
            "prompt_version": prompt.request_payload["prompt_version"],
            "window_metadata": prompt.request_payload["metadata"],
            "message_count": len(prompt.request_payload["messages"]),
        },
        "response": response,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh offline provider fixtures using live API keys.")
    parser.add_argument("--groq-api-key", default=os.getenv("GROQ_API_KEY", ""))
    parser.add_argument("--gemini-api-key", default=os.getenv("GEMINI_API_KEY", ""))
    args = parser.parse_args()
    if not args.groq_api_key.strip():
        raise SystemExit("GROQ_API_KEY ausente")
    if not args.gemini_api_key.strip():
        raise SystemExit("GEMINI_API_KEY ausente")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        groq_fixture = capture_groq_fixture(args.groq_api_key.strip())
    except Exception as exc:
        groq_error = {
            "provider": "groq",
            "captured_error": str(exc),
        }
        (FIXTURES_DIR / "groq_transcription_error_fixture.json").write_text(
            json.dumps(groq_error, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print("groq fixture refresh failed:", exc)
    else:
        (FIXTURES_DIR / "groq_transcription_fixture.json").write_text(
            json.dumps(groq_fixture, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print("groq fixture refreshed")

    gemini_fixture = capture_gemini_fixture(args.gemini_api_key.strip())
    (FIXTURES_DIR / "gemini_generate_content_fixture.json").write_text(
        json.dumps(gemini_fixture, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print("gemini fixture refreshed")
    print("fixtures refreshed in", FIXTURES_DIR)


if __name__ == "__main__":
    main()
