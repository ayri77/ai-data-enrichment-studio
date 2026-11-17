"""Tests never write into a real state folder of the machine they run on."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def usage_file_in_a_temporary_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Where the local launcher's usage file would go, and no setting that names another."""
    state = tmp_path_factory.mktemp("state")
    monkeypatch.delenv("ENRICHMENT_USAGE_FILE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    return state
