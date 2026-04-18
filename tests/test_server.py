from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.config import AppConfig
from sentinel.server import SentinelHTTPServer


class ServerTests(unittest.TestCase):
    def test_health_and_ingest_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AppConfig(db_path=str(Path(temp_dir) / "sentinel.db"))
            config.server.host = "127.0.0.1"
            config.server.port = 0
            server = SentinelHTTPServer((config.server.host, config.server.port), config)
            server_address = server.server_address
            host = str(server_address[0])
            port = int(server_address[1])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = HTTPConnection(host, port, timeout=5)
                conn.request("GET", "/healthz")
                response = conn.getresponse()
                health = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(health["status"], "ok")
                conn.close()

                conn = HTTPConnection(host, port, timeout=5)
                payload = {
                    "platform": "demo",
                    "external_group_id": "grupo-1",
                    "group_name": "Grupo Demo",
                    "external_user_id": "alice",
                    "user_name": "Alice",
                    "message_type": "text",
                    "raw_text": "Voce esta distorcendo tudo de novo!!!",
                    "sent_at": "2026-04-18T16:00:01Z",
                    "received_at": "2026-04-18T16:00:01Z",
                }
                conn.request(
                    "POST",
                    "/ingest",
                    body=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(body["severity"], "normal")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
