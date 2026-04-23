"""Dynamic resolution tests for DiscordRegistry via HermesIndex."""

from __future__ import annotations

from pathlib import Path

import pytest

from parliament.hermes_index import HermesIndex
from parliament.integrations.discord.registry import DiscordRegistry


def _write_env(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"DISCORD_BOT_TOKEN={token}\n", encoding="utf-8")


@pytest.fixture
def hermes_index(tmp_path: Path) -> HermesIndex:
    hermes_root = tmp_path / "hermes"
    _write_env(hermes_root / "profiles" / "architect-devil" / ".env", "devil-tok")
    _write_env(hermes_root / "profiles" / "architect-angel" / ".env", "angel-tok")

    identities = {
        "devil-tok": ("200", "DevilBot"),
        "angel-tok": ("300", "AngelBot"),
    }
    return HermesIndex(
        hermes_root=hermes_root,
        cache_path=tmp_path / "parliament" / "hermes-index.json",
        identity_fetcher=lambda t: identities[t],
    )


def test_resolve_profile_falls_back_to_hermes_index(
    hermes_index: HermesIndex,
) -> None:
    registry = DiscordRegistry(
        profiles={},
        coordinator={"bot_token": "coord"},
        hermes_index=hermes_index,
    )
    profile = registry.resolve_profile("200")
    assert profile.hermes_profile == "architect-devil"
    assert profile.discord_bot_token == "devil-tok"


def test_resolve_by_hermes_profile_falls_back_to_index(
    hermes_index: HermesIndex,
) -> None:
    registry = DiscordRegistry(
        profiles={},
        coordinator={"bot_token": "coord"},
        hermes_index=hermes_index,
    )
    profile = registry.resolve_by_hermes_profile("architect-angel")
    assert profile.discord_user_id == "300"


def test_resolve_raises_when_not_in_index_or_static(
    hermes_index: HermesIndex,
) -> None:
    registry = DiscordRegistry(
        profiles={},
        coordinator={"bot_token": "coord"},
        hermes_index=hermes_index,
    )
    with pytest.raises(KeyError):
        registry.resolve_profile("999")


def test_static_profiles_take_precedence(hermes_index: HermesIndex) -> None:
    from parliament.integrations.discord.registry import HermesProfile

    static = HermesProfile(
        hermes_profile="architect-devil",
        discord_bot_token="static-override",
        discord_user_id="200",
    )
    registry = DiscordRegistry(
        profiles={"200": static},
        coordinator={"bot_token": "coord"},
        hermes_index=hermes_index,
    )
    profile = registry.resolve_profile("200")
    assert profile.discord_bot_token == "static-override"
