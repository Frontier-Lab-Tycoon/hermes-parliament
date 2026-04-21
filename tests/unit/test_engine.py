"""Phase 4 acceptance criteria: Turn Loop Engine Core."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parliament.backends.base import AgentBackend
from parliament.config import ProtocolConfig, TerminationConfig, TopicConfig
from parliament.discord_registry import DiscordRegistry
from parliament.engine import DebateEngine
from parliament.models import BackendResult, TurnRecord
from parliament.publishers.noop import NoOpPublisher
from parliament.session import SessionStore


@pytest.fixture
def tmp_parliament_dir(tmp_path: Path) -> Path:
    return tmp_path / ".parliament"


@pytest.fixture
def store(tmp_parliament_dir: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_parliament_dir)


class MockBackend(AgentBackend):
    """Backend that returns a predefined sequence of responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.index = 0

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        text = self.responses[self.index % len(self.responses)]
        self.index += 1
        return BackendResult(text=text, code=0)

    def cancel(self, handle: object) -> None:
        pass


class TestT4AlternatingOrder:
    """T4-1: 2-person alternating 4 turns → A→B→A→B."""

    def test_four_turns_alternating(self) -> None:
        ordering = ["architect-devil", "product-angel"]
        turns: list[TurnRecord] = []

        for _ in range(4):
            speaker = DebateEngine.determine_next_speaker(turns, ordering)
            turns.append(
                TurnRecord(
                    turn_uuid=f"t-{len(turns)}",
                    seq=len(turns),
                    profile=speaker,
                    role="debater",
                    content="hello",
                )
            )

        assert turns[0].profile == "architect-devil"
        assert turns[1].profile == "product-angel"
        assert turns[2].profile == "architect-devil"
        assert turns[3].profile == "product-angel"


class TestT4ParseSignalBlock:
    """T4-2: tail block ``=== PARLIAMENT SIGNAL ===\\nagree`` → consensus_signal="agree"."""

    def test_signal_block_parsed(self) -> None:
        raw = "=== PARLIAMENT SIGNAL ===\nagree"
        content, signal, structured = DebateEngine.parse_output(raw)
        assert content == ""
        assert signal == "agree"
        assert structured is None

    def test_signal_block_with_content(self) -> None:
        raw = "I agree with the proposal.\n\n=== PARLIAMENT SIGNAL ===\nagree"
        content, signal, structured = DebateEngine.parse_output(raw)
        assert content == "I agree with the proposal."
        assert signal == "agree"
        assert structured is None


class TestT4ParseJsonTail:
    """T4-3: tail JSON ``{"consensus_signal":"agree"}`` → consensus_signal="agree"."""

    def test_json_tail_parsed(self) -> None:
        raw = '{"consensus_signal":"agree"}'
        content, signal, structured = DebateEngine.parse_output(raw)
        assert content == ""
        assert signal == "agree"
        assert structured is None

    def test_json_tail_with_content(self) -> None:
        raw = 'Looks good\n{"consensus_signal":"agree"}'
        content, signal, structured = DebateEngine.parse_output(raw)
        assert content == "Looks good"
        assert signal == "agree"
        assert structured is None

    def test_json_tail_with_extra_fields(self) -> None:
        raw = '{"consensus_signal":"agree","confidence":0.9}'
        content, signal, structured = DebateEngine.parse_output(raw)
        assert content == ""
        assert signal == "agree"
        assert structured == {"confidence": 0.9}


class TestT4TerminationMinTurns:
    """T4-4 / T4-5: min_turns consensus termination rules."""

    def test_turn_1_agree_does_not_terminate(self) -> None:
        """T4-4: min_turns=2, turn 1 agree → does NOT terminate."""
        turns = [
            TurnRecord(
                turn_uuid="t-0",
                seq=0,
                profile="architect-devil",
                role="debater",
                content="ok",
                consensus_signal="agree",
            ),
        ]
        config = ProtocolConfig(
            termination=TerminationConfig(max_turns=10, min_turns=2)
        )
        assert DebateEngine.check_termination(turns, config) is False

    def test_turn_3_both_agree_terminates(self) -> None:
        """T4-5: min_turns=2, turn 3 both agree → terminates."""
        turns = [
            TurnRecord(
                turn_uuid="t-0",
                seq=0,
                profile="architect-devil",
                role="debater",
                content="ok",
                consensus_signal="agree",
            ),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="product-angel",
                role="debater",
                content="sure",
                consensus_signal="agree",
            ),
            TurnRecord(
                turn_uuid="t-2",
                seq=2,
                profile="architect-devil",
                role="debater",
                content="confirmed",
                consensus_signal="agree",
            ),
        ]
        config = ProtocolConfig(
            termination=TerminationConfig(max_turns=10, min_turns=2)
        )
        assert DebateEngine.check_termination(turns, config) is True


class TestT4TerminationMaxTurns:
    """T4-6: max_turns=4 reached → terminates."""

    def test_max_turns_reached(self) -> None:
        turns = [
            TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=f"p{i % 2}",
                role="debater",
                content="...",
            )
            for i in range(4)
        ]
        config = ProtocolConfig(
            termination=TerminationConfig(max_turns=4, min_turns=2)
        )
        assert DebateEngine.check_termination(turns, config) is True


class TestT4NoConsensusSignal:
    """T4-7: no consensus signal → null, no termination."""

    def test_no_signal_no_termination(self) -> None:
        turns = [
            TurnRecord(
                turn_uuid="t-0",
                seq=0,
                profile="architect-devil",
                role="debater",
                content="nope",
                consensus_signal=None,
            ),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="product-angel",
                role="debater",
                content="nah",
                consensus_signal=None,
            ),
        ]
        config = ProtocolConfig(
            termination=TerminationConfig(max_turns=10, min_turns=2)
        )
        assert DebateEngine.check_termination(turns, config) is False

    def test_parse_no_signal(self) -> None:
        content, signal, structured = DebateEngine.parse_output("just a normal reply")
        assert content == "just a normal reply"
        assert signal is None
        assert structured is None


class TestT4RunTurn:
    """Acceptance criteria for run_turn."""

    async def test_run_turn_returns_content(self, store: SessionStore) -> None:
        engine = DebateEngine(store=store, publisher=NoOpPublisher())
        backend = MockBackend(["I think this is a good idea."])
        result = await engine.run_turn("architect-devil", "하드코딩 프롬프트", backend)
        assert result.content is not None
        assert result.profile == "architect-devil"


class TestT4FullRun:
    """T4-8: 4 turns run → 4 turn_content events in history.jsonl."""

    async def test_four_turns_in_history(self, store: SessionStore) -> None:
        sid = store.create_session("topic", ["p1", "p2"], {})

        config = TopicConfig(
            participant_1="p1",
            participant_2="p2",
            protocol=ProtocolConfig(
                termination=TerminationConfig(max_turns=4, min_turns=2)
            ),
        )
        registry = DiscordRegistry(profiles={}, coordinator={})
        backend = MockBackend([f"response {i}" for i in range(4)])

        engine = DebateEngine(store, NoOpPublisher())
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
