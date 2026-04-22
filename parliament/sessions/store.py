"""File-based session store with append-only event logs and checkpointing."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from parliament.models import Checkpoint, DeliveryEvent, PublishState, Session, TurnRecord


def _default_parliament_dir() -> Path:
    return Path.home() / ".parliament"


def _safe_jsonl_replay(path: Path) -> list[dict[str, Any]]:
    """Replay a JSONL file, skipping the last line if it's partial/invalid JSON."""
    lines: list[str] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()

    records: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                # Last line is partial/invalid — skip with a warning
                continue
            raise
    return records


class SessionStore:
    """Persistent session store backed by append-only JSONL files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _default_parliament_dir()
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _history_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "history.jsonl"

    def _delivery_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "delivery.jsonl"

    def _checkpoint_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "checkpoint.json"

    def create_session(
        self,
        topic: str,
        participants: list[str],
        config: dict[str, Any],
    ) -> str:
        """Create a new session directory and return its ID."""
        session_id = str(uuid.uuid4())
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Initialize empty append-only files
        self._history_path(session_id).write_text("", encoding="utf-8")
        self._delivery_path(session_id).write_text("", encoding="utf-8")

        from datetime import datetime, timezone

        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        full_config = {**config, "topic": topic, "participants": participants}

        checkpoint = {
            "session_id": session_id,
            "status": "running",
            "config": full_config,
            "created_at": created_at,
            "next_turn_index": 0,
            "next_speaker": None,
            "last_safe_published_turn_uuid": None,
            "pending_turn_uuid": None,
        }
        self._checkpoint_path(session_id).write_text(
            json.dumps(checkpoint, ensure_ascii=False),
            encoding="utf-8",
        )

        return session_id

    def append_turn(self, session_id: str, turn: TurnRecord) -> None:
        """Append a turn to history.jsonl."""
        path = self._history_path(session_id)
        record = {
            "type": "turn_content",
            **turn.model_dump(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_raw(self, session_id: str, record: dict[str, Any]) -> None:
        """Append a raw JSON record to history.jsonl."""
        path = self._history_path(session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def generate_nonce(self, session_id: str, turn_uuid: str, publisher_identity: str) -> str:
        """Deterministic 25-character nonce hash."""
        raw = f"{session_id}:{turn_uuid}:{publisher_identity}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:25]

    def _append_delivery_event(
        self,
        session_id: str,
        turn_uuid: str,
        new_state: PublishState,
        metadata: dict[str, Any],
    ) -> None:
        """Append a delivery event to delivery.jsonl."""
        path = self._delivery_path(session_id)
        seq = 1
        if path.exists():
            records = _safe_jsonl_replay(path)
            if records:
                seq = records[-1]["seq"] + 1

        event = DeliveryEvent(
            seq=seq,
            turn_uuid=turn_uuid,
            new_state=new_state,
            metadata=metadata,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def _overwrite_checkpoint(self, session_id: str, **updates: Any) -> None:
        """Read checkpoint, apply updates, and overwrite."""
        cp_path = self._checkpoint_path(session_id)
        checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))
        checkpoint.update(updates)
        cp_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")

    def mark_turn_publish_pending(self, session_id: str, turn_uuid: str) -> None:
        """Mark turn as pending publish."""
        self._append_delivery_event(
            session_id,
            turn_uuid,
            PublishState.pending,
            {},
        )

    def mark_turn_publish_in_flight(
        self,
        session_id: str,
        turn_uuid: str,
        nonce: str,
        intended_publisher: str,
        attempt_publisher: str,
    ) -> None:
        """Mark turn as in-flight and update checkpoint."""
        self._append_delivery_event(
            session_id,
            turn_uuid,
            PublishState.in_flight,
            {
                "nonce": nonce,
                "intended_publisher": intended_publisher,
                "attempt_publisher": attempt_publisher,
            },
        )
        self._overwrite_checkpoint(session_id, pending_turn_uuid=turn_uuid)

    def mark_turn_publish_fallback_pending(
        self,
        session_id: str,
        turn_uuid: str,
        error: str,
        attempt_publisher: str,
    ) -> None:
        """Mark turn as fallback pending and update checkpoint."""
        self._append_delivery_event(
            session_id,
            turn_uuid,
            PublishState.fallback_pending,
            {
                "error": error,
                "attempt_publisher": attempt_publisher,
            },
        )
        self._overwrite_checkpoint(session_id, pending_turn_uuid=None)

    def mark_turn_published(
        self,
        session_id: str,
        turn_uuid: str,
        message_id: str,
        published_by: str,
        published_at: str,
        state: str,
        attempt_publisher: str,
    ) -> None:
        """Mark turn as published and update checkpoint."""
        self._append_delivery_event(
            session_id,
            turn_uuid,
            PublishState(state),
            {
                "message_id": message_id,
                "published_by": published_by,
                "published_at": published_at,
                "attempt_publisher": attempt_publisher,
            },
        )
        self._overwrite_checkpoint(
            session_id,
            last_safe_published_turn_uuid=turn_uuid,
            pending_turn_uuid=None,
        )

    def mark_turn_publish_failed(
        self,
        session_id: str,
        turn_uuid: str,
        error: str,
        retryable: bool,
        attempt_publisher: str,
    ) -> None:
        """Mark turn as failed and update checkpoint."""
        new_state = PublishState.failed_retryable if retryable else PublishState.failed_terminal
        self._append_delivery_event(
            session_id,
            turn_uuid,
            new_state,
            {
                "error": error,
                "attempt_publisher": attempt_publisher,
            },
        )
        self._overwrite_checkpoint(session_id, pending_turn_uuid=None)

    def _replay_delivery(self, session_id: str) -> dict[str, PublishState]:
        """Replay delivery.jsonl and return the latest state per turn_uuid."""
        path = self._delivery_path(session_id)
        records = _safe_jsonl_replay(path)
        states: dict[str, PublishState] = {}
        for rec in records:
            states[rec["turn_uuid"]] = PublishState(rec["new_state"])
        return states

    def get_turn_publish_state(self, session_id: str, turn_uuid: str) -> str | None:
        """Return the latest publish state for a turn."""
        states = self._replay_delivery(session_id)
        state = states.get(turn_uuid)
        return state.value if state else None

    def get_turn_nonce(
        self,
        session_id: str,
        turn_uuid: str,
        publisher_identity: str,
    ) -> str:
        """Return the deterministic nonce for a turn."""
        return self.generate_nonce(session_id, turn_uuid, publisher_identity)

    def get_unpublished_turns(self, session_id: str) -> list[TurnRecord]:
        """Return turns whose latest publish state is not sent/sent_via_fallback."""
        history_path = self._history_path(session_id)
        records = _safe_jsonl_replay(history_path)
        turns = [TurnRecord(**rec) for rec in records if rec.get("type") == "turn_content"]

        states = self._replay_delivery(session_id)
        published_states = {PublishState.sent, PublishState.sent_via_fallback}
        return [t for t in turns if states.get(t.turn_uuid) not in published_states]

    def load_session(self, session_id: str) -> Session:
        """Replay both jsonl files and join into a Session object."""
        cp_path = self._checkpoint_path(session_id)
        checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))

        history_path = self._history_path(session_id)
        records = _safe_jsonl_replay(history_path)
        turns = [TurnRecord(**rec) for rec in records if rec.get("type") == "turn_content"]

        return Session(
            session_id=checkpoint["session_id"],
            status=checkpoint.get("status", "running"),
            config=checkpoint.get("config", {}),
            turns=turns,
            created_at=checkpoint.get("created_at", ""),
        )
