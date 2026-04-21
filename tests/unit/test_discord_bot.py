"""Phase 6 acceptance criteria: Discord Slash Command."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.discord_bot import ParliamentBot, _run_parliament_handler
from parliament.discord_registry import DiscordRegistry, HermesProfile
from parliament.engine import DebateEngine
from parliament.index import GlobalIndex
from parliament.session import SessionStore


class MockInteraction:
    def __init__(self):
        self.response = MagicMock()
        self.response.send_message = AsyncMock()
        self.user = MagicMock()
        self.user.id = 999999


def _make_registry() -> DiscordRegistry:
    return DiscordRegistry(
        profiles={
            "123456789": HermesProfile(
                hermes_profile="architect-devil",
                discord_bot_token="devil-token",
                discord_user_id="123456789",
                discord_name="악마의 대변인",
            ),
            "987654321": HermesProfile(
                hermes_profile="architect-angel",
                discord_bot_token="angel-token",
                discord_user_id="987654321",
                discord_name="천사의 대변인",
            ),
        },
        coordinator={"bot_token": "coordinator-token", "application_id": "app-123"},
    )


@pytest.fixture
def mock_registry() -> DiscordRegistry:
    return _make_registry()


@pytest.fixture
def tmp_store(tmp_path) -> SessionStore:
    return SessionStore(base_dir=tmp_path / ".parliament")


@pytest.fixture
def tmp_index(tmp_path) -> GlobalIndex:
    return GlobalIndex(db_path=tmp_path / ".parliament" / "index.db")


class TestT6CommandRegistration:
    """T6-1: command registration structure exists."""

    def test_parliament_command_registered(self):
        bot = ParliamentBot(registry_path="dummy")
        cmd = bot.tree.get_command("parliament")
        assert cmd is not None
        assert cmd.name == "parliament"


class TestT6ValidMentions:
    """T6-2: valid mention parsing → session created."""

    @pytest.mark.asyncio
    async def test_valid_mentions_create_session(
        self,
        mock_registry: DiscordRegistry,
        tmp_store: SessionStore,
        tmp_index: GlobalIndex,
    ):
        interaction = MockInteraction()
        with patch.object(DebateEngine, "run", new_callable=AsyncMock) as mock_run:
            await _run_parliament_handler(
                interaction=interaction,
                topic="Test topic",
                participant_1_id="123456789",
                participant_2_id="987654321",
                max_turns=10,
                registry=mock_registry,
                store=tmp_store,
                index=tmp_index,
            )

        # Check ephemeral response
        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "🟢 토론 시작!" in args[0]
        assert "123456789" in args[0]
        assert "987654321" in args[0]
        assert kwargs.get("ephemeral") is True

        # Check engine run was scheduled
        mock_run.assert_called_once()


class TestT6UnregisteredBot:
    """T6-3: unregistered bot → error."""

    @pytest.mark.asyncio
    async def test_unregistered_bot_returns_error(
        self,
        mock_registry: DiscordRegistry,
        tmp_store: SessionStore,
        tmp_index: GlobalIndex,
    ):
        interaction = MockInteraction()
        await _run_parliament_handler(
            interaction=interaction,
            topic="Test topic",
            participant_1_id="123456789",
            participant_2_id="000000000",  # unregistered
            max_turns=10,
            registry=mock_registry,
            store=tmp_store,
            index=tmp_index,
        )

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "등록되지 않은 봇입니다" in args[0]
        assert kwargs.get("ephemeral") is True


class TestT6SameParticipants:
    """T6-4: same participants → error."""

    @pytest.mark.asyncio
    async def test_same_participants_returns_error(
        self,
        mock_registry: DiscordRegistry,
        tmp_store: SessionStore,
        tmp_index: GlobalIndex,
    ):
        interaction = MockInteraction()
        await _run_parliament_handler(
            interaction=interaction,
            topic="Test topic",
            participant_1_id="123456789",
            participant_2_id="123456789",
            max_turns=10,
            registry=mock_registry,
            store=tmp_store,
            index=tmp_index,
        )

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "서로 다른 봇을 선택하세요" in args[0]
        assert kwargs.get("ephemeral") is True


class TestT6MaxTurnsTooLow:
    """T6-5: max_turns=1 → error."""

    @pytest.mark.asyncio
    async def test_max_turns_less_than_two_returns_error(
        self,
        mock_registry: DiscordRegistry,
        tmp_store: SessionStore,
        tmp_index: GlobalIndex,
    ):
        interaction = MockInteraction()
        await _run_parliament_handler(
            interaction=interaction,
            topic="Test topic",
            participant_1_id="123456789",
            participant_2_id="987654321",
            max_turns=1,
            registry=mock_registry,
            store=tmp_store,
            index=tmp_index,
        )

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "max_turns는 2 이상이어야 합니다" in args[0]
        assert kwargs.get("ephemeral") is True


class TestT6ConcurrentSessions:
    """T6-6: concurrent sessions → separate session IDs."""

    @pytest.mark.asyncio
    async def test_concurrent_sessions_have_different_ids(
        self,
        mock_registry: DiscordRegistry,
        tmp_store: SessionStore,
        tmp_index: GlobalIndex,
    ):
        interaction1 = MockInteraction()
        with patch.object(DebateEngine, "run", new_callable=AsyncMock):
            await _run_parliament_handler(
                interaction=interaction1,
                topic="Topic A",
                participant_1_id="123456789",
                participant_2_id="987654321",
                max_turns=5,
                registry=mock_registry,
                store=tmp_store,
                index=tmp_index,
            )

        interaction2 = MockInteraction()
        with patch.object(DebateEngine, "run", new_callable=AsyncMock):
            await _run_parliament_handler(
                interaction=interaction2,
                topic="Topic B",
                participant_1_id="123456789",
                participant_2_id="987654321",
                max_turns=5,
                registry=mock_registry,
                store=tmp_store,
                index=tmp_index,
            )

        # Verify that two sessions were created
        sessions = tmp_index.list_sessions()
        assert len(sessions) == 2
        assert sessions[0]["session_id"] != sessions[1]["session_id"]
