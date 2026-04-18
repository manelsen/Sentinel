from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_applies_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "sentinel.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[app]",
                        'db_path = "from-file.db"',
                        "",
                        "[server]",
                        'host = "127.0.0.1"',
                        "port = 8080",
                        'auth_token = ""',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "SENTINEL_DB_PATH": "/data/sentinel.db",
                    "SENTINEL_SERVER_HOST": "0.0.0.0",
                    "SENTINEL_SERVER_PORT": "9090",
                    "SENTINEL_AUTH_TOKEN": "token-from-env",
                },
                clear=False,
            ):
                config = load_config(config_path)
        self.assertEqual(config.db_path, "/data/sentinel.db")
        self.assertEqual(config.server.host, "0.0.0.0")
        self.assertEqual(config.server.port, 9090)
        self.assertEqual(config.server.auth_token, "token-from-env")

    def test_load_config_rejects_invalid_env_port(self) -> None:
        with patch.dict(os.environ, {"SENTINEL_SERVER_PORT": "not-a-number"}, clear=False):
            with self.assertRaises(ValueError):
                load_config()


if __name__ == "__main__":
    unittest.main()
