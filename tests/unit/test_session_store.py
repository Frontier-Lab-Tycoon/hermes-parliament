"""Session persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from parliament.models import PublishState, TurnRecord
from parliament.sessions.store import SessionStore


class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> SessionStore:
        return SessionStore(base_dir=tmp_path / ".parliament")

    @pytest.fixture
    def session_id(self, store: SessionStore) -> str:
        return store.create_session("topic", ["p1", "p2"], {})

    @pytest.fixture
    def turn(self) -> TurnRecord:
        return TurnRecord(
            turn_uuid="t-1",
            seq=1,
            profile="p1",
            role="debater",
            content="hello",
        )

    async def test_session_round_trips_turn_history(
        self, store: SessionStore, session_id: str, turn: TurnRecord
    ) -> None:
        store.append_turn(session_id, turn)

        session = store.load_session(session_id)

        assert session.session_id == session_id
        assert session.turns[0].content == "hello"

    async def test_failed_retryable_turn_remains_unpublished(
        self, store: SessionStore, session_id: str, turn: TurnRecord
    ) -> None:
        store.append_turn(session_id, turn)

        store.mark_turn_publish_failed(
            session_id, "t-1", "network error", retryable=True, attempt_publisher="bot"
        )

        assert store.get_turn_publish_state(session_id, "t-1") == PublishState.FAILED_RETRYABLE
        assert store.get_unpublished_turns(session_id) == [turn]

    async def test_delivery_log_overrides_stale_checkpoint(
        self, store: SessionStore, session_id: str, turn: TurnRecord
    ) -> None:
        store.append_turn(session_id, turn)

        store.mark_turn_publish_pending(session_id, "t-1")
        store.mark_turn_published(
            session_id,
            "t-1",
            "msg-123",
            "participant_bot",
            "2026-04-21T12:00:00Z",
            state=PublishState.SENT,
            attempt_publisher="participant_bot",
        )
        store._overwrite_checkpoint(
            session_id,
            last_safe_published_turn_uuid=None,
            pending_turn_uuid="t-1",
        )

        assert store.get_turn_publish_state(session_id, "t-1") == PublishState.SENT
        assert store.get_unpublished_turns(session_id) == []
