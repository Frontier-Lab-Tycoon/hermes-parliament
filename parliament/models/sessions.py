"""Session persistence models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from parliament.models.common import JSONObject, utc_timestamp
from parliament.models.debate import TurnRecord


class PublishState(StrEnum):
    """Publish lifecycle states for a turn."""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    FALLBACK_PENDING = "fallback_pending"
    SENT = "sent"
    SENT_VIA_FALLBACK = "sent_via_fallback"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class DeliveryEventType(StrEnum):
    """Types of delivery events persisted to delivery.jsonl."""

    PUBLISH_STATE_CHANGED = "publish_state_changed"


class SessionStatus(StrEnum):
    """Session lifecycle states."""

    RUNNING = "running"
    COMPLETED = "completed"


class DeliveryEvent(BaseModel):
    """Append-only publish state change event."""

    seq: int
    turn_uuid: str
    event_type: DeliveryEventType = DeliveryEventType.PUBLISH_STATE_CHANGED
    new_state: PublishState
    metadata: JSONObject = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_timestamp)


class Checkpoint(BaseModel):
    """Crash-recovery checkpoint (overwritten)."""

    session_id: str
    status: SessionStatus = SessionStatus.RUNNING
    config: JSONObject = Field(default_factory=dict)
    created_at: str = ""
    next_turn_index: int = 0
    next_speaker: str | None = None
    last_safe_published_turn_uuid: str | None = None
    pending_turn_uuid: str | None = None


class Session(BaseModel):
    """Joined session view (history + delivery replay)."""

    session_id: str
    status: SessionStatus
    config: JSONObject
    turns: list[TurnRecord]
    created_at: str
