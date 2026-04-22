"""Integration tests for Hermes Parliament."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aioresponses import CallbackResult

from parliament.debate.engine import DebateEngine
from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.models import PublishState, SessionStatus, TurnRecord
from parliament.sessions.store import SessionStore
from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig
from tests.conftest import MockBackend, register_all_discord_posts

DISCORD_API_URL = "https://discord.com/api/v10/channels/999999999/messages"


class TestParliamentIntegration:
    @staticmethod
    def synthesis_json(
        decision: str = "test",
        confidence: float = 0.5,
        reasoning: str = "test",
        consensus_reached: bool = False,
    ) -> str:
        return (
            "```json\n"
            "{\n"
            f'  "decision": "{decision}",\n'
            f'  "confidence": {confidence},\n'
            f'  "reasoning": "{reasoning}",\n'
            f'  "consensus_reached": {str(consensus_reached).lower()}\n'
            "}\n"
            "```"
        )

    @pytest.fixture(autouse=True)
    def coordinator_profile(self, fake_hermes_home: Path) -> None:
        profile_dir = fake_hermes_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul", encoding="utf-8")

    @pytest.fixture
    def config(self) -> TopicConfig:
        return self.make_config(max_turns=4)

    @pytest.fixture
    def short_config(self) -> TopicConfig:
        return self.make_config(max_turns=2)

    @pytest.fixture
    def early_stop_config(self) -> TopicConfig:
        return self.make_config(max_turns=10, early_stop=True)

    @pytest.fixture
    def publisher(self, registry: DiscordRegistry, store: SessionStore) -> DiscordPublisher:
        return DiscordPublisher(registry, store)

    @pytest.fixture
    def discord_posts(self, mock_discord_api) -> list[dict[str, Any]]:
        return register_all_discord_posts(mock_discord_api)

    @pytest.fixture
    def fallback_discord_api(self, mock_discord_api) -> None:
        def callback(url, **kwargs):
            headers = kwargs.get("headers", {})
            auth = headers.get("Authorization", "")
            if "devil-token" in auth or "angel-token" in auth:
                return CallbackResult(status=403)
            return CallbackResult(status=200, payload={"id": "msg-fallback"})

        mock_discord_api.post(DISCORD_API_URL, callback=callback, repeat=True)

    @pytest.fixture
    def happy_path_backend(self, mock_backend) -> MockBackend:
        return mock_backend(
            [
                "devil turn 1",
                "angel turn 1",
                "devil turn 2",
                "angel turn 2",
                self.synthesis_json(decision="go monolith", consensus_reached=True),
            ]
        )

    @pytest.fixture
    def early_stop_backend(self, mock_backend) -> MockBackend:
        return mock_backend(
            [
                "I disagree",
                "I agree\n\n=== PARLIAMENT SIGNAL ===\nagree",
                "I also agree\n\n=== PARLIAMENT SIGNAL ===\nagree",
                self.synthesis_json(consensus_reached=True),
            ]
        )

    @pytest.fixture
    def timeout_backend(self, mock_backend) -> MockBackend:
        return mock_backend(
            [
                "angel turn 1",
                "angel turn 2",
                self.synthesis_json(),
            ],
            timeout_profile="architect-devil",
        )

    def make_config(self, max_turns: int, early_stop: bool = True) -> TopicConfig:
        return TopicConfig(
            participant_1="architect-devil",
            participant_2="architect-angel",
            protocol=ProtocolConfig(
                termination=TerminationConfig(
                    max_turns=max_turns,
                    min_turns=2,
                    early_stop=early_stop,
                )
            ),
            synthesis={
                "enabled": True,
                "profile": "coordinator",
                "output": {"schema": {}},
            },
        )

    def create_session(self, store: SessionStore, config: TopicConfig, topic: str) -> str:
        return store.create_session(
            topic,
            ["architect-devil", "architect-angel"],
            config.model_dump(),
        )

    def turn_events(self, store: SessionStore, session_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in store._history_path(session_id).read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("type") == "turn_content"
        ]

    async def run_engine(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        session_id: str,
        config: TopicConfig,
        backend: MockBackend,
    ) -> None:
        await DebateEngine(store, publisher).run(session_id, config, registry, backend)

    async def test_happy_path_publishes_turns_and_final_result(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        config: TopicConfig,
        happy_path_backend: MockBackend,
        discord_posts: list[dict[str, Any]],
    ) -> None:
        sid = self.create_session(store, config, "topic")

        await self.run_engine(store, registry, publisher, sid, config, happy_path_backend)

        assert len(self.turn_events(store, sid)) == 4
        assert len(discord_posts) == 5
        assert store.load_session(sid).status == SessionStatus.COMPLETED

    async def test_early_stop_finishes_after_consensus(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        early_stop_config: TopicConfig,
        early_stop_backend: MockBackend,
        discord_posts: list[dict[str, Any]],
    ) -> None:
        sid = self.create_session(store, early_stop_config, "topic")

        await self.run_engine(
            store, registry, publisher, sid, early_stop_config, early_stop_backend
        )

        assert len(self.turn_events(store, sid)) == 3
        assert len(discord_posts) == 4
        assert store.load_session(sid).status == SessionStatus.COMPLETED

    async def test_resume_republishes_in_flight_turn_without_duplicate_history(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        config: TopicConfig,
        mock_backend,
        discord_posts: list[dict[str, Any]],
    ) -> None:
        sid = self.create_session(store, config, "topic")
        for i, profile in enumerate(["architect-devil", "architect-angel"]):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=profile,
                role="debater",
                content=f"turn {i}",
            )
            store.append_turn(sid, turn)
            publisher_id = registry.resolve_by_hermes_profile(profile).discord_user_id
            store.mark_turn_published(
                sid,
                f"t-{i}",
                f"msg-{i}",
                publisher_id,
                "2026-04-22T00:00:00Z",
                state=PublishState.SENT,
                attempt_publisher=publisher_id,
            )

        turn = TurnRecord(
            turn_uuid="t-2",
            seq=2,
            profile="architect-devil",
            role="debater",
            content="turn 2",
        )
        store.append_turn(sid, turn)
        nonce = store.generate_nonce(sid, "t-2", "123456789")
        store.mark_turn_publish_in_flight(sid, "t-2", nonce, "123456789", "123456789")

        await self.run_engine(
            store,
            registry,
            publisher,
            sid,
            config,
            mock_backend(["angel turn 2", self.synthesis_json()]),
        )

        assert len(discord_posts) == 3
        assert len(self.turn_events(store, sid)) == 4
        assert store.get_turn_publish_state(sid, "t-2") == PublishState.SENT
        assert store.get_turn_publish_state(sid, "t-3") == PublishState.SENT

    async def test_participant_publish_failure_falls_back_and_continues(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        config: TopicConfig,
        happy_path_backend: MockBackend,
        fallback_discord_api,
    ) -> None:
        sid = self.create_session(store, config, "topic")

        await self.run_engine(store, registry, publisher, sid, config, happy_path_backend)

        states = [store.get_turn_publish_state(sid, f"t-{i}") for i in range(4)]
        assert PublishState.SENT_VIA_FALLBACK in states
        assert len(self.turn_events(store, sid)) == 4
        assert store.load_session(sid).status == SessionStatus.COMPLETED

    async def test_agent_timeout_is_recorded_and_debate_continues(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        config: TopicConfig,
        timeout_backend: MockBackend,
        discord_posts: list[dict[str, Any]],
    ) -> None:
        sid = self.create_session(store, config, "topic")

        await self.run_engine(store, registry, publisher, sid, config, timeout_backend)

        turns = self.turn_events(store, sid)
        assert [turn["content"] for turn in turns] == [
            "[TIMEOUT] 응답 없음",
            "angel turn 1",
            "[TIMEOUT] 응답 없음",
            "angel turn 2",
        ]
        assert len(discord_posts) == 5
        assert store.load_session(sid).status == SessionStatus.COMPLETED

    async def test_concurrent_sessions_are_isolated(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        publisher: DiscordPublisher,
        short_config: TopicConfig,
        mock_backend,
        discord_posts: list[dict[str, Any]],
    ) -> None:
        sid_a = self.create_session(store, short_config, "topic A")
        sid_b = self.create_session(store, short_config, "topic B")

        await asyncio.gather(
            self.run_engine(
                store,
                registry,
                publisher,
                sid_a,
                short_config,
                mock_backend(["A devil", "A angel", self.synthesis_json(decision="A")]),
            ),
            self.run_engine(
                store,
                registry,
                publisher,
                sid_b,
                short_config,
                mock_backend(["B devil", "B angel", self.synthesis_json(decision="B")]),
            ),
        )

        assert [turn["content"] for turn in self.turn_events(store, sid_a)] == [
            "A devil",
            "A angel",
        ]
        assert [turn["content"] for turn in self.turn_events(store, sid_b)] == [
            "B devil",
            "B angel",
        ]
        assert len(discord_posts) == 6
