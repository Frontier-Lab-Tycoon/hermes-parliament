"""Backend registry."""

from __future__ import annotations

from parliament.backends.hermes import HermesBackend

BACKENDS: dict[str, type[HermesBackend]] = {
    "hermes": HermesBackend,
}
