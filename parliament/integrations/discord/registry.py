"""Discord bot configuration: maps Discord user IDs to Hermes profiles."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from parliament.hermes_index import HermesBotEntry, HermesIndex

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute ${VAR} placeholders with environment variables."""
    if isinstance(value, str):

        def _replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                raise ValueError(f"Environment variable {var_name} not set")
            return env_value

        return _ENV_PATTERN.sub(_replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(v) for v in value]
    return value


@dataclass(frozen=True)
class HermesProfile:
    hermes_profile: str
    discord_bot_token: str
    discord_user_id: str
    discord_name: str | None = None
    avatar_url: str | None = None


def _entry_to_profile(entry: HermesBotEntry) -> HermesProfile:
    return HermesProfile(
        hermes_profile=entry.hermes_profile,
        discord_bot_token=entry.discord_bot_token,
        discord_user_id=entry.discord_user_id,
        discord_name=entry.discord_name,
    )


class DiscordRegistry:
    def __init__(
        self,
        profiles: dict[str, HermesProfile],
        coordinator: dict[str, Any],
        hermes_index: HermesIndex | None = None,
    ) -> None:
        self._profiles = profiles
        self.coordinator = coordinator
        self._hermes_index = hermes_index

    def resolve_profile(self, discord_user_id: str) -> HermesProfile:
        if discord_user_id in self._profiles:
            return self._profiles[discord_user_id]
        if self._hermes_index is not None:
            try:
                entry = self._hermes_index.resolve_by_user_id(discord_user_id)
            except KeyError:
                pass
            else:
                profile = _entry_to_profile(entry)
                self._profiles[discord_user_id] = profile
                return profile
        raise KeyError(
            f"No profile found for discord user id: {discord_user_id}"
        )

    def resolve_by_hermes_profile(self, hermes_profile: str) -> HermesProfile:
        for profile in self._profiles.values():
            if profile.hermes_profile == hermes_profile:
                return profile
        if self._hermes_index is not None:
            try:
                entry = self._hermes_index.resolve_by_profile(hermes_profile)
            except KeyError:
                pass
            else:
                profile = _entry_to_profile(entry)
                self._profiles[profile.discord_user_id] = profile
                return profile
        raise KeyError(
            f"No profile found for hermes profile: {hermes_profile}"
        )

    def list_profiles(self) -> list[HermesProfile]:
        static = list(self._profiles.values())
        if self._hermes_index is not None:
            seen_ids = {p.discord_user_id for p in static}
            for entry in self._hermes_index.list_profiles():
                if entry.discord_user_id not in seen_ids:
                    static.append(_entry_to_profile(entry))
        return sorted(static, key=lambda profile: profile.hermes_profile)


def load_registry(
    path: str,
    hermes_index: HermesIndex | None = None,
) -> DiscordRegistry:
    raw_data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = _substitute_env_vars(raw_data)

    raw_profiles = data.get("profiles") or {}
    coordinator = data.get("parliament_application") or data.get("coordinator", {})

    coord_token = coordinator.get("bot_token")
    if coord_token is not None and not coord_token:
        raise ValueError("Parliament bot token is empty after substitution")

    profiles: dict[str, HermesProfile] = {}
    for name, info in raw_profiles.items():
        hermes_profile = info.get("hermes_profile", name)
        discord_user_id = info.get("discord_user_id", "")

        if not discord_user_id:
            raise ValueError(f"discord_user_id missing for profile {name}")

        token = info.get("discord_bot_token", "")
        if not token:
            raise ValueError(
                f"Discord bot token for profile {name} is empty after substitution"
            )

        if hermes_profile != "default":
            profile_path = Path.home() / ".hermes" / "profiles" / hermes_profile
            if not profile_path.exists():
                raise FileNotFoundError(f"Hermes profile not found: {profile_path}")

        profiles[discord_user_id] = HermesProfile(
            hermes_profile=hermes_profile,
            discord_bot_token=token,
            discord_user_id=discord_user_id,
            discord_name=info.get("discord_name"),
            avatar_url=info.get("avatar_url"),
        )

    if hermes_index is None:
        hermes_index = HermesIndex()

    return DiscordRegistry(
        profiles=profiles,
        coordinator=coordinator,
        hermes_index=hermes_index,
    )
