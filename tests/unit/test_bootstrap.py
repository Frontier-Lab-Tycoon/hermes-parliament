"""Phase 0 bootstrap sanity checks."""

from parliament import __version__


def test_version() -> None:
    assert __version__ == "0.1.0"
