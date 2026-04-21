"""Discord Bot Registry: maps Discord user IDs to Hermes profiles."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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


class DiscordRegistry:
    def __init__(
        self,
        profiles: dict[str, HermesProfile],
        coordinator: dict[str, Any],
    ) -> None:
        self._profiles = profiles
        self.coordinator = coordinator

    def resolve_profile(self, discord_user_id: str) -> HermesProfile:
        if discord_user_id not in self._profiles:
            raise KeyError(
                f"No profile found for discord user id: {discord_user_id}"
            )
        return self._profiles[discord_user_id]


def load_registry(path: str) -> DiscordRegistry:
    raw_data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    data = _substitute_env_vars(raw_data)

    raw_profiles = data.get("profiles", {})
    coordinator = data.get("coordinator", {})

    # Validate coordinator token if present
    coord_token = coordinator.get("bot_token")
    if coord_token is not None and not coord_token:
        raise ValueError("Coordinator bot token is empty after substitution")

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

        # Validate Hermes profile path exists
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

    return DiscordRegistry(profiles=profiles, coordinator=coordinator)
