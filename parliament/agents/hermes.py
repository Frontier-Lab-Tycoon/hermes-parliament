"""Hermes CLI backend implementation."""

from __future__ import annotations

import asyncio
import re
import time

import structlog

from parliament.models import BackendResult

from .base import AgentBackend, BackendTimeoutError

logger = structlog.get_logger()

# Comprehensive ANSI escape sequence matcher
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Lines emitted by ``hermes chat --quiet`` that are NOT part of the model reply.
_SESSION_ID_RE = re.compile(r"^session_id:\s*\S+", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    return _ANSI_RE.sub("", text)


def clean_hermes_output(text: str) -> str:
    """Strip ANSI codes and UI noise from *text*, keeping only the model reply.

    This is a thin safety-net in addition to passing ``--quiet`` to the Hermes
    CLI.  It removes session-id lines and, if anything unexpected remains,
    falls back to the last non-empty paragraph.
    """
    text = strip_ansi(text)
    lines = text.splitlines()
    cleaned_lines = [
        line for line in lines if not _SESSION_ID_RE.match(line.strip())
    ]
    cleaned = "\n".join(cleaned_lines).strip()
    if cleaned:
        return cleaned

    # Fallback: return the last non-empty block separated by blank lines.
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return blocks[-1] if blocks else ""


class HermesInvocationError(RuntimeError):
    """Raised when the Hermes CLI cannot be invoked."""


class HermesBackend(AgentBackend):
    """Backend that spawns the local ``hermes`` CLI."""

    def __init__(self) -> None:
        self._handles: dict[object, asyncio.subprocess.Process] = {}

    async def invoke(
        self, profile: str, prompt: str, timeout: int = 10
    ) -> BackendResult:
        """Run ``hermes -p <profile> chat -q <prompt>``.

        Args:
            profile: Hermes profile name.
            prompt: Prompt text (must not contain shell metacharacters).
            timeout: Seconds to wait for the process to finish.

        Returns:
            BackendResult with stdout, return code, and optional stderr.

        Raises:
            HermesInvocationError: If the ``hermes`` binary cannot be started.
            BackendTimeoutError: If the process does not finish within *timeout*.
        """
        logger.info("hermes_invoke_start", profile=profile, prompt_length=len(prompt), timeout=timeout)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes",
                "-p",
                profile,
                "chat",
                "-q",
                prompt,
                "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.error("hermes_invoke_failed", profile=profile, error=str(exc))
            raise HermesInvocationError(f"Failed to start hermes: {exc}") from exc

        self._handles[proc.pid] = proc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            elapsed = time.monotonic() - start
            logger.error("hermes_invoke_timeout", profile=profile, elapsed=elapsed, timeout=timeout)
            proc.kill()
            await proc.wait()
            raise BackendTimeoutError(
                f"hermes invocation timed out after {timeout}s"
            ) from exc
        finally:
            self._handles.pop(proc.pid, None)

        elapsed = time.monotonic() - start
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        cleaned = clean_hermes_output(stdout)
        logger.info(
            "hermes_invoke_done",
            profile=profile,
            elapsed=elapsed,
            returncode=proc.returncode,
            stdout_length=len(stdout),
            cleaned_length=len(cleaned),
            stderr_length=len(stderr),
        )

        return BackendResult(
            text=cleaned,
            code=proc.returncode or 0,
            error=stderr if proc.returncode != 0 else None,
        )

    def cancel(self, handle: object) -> None:
        proc = self._handles.pop(handle, None)
        if proc is not None and proc.returncode is None:
            proc.kill()
