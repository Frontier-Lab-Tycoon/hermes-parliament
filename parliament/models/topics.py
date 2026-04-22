"""Topic configuration enums."""

from __future__ import annotations

from enum import StrEnum


class ProtocolType(StrEnum):
    """Supported debate protocol types."""

    DEBATE = "debate"


class ProtocolOrdering(StrEnum):
    """Supported speaker ordering strategies."""

    ALTERNATING = "alternating"
