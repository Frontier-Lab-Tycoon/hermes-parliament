"""Discord coordinator bot with /parliament slash command."""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from parliament.integrations.discord.registry import DiscordRegistry, load_registry
from parliament.models import SessionStatus
from parliament.sessions.index import GlobalIndex
from parliament.sessions.store import SessionStore
from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig, load_topic


async def _run_parliament_handler(
    interaction: discord.Interaction,
    topic: str,
    participant_1_id: str,
    participant_2_id: str,
    max_turns: int,
    registry: DiscordRegistry,
    store: SessionStore,
    index: GlobalIndex,
    default_topic_path: str | None = None,
) -> None:
    """Core logic for /parliament command handler."""
    # Validate: same participants
    if participant_1_id == participant_2_id:
        await interaction.response.send_message("서로 다른 봇을 선택하세요", ephemeral=True)
        return

    # Validate: max_turns >= 2
    if max_turns < 2:
        await interaction.response.send_message("max_turns는 2 이상이어야 합니다", ephemeral=True)
        return

    # Lookup registry
    try:
        profile_1 = registry.resolve_profile(participant_1_id)
        profile_2 = registry.resolve_profile(participant_2_id)
    except KeyError:
        await interaction.response.send_message("등록되지 않은 봇입니다", ephemeral=True)
        return

    # Load topic config
    if default_topic_path and Path(default_topic_path).exists():
        topic_config = load_topic(default_topic_path)
        config = topic_config.model_dump()
        config.setdefault("session", {})["topic"] = topic
        config.setdefault("protocol", {}).setdefault("termination", {})["max_turns"] = max_turns
    else:
        topic_config = TopicConfig(
            session={"topic": topic, "max_turns": max_turns},
            protocol=ProtocolConfig(
                termination=TerminationConfig(max_turns=max_turns, min_turns=2)
            ),
        )
        config = topic_config.model_dump()

    # Create session
    participants = [profile_1.hermes_profile, profile_2.hermes_profile]
    session_id = store.create_session(topic, participants, config)

    # Register in global index
    from datetime import datetime

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    index.register_session(session_id, SessionStatus.RUNNING, topic, created_at)

    # Respond ephemerally
    await interaction.response.send_message(
        f"🟢 토론 시작! 참가자: <@{participant_1_id}>, <@{participant_2_id}> / 주제: {topic}",
        ephemeral=True,
    )

    # Start background task
    from parliament.debate.engine import DebateEngine

    engine = DebateEngine(store)
    asyncio.create_task(engine.run(session_id))


class ParliamentBot(discord.Client):
    """Discord coordinator bot for Hermes Parliament."""

    def __init__(
        self,
        registry_path: str | None = None,
        default_topic_path: str | None = None,
        **kwargs: Any,
    ):
        intents = discord.Intents.default()
        super().__init__(intents=intents, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.registry_path = registry_path or str(
            Path.home() / ".parliament" / "discord-registry.yaml"
        )
        self.default_topic_path = default_topic_path
        self.registry: DiscordRegistry | None = None
        self.store = SessionStore()
        self.index = GlobalIndex()
        self._add_commands()

    def _add_commands(self) -> None:
        @app_commands.command(
            name="parliament",
            description="Start a Parliament debate session",
        )
        @app_commands.describe(
            topic="Debate topic",
            participant_1="First participant bot (mention)",
            participant_2="Second participant bot (mention)",
            max_turns="Maximum number of turns (default: 10)",
        )
        async def parliament_cmd(
            interaction: discord.Interaction,
            topic: str,
            participant_1: discord.User,
            participant_2: discord.User,
            max_turns: int = 10,
        ) -> None:
            await self._handle_parliament(
                interaction, topic, participant_1, participant_2, max_turns
            )

        self.tree.add_command(parliament_cmd)

    async def _handle_parliament(
        self,
        interaction: discord.Interaction,
        topic: str,
        participant_1: discord.User,
        participant_2: discord.User,
        max_turns: int,
    ) -> None:
        if self.registry is None:
            await interaction.response.send_message(
                "Bot not ready: registry not loaded", ephemeral=True
            )
            return

        await _run_parliament_handler(
            interaction=interaction,
            topic=topic,
            participant_1_id=str(participant_1.id),
            participant_2_id=str(participant_2.id),
            max_turns=max_turns,
            registry=self.registry,
            store=self.store,
            index=self.index,
            default_topic_path=self.default_topic_path,
        )

    async def setup_hook(self) -> None:
        self.registry = load_registry(self.registry_path)
