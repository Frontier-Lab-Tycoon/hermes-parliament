"""Phase 2 acceptance criteria: Config & Validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from parliament.topics.config import load_topic
from parliament.integrations.discord.registry import load_registry


class TestT2ValidTopic:
    """T2-1: 유효한 topic.yaml 파싱."""

    def test_example_debate_yaml(self) -> None:
        cfg = load_topic("topics/example-debate.yaml")
        assert cfg.topic == "스타트업 초기 아키텍처: 모놀리스 vs 마이크로서비스"
        assert cfg.protocol.type == "debate"
        assert cfg.protocol.ordering == "alternating"
        assert cfg.protocol.termination.max_turns == 10
        assert cfg.protocol.termination.min_turns == 2
        assert cfg.synthesis.enabled is True
        assert cfg.synthesis.profile == "coordinator"

    def test_valid_topic_fixture(self) -> None:
        cfg = load_topic("tests/fixtures/valid-topic.yaml")
        assert cfg.topic == "Valid test topic"
        assert cfg.protocol.termination.max_turns == 8
        assert cfg.protocol.termination.min_turns == 2
        assert cfg.participant_1 == "user-a"
        assert cfg.participant_2 == "user-b"

    def test_minimal_topic_with_defaults(self) -> None:
        cfg = load_topic("tests/fixtures/minimal-topic.yaml")
        assert cfg.topic == "Minimal test"
        assert cfg.protocol.termination.min_turns == 2
        assert cfg.protocol.termination.max_turns == 10
        assert cfg.participant_1 == "user-a"
        assert cfg.participant_2 == "user-b"


class TestT2InvalidTurns:
    """T2-2: max_turns < min_turns raises ValidationError."""

    def test_max_turns_less_than_min_turns(self) -> None:
        with pytest.raises(ValidationError):
            load_topic("tests/fixtures/invalid-turns-topic.yaml")


class TestT2RegistryTokenSubstitution:
    """T2-3: discord-registry.yaml with ${TOKEN} substitution."""

    def test_token_substitution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "architect-devil"
        profile_dir.mkdir(parents=True)

        monkeypatch.setenv("DEVIL_BOT_TOKEN", "devil-token-123")
        monkeypatch.setenv("COORDINATOR_BOT_TOKEN", "coordinator-token-456")

        registry = load_registry("tests/fixtures/discord-registry.yaml")
        profile = registry.resolve_profile("123456789")
        assert profile.hermes_profile == "architect-devil"
        assert profile.discord_bot_token == "devil-token-123"
        assert profile.discord_name == "악마의 대변인"
        assert registry.coordinator["bot_token"] == "coordinator-token-456"


class TestT2NonExistentProfile:
    """T2-4: non-existent Hermes profile raises error."""

    def test_missing_profile_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        with pytest.raises(FileNotFoundError):
            load_registry("tests/fixtures/invalid-profile-registry.yaml")


class TestT2SameParticipants:
    """T2-5: participant_1 == participant_2 raises ValidationError."""

    def test_same_participants(self) -> None:
        with pytest.raises(ValidationError):
            load_topic("tests/fixtures/same-participants-topic.yaml")
