"""Structured logging configuration with Rich console output."""

import logging

import structlog
from rich.console import Console
from rich.logging import RichHandler

_console = Console(stderr=True)


def configure_logging() -> None:
    """Configure structlog and stdlib logging for Parliament."""
    # Standard library logging → Rich
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=[RichHandler(console=_console, show_path=False)],
        force=True,
    )

    # structlog processors
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
