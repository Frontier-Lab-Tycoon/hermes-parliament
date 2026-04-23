"""Tests for parliament.hermes_index."""

from __future__ import annotations

from pathlib import Path

from parliament.hermes_index import HermesIndex, _parse_env_file


def _write_env(path: Path, lines: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in lines.items()), encoding="utf-8"
    )


class TestParseEnvFile:
    def test_basic_kv(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("FOO=bar\nBAZ=qux\n")
        assert _parse_env_file(env) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_quotes_and_comments(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text('# comment\nA="quoted"\nB=\'single\'\n')
        assert _parse_env_file(env) == {"A": "quoted", "B": "single"}

    def test_export_prefix(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("export DISCORD_BOT_TOKEN=abc123\n")
        assert _parse_env_file(env) == {"DISCORD_BOT_TOKEN": "abc123"}

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _parse_env_file(tmp_path / "nope.env") == {}


class TestHermesIndex:
    def _make_hermes_layout(self, root: Path) -> None:
        _write_env(root / ".env", {"DISCORD_BOT_TOKEN": "default-token"})
        _write_env(
            root / "profiles" / "architect-devil" / ".env",
            {"DISCORD_BOT_TOKEN": "devil-token"},
        )
        _write_env(
            root / "profiles" / "architect-angel" / ".env",
            {"DISCORD_BOT_TOKEN": "angel-token"},
        )
        # Profile without a DISCORD_BOT_TOKEN is skipped:
        _write_env(
            root / "profiles" / "no-discord" / ".env",
            {"OTHER_KEY": "x"},
        )

    def test_scans_all_profiles_and_resolves_by_user_id(
        self, tmp_path: Path
    ) -> None:
        hermes_root = tmp_path / "hermes"
        cache_path = tmp_path / "parliament" / "hermes-index.json"
        self._make_hermes_layout(hermes_root)

        fake_identities = {
            "default-token": ("100", "CoordinatorBot"),
            "devil-token": ("200", "DevilBot"),
            "angel-token": ("300", "AngelBot"),
        }

        calls: list[str] = []

        def fetch(token: str) -> tuple[str, str]:
            calls.append(token)
            return fake_identities[token]

        index = HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=fetch,
        )

        entry = index.resolve_by_user_id("200")
        assert entry.hermes_profile == "architect-devil"
        assert entry.discord_bot_token == "devil-token"
        assert entry.discord_name == "DevilBot"

        by_profile = index.resolve_by_profile("architect-angel")
        assert by_profile.discord_user_id == "300"

        # All three valid tokens were verified exactly once.
        assert sorted(calls) == ["angel-token", "default-token", "devil-token"]

    def test_cache_avoids_network_on_unchanged_tokens(
        self, tmp_path: Path
    ) -> None:
        hermes_root = tmp_path / "hermes"
        cache_path = tmp_path / "parliament" / "hermes-index.json"
        self._make_hermes_layout(hermes_root)

        fake_identities = {
            "default-token": ("100", "CoordinatorBot"),
            "devil-token": ("200", "DevilBot"),
            "angel-token": ("300", "AngelBot"),
        }

        def fetch(token: str) -> tuple[str, str]:
            return fake_identities[token]

        first = HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=fetch,
        )
        first.refresh()

        # Second index shares the cache path.
        calls: list[str] = []

        def exploding_fetch(token: str) -> tuple[str, str]:
            calls.append(token)
            return fake_identities[token]

        second = HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=exploding_fetch,
        )
        second.refresh()
        assert calls == []  # fully served from cache
        assert second.resolve_by_user_id("300").hermes_profile == "architect-angel"

    def test_cache_miss_on_rotated_token(self, tmp_path: Path) -> None:
        hermes_root = tmp_path / "hermes"
        cache_path = tmp_path / "parliament" / "hermes-index.json"
        self._make_hermes_layout(hermes_root)

        identities = {
            "default-token": ("100", "Coord"),
            "devil-token": ("200", "Devil"),
            "angel-token": ("300", "Angel"),
        }

        HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=lambda t: identities[t],
        ).refresh()

        # Rotate devil token.
        _write_env(
            hermes_root / "profiles" / "architect-devil" / ".env",
            {"DISCORD_BOT_TOKEN": "devil-token-v2"},
        )
        identities["devil-token-v2"] = ("201", "DevilV2")

        calls: list[str] = []

        def fetch(t: str) -> tuple[str, str]:
            calls.append(t)
            return identities[t]

        index = HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=fetch,
        )
        index.refresh()
        # Only the rotated token triggers a network call.
        assert calls == ["devil-token-v2"]
        assert index.resolve_by_user_id("201").hermes_profile == "architect-devil"

    def test_unauthorized_token_is_skipped(self, tmp_path: Path) -> None:
        hermes_root = tmp_path / "hermes"
        cache_path = tmp_path / "parliament" / "hermes-index.json"
        _write_env(
            hermes_root / "profiles" / "good" / ".env",
            {"DISCORD_BOT_TOKEN": "good"},
        )
        _write_env(
            hermes_root / "profiles" / "bad" / ".env",
            {"DISCORD_BOT_TOKEN": "bad"},
        )

        def fetch(token: str) -> tuple[str, str]:
            if token == "bad":
                raise RuntimeError("401")
            return ("1", "Good")

        index = HermesIndex(
            hermes_root=hermes_root,
            cache_path=cache_path,
            identity_fetcher=fetch,
        )
        profiles = {e.hermes_profile for e in index.list_profiles()}
        assert profiles == {"good"}
