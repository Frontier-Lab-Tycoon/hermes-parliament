"""Abstract base class for external publisher integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from parliament.models import SynthesisResult, TurnRecord


class Publisher(ABC):
    """Abstract publisher that sends turns and final results to an external channel."""

    @abstractmethod
    async def send_turn(self, session_id: str, turn_record: TurnRecord) -> str | None:
        """Send a turn record to the external channel.

        Returns the published message ID, or ``None`` if skipped or failed.
        """

    @abstractmethod
    async def send_final(
        self, coordinator_token: str, synthesis_result: SynthesisResult
    ) -> str | None:
        """Send the final synthesis result to the external channel.

        Returns the published message ID, or ``None`` if failed.
        """
