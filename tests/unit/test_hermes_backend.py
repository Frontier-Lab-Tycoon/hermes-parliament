"""Hermes backend tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.agents.base import BackendTimeoutError
from parliament.agents.hermes import (
    HermesBackend,
    HermesInvocationError,
    clean_hermes_output,
)


class TestHermesBackend:
    @pytest.fixture
    def successful_subprocess(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Hello world\n", b""))
        mock_proc.pid = 1234
        with patch(
            "parliament.agents.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ) as create_proc:
            yield create_proc

    @pytest.fixture
    def timeout_subprocess(self):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        with patch(
            "parliament.agents.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ), patch(
            "parliament.agents.hermes.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            yield mock_proc

    @pytest.fixture
    def missing_binary(self):
        with patch(
            "parliament.agents.hermes.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("hermes"),
        ):
            yield

    @pytest.fixture
    def nonzero_subprocess(self):
        mock_proc = MagicMock()
        mock_proc.returncode = -11
        mock_proc.communicate = AsyncMock(
            return_value=(b"partial output", b"Segmentation fault")
        )
        mock_proc.pid = 1234
        with patch(
            "parliament.agents.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            yield

    async def test_invokes_hermes_cli_and_returns_stdout(
        self, successful_subprocess: AsyncMock
    ) -> None:
        result = await HermesBackend().invoke("architect-devil", "안녕하세요")

        successful_subprocess.assert_called_once_with(
            "hermes",
            "-p",
            "architect-devil",
            "chat",
            "-q",
            "안녕하세요",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert result.text == "Hello world"
        assert result.code == 0

    async def test_timeout_kills_process(self, timeout_subprocess: MagicMock) -> None:
        with pytest.raises(BackendTimeoutError):
            await HermesBackend().invoke("architect-devil", "slow prompt", timeout=1)

        timeout_subprocess.kill.assert_called_once()

    async def test_missing_binary_raises_invocation_error(self, missing_binary) -> None:
        with pytest.raises(HermesInvocationError):
            await HermesBackend().invoke("profile", "hello")

    async def test_nonzero_exit_returns_error(self, nonzero_subprocess) -> None:
        result = await HermesBackend().invoke("profile", "crash test")

        assert result.code == -11
        assert result.error == "Segmentation fault"

    async def test_clean_hermes_output_strips_ansi_and_session_id(self) -> None:
        raw = "\x1b[32msession_id: abc-123\x1b[0m\nHello world"
        assert clean_hermes_output(raw) == "Hello world"

    async def test_clean_hermes_output_fallback_to_last_block(self) -> None:
        # When only session metadata remains, fallback to the last non-empty block.
        raw = "session_id: abc-123\n\nActual reply"
        assert clean_hermes_output(raw) == "Actual reply"

    async def test_clean_hermes_output_returns_empty_when_nothing_left(self) -> None:
        assert clean_hermes_output("") == ""
