"""Synthesis tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from parliament.agents.base import AgentBackend
from parliament.debate.synthesis import Synthesizer
from parliament.models import BackendResult, TurnRecord


class MockBackend(AgentBackend):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.call_index = 0
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        self.calls.append((profile, prompt))
        text = self.responses[self.call_index]
        self.call_index += 1
        return BackendResult(text=text, code=0, error=None)

    def cancel(self, handle: object) -> None:
        pass


class TestSynthesizer:
    @pytest.fixture
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
                "consensus_reached": {"type": "boolean"},
                "disagreeing_profiles": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["decision", "confidence", "reasoning", "consensus_reached"],
        }

    @pytest.fixture
    def history(self) -> list[TurnRecord]:
        return [
            TurnRecord(turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="p1",
                role="debater",
                content="agree",
                consensus_signal="agree",
            ),
            TurnRecord(
                turn_uuid="t-2",
                seq=2,
                profile="p2",
                role="debater",
                content="agree too",
                consensus_signal="agree",
            ),
        ]

    @pytest.fixture
    def coordinator_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        fake_home = tmp_path / "home"
        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    @pytest.fixture
    def empty_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    @pytest.fixture
    def successful_backend(self) -> MockBackend:
        response = (
            "```json\n"
            "{\n"
            '  "decision": "모놀리스",\n'
            '  "confidence": 0.85,\n'
            '  "reasoning": "모두 동의함",\n'
            '  "consensus_reached": true\n'
            "}\n"
            "```"
        )
        return MockBackend([response])

    @pytest.fixture
    def parse_failure_backend(self) -> MockBackend:
        return MockBackend(["not json", "still not json", "final failure"])

    async def test_successful_synthesis_returns_structured_decision(
        self,
        coordinator_home: Path,
        schema: dict[str, Any],
        history: list[TurnRecord],
        successful_backend: MockBackend,
    ) -> None:
        result = await Synthesizer(successful_backend).run("sid", "coordinator", history, schema)

        assert result.decision == "모놀리스"
        assert result.consensus_reached is True
        assert result.structured["confidence"] == 0.85

    async def test_parse_failures_retry_then_fallback(
        self,
        coordinator_home: Path,
        schema: dict[str, Any],
        history: list[TurnRecord],
        parse_failure_backend: MockBackend,
    ) -> None:
        result = await Synthesizer(parse_failure_backend).run("sid", "coordinator", history, schema)

        assert result.decision == "inconclusive"
        assert result.consensus_reached is True
        assert len(parse_failure_backend.calls) == 3

    async def test_no_coordinator_profile_uses_rule_based_fallback(
        self,
        empty_home: Path,
        schema: dict[str, Any],
        history: list[TurnRecord],
    ) -> None:
        backend = MockBackend([])

        result = await Synthesizer(backend).run("sid", None, history, schema)

        assert result.decision == "inconclusive"
        assert result.consensus_reached is True
        assert len(backend.calls) == 0

    async def test_invalid_synthesis_profile_raises(self, schema: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            await Synthesizer(MockBackend([])).run(
                "sid",
                "nonexistent-profile",
                [
                    TurnRecord(
                        turn_uuid="t-0",
                        seq=0,
                        profile="user",
                        role="user",
                        content="topic",
                    )
                ],
                schema,
            )
