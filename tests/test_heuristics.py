from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.config import HeuristicConfig
from sentinel.heuristics import compute_message_features, compute_window_features, heuristic_severity


class HeuristicTests(unittest.TestCase):
    def test_hostile_window_scores_high(self) -> None:
        rows = []
        for index, author in enumerate(("a", "b", "a", "b"), start=1):
            features = compute_message_features(
                "VOCE esta distorcendo tudo de novo!!!",
                reply_to_message_id=f"m{index - 1}" if index > 1 else None,
                contains_profanity=False,
                contains_direct_address=True,
            )
            rows.append(
                {
                    "user_id": author,
                    "reply_to_message_id": f"m{index - 1}" if index > 1 else None,
                    "sort_ts": f"2026-04-18T16:00:0{index}Z",
                    "direct_attack_score": features.direct_attack_score,
                    "profanity_score": features.profanity_score,
                    "negativity_score": features.negativity_score,
                    "caps_ratio": features.caps_ratio,
                    "exclamation_count": features.exclamation_count,
                    "question_count": features.question_count,
                    "message_type": "text",
                    "duration_seconds": None,
                }
            )
        window = compute_window_features(rows, HeuristicConfig())
        self.assertGreater(window.heuristic_risk_score, 0.55)
        self.assertEqual(heuristic_severity(window.heuristic_risk_score), "tensao")


if __name__ == "__main__":
    unittest.main()
