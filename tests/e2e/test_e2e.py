"""E2E tests for Hermes Parliament."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig
from parliament.integrations.discord.bot import _run_parliament_handler
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.debate.engine import DebateEngine
from parliament.sessions.index import GlobalIndex
from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.sessions.store import SessionStore

from tests.conftest import MockBackend, register_all_discord_posts


def _synthesis_json(decision: str = "go") -> str:
    return (
        "```json\n"
        f"{{\n"
        f'  "decision": "{decision}",\n'
        f'  "confidence": 0.9,\n'
        f'  "reasoning": "test",\n'
        f'  "consensus_reached": true\n'
        f"}}\n"
        "```"
    )


def _make_config(max_turns: int = 2) -> TopicConfig:
    return TopicConfig(
        participant_1="architect-devil",
        participant_2="architect-angel",
        protocol=ProtocolConfig(
            termination=TerminationConfig(
                max_turns=max_turns, min_turns=2, early_stop=True
            )
        ),
        synthesis={"enabled": True, "profile": None, "output": {"schema": {}}},
    )


@pytest.fixture
def mock_hermes_cli(mock_backend):
    """Return a MockBackend with canned responses."""
    return mock_backend(
        responses=["devil says hi", "angel says hi", _synthesis_json()],
    )


@pytest.fixture
def mock_discord_server(mock_discord_api):
    """Set up a catch-all mock Discord API server."""
    return register_all_discord_posts(mock_discord_api)


class TestE2EEngineFlow:
    """Minimal E2E: create session, run engine, verify history."""

    async def test_full_flow_creates_history(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_hermes_cli: MockBackend,
        mock_discord_server: list[dict[str, Any]],
        fake_hermes_home: Path,
    ) -> None:
        config = _make_config(max_turns=2)
        sid = store.create_session(
            "E2E topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, mock_hermes_cli)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turns = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turns) == 2
        assert turns[0]["profile"] == "architect-devil"
        assert turns[1]["profile"] == "architect-angel"

        for i in range(2):
            assert store.get_turn_publish_state(sid, f"t-{i}") == "sent"

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"

        # 2 turns + 1 final message
        assert len(mock_discord_server) == 3


class MockInteraction:
    """Minimal mock for discord.Interaction."""

    def __init__(self):
        self.response = MagicMock()
        self.response.send_message = AsyncMock()
        self.user = MagicMock()
        self.user.id = 999999


class TestE2EDiscordCommand:
    """E2E through the Discord slash command handler."""

    async def test_slash_command_creates_session(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        index: GlobalIndex,
    ) -> None:
        interaction = MockInteraction()
        with patch.object(DebateEngine, "run", new_callable=AsyncMock) as mock_run:
            await _run_parliament_handler(
                interaction=interaction,
                topic="E2E Discord topic",
                participant_1_id="123456789",
                participant_2_id="987654321",
                max_turns=2,
                registry=registry,
                store=store,
                index=index,
            )

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "🟢 토론 시작!" in args[0]
        assert kwargs.get("ephemeral") is True

        mock_run.assert_called_once()

        sessions = index.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["topic"] == "E2E Discord topic"
