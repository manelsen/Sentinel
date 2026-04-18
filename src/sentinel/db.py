"""SQLite connection and schema initialization helpers."""

from __future__ import annotations

import sqlite3

from .schema import SCHEMA_SQL


def connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection configured for Sentinel usage.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Connection with ``Row`` factory and foreign keys enabled.
    """
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    """Apply the Sentinel schema to the target database.

    Args:
        connection: Open SQLite connection.
    """
    connection.executescript(SCHEMA_SQL)
    connection.commit()
