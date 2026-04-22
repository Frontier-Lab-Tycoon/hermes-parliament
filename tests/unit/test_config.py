"""Configuration validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from parliament.integrations.discord.registry import load_registry
from parliament.topics.config import load_topic


class TestConfigValidation:
    @pytest.fixture
    def empty_hermes_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
