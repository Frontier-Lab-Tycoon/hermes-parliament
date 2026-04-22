"""E2E tests for Hermes Parliament."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.debate.engine import DebateEngine
from parliament.integrations.discord.bot import _run_parliament_handler
from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.sessions.index import GlobalIndex
from parliament.sessions.store import SessionStore
from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig

from tests.conftest import MockBackend, register_all_discord_posts


class MockInteraction:
    def __init__(self):
        self.response = MagicMock()
        self.response.send_message = AsyncMock()


class TestE2EEngineFlow:
    @pytest.fixture
    def config(self) -> TopicConfig:
        return TopicConfig(
            participant_1="architect-devil",
            participant_2="architect-angel",
            protocol=ProtocolConfig(
                termination=TerminationConfig(
                    max_turns=2,
                    min_turns=2,
                    early_stop=True,
                )
            ),
            synthesis={"enabled": True, "profile": None, "output": {"schema": {}}},
        )

    @pytest.fixture
    def backend(self, mock_backend) -> MockBackend:
        return mock_backend(
            responses=[
                "devil says hi",
                "angel says hi",
                self.synthesis_json(),
            ],
        )

    @pytest.fixture
    def discord_posts(self, mock_discord_api) -> list[dict[str, Any]]:
        return register_all_discord_posts(mock_discord_api)

    @pytest.fixture
    def session_id(self, store: SessionStore, config: TopicConfig) -> str:
        return store.create_session(
            "E2E topic",
            ["architect-devil", "architect-angel"],
            config.model_dump(),
        )

    @pytest.fixture
    def engine(
        self, store: SessionStore, registry: DiscordRegistry
    ) -> DebateEngine:
        return DebateEngine(store, DiscordPublisher(registry, store))

    @staticmethod
    def synthesis_json(decision: str = "go") -> str:
        return (
            "```json\n"
            "{\n"
            f'  "decision": "{decision}",\n'
            '  "confidence": 0.9,\n'
            '  "reasoning": "test",\n'
            '  "consensus_reached": true\n'
            "}\n"
            "```"
        )

    def turn_events(self, store: SessionStore, session_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in store._history_path(session_id).read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("type") == "turn_content"
        ]

    async def test_full_flow_completes_debate(
        self,
        engine: DebateEngine,
        store: SessionStore,
        registry: DiscordRegistry,
        config: TopicConfig,
        backend: MockBackend,
        discord_posts: list[dict[str, Any]],
        session_id: str,
        fake_hermes_home: Path,
    ) -> None:
        await engine.run(session_id, config, registry, backend)

        assert [turn["profile"] for turn in self.turn_events(store, session_id)] == [
            "architect-devil",
            "architect-angel",
        ]
        assert store.load_session(session_id).status == "completed"
        assert len(discord_posts) == 3


class TestE2EDiscordCommand:
    @pytest.fixture
    def interaction(self) -> MockInteraction:
        return MockInteraction()

    @pytest.fixture
    def patched_engine_run(self):
        with patch.object(DebateEngine, "run", new_callable=AsyncMock) as mock_run:
            yield mock_run

    async def test_slash_command_creates_session(
        self,
        interaction: MockInteraction,
        store: SessionStore,
        registry: DiscordRegistry,
        index: GlobalIndex,
        patched_engine_run: AsyncMock,
    ) -> None:
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
        patched_engine_run.assert_called_once()
        assert index.list_sessions()[0]["topic"] == "E2E Discord topic"
