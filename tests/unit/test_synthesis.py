"""Phase 8 acceptance criteria: Synthesis Step."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from parliament.agents.base import AgentBackend
from parliament.models import BackendResult, SynthesisResult, TurnRecord
from parliament.debate.synthesis import Synthesizer, _extract_json, _profile_exists


class MockBackend(AgentBackend):
    """Backend that returns pre-canned responses."""

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


class ErrorBackend(AgentBackend):
    """Backend that always returns an error."""

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
        return BackendResult(text="", code=1, error="backend failure")

    def cancel(self, handle: object) -> None:
        pass


@pytest.fixture
def schema() -> dict[str, Any]:
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


class TestT8Consensus:
    """T8-1: consensus history → consensus_reached: true, decision exists."""

    @pytest.mark.asyncio
    async def test_consensus_history(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        response = (
            "```json\n"
            '{\n'
            '  "decision": "모놀리스가 적합함",\n'
            '  "confidence": 0.85,\n'
            '  "reasoning": "모두 동의함",\n'
            '  "consensus_reached": true\n'
            '}\n'
            "```"
        )
        backend = MockBackend([response])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
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

        result = await synth.run("sid", "coordinator", history, schema)
        assert isinstance(result, SynthesisResult)
        assert result.consensus_reached is True
        assert result.decision == "모놀리스가 적합함"
        assert result.structured["consensus_reached"] is True


class TestT8Disagreement:
    """T8-2: disagreement history → consensus_reached: false, disagreeing_profiles."""

    @pytest.mark.asyncio
    async def test_disagreement_history(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        response = (
            "```json\n"
            '{\n'
            '  "decision": "미정",\n'
            '  "confidence": 0.3,\n'
            '  "reasoning": "의견이 갈림",\n'
            '  "consensus_reached": false,\n'
            '  "disagreeing_profiles": ["p2"]\n'
            '}\n'
            "```"
        )
        backend = MockBackend([response])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
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
                content="disagree",
                consensus_signal="continue",
            ),
        ]

        result = await synth.run("sid", "coordinator", history, schema)
        assert result.consensus_reached is False
        assert result.disagreeing_profiles == ["p2"]
        assert result.structured["consensus_reached"] is False
        assert result.structured["disagreeing_profiles"] == ["p2"]


class TestT8ParseFailure:
    """T8-3: JSON parse failure → retry 2x, then fallback JSON."""

    @pytest.mark.asyncio
    async def test_json_parse_failure_retries_then_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        backend = MockBackend(["not json", "still not json", "final failure"])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="p1",
                role="debater",
                content="content",
            ),
        ]

        result = await synth.run("sid", "coordinator", history, schema)
        assert result.consensus_reached is False
        assert result.decision == "inconclusive"
        assert result.confidence == 0.0
        assert len(backend.calls) == 3  # initial + 2 retries


class TestT8NoProfileCoordinatorExists:
    """T8-4: no synthesis profile, coordinator exists → uses coordinator."""

    @pytest.mark.asyncio
    async def test_uses_coordinator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        response = (
            "```json\n"
            '{\n'
            '  "decision": "test",\n'
            '  "confidence": 0.5,\n'
            '  "reasoning": "test",\n'
            '  "consensus_reached": true\n'
            '}\n'
            "```"
        )
        backend = MockBackend([response])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="p1",
                role="debater",
                content="content",
            ),
        ]

        result = await synth.run("sid", None, history, schema)
        assert result.consensus_reached is True
        assert backend.calls[0][0] == "coordinator"


class TestT8NoProfileNoCoordinator:
    """T8-5: no synthesis profile, no coordinator → rule-based fallback."""

    @pytest.mark.asyncio
    async def test_rule_based_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        backend = MockBackend([])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
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
                content="disagree",
                consensus_signal="continue",
            ),
        ]

        result = await synth.run("sid", None, history, schema)
        assert result.consensus_reached is False
        assert result.decision == "inconclusive"
        assert result.disagreeing_profiles == ["p2"]
        assert len(backend.calls) == 0  # no backend call


class TestT8ExtraFields:
    """T8-6: extra fields in response → ignored/stripped."""

    @pytest.mark.asyncio
    async def test_extra_fields_stripped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        response = (
            "```json\n"
            '{\n'
            '  "decision": "test",\n'
            '  "confidence": 0.5,\n'
            '  "reasoning": "test",\n'
            '  "consensus_reached": true,\n'
            '  "extra_field": "should be removed",\n'
            '  "another_extra": 123\n'
            '}\n'
            "```"
        )
        backend = MockBackend([response])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
            TurnRecord(
                turn_uuid="t-1",
                seq=1,
                profile="p1",
                role="debater",
                content="content",
            ),
        ]

        result = await synth.run("sid", "coordinator", history, schema)
        assert result.consensus_reached is True
        assert "extra_field" not in result.structured
        assert "another_extra" not in result.structured


class TestT8BackendError:
    """Backend error on every attempt → fallback JSON."""

    @pytest.mark.asyncio
    async def test_backend_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        schema: dict[str, Any],
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "coordinator"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("coordinator soul")

        backend = ErrorBackend()
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
        ]

        result = await synth.run("sid", "coordinator", history, schema)
        assert result.decision == "inconclusive"
        assert result.consensus_reached is False


class TestT8InvalidProfile:
    """Specified profile does not exist → error."""

    @pytest.mark.asyncio
    async def test_invalid_profile_raises(self, schema: dict[str, Any]) -> None:
        backend = MockBackend([])
        synth = Synthesizer(backend)

        history = [
            TurnRecord(
                turn_uuid="t-0", seq=0, profile="user", role="user", content="topic"
            ),
        ]

        with pytest.raises(ValueError, match="does not exist"):
            await synth.run("sid", "nonexistent-profile", history, schema)
