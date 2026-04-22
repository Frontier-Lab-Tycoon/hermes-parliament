"""Common JSON and record primitives."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import JsonValue

JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JSONValue]


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp suitable for persisted JSON records."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class HistoryRecordType(StrEnum):
    """Types of records persisted to history.jsonl."""

    TURN_CONTENT = "turn_content"
    SUMMARY = "summary"
    PROMPT_SNAPSHOT = "prompt_snapshot"
