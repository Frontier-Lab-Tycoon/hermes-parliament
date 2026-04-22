"""Phase 1 acceptance criteria: SessionStore + persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parliament.sessions.index import GlobalIndex
from parliament.models import TurnRecord
from parliament.sessions.store import SessionStore


@pytest.fixture
def tmp_parliament_dir(tmp_path: Path) -> Path:
    return tmp_path / ".parliament"


@pytest.fixture
def store(tmp_parliament_dir: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_parliament_dir)


@pytest.fixture
def index(tmp_parliament_dir: Path) -> GlobalIndex:
    return GlobalIndex(db_path=tmp_parliament_dir / "index.db")


class TestT1SessionCreation:
    """T1-1: 세션 생성 후 디렉터리 확인."""

    def test_create_session_makes_directory_and_files(self, store: SessionStore) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {"max_turns": 10})
        session_dir = store._session_dir(sid)
        assert session_dir.exists()
        assert (session_dir / "history.jsonl").exists()
        assert (session_dir / "delivery.jsonl").exists()
        assert (session_dir / "checkpoint.json").exists()


class TestT1HistoryAppend:
    """T1-2: TurnRecord 3개 append 후 history.jsonl 읽기."""

    def test_append_turns_writes_ndjson_without_publish_metadata(self, store: SessionStore) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {})
        for i in range(3):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile="p1",
                role="user",
                content=f"hello {i}",
            )
            store.append_turn(sid, turn)

        lines = (store._history_path(sid)).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        for line in lines:
            record = json.loads(line)
            assert record["type"] == "turn_content"
            assert "turn_uuid" in record
            assert "publish_state" not in record
            assert "published_message_id" not in record


class TestT1DeliveryBeforeCheckpoint:
    """T1-3: delivery.jsonl append 완료, checkpoint overwrite 전 crash 시뮬레이션."""

    def test_delivery_replay_recovers_without_checkpoint_update(self, store: SessionStore) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {})
        turn = TurnRecord(turn_uuid="t-1", seq=1, profile="p1", role="user", content="hi")
        store.append_turn(sid, turn)

        # Simulate: delivery append happens, checkpoint overwrite crashes
        store._append_delivery_event(sid, "t-1", "sent", {"message_id": "msg-1"})
        # Intentionally do NOT call _overwrite_checkpoint

        # Replay should still see the sent state
        assert store.get_turn_publish_state(sid, "t-1") == "sent"
        assert len(store.get_unpublished_turns(sid)) == 0


class TestT1GlobalIndex:
    """T1-4: index.db에 세션 등록 후 list_sessions."""

    def test_register_and_list_sessions(self, store: SessionStore, index: GlobalIndex) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {})
        # Read created_at from checkpoint for consistency
        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        index.register_session(sid, cp["status"], cp["config"]["topic"], cp["created_at"])

        sessions = index.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["status"] == "running"
        assert sessions[0]["topic"] == "topic"

        index.update_status(sid, "completed")
        sessions = index.list_sessions()
        assert sessions[0]["status"] == "completed"


class TestT1ConcurrentSessions:
    """T1-5: 동시에 2개 세션 생성 — 데이터가 섞이지 않음."""

    def test_two_sessions_are_independent(self, store: SessionStore) -> None:
        sid_a = store.create_session("topic-a", ["p1"], {})
        sid_b = store.create_session("topic-b", ["p2"], {})

        turn_a = TurnRecord(turn_uuid="t-a", seq=0, profile="p1", role="user", content="a")
        turn_b = TurnRecord(turn_uuid="t-b", seq=0, profile="p2", role="user", content="b")

        store.append_turn(sid_a, turn_a)
        store.append_turn(sid_b, turn_b)

        history_a = store._history_path(sid_a).read_text(encoding="utf-8").strip().split("\n")
        history_b = store._history_path(sid_b).read_text(encoding="utf-8").strip().split("\n")

        assert len(history_a) == 1
        assert len(history_b) == 1
        assert json.loads(history_a[0])["content"] == "a"
        assert json.loads(history_b[0])["content"] == "b"


class TestT1PublishStateTransitions:
    """T1-6, T1-7, T1-8: publish 상태 전이 및 unpublished 목록."""

    def test_pending_to_sent_unpublished_empty(self, store: SessionStore) -> None:
        """T1-6."""
        sid = store.create_session("topic", ["p1", "p2"], {})
        turn = TurnRecord(turn_uuid="t-1", seq=1, profile="p1", role="user", content="hello")
        store.append_turn(sid, turn)

        store.mark_turn_publish_pending(sid, "t-1")
        assert store.get_turn_publish_state(sid, "t-1") == "pending"
        assert len(store.get_unpublished_turns(sid)) == 1

        nonce = store.generate_nonce(sid, "t-1", "participant_bot")
        store.mark_turn_publish_in_flight(sid, "t-1", nonce, "participant_bot", "participant_bot")
        assert store.get_turn_publish_state(sid, "t-1") == "in_flight"

        store.mark_turn_published(
            sid, "t-1", "msg-123", "participant_bot",
            "2026-04-21T12:00:00Z", state="sent", attempt_publisher="participant_bot",
        )
        assert store.get_turn_publish_state(sid, "t-1") == "sent"
        assert len(store.get_unpublished_turns(sid)) == 0

    def test_pending_to_failed_retryable_in_unpublished(self, store: SessionStore) -> None:
        """T1-7."""
        sid = store.create_session("topic", ["p1", "p2"], {})
        turn = TurnRecord(turn_uuid="t-1", seq=1, profile="p1", role="user", content="hello")
        store.append_turn(sid, turn)

        store.mark_turn_publish_pending(sid, "t-1")
        store.mark_turn_publish_failed(sid, "t-1", "network error", retryable=True, attempt_publisher="bot")
        assert store.get_turn_publish_state(sid, "t-1") == "failed_retryable"
        assert len(store.get_unpublished_turns(sid)) == 1

    def test_published_metadata_recorded(self, store: SessionStore) -> None:
        """T1-8."""
        sid = store.create_session("topic", ["p1", "p2"], {})
        turn = TurnRecord(turn_uuid="t-1", seq=1, profile="p1", role="user", content="hello")
        store.append_turn(sid, turn)

        store.mark_turn_publish_pending(sid, "t-1")
        store.mark_turn_published(
            sid, "t-1", "msg-456", "participant_bot",
            "2026-04-21T12:00:00Z", state="sent", attempt_publisher="participant_bot",
        )

        lines = store._delivery_path(sid).read_text(encoding="utf-8").strip().split("\n")
        last_event = json.loads(lines[-1])
        assert last_event["metadata"]["published_by"] == "participant_bot"
        assert last_event["metadata"]["message_id"] == "msg-456"


class TestT1CheckpointCrashRecovery:
    """T1-9: checkpoint overwrite 중 crash 시뮬레이션."""

    def test_stale_checkpoint_is_overridden_by_delivery_replay(self, store: SessionStore) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {})
        turn = TurnRecord(turn_uuid="t-1", seq=1, profile="p1", role="user", content="hello")
        store.append_turn(sid, turn)

        # Normal publish flow
        store.mark_turn_publish_pending(sid, "t-1")
        nonce = store.generate_nonce(sid, "t-1", "participant_bot")
        store.mark_turn_publish_in_flight(sid, "t-1", nonce, "participant_bot", "participant_bot")
        store.mark_turn_published(
            sid, "t-1", "msg-123", "participant_bot",
            "2026-04-21T12:00:00Z", state="sent", attempt_publisher="participant_bot",
        )

        # Simulate crash: revert checkpoint to stale state (before publish)
        store._overwrite_checkpoint(
            sid,
            last_safe_published_turn_uuid=None,
            pending_turn_uuid="t-1",
        )

        # Delivery replay should still report sent, overriding stale checkpoint
        assert store.get_turn_publish_state(sid, "t-1") == "sent"
        assert len(store.get_unpublished_turns(sid)) == 0

        # load_session should also return consistent data (turns from history)
        session = store.load_session(sid)
        assert len(session.turns) == 1
        assert session.turns[0].turn_uuid == "t-1"
