"""Pydantic data models for Hermes Parliament."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PublishState(str, Enum):
    """Publish lifecycle states for a turn."""

    pending = "pending"
    in_flight = "in_flight"
    fallback_pending = "fallback_pending"
    sent = "sent"
    sent_via_fallback = "sent_via_fallback"
    failed_retryable = "failed_retryable"
    failed_terminal = "failed_terminal"


class TurnRecord(BaseModel):
    """Immutable turn content record."""

    turn_uuid: str
    seq: int
    profile: str
    role: str
    content: str
    structured: dict[str, Any] | None = None
    consensus_signal: str | None = None


class DeliveryEvent(BaseModel):
    """Append-only publish state change event."""

    seq: int
    turn_uuid: str
    event_type: str = "publish_state_changed"
    new_state: PublishState
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class Checkpoint(BaseModel):
    """Crash-recovery checkpoint (overwritten)."""

    session_id: str
    next_turn_index: int = 0
    next_speaker: str | None = None
    last_safe_published_turn_uuid: str | None = None
    pending_turn_uuid: str | None = None


class Session(BaseModel):
    """Joined session view (history + delivery replay)."""

    session_id: str
    status: str
    config: dict[str, Any]
    turns: list[TurnRecord]
    created_at: str


class BackendResult(BaseModel):
    """Result from invoking an agent backend."""

    text: str
    code: int
    error: str | None = None
