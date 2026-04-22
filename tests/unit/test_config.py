"""Configuration validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from parliament.integrations.discord.registry import load_registry
from parliament.topics.config import load_topic


class TestConfigValidation:
    @pytest.fixture
    def empty_hermes_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Path:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    async def test_rejects_invalid_turn_bounds(self) -> None:
        with pytest.raises(ValidationError):
            load_topic("tests/fixtures/invalid-turns-topic.yaml")

    async def test_rejects_same_participants(self) -> None:
        with pytest.raises(ValidationError):
            load_topic("tests/fixtures/same-participants-topic.yaml")

    async def test_rejects_registry_profile_without_hermes_profile_dir(
        self, empty_hermes_home: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            load_registry("tests/fixtures/invalid-profile-registry.yaml")

    async def test_loads_parliament_application_bot_config(
        self, monkeypatch: pytest.MonkeyPatch, empty_hermes_home: Path
    ) -> None:
        profile_dir = empty_hermes_home / ".hermes" / "profiles" / "architect-devil"
        profile_dir.mkdir(parents=True)

        bot_config = empty_hermes_home / ".parliament" / "bots.yaml"
        bot_config.parent.mkdir()
        bot_config.write_text(
            """
profiles:
  architect-devil:
    hermes_profile: "architect-devil"
    discord_bot_token: "${DEVIL_BOT_TOKEN}"
    discord_user_id: "123456789"
parliament_application:
  bot_token: "${PARLIAMENT_BOT_TOKEN}"
  channel_id: "999999999"
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("DEVIL_BOT_TOKEN", "devil-token")
        monkeypatch.setenv("PARLIAMENT_BOT_TOKEN", "parliament-token")

        config = load_registry(str(bot_config))

        assert config.coordinator["bot_token"] == "parliament-token"
        assert config.coordinator["channel_id"] == "999999999"
        assert (
            config.resolve_profile("123456789").discord_bot_token
            == "devil-token"
        )
