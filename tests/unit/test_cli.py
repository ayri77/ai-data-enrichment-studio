"""CLI smoke tests."""

from typer.testing import CliRunner

from ai_data_enrichment.cli import app

runner = CliRunner()


def test_version_command() -> None:
    """Version command exits successfully and prints the package version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_inspect_reports_missing_file() -> None:
    """Invalid file paths produce a controlled error."""
    result = runner.invoke(app, ["inspect", "missing.xlsx"])
    assert result.exit_code == 2
    assert "Workbook not found" in result.output
