"""Discord integration enums."""

from __future__ import annotations

from enum import StrEnum


class DiscordPublishMode(StrEnum):
    """Supported Discord publishing modes."""

    PER_TURN = "per_turn"
