"""Environment file helpers for local and containerized execution."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_value(raw_value: str) -> str:
    """Parse a dotenv value preserving simple quoted and unquoted conventions.

    Args:
        raw_value: Raw value part after ``KEY=``.

    Returns:
        Parsed value ready to be injected into ``os.environ``.
    """
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and value[0] == value[-1]:
        return value[1:-1]
    inline_comment_index = value.find(" #")
    if inline_comment_index >= 0:
        return value[:inline_comment_index].strip()
    return value


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> int:
    """Load environment variables from a dotenv file when it exists.

    Missing files are ignored, which keeps local development ergonomic when
    environment variables are already exported by other means.

    Args:
        path: Dotenv file path.
        override: Whether values from file should override existing variables.

    Returns:
        Number of variables inserted or updated.

    Raises:
        ValueError: If a dotenv line is malformed or contains invalid key name.
        OSError: If the file exists but cannot be read.
    """
    env_path = Path(path)
    if not env_path.exists():
        return 0

    loaded = 0
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            raise ValueError(f"Linha invalida em {env_path}:{line_number}: esperado KEY=VALUE")
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_PATTERN.match(key):
            raise ValueError(f"Chave invalida em {env_path}:{line_number}: {key!r}")
        if not override and key in os.environ:
            continue
        os.environ[key] = _parse_env_value(raw_value)
        loaded += 1
    return loaded
