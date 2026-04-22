"""Abstract base class for agent backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from parliament.models import BackendResult


class BackendTimeoutError(TimeoutError):
    """Raised when a backend invocation exceeds its timeout."""


class AgentBackend(ABC):
    """Abstract agent backend."""

    @abstractmethod
    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        """Invoke the backend for *profile* with *prompt*.

        Args:
            profile: Hermes profile name.
            prompt: Assembled prompt text.
            timeout: Maximum seconds to wait for a response.

        Returns:
            BackendResult with the agent's text output.

        Raises:
            BackendTimeoutError: If the invocation exceeds *timeout*.
        """

    @abstractmethod
    def cancel(self, handle: object) -> None:
        """Cancel an in-flight invocation."""
