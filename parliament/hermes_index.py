"""Hermes profile ↔ Discord bot index.

Dynamically resolves which Hermes profile owns a given Discord bot user_id by
scanning ``~/.hermes/.env`` and ``~/.hermes/profiles/<name>/.env`` for
``DISCORD_BOT_TOKEN`` and verifying each token against Discord ``/users/@me``.
Results are cached at ``~/.parliament/hermes-index.json`` and reused when the
token fingerprint is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import urllib.error
import urllib.request


@dataclass(frozen=True)
class HermesBotEntry:
    hermes_profile: str
    discord_bot_token: str
    discord_user_id: str
    discord_name: str


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


_USER_AGENT = "DiscordBot (https://github.com/lablup/hermes-parliament, 0.1)"


def _fetch_discord_identity(token: str) -> tuple[str, str]:
    request = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}", "User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    bot_id = data.get("id")
    if not bot_id:
        raise RuntimeError("Discord identity response missing id")
    return bot_id, data.get("username") or bot_id


def _iter_hermes_profile_envs(hermes_root: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    default_env = hermes_root / ".env"
    if default_env.exists():
        entries.append(("default", default_env))
    profiles_dir = hermes_root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if not child.is_dir():
                continue
            env_path = child / ".env"
            if env_path.exists():
                entries.append((child.name, env_path))
    return entries


class HermesIndex:
    """Resolves Hermes profiles by Discord bot user_id or profile name."""

    def __init__(
        self,
        hermes_root: Path | None = None,
        cache_path: Path | None = None,
        identity_fetcher: Callable[[str], tuple[str, str]] | None = None,
    ) -> None:
        self.hermes_root = hermes_root or Path.home() / ".hermes"
        self.cache_path = (
            cache_path or Path.home() / ".parliament" / "hermes-index.json"
        )
        self._identity_fetcher = identity_fetcher or _fetch_discord_identity
        self._by_user_id: dict[str, HermesBotEntry] = {}
        self._by_profile: dict[str, HermesBotEntry] = {}
        self._loaded = False

    def resolve_by_user_id(self, user_id: str) -> HermesBotEntry:
        if not self._loaded:
            self.refresh()
        if user_id in self._by_user_id:
            return self._by_user_id[user_id]
        self.refresh()
        if user_id in self._by_user_id:
            return self._by_user_id[user_id]
        raise KeyError(f"No hermes-paired bot for discord user_id={user_id}")

    def resolve_by_profile(self, profile: str) -> HermesBotEntry:
        if not self._loaded:
            self.refresh()
        if profile in self._by_profile:
            return self._by_profile[profile]
        self.refresh()
        if profile in self._by_profile:
            return self._by_profile[profile]
        raise KeyError(f"No hermes profile paired with discord: {profile}")

    def list_profiles(self) -> list[HermesBotEntry]:
        if not self._loaded:
            self.refresh()
        return sorted(self._by_profile.values(), key=lambda e: e.hermes_profile)

    def refresh(self) -> None:
        cache = self._load_cache()
        new_cache: dict[str, dict[str, str]] = {}
        by_user_id: dict[str, HermesBotEntry] = {}
        by_profile: dict[str, HermesBotEntry] = {}

        for profile_name, env_path in _iter_hermes_profile_envs(self.hermes_root):
            token = _parse_env_file(env_path).get("DISCORD_BOT_TOKEN", "").strip()
            if not token:
                continue

            fp = _token_fingerprint(token)
            cached = cache.get(profile_name)
            if cached and cached.get("token_fingerprint") == fp:
                user_id = cached["discord_user_id"]
                name = cached.get("discord_name") or user_id
            else:
                try:
                    user_id, name = self._identity_fetcher(token)
                except (urllib.error.HTTPError, urllib.error.URLError, OSError, RuntimeError):
                    continue

            entry = HermesBotEntry(
                hermes_profile=profile_name,
                discord_bot_token=token,
                discord_user_id=user_id,
                discord_name=name,
            )
            by_user_id[user_id] = entry
            by_profile[profile_name] = entry
            new_cache[profile_name] = {
                "token_fingerprint": fp,
                "discord_user_id": user_id,
                "discord_name": name,
            }

        self._by_user_id = by_user_id
        self._by_profile = by_profile
        self._loaded = True
        self._save_cache(new_cache)

    def _load_cache(self) -> dict[str, dict[str, str]]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save_cache(self, cache: dict[str, dict[str, str]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        os.replace(tmp, self.cache_path)
        try:
            os.chmod(self.cache_path, 0o600)
        except OSError:
            pass
