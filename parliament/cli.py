"""Parliament CLI entrypoint."""

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


if __name__ == "__main__":
    main()
