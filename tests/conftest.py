"""Pytest fixtures and configuration for Parliament."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses

from parliament.agents.base import AgentBackend
from parliament.integrations.discord.registry import DiscordRegistry, HermesProfile
from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.sessions.index import GlobalIndex
from parliament.models import BackendResult
from parliament.sessions.store import SessionStore


@pytest.fixture
def tmp_parliament_dir(tmp_path: Path) -> Path:
    return tmp_path / ".parliament"


@pytest.fixture
def store(tmp_parliament_dir: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_parliament_dir)


@pytest.fixture
def index(tmp_parliament_dir: Path) -> GlobalIndex:
    return GlobalIndex(db_path=tmp_parliament_dir / "index.db")


@pytest.fixture
def registry() -> DiscordRegistry:
    profiles = {
        "123456789": HermesProfile(
            hermes_profile="architect-devil",
            discord_bot_token="devil-token-123",
            discord_user_id="123456789",
            discord_name="Test Bot",
        ),
        "987654321": HermesProfile(
            hermes_profile="architect-angel",
            discord_bot_token="angel-token-456",
            discord_user_id="987654321",
            discord_name="Test Bot 2",
        ),
    }
    coordinator = {
        "bot_token": "coordinator-token-789",
        "channel_id": "999999999",
    }
    return DiscordRegistry(profiles=profiles, coordinator=coordinator)


class MockBackend(AgentBackend):
    """Backend that returns a predefined sequence of responses."""

    def __init__(
        self,
        responses: list[str],
        timeout_profile: str | None = None,
    ) -> None:
        self.responses = responses
        self.index = 0
        self.timeout_profile = timeout_profile

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        if self.timeout_profile and profile == self.timeout_profile:
            from parliament.agents.base import BackendTimeoutError
            raise BackendTimeoutError("timed out")
        text = self.responses[self.index % len(self.responses)]
        self.index += 1
        return BackendResult(text=text, code=0)

    def cancel(self, handle: object) -> None:
        pass


@pytest.fixture
def mock_backend():
    def _make(responses: list[str], timeout_profile: str | None = None) -> MockBackend:
        return MockBackend(responses, timeout_profile)
    return _make


@pytest.fixture
def mock_discord_api():
    with aioresponses() as m:
        yield m


@pytest.fixture
def fake_hermes_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def make_publisher(registry: DiscordRegistry, store: SessionStore) -> DiscordPublisher:
    return DiscordPublisher(registry, store)


def register_all_discord_posts(mock_discord_api, channel_id: str = "999999999") -> list[dict[str, Any]]:
    """Register a catch-all POST handler for Discord messages and return a call log."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    calls: list[dict[str, Any]] = []
    call_counter = 0

    def _callback(url, **kwargs):
        nonlocal call_counter
        call_counter += 1
        calls.append({"url": str(url), "kwargs": kwargs})
        return CallbackResult(status=200, payload={"id": f"msg-{call_counter}"})

    mock_discord_api.post(url, callback=_callback, repeat=True)
    return calls
