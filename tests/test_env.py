from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.env import load_dotenv


class EnvTests(unittest.TestCase):
    def test_loads_dotenv_without_overriding_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "GROQ_API_KEY=from-dotenv",
                        "export GEMINI_API_KEY='gemini-secret'",
                        "SENTINEL_AUTH_TOKEN=abc123 # inline comment",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GROQ_API_KEY": "preexisting"}, clear=False):
                loaded = load_dotenv(env_path)
                self.assertEqual(loaded, 2)
                self.assertEqual(os.environ["GROQ_API_KEY"], "preexisting")
                self.assertEqual(os.environ["GEMINI_API_KEY"], "gemini-secret")
                self.assertEqual(os.environ["SENTINEL_AUTH_TOKEN"], "abc123")

    def test_loads_missing_file_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / ".env"
            loaded = load_dotenv(missing_path)
            self.assertEqual(loaded, 0)

    def test_raises_for_invalid_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("INVALID-LINE\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_dotenv(env_path)


if __name__ == "__main__":
    unittest.main()
