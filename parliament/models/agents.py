"""Agent backend value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendResult:
    """Result from invoking an agent backend."""

    text: str
    code: int
    error: str | None = None
