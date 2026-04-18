"""Provider clients and adapters for transcription and structured classification."""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .models import ClassificationResult, PromptBundle, TranscriptionResult


class ProviderError(RuntimeError):
    """Raised when an external provider returns invalid or unusable data."""

    pass


def _http_json(
    *,
    url: str,
    payload: dict[str, object],
    headers: Mapping[str, str],
    timeout: int,
) -> dict[str, object]:
    """Send an HTTP JSON POST request and decode a JSON object response.

    Args:
        url: Target endpoint URL.
        payload: Request payload.
        headers: Additional HTTP headers.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON object.

    Raises:
        ProviderError: If network, HTTP or decoding errors occur.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        body = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(f"HTTP {exc.code} em {url}: {body}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network path
        raise ProviderError(f"Falha de rede em {url}: {exc.reason}") from exc
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Resposta JSON invalida de {url}: {response_body[:400]}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"Resposta inesperada de {url}: {type(parsed).__name__}")
    return parsed


def _decode_json_object(response_body: str, *, url: str) -> dict[str, object]:
    """Decode and validate that a response body is a JSON object.

    Args:
        response_body: Raw response body text.
        url: Logical URL used in error context.

    Returns:
        Parsed JSON object.

    Raises:
        ProviderError: If JSON is invalid or not an object.
    """
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Resposta JSON invalida de {url}: {response_body[:400]}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"Resposta inesperada de {url}: {type(parsed).__name__}")
    return parsed


def _normalize_groq_language(language: str | None) -> str | None:
    """Normalize locale-like language tags into Groq-friendly short codes.

    Args:
        language: Optional language code (e.g. ``pt-BR`` or ``en_US``).

    Returns:
        Primary lowercase language subtag, or ``None`` when unavailable.
    """
    if not language:
        return None
    normalized = language.strip().replace("_", "-")
    if not normalized:
        return None
    return normalized.split("-", 1)[0].lower()


def _multipart_body(
    *,
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    file_content_type: str,
) -> tuple[bytes, str]:
    """Build a multipart/form-data body with fields and one file.

    Args:
        fields: Form fields.
        file_field: Name of the file field.
        file_name: File name sent in multipart disposition.
        file_bytes: Binary file content.
        file_content_type: MIME type for the file payload.

    Returns:
        Tuple with encoded body and generated boundary.
    """
    boundary = f"sentinel-{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        field_name = key.split("#", 1)[0]
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
                f"Content-Type: {file_content_type}\r\n\r\n"
            ).encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


@dataclass(slots=True)
class NoopTranscriber:
    """Fallback transcriber used when real transcription provider is unavailable."""

    def transcribe(self, media_path: str, language: str | None) -> TranscriptionResult:
        """Return a deterministic failure result for missing provider setup.

        Args:
            media_path: Ignored media path.
            language: Optional input language hint.

        Returns:
            Failed transcription result with explanatory error message.
        """
        return TranscriptionResult(
            transcript_text=None,
            language=language,
            confidence=None,
            duration_seconds=None,
            status="failed",
            error_message="Transcriber provider nao configurado.",
        )


@dataclass(slots=True)
class GroqTranscriber:
    """Groq Speech-to-Text client adapter."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: int
    response_format: str
    timestamp_granularities: list[str]

    @property
    def transcription_url(self) -> str:
        """Return transcription endpoint URL derived from configured base URL."""
        return f"{self.base_url.rstrip('/')}/audio/transcriptions"

    def transcribe(self, media_path: str, language: str | None) -> TranscriptionResult:
        """Transcribe an audio file through Groq Speech-to-Text.

        Args:
            media_path: Local path to an audio file.
            language: Optional language hint.

        Returns:
            Structured transcription result.
        """
        path = Path(media_path)
        if not path.exists():
            return TranscriptionResult(
                transcript_text=None,
                language=language,
                confidence=None,
                duration_seconds=None,
                status="failed",
                error_message=f"Arquivo de audio nao encontrado: {media_path}",
            )
        file_bytes = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        fields = {
            "model": self.model,
            "response_format": self.response_format,
        }
        resolved_language = _normalize_groq_language(language)
        if resolved_language:
            fields["language"] = resolved_language
        if self.response_format == "verbose_json" and self.timestamp_granularities:
            for index, granularity in enumerate(self.timestamp_granularities):
                fields[f"timestamp_granularities[]#{index}"] = granularity
        body, boundary = _multipart_body(
            fields=fields,
            file_field="file",
            file_name=path.name,
            file_bytes=file_bytes,
            file_content_type=content_type,
        )
        request = urllib.request.Request(
            self.transcription_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Sentinel/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            body = exc.read().decode("utf-8", errors="replace")
            return TranscriptionResult(
                transcript_text=None,
                language=language,
                confidence=None,
                duration_seconds=None,
                status="failed",
                error_message=f"Groq HTTP {exc.code}: {body[:400]}",
            )
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            return TranscriptionResult(
                transcript_text=None,
                language=language,
                confidence=None,
                duration_seconds=None,
                status="failed",
                error_message=f"Groq network error: {exc.reason}",
            )
        return _parse_groq_transcription_response(response_body, fallback_language=resolved_language or language)


@dataclass(slots=True)
class GeminiStructuredClient:
    """Gemini client that requests JSON constrained by schema."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: int

    @property
    def generate_content_url(self) -> str:
        """Return generateContent endpoint URL derived from configured base URL."""
        return f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"

    def classify_json(
        self,
        prompt: PromptBundle,
        response_schema: dict[str, object],
    ) -> tuple[dict[str, object], str]:
        """Generate structured classification JSON using Gemini.

        Args:
            prompt: Prompt bundle with system and user context.
            response_schema: JSON schema expected from the model.

        Returns:
            Tuple containing parsed JSON object and raw text output.

        Raises:
            ProviderError: If response extraction or JSON parsing fails.
        """
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{prompt.system_prompt.strip()}\n\n{prompt.user_prompt.strip()}"}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": response_schema,
                "temperature": 0,
            },
        }
        parsed = _http_json(
            url=self.generate_content_url,
            payload=payload,
            headers={"x-goog-api-key": self.api_key},
            timeout=self.timeout_seconds,
        )
        text = _extract_gemini_text(parsed)
        try:
            json_payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gemini retornou JSON invalido: {text[:400]}") from exc
        return json_payload, text


def _extract_gemini_text(payload: Mapping[str, object]) -> str:
    """Extract textual candidate output from Gemini ``generateContent`` response.

    Args:
        payload: Parsed provider response.

    Returns:
        Concatenated candidate text.

    Raises:
        ProviderError: If response does not contain expected candidate fields.
    """
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProviderError("Gemini retornou sem candidates")
    first = candidates[0]
    if not isinstance(first, dict):
        raise ProviderError("Gemini candidate invalido")
    content = first.get("content")
    if not isinstance(content, dict):
        raise ProviderError("Gemini candidate sem content")
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ProviderError("Gemini candidate sem parts")
    text_fragments = [str(part.get("text")) for part in parts if isinstance(part, dict) and "text" in part]
    text = "".join(text_fragments).strip()
    if not text:
        raise ProviderError("Gemini retornou sem texto")
    return text


def _parse_groq_transcription_payload(
    payload: Mapping[str, object],
    *,
    fallback_language: str | None,
) -> TranscriptionResult:
    """Convert Groq verbose JSON payload into ``TranscriptionResult``.

    Args:
        payload: Parsed transcription response payload.
        fallback_language: Language used when provider omits explicit language.

    Returns:
        Normalized transcription result.
    """
    segments = payload.get("segments") or []
    avg_logprob = None
    duration_seconds = None
    if isinstance(segments, list) and segments:
        duration_seconds = max(
            float(segment.get("end", 0.0) or 0.0)
            for segment in segments
            if isinstance(segment, dict)
        )
        avg_values = [
            float(segment.get("avg_logprob", 0.0) or 0.0)
            for segment in segments
            if isinstance(segment, dict) and "avg_logprob" in segment
        ]
        if avg_values:
            avg_logprob = sum(avg_values) / len(avg_values)
    confidence = None
    if avg_logprob is not None:
        confidence = max(0.0, min(1.0, 1.0 + (avg_logprob / 5.0)))
    transcript_text = str(payload.get("text") or "").strip() or None
    resolved_language = payload.get("language") or fallback_language
    return TranscriptionResult(
        transcript_text=transcript_text,
        language=str(resolved_language) if resolved_language else None,
        confidence=round(confidence, 4) if confidence is not None else None,
        duration_seconds=round(duration_seconds, 3) if duration_seconds is not None else None,
        status="completed" if transcript_text else "failed",
        error_message=None if transcript_text else "Groq response had no text field",
    )


def _parse_groq_transcription_response(response_body: str, *, fallback_language: str | None) -> TranscriptionResult:
    """Parse Groq transcription response supporting JSON and plaintext modes.

    Args:
        response_body: Raw provider response body.
        fallback_language: Language used when provider omits explicit language.

    Returns:
        Normalized transcription result.
    """
    stripped = response_body.strip()
    if not stripped:
        return TranscriptionResult(
            transcript_text=None,
            language=fallback_language,
            confidence=None,
            duration_seconds=None,
            status="failed",
            error_message="Groq returned empty response",
        )
    try:
        parsed = _decode_json_object(response_body, url="groq://audio/transcriptions")
    except ProviderError:
        return TranscriptionResult(
            transcript_text=stripped,
            language=fallback_language,
            confidence=None,
            duration_seconds=None,
            status="completed",
            error_message=None,
        )
    return _parse_groq_transcription_payload(parsed, fallback_language=fallback_language)


CLASSIFICATION_RESPONSE_SCHEMA = ClassificationResult.model_json_schema()


def build_transcriber(config: AppConfig) -> NoopTranscriber | GroqTranscriber:
    """Build the transcription adapter according to runtime configuration.

    Args:
        config: Application configuration.

    Returns:
        Configured transcriber instance.
    """
    if config.transcription.provider == "groq":
        api_key = os.getenv(config.transcription.api_key_env, "").strip()
        if not api_key:
            return NoopTranscriber()
        return GroqTranscriber(
            api_key=api_key,
            base_url=config.transcription.base_url,
            model=config.transcription.model,
            timeout_seconds=config.transcription.timeout_seconds,
            response_format=config.transcription.response_format,
            timestamp_granularities=config.transcription.timestamp_granularities,
        )
    return NoopTranscriber()


def build_gemini_client(config: AppConfig) -> GeminiStructuredClient | None:
    """Build Gemini structured client when provider and API key are available.

    Args:
        config: Application configuration.

    Returns:
        Configured Gemini client or ``None`` if unavailable.
    """
    if config.llm.provider != "gemini":
        return None
    api_key = os.getenv(config.llm.api_key_env, "").strip()
    if not api_key:
        return None
    return GeminiStructuredClient(
        api_key=api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        timeout_seconds=config.llm.timeout_seconds,
    )


def normalize_provider_classification(payload: dict[str, object]) -> ClassificationResult:
    """Normalize provider payload into a validated ``ClassificationResult``.

    Args:
        payload: Provider response payload.

    Returns:
        Validated classification model.
    """
    return ClassificationResult.model_validate(payload)
