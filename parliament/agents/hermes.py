"""Hermes CLI backend implementation."""

from __future__ import annotations

import asyncio
import re

from parliament.models import BackendResult

from .base import AgentBackend, BackendTimeoutError

# Comprehensive ANSI escape sequence matcher
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    return _ANSI_RE.sub("", text)


class HermesInvocationError(RuntimeError):
    """Raised when the Hermes CLI cannot be invoked."""


class HermesBackend(AgentBackend):
    """Backend that spawns the local ``hermes`` CLI."""

    def __init__(self) -> None:
        self._handles: dict[object, asyncio.subprocess.Process] = {}

    async def invoke(self, profile: str, prompt: str, timeout: int = 120) -> BackendResult:
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
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes",
                "-p",
                profile,
                "chat",
                "-q",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise HermesInvocationError(f"Failed to start hermes: {exc}") from exc

        self._handles[proc.pid] = proc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise BackendTimeoutError(f"hermes invocation timed out after {timeout}s") from exc
        finally:
            self._handles.pop(proc.pid, None)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        return BackendResult(
            text=strip_ansi(stdout),
            code=proc.returncode or 0,
            error=stderr if proc.returncode != 0 else None,
        )

    def cancel(self, handle: object) -> None:
        proc = self._handles.pop(handle, None)
        if proc is not None and proc.returncode is None:
            proc.kill()
