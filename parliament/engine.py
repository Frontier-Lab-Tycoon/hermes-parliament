"""Debate engine stub for Phase 6."""

from __future__ import annotations

from parliament.session import SessionStore


class DebateEngine:
    """Stub debate engine."""

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()

    async def run(self, session_id: str) -> None:
        """Run the debate loop for the given session."""
        pass
