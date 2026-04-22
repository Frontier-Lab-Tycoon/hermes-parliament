"""Debate engine tests."""

from __future__ import annotations

import pytest

from parliament.agents.base import AgentBackend, BackendTimeoutError
from parliament.debate.engine import DebateEngine
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.integrations.noop import NoOpPublisher
from parliament.models import BackendResult, TurnRecord
from parliament.sessions.store import SessionStore
from parliament.topics.config import ProtocolConfig, TerminationConfig, TopicConfig


class MockBackend(AgentBackend):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.index = 0

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        text = self.responses[self.index % len(self.responses)]
        self.index += 1
        return BackendResult(text=text, code=0)

    def cancel(self, handle: object) -> None:
        pass


class TimeoutBackend(AgentBackend):
    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        raise BackendTimeoutError("timeout")

    def cancel(self, handle: object) -> None:
        pass


class TestDebateEngine:
    @pytest.fixture
    def store(self, tmp_path) -> SessionStore:
        return SessionStore(base_dir=tmp_path / ".parliament")

    @pytest.fixture
    def alternating_session(self, store: SessionStore) -> tuple[str, TopicConfig]:
        sid = store.create_session("topic", ["p1", "p2"], {})
        config = TopicConfig(
            participant_1="p1",
            participant_2="p2",
            protocol=ProtocolConfig(
                termination=TerminationConfig(max_turns=4, min_turns=2)
            ),
        )
        return sid, config

    @pytest.mark.parametrize(
        ("raw", "content", "signal"),
        [
            ("I agree\n\n=== PARLIAMENT SIGNAL ===\nagree", "I agree", "agree"),
            ('Looks good\n{"consensus_signal":"agree"}', "Looks good", "agree"),
            ("plain reply", "plain reply", None),
        ],
    )
    async def test_parse_output_extracts_consensus_signal(
        self, raw: str, content: str, signal: str | None
    ) -> None:
        parsed_content, parsed_signal, _ = DebateEngine.parse_output(raw)
        assert parsed_content == content
        assert parsed_signal == signal

    async def test_termination_respects_min_turns_and_consensus(self) -> None:
        config = ProtocolConfig(termination=TerminationConfig(max_turns=10, min_turns=2))
        first_agree = [
            TurnRecord(
                turn_uuid="t-0",
                seq=0,
                profile="p1",
                role="debater",
                content="ok",
                consensus_signal="agree",
            )
        ]
        both_agree = first_agree + [
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="p2",
                role="debater",
                content="ok",
                consensus_signal="agree",
            )
        ]

        assert DebateEngine.check_termination(first_agree, config) is False
        assert DebateEngine.check_termination(both_agree, config) is True

    async def test_timeout_turn_is_recorded_as_timeout(
        self, store: SessionStore
    ) -> None:
        turn = await DebateEngine(store, NoOpPublisher()).run_turn(
            "p1", "prompt", TimeoutBackend()
        )
        assert turn.content == "[TIMEOUT] 응답 없음"
        assert turn.profile == "p1"

    async def test_run_records_alternating_turns(
        self,
        store: SessionStore,
        alternating_session: tuple[str, TopicConfig],
    ) -> None:
        sid, config = alternating_session

        await DebateEngine(store, NoOpPublisher()).run(
            sid,
            config,
            DiscordRegistry(profiles={}, coordinator={}),
            MockBackend([f"response {i}" for i in range(4)]),
        )

        session = store.load_session(sid)
        assert [turn.profile for turn in session.turns] == ["p1", "p2", "p1", "p2"]
