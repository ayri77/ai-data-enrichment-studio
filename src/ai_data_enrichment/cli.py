"""Command-line entry point for the enrichment application."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from ai_data_enrichment import __version__
from ai_data_enrichment.ingestion.excel_inspector import inspect_workbook

app = typer.Typer(help="Evidence-driven AI Data Enrichment Studio.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the application version."""
    typer.echo(__version__)


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(help="Path to an XLSX workbook.")],
) -> None:
    """Inspect a workbook without modifying it (bootstrap feature)."""
    try:
        summary = inspect_workbook(path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
