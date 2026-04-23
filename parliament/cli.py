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
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": f"DiscordBot (https://github.com/lablup/hermes-parliament, {__version__})",
        },
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


def _write_coordinator_config(
    path: Path,
    bot_token: str,
    channel_id: str | None = None,
    identity: dict[str, str] | None = None,
) -> None:
    parliament_application: dict[str, str] = {"bot_token": bot_token}
    if channel_id:
        parliament_application["channel_id"] = channel_id
    if identity:
        parliament_application["discord_user_id"] = identity["id"]
        parliament_application["discord_name"] = identity["username"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"parliament_application": parliament_application},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _run_discord_bot(
    bot_config_path: Path,
    topic: str | None,
    sync_commands: bool,
    sync_guild_id: str | None = None,
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

    if not sync_guild_id:
        sync_guild_id = os.environ.get("PARLIAMENT_GUILD_ID")

    bot = ParliamentBot(
        registry_path=str(bot_config_path),
        default_topic_path=topic,
        sync_commands=sync_commands,
        sync_guild_id=sync_guild_id,
    )
    bot.run(token)


@main.command()
@click.option(
    "--bot-config",
    "bot_config",
    default=None,
    help="Path to bots.yaml (default: ~/.parliament/bots.yaml).",
)
@click.option(
    "--coordinator-token",
    default=None,
    help="Parliament (coordinator) Discord bot token. If omitted, reads "
    "PARLIAMENT_BOT_TOKEN or prompts interactively.",
)
@click.option(
    "--channel-id",
    default=None,
    help="Optional default Discord channel ID for debates.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing bots.yaml.",
)
@click.option(
    "--no-verify",
    is_flag=True,
    help="Skip Discord /users/@me verification of the coordinator token.",
)
def init(
    bot_config: str | None,
    coordinator_token: str | None,
    channel_id: str | None,
    force: bool,
    no_verify: bool,
) -> None:
    """Initialize Parliament: write coordinator config to bots.yaml.

    Agents are resolved dynamically from Hermes profiles
    (~/.hermes/profiles/<name>/.env) at /discuss time — no agent pre-registration
    is required.
    """
    bot_config_path = Path(bot_config) if bot_config else _default_bot_config_path()
    if bot_config_path.exists() and not force:
        raise click.ClickException(
            f"{bot_config_path} already exists. Re-run with --force to overwrite."
        )

    if not coordinator_token:
        coordinator_token, _ = _parliament_bot_token()
    if not coordinator_token:
        coordinator_token = click.prompt(
            "Parliament (coordinator) Discord bot token",
            hide_input=True,
        )
    coordinator_token = (coordinator_token or "").strip()
    if not coordinator_token:
        raise click.ClickException("Coordinator token is required")

    if not channel_id and os.environ.get("PARLIAMENT_CHANNEL_ID"):
        channel_id = os.environ["PARLIAMENT_CHANNEL_ID"]

    identity: dict[str, str] | None = None
    if not no_verify:
        identity = _fetch_discord_bot_identity(coordinator_token)
        click.echo(
            f"Verified coordinator bot: {identity['username']} ({identity['id']})"
        )

    _write_coordinator_config(
        bot_config_path,
        coordinator_token,
        channel_id=channel_id,
        identity=identity,
    )
    click.echo(f"Wrote {bot_config_path}")
    click.echo("Next: `parliament start` to launch the bot.")


@main.command()
def list() -> None:
    """List active sessions."""
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
    help="Path to bots.yaml.",
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
    "--sync-commands/--no-sync-commands",
    default=True,
    help="Sync Discord slash commands on startup.",
)
@click.option(
    "--sync-guild",
    "sync_guild_id",
    default=None,
    help="Sync slash commands to this guild ID instead of globally. "
    "Guild-scoped commands propagate instantly; global commands can take "
    "up to 1 hour. Can also be set via PARLIAMENT_GUILD_ID.",
)
def start(
    bot_config: str | None,
    topic: str | None,
    sync_commands: bool,
    sync_guild_id: str | None,
) -> None:
    """Start the Parliament Discord bot."""
    bot_config_path = Path(bot_config) if bot_config else _default_bot_config_path()
    if not bot_config_path.exists():
        raise click.ClickException(
            f"{bot_config_path} not found. Run `parliament init` first."
        )
    _run_discord_bot(bot_config_path, topic, sync_commands, sync_guild_id)


if __name__ == "__main__":
    main()
