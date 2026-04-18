from __future__ import annotations

import json
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from pydantic import ValidationError

from .config import AppConfig
from .models import DailyReportRequest, FeedbackRequest, IncomingMessage
from .service import SentinelService


class SentinelHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: AppConfig):
        self.config = config
        self.service = SentinelService(config)
        try:
            super().__init__(server_address, SentinelRequestHandler)
        except Exception:
            self.service.close()
            raise

    def server_close(self) -> None:
        try:
            if hasattr(self, "service"):
                self.service.close()
        finally:
            super().server_close()


class SentinelRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        server = cast(SentinelHTTPServer, self.server)
        if not self._authorize():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        try:
            if self.path == "/ingest":
                result = server.service.ingest_message(IncomingMessage.model_validate(payload))
                self._send_json(HTTPStatus.OK, result)
                return
            if self.path == "/feedback":
                request = FeedbackRequest.model_validate(payload)
                feedback_id = server.service.record_feedback(
                    request.incident_id,
                    request.feedback_type.value,
                    note=request.note,
                    reviewer_id=request.reviewer_id,
                )
                self._send_json(HTTPStatus.OK, {"feedback_id": feedback_id})
                return
            if self.path == "/report-daily":
                request = DailyReportRequest.model_validate(payload)
                markdown, report_payload = server.service.build_daily_report(
                    request.group_id,
                    request.date,
                )
                self._send_json(HTTPStatus.OK, {"markdown": markdown, "payload": report_payload})
                return
        except (ValidationError, KeyError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - defensive server path
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def log_message(self, format: str, *args) -> None:
        return

    def _authorize(self) -> bool:
        server = cast(SentinelHTTPServer, self.server)
        expected = server.config.server.auth_token.strip()
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {expected}"

    def _read_json(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length ausente")
        length = int(raw_length)
        body = self.rfile.read(length).decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON invalido") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Payload deve ser objeto JSON")
        return parsed

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(config: AppConfig) -> None:
    server = SentinelHTTPServer((config.server.host, config.server.port), config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
