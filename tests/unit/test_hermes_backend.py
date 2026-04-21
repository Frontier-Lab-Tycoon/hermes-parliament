"""Phase 3 acceptance criteria: HermesBackend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from parliament.backends.base import BackendTimeoutError
from parliament.backends.hermes import HermesBackend, HermesInvocationError, strip_ansi


class TestT3ValidInvocation:
    """T3-1: 유효한 profile로 간단한 프롬프트 호출."""

    @pytest.mark.asyncio
    async def test_valid_call_returns_text_and_zero_code(self) -> None:
        backend = HermesBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Hello world\n", b""))
        mock_proc.pid = 1234

        with patch(
            "parliament.backends.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await backend.invoke("architect-devil", "안녕하세요")

        assert result.text == "Hello world\n"
        assert result.code == 0
        assert result.error is None


class TestT3Timeout:
    """T3-2: 150초 이상 걸리는 프롬프트 → BackendTimeoutError."""

    @pytest.mark.asyncio
    async def test_timeout_raises_backend_timeout_error(self) -> None:
        backend = HermesBackend()
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch(
            "parliament.backends.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ), patch(
            "parliament.backends.hermes.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            with pytest.raises(BackendTimeoutError):
                await backend.invoke("architect-devil", "slow prompt", timeout=1)

        mock_proc.kill.assert_called_once()


class TestT3NonExistentProfile:
    """T3-3: 존재하지 않는 profile 호출 → HermesInvocationError."""

    @pytest.mark.asyncio
    async def test_missing_binary_raises_hermes_invocation_error(self) -> None:
        backend = HermesBackend()

        with patch(
            "parliament.backends.hermes.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("hermes"),
        ):
            with pytest.raises(HermesInvocationError):
                await backend.invoke("nonexistent-profile", "hello")


class TestT3AnsiStripping:
    """T3-4: ANSI color code가 포함된 출력 → strip_ansi 후 깨끗한 텍스트."""

    @pytest.mark.asyncio
    async def test_ansi_codes_are_stripped_from_stdout(self) -> None:
        backend = HermesBackend()
        raw = b"\x1b[32mGreen\x1b[0m \x1b[1mBold\x1b[0m"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(raw, b""))
        mock_proc.pid = 1234

        with patch(
            "parliament.backends.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await backend.invoke("architect-devil", "color test")

        assert result.text == "Green Bold"
        assert "\x1b[" not in result.text
        assert result.code == 0

    def test_strip_ansi_function(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
        assert strip_ansi("\x1b[1m\x1b[32mgreen\x1b[0m") == "green"
        assert strip_ansi("no codes") == "no codes"


class TestT3NonZeroExit:
    """T3-5: subprocess가 segfault 등으로 비정상 종료 → code != 0, error has stderr."""

    @pytest.mark.asyncio
    async def test_segfault_returns_nonzero_code_and_stderr(self) -> None:
        backend = HermesBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = -11  # SIGSEGV
        mock_proc.communicate = AsyncMock(
            return_value=(b"partial output", b"Segmentation fault")
        )
        mock_proc.pid = 1234

        with patch(
            "parliament.backends.hermes.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await backend.invoke("architect-devil", "crash test")

        assert result.code == -11
        assert result.error == "Segmentation fault"
        assert result.text == "partial output"
