"""Hermes backend tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.agents.base import BackendTimeoutError
from parliament.agents.hermes import HermesBackend, HermesInvocationError


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
        with (
            patch(
                "parliament.agents.hermes.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ),
            patch(
                "parliament.agents.hermes.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ),
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
        mock_proc.communicate = AsyncMock(return_value=(b"partial output", b"Segmentation fault"))
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert result.text == "Hello world\n"
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
