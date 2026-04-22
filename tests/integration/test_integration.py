"""Integration tests for Hermes Parliament."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aioresponses import CallbackResult

from parliament.config import ProtocolConfig, TerminationConfig, TopicConfig
from parliament.discord_registry import DiscordRegistry
from parliament.engine import DebateEngine
from parliament.models import TurnRecord
from parliament.publishers.discord import DiscordPublisher
from parliament.session import SessionStore

from tests.conftest import MockBackend, register_all_discord_posts


DISCORD_API_URL = "https://discord.com/api/v10/channels/999999999/messages"


def _synthesis_json(
    decision: str = "test",
    confidence: float = 0.5,
    reasoning: str = "test",
    consensus_reached: bool = False,
) -> str:
    return (
        "```json\n"
        f"{{\n"
        f'  "decision": "{decision}",\n'
        f'  "confidence": {confidence},\n'
        f'  "reasoning": "{reasoning}",\n'
        f'  "consensus_reached": {str(consensus_reached).lower()}\n'
        f"}}\n"
        "```"
    )


def _make_topic_config(max_turns: int = 10, early_stop: bool = True) -> TopicConfig:
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
        synthesis={"enabled": True, "profile": "coordinator", "output": {"schema": {}}},
    )


def _setup_coordinator_profile(fake_home: Path) -> None:
    profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
    profile_dir.mkdir(parents=True)
    (profile_dir / "SOUL.md").write_text("coordinator soul")


class TestT9HappyPath:
    """T9-1: 4 turns + synthesis."""

    async def test_four_turns_plus_synthesis(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=4)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        responses = [
            "devil turn 1",
            "angel turn 1",
            "devil turn 2",
            "angel turn 2",
            _synthesis_json(decision="go monolith", consensus_reached=True),
        ]
        backend = mock_backend(responses)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 4

        for i in range(4):
            assert store.get_turn_publish_state(sid, f"t-{i}") == "sent"

        # 4 turns + 1 final message
        assert len(calls) == 5

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9EarlyStop:
    """T9-2: turn 3 both agree → stops at 3."""

    async def test_stops_at_three(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=10, early_stop=True)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        responses = [
            "I disagree",  # devil – no signal
            "I agree\n\n=== PARLIAMENT SIGNAL ===\nagree",  # angel
            "I also agree\n\n=== PARLIAMENT SIGNAL ===\nagree",  # devil
            _synthesis_json(consensus_reached=True),
        ]
        backend = mock_backend(responses)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 3

        for i in range(3):
            assert store.get_turn_publish_state(sid, f"t-{i}") == "sent"

        assert len(calls) == 4  # 3 turns + final

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9MaxTurns:
    """T9-3: 10 turns disagreement → stops at 10."""

    async def test_runs_all_ten(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=10, early_stop=True)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        turn_texts = [f"turn {i}" for i in range(10)]
        responses = turn_texts + [_synthesis_json()]
        backend = mock_backend(responses)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 10

        for i in range(10):
            assert store.get_turn_publish_state(sid, f"t-{i}") == "sent"

        assert len(calls) == 11  # 10 turns + final

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9CrashRecovery:
    """T9-4: crash after in_flight, resume with no duplicate publish."""

    async def test_resume_after_in_flight_crash(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=4)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        # Two successfully published turns
        for i, profile in enumerate(["architect-devil", "architect-angel"]):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=profile,
                role="debater",
                content=f"turn {i}",
            )
            store.append_turn(sid, turn)
            pub_id = registry.resolve_by_hermes_profile(profile).discord_user_id
            store.mark_turn_published(
                sid,
                f"t-{i}",
                f"msg-{i}",
                pub_id,
                "2026-04-22T00:00:00Z",
                state="sent",
                attempt_publisher=pub_id,
            )

        # Turn 2: in-flight but crashed before published
        turn2 = TurnRecord(
            turn_uuid="t-2",
            seq=2,
            profile="architect-devil",
            role="debater",
            content="turn 2",
        )
        store.append_turn(sid, turn2)
        nonce = store.generate_nonce(sid, "t-2", "123456789")
        store.mark_turn_publish_in_flight(
            sid, "t-2", nonce, "123456789", "123456789"
        )

        responses = [
            "angel turn 2",  # turn 3
            _synthesis_json(),
        ]
        backend = mock_backend(responses)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        # Resume publishes turn 2, then turn 3, then final = 3 POSTs total
        assert len(calls) == 3

        assert store.get_turn_publish_state(sid, "t-2") == "sent"
        assert store.get_turn_publish_state(sid, "t-3") == "sent"

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 4

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9CrashRecoveryFallback:
    """T9-4b: crash during fallback flow, resume correctly."""

    async def test_resume_after_fallback_crash(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=4)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        # Two successfully published turns
        for i, profile in enumerate(["architect-devil", "architect-angel"]):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=profile,
                role="debater",
                content=f"turn {i}",
            )
            store.append_turn(sid, turn)
            pub_id = registry.resolve_by_hermes_profile(profile).discord_user_id
            store.mark_turn_published(
                sid,
                f"t-{i}",
                f"msg-{i}",
                pub_id,
                "2026-04-22T00:00:00Z",
                state="sent",
                attempt_publisher=pub_id,
            )

        # Turn 2: participant bot failed → fallback_pending → crash before fallback post
        turn2 = TurnRecord(
            turn_uuid="t-2",
            seq=2,
            profile="architect-devil",
            role="debater",
            content="turn 2",
        )
        store.append_turn(sid, turn2)
        store.mark_turn_publish_fallback_pending(
            sid, "t-2", "HTTP 403: unauthorized", attempt_publisher="123456789"
        )

        responses = [
            "angel turn 2",  # turn 3
            _synthesis_json(),
        ]
        backend = mock_backend(responses)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        # Resume fallback-publishes turn 2, then turn 3, then final = 3 POSTs
        assert len(calls) == 3

        assert store.get_turn_publish_state(sid, "t-2") == "sent_via_fallback"
        assert store.get_turn_publish_state(sid, "t-3") == "sent"

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 4

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9BotOffline:
    """T9-5: 403 from participant bot → coordinator fallback, debate continues."""

    async def test_fallback_on_403(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=4)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        responses = [
            "devil turn 1",
            "angel turn 1",
            "devil turn 2",
            "angel turn 2",
            _synthesis_json(),
        ]
        backend = mock_backend(responses)

        call_counter = 0

        def _callback(url, **kwargs):
            nonlocal call_counter
            call_counter += 1
            headers = kwargs.get("headers", {})
            auth = headers.get("Authorization", "")
            if "devil-token" in auth or "angel-token" in auth:
                return CallbackResult(status=403)
            return CallbackResult(status=200, payload={"id": f"msg-{call_counter}"})

        mock_discord_api.post(DISCORD_API_URL, callback=_callback, repeat=True)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        for i in range(4):
            state = store.get_turn_publish_state(sid, f"t-{i}")
            assert state in ("sent", "sent_via_fallback")

        states = [store.get_turn_publish_state(sid, f"t-{i}") for i in range(4)]
        assert any(s == "sent_via_fallback" for s in states)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 4

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9HermesTimeout:
    """T9-6: one participant times out → [TIMEOUT] recorded, next turn proceeds."""

    async def test_timeout_recorded_and_continues(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=4)
        sid = store.create_session(
            "topic", ["architect-devil", "architect-angel"], config.model_dump()
        )

        responses = [
            "angel turn 1",
            "angel turn 2",
            _synthesis_json(),
        ]
        backend = mock_backend(responses, timeout_profile="architect-devil")

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine = DebateEngine(store, publisher)
        await engine.run(sid, config, registry, backend)

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 4

        assert turn_events[0]["content"] == "[TIMEOUT] 응답 없음"
        assert turn_events[0]["profile"] == "architect-devil"
        assert turn_events[2]["content"] == "[TIMEOUT] 응답 없음"
        assert turn_events[2]["profile"] == "architect-devil"

        assert turn_events[1]["content"] == "angel turn 1"
        assert turn_events[3]["content"] == "angel turn 2"

        cp = json.loads(store._checkpoint_path(sid).read_text(encoding="utf-8"))
        assert cp["status"] == "completed"


class TestT9ConcurrentSessions:
    """T9-7: two simultaneous sessions, data isolation."""

    async def test_two_sessions_are_isolated(
        self,
        store: SessionStore,
        registry: DiscordRegistry,
        mock_backend,
        mock_discord_api,
        fake_hermes_home: Path,
    ) -> None:
        _setup_coordinator_profile(fake_hermes_home)
        config = _make_topic_config(max_turns=2)
        sid_a = store.create_session(
            "topic A", ["architect-devil", "architect-angel"], config.model_dump()
        )
        sid_b = store.create_session(
            "topic B", ["architect-devil", "architect-angel"], config.model_dump()
        )

        responses_a = [
            "A devil",
            "A angel",
            _synthesis_json(decision="A"),
        ]
        responses_b = [
            "B devil",
            "B angel",
            _synthesis_json(decision="B"),
        ]
        backend_a = mock_backend(responses_a)
        backend_b = mock_backend(responses_b)

        calls = register_all_discord_posts(mock_discord_api)

        publisher = DiscordPublisher(registry, store)
        engine_a = DebateEngine(store, publisher)
        engine_b = DebateEngine(store, publisher)

        await asyncio.gather(
            engine_a.run(sid_a, config, registry, backend_a),
            engine_b.run(sid_b, config, registry, backend_b),
        )

        lines_a = (
            store._history_path(sid_a).read_text(encoding="utf-8").strip().split("\n")
        )
        turns_a = [
            json.loads(line)
            for line in lines_a
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turns_a) == 2
        assert turns_a[0]["content"] == "A devil"
        assert turns_a[1]["content"] == "A angel"

        lines_b = (
            store._history_path(sid_b).read_text(encoding="utf-8").strip().split("\n")
        )
        turns_b = [
            json.loads(line)
            for line in lines_b
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turns_b) == 2
        assert turns_b[0]["content"] == "B devil"
        assert turns_b[1]["content"] == "B angel"

        # 2 turns * 2 sessions + 2 finals = 6
        assert len(calls) == 6
