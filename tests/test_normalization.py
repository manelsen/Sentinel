from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.normalization import contains_direct_address, contains_profanity, normalize_text, token_estimate


class NormalizationTests(unittest.TestCase):
    def test_normalization_preserves_aggressive_markers(self) -> None:
        text = "VOCE   esta  distorcendo tudo!!! 😡"
        normalized = normalize_text(text)
        self.assertIn("VOCE", normalized)
        self.assertIn("!!!", normalized)
        self.assertIn("[emoji_raiva]", normalized)

    def test_detects_profanity_and_direct_address(self) -> None:
        text = "Voce esta sendo um idiota"
        self.assertTrue(contains_profanity(text))
        self.assertTrue(contains_direct_address(text))
        self.assertGreater(token_estimate(text), 0)


if __name__ == "__main__":
    unittest.main()
