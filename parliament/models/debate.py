"""Debate turn and synthesis models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from pydantic import BaseModel

from parliament.models.common import (
    HistoryRecordType,
    JSONObject,
    JSONValue,
    utc_timestamp,
)


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


class TurnRecord(BaseModel):
    """Immutable turn content record."""

    turn_uuid: str
    seq: int
    profile: str
    role: TurnRole
    content: str
    structured: JSONObject | None = None
    consensus_signal: ConsensusSignal | None = None


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
