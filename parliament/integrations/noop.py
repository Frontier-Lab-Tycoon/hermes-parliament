"""No-op publisher for engine unit tests."""

from __future__ import annotations

from parliament.integrations.base import Publisher
from parliament.models import SynthesisResult, TurnRecord


class NoOpPublisher(Publisher):
    """Publisher that does nothing — useful for unit tests."""

    async def send_turn(self, session_id: str, turn_record: TurnRecord) -> str | None:
        """Return ``None`` immediately without doing anything."""
        return None

    async def send_final(
        self, coordinator_token: str, synthesis_result: SynthesisResult
    ) -> str | None:
        """Return ``None`` immediately without doing anything."""
        return None
