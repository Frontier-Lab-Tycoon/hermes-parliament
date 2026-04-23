"""Discord command handler tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.debate.engine import DebateEngine
from parliament.integrations.discord.bot import _run_parliament_handler
from parliament.integrations.discord.registry import DiscordRegistry, HermesProfile
from parliament.sessions.index import GlobalIndex
from parliament.sessions.store import SessionStore


class MockInteraction:
    def __init__(self):
        self.response = MagicMock()
        self.response.send_message = AsyncMock()


class TestDiscordCommandHandler:
    @pytest.fixture
    def registry(self) -> DiscordRegistry:
        return DiscordRegistry(
            profiles={
                "123456789": HermesProfile(
                    hermes_profile="architect-devil",
                    discord_bot_token="devil-token",
                    discord_user_id="123456789",
                ),
                "987654321": HermesProfile(
                    hermes_profile="architect-angel",
                    discord_bot_token="angel-token",
                    discord_user_id="987654321",
                ),
            },
            coordinator={"bot_token": "coordinator-token"},
        )

    @pytest.fixture
    def store(self, tmp_path) -> SessionStore:
        return SessionStore(base_dir=tmp_path / ".parliament")

    @pytest.fixture
    def index(self, tmp_path) -> GlobalIndex:
        return GlobalIndex(db_path=tmp_path / ".parliament" / "index.db")

    @pytest.fixture
    def interaction(self) -> MockInteraction:
        return MockInteraction()

    @pytest.fixture
    def patched_engine_run(self):
        with patch.object(DebateEngine, "run", new_callable=AsyncMock) as mock_run:
            yield mock_run

    async def test_valid_command_creates_session_and_starts_engine(
        self,
        registry: DiscordRegistry,
        store: SessionStore,
        index: GlobalIndex,
        interaction: MockInteraction,
        patched_engine_run: AsyncMock,
    ) -> None:
        await _run_parliament_handler(
            interaction=interaction,
            topic="Test topic",
            participant_1="123456789",
            participant_2="987654321",
            max_turns=10,
            registry=registry,
            store=store,
            index=index,
        )

        interaction.response.send_message.assert_called_once()
        assert index.list_sessions()[0]["topic"] == "Test topic"
        session = store.load_session(index.list_sessions()[0]["session_id"])
        assert session.config["participants"] == [
            "architect-devil",
            "architect-angel",
        ]
        patched_engine_run.assert_called_once()

    @pytest.mark.parametrize(
        ("participant_1", "participant_2", "max_turns", "message"),
        [
            (
                "123456789",
                "000000000",
                10,
                "등록되지 않은 봇입니다",
            ),
            (
                "123456789",
                "123456789",
                10,
                "서로 다른 봇을 선택하세요",
            ),
            (
                "123456789",
                "987654321",
                1,
                "max_turns는 2 이상이어야 합니다",
            ),
        ],
    )
    async def test_invalid_command_inputs_return_ephemeral_error(
        self,
        registry: DiscordRegistry,
        store: SessionStore,
        index: GlobalIndex,
        interaction: MockInteraction,
        participant_1: str,
        participant_2: str,
        max_turns: int,
        message: str,
    ) -> None:
        await _run_parliament_handler(
            interaction=interaction,
            topic="Test topic",
            participant_1=participant_1,
            participant_2=participant_2,
            max_turns=max_turns,
            registry=registry,
            store=store,
            index=index,
        )

        args, kwargs = interaction.response.send_message.call_args
        assert message in args[0]
        assert kwargs.get("ephemeral") is True
