"""Parliament CLI entrypoint."""

import json
import os
from pathlib import Path
import urllib.error
import urllib.request

import click
import yaml

from parliament import __version__


@click.group()
@click.version_option(version=__version__, prog_name="parliament")
def main() -> None:
    """Hermes Parliament — Multi-Agent Turn-Based Orchestrator."""


def _default_bot_config_path() -> Path:
    config_dir = Path.home() / ".parliament"
    preferred = config_dir / "bots.yaml"
    legacy = config_dir / "discord-registry.yaml"
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def _parliament_bot_token() -> tuple[str | None, str]:
    if os.environ.get("PARLIAMENT_BOT_TOKEN"):
        return os.environ["PARLIAMENT_BOT_TOKEN"], "PARLIAMENT_BOT_TOKEN"
    if os.environ.get("COORDINATOR_BOT_TOKEN"):
        return os.environ["COORDINATOR_BOT_TOKEN"], "COORDINATOR_BOT_TOKEN"
    return None, "PARLIAMENT_BOT_TOKEN"


def _fetch_discord_bot_identity(token: str) -> dict[str, str]:
    request = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise click.ClickException(
            f"Discord bot identity lookup failed: HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise click.ClickException(
            f"Discord bot identity lookup failed: {exc.reason}"
        ) from exc

    bot_id = data.get("id")
    username = data.get("username")
    if not bot_id:
        raise click.ClickException("Discord bot identity response did not include id")
    return {"id": bot_id, "username": username or bot_id}


def _resolve_token_reference(value: str) -> tuple[str, str]:
    value = value.strip()
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
    elif value in os.environ:
        env_name = value
    else:
        env_name = ""

    if env_name:
        token = os.environ.get(env_name)
        if not token:
            raise click.ClickException(f"Environment variable {env_name} is empty")
        return token, f"${{{env_name}}}"

    return value, value


def _parse_agent_specs(value: str) -> list[tuple[str, str, str]]:
    specs: list[tuple[str, str, str]] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue

        separator = "=" if "=" in item else ":"
        if separator not in item:
            raise click.ClickException(
                "PARLIAMENT_AGENTS must use profile=TOKEN_ENV format"
            )

        profile, token_ref = item.split(separator, 1)
        profile = profile.strip()
        token_ref = token_ref.strip()
        if not profile or not token_ref:
            raise click.ClickException(
                "PARLIAMENT_AGENTS entries must include both profile and token"
            )

        token, stored_token = _resolve_token_reference(token_ref)
        specs.append((profile, token, stored_token))

    if not specs:
        raise click.ClickException("PARLIAMENT_AGENTS did not include any agents")
    return specs


def _ensure_bot_config(path: Path, force: bool = False) -> Path:
    if path.exists() and not force:
        return path

    parliament_token, parliament_token_env = _parliament_bot_token()
    if not parliament_token:
        raise click.ClickException(
            "PARLIAMENT_BOT_TOKEN is required to auto-create the bot config"
        )

    agents = os.environ.get("PARLIAMENT_AGENTS")
    if not agents:
        raise click.ClickException(
            "PARLIAMENT_AGENTS is required when the bot config does not exist. "
            "Example: architect-devil=DEVIL_BOT_TOKEN,architect-angel=ANGEL_BOT_TOKEN"
        )

    profiles: dict[str, dict[str, str]] = {}
    for hermes_profile, token, stored_token in _parse_agent_specs(agents):
        identity = _fetch_discord_bot_identity(token)
        profiles[hermes_profile] = {
            "hermes_profile": hermes_profile,
            "discord_bot_token": stored_token,
            "discord_user_id": identity["id"],
            "discord_name": identity["username"],
        }

    parliament_application: dict[str, str] = {
        "bot_token": f"${{{parliament_token_env}}}"
    }
    channel_id = os.environ.get("PARLIAMENT_CHANNEL_ID")
    if channel_id:
        parliament_application["channel_id"] = channel_id

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "profiles": profiles,
                "parliament_application": parliament_application,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    click.echo(f"Created bot config: {path}")
    return path


def _run_discord_bot(
    bot_config_path: Path,
    topic: str | None,
    sync_commands: bool,
) -> None:
    from parliament.integrations.discord.bot import ParliamentBot
    from parliament.integrations.discord.registry import load_registry

    token, _ = _parliament_bot_token()
    if not token:
        reg = load_registry(str(bot_config_path))
        token = reg.coordinator.get("bot_token")

    if not token:
        raise click.ClickException(
            "Parliament bot token not found. Set PARLIAMENT_BOT_TOKEN or define "
            "parliament_application.bot_token in the bot config."
        )

    bot = ParliamentBot(
        registry_path=str(bot_config_path),
        default_topic_path=topic,
        sync_commands=sync_commands,
    )
    bot.run(token)


@main.command()
def list() -> None:
    """List active sessions."""
    # Phase 0: no sessions yet
    click.echo("[]")


@main.command(name="run-bot")
@click.option(
    "--bot-config",
    "bot_config",
    default=None,
    help="Path to bots.yaml",
)
@click.option(
    "--registry",
    "bot_config",
    default=None,
    hidden=True,
)
@click.option(
    "--topic",
    default=None,
    help="Path to default topic configuration file",
)
@click.option(
    "--sync-commands/--no-sync-commands",
    default=True,
    help="Sync Discord slash commands on startup.",
)
def run_bot(bot_config: str | None, topic: str | None, sync_commands: bool) -> None:
    """Start the Parliament Discord bot."""
    bot_config_path = Path(bot_config) if bot_config else _default_bot_config_path()
    _run_discord_bot(bot_config_path, topic, sync_commands)


@main.command()
@click.option(
    "--bot-config",
    "bot_config",
    default=None,
    help="Path to bots.yaml. Auto-created when missing.",
)
@click.option(
    "--registry",
    "bot_config",
    default=None,
    hidden=True,
)
@click.option(
    "--topic",
    default=None,
    help="Path to default topic configuration file.",
)
@click.option(
    "--force-bot-config",
    is_flag=True,
    help="Recreate the bot config from environment variables before starting.",
)
@click.option(
    "--sync-commands/--no-sync-commands",
    default=True,
    help="Sync Discord slash commands on startup.",
)
def start(
    bot_config: str | None,
    topic: str | None,
    force_bot_config: bool,
    sync_commands: bool,
) -> None:
    """Bootstrap configuration if needed, then start the Parliament Discord bot."""
    bot_config_path = Path(bot_config) if bot_config else _default_bot_config_path()
    bot_config_path = _ensure_bot_config(bot_config_path, force=force_bot_config)
    _run_discord_bot(bot_config_path, topic, sync_commands)


if __name__ == "__main__":
    main()
