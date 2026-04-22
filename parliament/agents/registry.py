"""Backend registry."""

from __future__ import annotations

from parliament.agents.hermes import HermesBackend

BACKENDS: dict[str, type[HermesBackend]] = {
    "hermes": HermesBackend,
}
