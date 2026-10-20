"""Smoke tests for initial application wiring."""

from ai_data_enrichment import __version__


def test_package_has_version() -> None:
    """The project package exposes a stable application version."""
    assert __version__ == "0.1.0"
