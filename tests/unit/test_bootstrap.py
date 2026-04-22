"""Phase 0 bootstrap sanity checks."""

from parliament import __version__


class TestBootstrap:
    async def test_version(self) -> None:
        assert __version__ == "0.1.0"
