"""Global SQLite index for session metadata."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    return Path.home() / ".parliament" / "index.db"


class GlobalIndex:
    """SQLite-backed global session index."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def register_session(
        self,
        session_id: str,
        status: str,
        topic: str,
        created_at: str,
    ) -> None:
        """Register a new session in the global index."""
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, status, topic, created_at) VALUES (?, ?, ?, ?)",
                (session_id, status, topic, created_at),
            )
            conn.commit()

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all registered sessions."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT session_id, status, topic, created_at FROM sessions ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def update_status(self, session_id: str, status: str) -> None:
        """Update the status of a session."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE sessions SET status = ? WHERE session_id = ?",
                (status, session_id),
            )
            conn.commit()
