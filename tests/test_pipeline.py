from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.config import AppConfig
from sentinel.models import DailyReportPayload, IngestResult
from sentinel.service import SentinelService


class PipelineTests(unittest.TestCase):
    def test_pipeline_creates_incident_and_alert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "sentinel.db")
            config = AppConfig(db_path=db_path)
            config.heuristics.llm_threshold = 0.40
            service = SentinelService(config)
            try:
                base = {
                    "platform": "demo",
                    "external_group_id": "grupo-1",
                    "group_name": "Grupo Demo",
                    "message_type": "text",
                }
                events = [
                    {
                        **base,
                        "external_user_id": "alice",
                        "user_name": "Alice",
                        "raw_text": "Voce esta distorcendo tudo de novo!!!",
                        "sent_at": "2026-04-18T16:00:01Z",
                        "received_at": "2026-04-18T16:00:01Z",
                    },
                    {
                        **base,
                        "external_user_id": "bruno",
                        "user_name": "Bruno",
                        "raw_text": "Nao, voce que nunca le antes de falar.",
                        "reply_to_message_id": None,
                        "sent_at": "2026-04-18T16:00:10Z",
                        "received_at": "2026-04-18T16:00:10Z",
                    },
                    {
                        **base,
                        "external_user_id": "alice",
                        "user_name": "Alice",
                        "raw_text": "Para de mentir, isso esta ridiculo!!!",
                        "reply_to_message_id": None,
                        "sent_at": "2026-04-18T16:00:20Z",
                        "received_at": "2026-04-18T16:00:20Z",
                    },
                ]
                last_result: IngestResult | None = None
                all_alert_ids = []
                for event in events:
                    last_result = service.ingest_message(event)
                    all_alert_ids.extend(last_result["alert_ids"])
                assert last_result is not None
                self.assertIn(last_result["severity"], {"tensao", "incendio"})
                self.assertTrue(last_result["assessment_id"])
                alert_total = service.connection.execute("SELECT COUNT(*) AS total FROM alerts").fetchone()["total"]
                self.assertGreaterEqual(len(all_alert_ids), 1)
                self.assertGreaterEqual(alert_total, 1)

                group_id = last_result["group_id"]
                markdown, payload = service.build_daily_report(group_id, "2026-04-18")
                payload = cast(DailyReportPayload, payload)
                self.assertIn("Incidentes potenciais", markdown)
                self.assertGreaterEqual(len(payload["critical_incidents"]), 1)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
