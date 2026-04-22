"""Pydantic data models and typed value objects for Hermes Parliament."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias, cast

from pydantic import BaseModel, Field, JsonValue

JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JSONValue]


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp suitable for persisted JSON records."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


class HistoryRecordType(StrEnum):
    """Types of records persisted to history.jsonl."""

    TURN_CONTENT = "turn_content"
    SUMMARY = "summary"
    PROMPT_SNAPSHOT = "prompt_snapshot"


class SessionStatus(StrEnum):
    """Session lifecycle states."""

    RUNNING = "running"
    COMPLETED = "completed"


class TurnRole(StrEnum):
    """Roles that can appear in a turn record."""

    USER = "user"
    DEBATER = "debater"


class ConsensusSignal(StrEnum):
    """Recognized consensus signals emitted by agents."""

    AGREE = "agree"
    DISAGREE = "disagree"

    @classmethod
    def parse(cls, value: object) -> ConsensusSignal | None:
        """Return a known consensus signal, or ``None`` for unknown agent text."""
        if not isinstance(value, str):
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class ProtocolType(StrEnum):
    """Supported debate protocol types."""

    DEBATE = "debate"


class ProtocolOrdering(StrEnum):
    """Supported speaker ordering strategies."""

    ALTERNATING = "alternating"


class DiscordPublishMode(StrEnum):
    """Supported Discord publishing modes."""

    PER_TURN = "per_turn"


class TurnRecord(BaseModel):
    """Immutable turn content record."""

    turn_uuid: str
    seq: int
    profile: str
    role: TurnRole
    content: str
    structured: JSONObject | None = None
    consensus_signal: ConsensusSignal | None = None


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


@dataclass(frozen=True)
class BackendResult:
    """Result from invoking an agent backend."""

    text: str
    code: int
    error: str | None = None


@dataclass(frozen=True)
class SynthesisResult:
    """Result of the synthesis step."""

    decision: str
    confidence: float
    reasoning: str
    consensus_reached: bool
    disagreeing_profiles: list[str] | None = None
    structured: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        structured: JSONObject = {
            "decision": self.decision,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "consensus_reached": self.consensus_reached,
        }
        if self.disagreeing_profiles is not None:
            structured["disagreeing_profiles"] = cast(JSONValue, self.disagreeing_profiles)
        object.__setattr__(self, "structured", structured)


@dataclass(frozen=True)
class SummaryEvent:
    """Summary record persisted to history.jsonl."""

    turn_uuid: str
    seq: int
    content: str
    timestamp: str = field(default_factory=utc_timestamp)
    type: HistoryRecordType = field(init=False, default=HistoryRecordType.SUMMARY)

    def to_record(self) -> JSONObject:
        return {
            "type": self.type,
            "turn_uuid": self.turn_uuid,
            "seq": self.seq,
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class PromptSnapshotEvent:
    """Prompt-window snapshot record persisted to history.jsonl."""

    history_window: tuple[int, int]
    excluded_turn_uuids: list[str]
    timestamp: str = field(default_factory=utc_timestamp)
    type: HistoryRecordType = field(init=False, default=HistoryRecordType.PROMPT_SNAPSHOT)

    def to_record(self) -> JSONObject:
        return {
            "type": self.type,
            "history_window": cast(JSONValue, [self.history_window[0], self.history_window[1]]),
            "excluded_turn_uuids": cast(JSONValue, self.excluded_turn_uuids),
            "timestamp": self.timestamp,
        }
