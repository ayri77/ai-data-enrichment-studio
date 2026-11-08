"""Integration tests for the M3 Excel benchmark CLI."""

import json
from pathlib import Path

import pytest
from scripts.benchmark_m3_excel import main


def _scenario_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "benchmarks/scenarios/capitals_repeated.json"


def test_cli_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The original --rows interface must remain supported."""
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark_m3_excel", "--rows", "8"],
    )

    assert main() == 0

    result = json.loads(capsys.readouterr().out)

    assert result["rows"] == 8
    assert result["logical_operations"] == 24
    assert result["executed_handler_calls"] == 4
    assert result["transport_calls"] == 2
    assert result["successful_rows"] == 8
    assert result["source_unchanged"] is True


def test_cli_scenario_with_rows_override(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario parameters must control the actual pipeline."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark_m3_excel",
            "--scenario",
            str(_scenario_path()),
            "--rows",
            "100",
            "--profile-memory",
        ],
    )

    assert main() == 0

    result = json.loads(capsys.readouterr().out)

    assert result["rows"] == 100
    assert result["logical_operations"] == 300
    assert result["executed_handler_calls"] == 4
    assert result["reused_operations"] == 296
    assert result["transport_calls"] == 2
    assert result["audit_operations"] == 2
    assert result["audit_record_step_links"] == 200
    assert result["successful_rows"] == 100
    assert result["failed_rows"] == 0
    assert result["output_rows_verified"] == 100
    assert result["source_unchanged"] is True

    assert result["peak_python_memory_bytes"] > 0


def test_cli_rejects_invalid_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid row counts must be rejected before execution."""
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark_m3_excel", "--rows", "1001"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_cli_rejects_missing_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing configuration files must not run a benchmark."""
    missing = tmp_path / "missing.json"

    monkeypatch.setattr(
        "sys.argv",
        ["benchmark_m3_excel", "--scenario", str(missing)],
    )

    with pytest.raises(FileNotFoundError):
        main()
