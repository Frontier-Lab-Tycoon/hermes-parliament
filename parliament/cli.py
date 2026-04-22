"""Parliament CLI entrypoint."""

import os
from pathlib import Path

import click

from parliament import __version__


@click.group()
@click.version_option(version=__version__, prog_name="parliament")
def main() -> None:
    """Hermes Parliament — Multi-Agent Turn-Based Orchestrator."""


@main.command()
def list() -> None:
    """List active sessions."""
    # Phase 0: no sessions yet
    click.echo("[]")


@main.command(name="run-bot")
@click.option(
    "--registry",
    default=None,
    help="Path to discord-registry.yaml",
)
@click.option(
    "--topic",
    default=None,
    help="Path to default topic configuration file",
)
def run_bot(registry: str | None, topic: str | None) -> None:
    """Start the Discord coordinator bot."""
    from parliament.integrations.discord.bot import ParliamentBot
    from parliament.integrations.discord.registry import load_registry

    registry_path = registry or str(
        Path.home() / ".parliament" / "discord-registry.yaml"
    )

    # Try environment first, then registry
    token = os.environ.get("COORDINATOR_BOT_TOKEN")
    if not token:
        reg = load_registry(registry_path)
        token = reg.coordinator.get("bot_token")

    if not token:
        raise click.ClickException(
            "Coordinator bot token not found. Set COORDINATOR_BOT_TOKEN environment variable "
            "or define coordinator.bot_token in the registry."
        )

    bot = ParliamentBot(registry_path=registry_path, default_topic_path=topic)
    bot.run(token)


if __name__ == "__main__":
    main()
