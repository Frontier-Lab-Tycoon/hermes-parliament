"""Parliament Discord bot with /parliament slash command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig, load_topic
from parliament.integrations.discord.registry import DiscordRegistry, load_registry
from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.sessions.index import GlobalIndex
from parliament.sessions.store import SessionStore


def _default_bot_config_path() -> Path:
    config_dir = Path.home() / ".parliament"
    preferred = config_dir / "bots.yaml"
    legacy = config_dir / "discord-registry.yaml"
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


async def _run_parliament_handler(
    interaction: discord.Interaction,
    topic: str,
    participant_1: str,
    participant_2: str,
    max_turns: int,
    registry: DiscordRegistry,
    store: SessionStore,
    index: GlobalIndex,
    default_topic_path: str | None = None,
) -> None:
    """Core logic for /parliament command handler."""
    # Validate: same participants
    if participant_1 == participant_2:
        await interaction.response.send_message(
            "서로 다른 봇을 선택하세요", ephemeral=True
        )
        return

    # Validate: max_turns >= 2
    if max_turns < 2:
        await interaction.response.send_message(
            "max_turns는 2 이상이어야 합니다", ephemeral=True
        )
        return

    # Lookup participant bot config (falls back to hermes profile scan).
    missing: list[str] = []
    profile_1 = None
    profile_2 = None
    try:
        profile_1 = registry.resolve_profile(participant_1)
    except KeyError:
        missing.append(f"<@{participant_1}>")
    try:
        profile_2 = registry.resolve_profile(participant_2)
    except KeyError:
        missing.append(f"<@{participant_2}>")
    if profile_1 is None or profile_2 is None:
        await interaction.response.send_message(
            f"등록되지 않은 봇입니다: {', '.join(missing)}. "
            "해당 봇이 이 머신의 hermes 프로필(~/.hermes/profiles/<name>/.env)과 "
            "연결되어 있어야 합니다.",
            ephemeral=True,
        )
        return

    # Load topic config
    if default_topic_path and Path(default_topic_path).exists():
        topic_config = load_topic(default_topic_path)
        config = topic_config.model_dump()
        config.setdefault("session", {})["topic"] = topic
        config.setdefault("protocol", {}).setdefault("termination", {})[
            "max_turns"
        ] = max_turns
        topic_config = TopicConfig(**config)
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
    config["participants"] = participants
    session_id = store.create_session(topic, participants, config)

    # Register in global index
    from datetime import datetime, timezone

    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    index.register_session(session_id, "running", topic, created_at)

    # Respond ephemerally
    await interaction.response.send_message(
        f"🟢 토론 시작! 참가자: <@{profile_1.discord_user_id}>, <@{profile_2.discord_user_id}> / 주제: {topic}",
        ephemeral=True,
    )

    # Start background task
    from parliament.debate.engine import DebateEngine

    publisher = None
    channel_id = getattr(interaction, "channel_id", None)
    if channel_id is not None or registry.coordinator.get("channel_id"):
        publisher = DiscordPublisher(
            registry,
            store,
            channel_id=str(channel_id) if channel_id is not None else None,
        )

    engine = DebateEngine(store, publisher)
    asyncio.create_task(engine.run(session_id, topic_config, registry))


class ParliamentBot(discord.Client):
    """Discord-facing Parliament application bot."""

    def __init__(
        self,
        registry_path: str | None = None,
        default_topic_path: str | None = None,
        sync_commands: bool = True,
        sync_guild_id: str | None = None,
        **kwargs: Any,
    ):
        intents = discord.Intents.default()
        super().__init__(intents=intents, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.registry_path = registry_path or str(_default_bot_config_path())
        self.default_topic_path = default_topic_path
        self.sync_commands = sync_commands
        self.sync_guild_id = sync_guild_id
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
            p1="First character bot",
            p2="Second character bot",
            turns="Maximum number of turns (default: 10)",
        )
        async def parliament_cmd(
            interaction: discord.Interaction,
            topic: str,
            p1: discord.User,
            p2: discord.User,
            turns: int = 10,
        ) -> None:
            await self._handle_parliament(
                interaction,
                topic,
                str(p1.id),
                str(p2.id),
                turns,
            )

        self.tree.add_command(parliament_cmd)

        @app_commands.command(
            name="discuss",
            description="Start a debate with two mentioned bots (resolved from Hermes profiles)",
        )
        @app_commands.describe(
            topic="Debate topic",
            p1="First participant bot (mention)",
            p2="Second participant bot (mention)",
            turns="Maximum number of turns (default: 10)",
        )
        async def discuss_cmd(
            interaction: discord.Interaction,
            topic: str,
            p1: discord.User,
            p2: discord.User,
            turns: int = 10,
        ) -> None:
            await self._handle_parliament(
                interaction,
                topic,
                str(p1.id),
                str(p2.id),
                turns,
            )

        self.tree.add_command(discuss_cmd)

    async def _handle_parliament(
        self,
        interaction: discord.Interaction,
        topic: str,
        participant_1: str,
        participant_2: str,
        max_turns: int,
    ) -> None:
        if self.registry is None:
            await interaction.response.send_message(
                "Bot not ready: bot config not loaded", ephemeral=True
            )
            return

        await _run_parliament_handler(
            interaction=interaction,
            topic=topic,
            participant_1=participant_1,
            participant_2=participant_2,
            max_turns=max_turns,
            registry=self.registry,
            store=self.store,
            index=self.index,
            default_topic_path=self.default_topic_path,
        )

    async def setup_hook(self) -> None:
        self.registry = load_registry(self.registry_path)
        if self.sync_commands:
            if self.sync_guild_id:
                guild = discord.Object(id=int(self.sync_guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
