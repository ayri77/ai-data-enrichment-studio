"""Command-line entry point for the enrichment application."""

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import typer
from openai import OpenAI, OpenAIError

from ai_data_enrichment import __version__
from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.evaluation.planning import evaluate_planning_case
from ai_data_enrichment.ingestion.excel_inspector import inspect_workbook
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.planning.llm_planner import (
    LLMPlanner,
    PlanGenerationError,
)
from ai_data_enrichment.planning.openai_client import (
    OpenAICompletionError,
    OpenAIPlanClient,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator

app = typer.Typer(help="Evidence-driven AI Data Enrichment Studio.", no_args_is_help=True)

# Planning commands stop at the plan boundary: row-level execution is a later stage.
plan_app = typer.Typer(help="Generate and validate enrichment plans.")
app.add_typer(plan_app, name="plan")


def _build_registry() -> CapabilityRegistry:
    """Create a registry with all built-in capabilities."""
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    return registry


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


@plan_app.command("validate")
def validate_plan(
    path: Annotated[Path, typer.Argument(help="Path to a saved plan JSON file.")],
    profile: Annotated[
        Path,
        typer.Option("--profile", help="Path to a dataset profile JSON file."),
    ],
) -> None:
    """Validate a saved enrichment plan without calling an LLM."""

    # Pydantic parses JSON shape; the validator below checks actual references and types.
    try:
        plan = EnrichmentPlan.model_validate_json(path.read_text(encoding="utf-8"))
        dataset_profile = DatasetProfile.model_validate_json(profile.read_text(encoding="utf-8"))

    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    registry = _build_registry()

    validator = PlanValidator(
        registry=registry,
        dataset_profile=dataset_profile,
    )

    result = validator.validate(plan)

    typer.echo(
        json.dumps(
            result.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
        )
    )

    if not result.valid:
        raise typer.Exit(code=1)


@plan_app.command("generate")
def generate_plan(
    path: Annotated[Path, typer.Argument(help="Path to an XLSX workbook.")],
    sheet: Annotated[str, typer.Option("--sheet", help="Worksheet name.")],
    instruction: Annotated[
        str,
        typer.Option("--instruction", help="Natural-language enrichment request."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination plan JSON file."),
    ],
    profile_output: Annotated[
        Path | None,
        typer.Option(
            "--profile-output",
            help="Optional destination for the dataset profile JSON.",
        ),
    ] = None,
) -> None:
    """Generate, validate, and save an enrichment plan."""

# Resolve aliases/symlinks first: output JSON must never overwrite the input workbook.
    try:
        input_path = path.resolve()
        plan_path = output.resolve()
        profile_path = profile_output.resolve() if profile_output is not None else None
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if (
        input_path == plan_path
        or (profile_path is not None and input_path == profile_path)
        or (profile_path is not None and plan_path == profile_path)
    ):
        typer.echo(
            "Error: Input workbook, plan output, and profile output must use distinct paths.",
            err=True,
        )
        raise typer.Exit(code=2)

    # Send the planner bounded schema statistics and samples, not the whole worksheet.
    try:
        dataset_profile = profile_worksheet(
            path=path,
            sheet_name=sheet,
            dataset_id=path.stem,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    registry = _build_registry()

    # Keep model transport separate from plan construction and deterministic validation.
    try:
        client = OpenAIPlanClient(
            api=OpenAI(timeout=30.0, max_retries=0),
            model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
            max_output_tokens=4096,
        )

        planner = LLMPlanner(
            client=client,
            registry=registry,
        )

        result = planner.generate(
            instruction=instruction,
            dataset_profile=dataset_profile,
        )

    except (OpenAIError, OpenAICompletionError, PlanGenerationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    # An ambiguous or unsupported request is a valid planning decision, not a network error.
    if result.response.status != "ready":
        typer.echo(
            f"Planning {result.response.status}: {result.response.reason}",
            err=True,
        )
        raise typer.Exit(code=1)

    plan = result.response.plan
    validation = result.validation

    if plan is None or validation is None or not validation.valid:
        typer.echo("Error: Generated plan failed validation.", err=True)
        raise typer.Exit(code=1)

    # Persist only accepted plans; saving JSON never executes capabilities or edits Excel.
    try:
        if profile_output is not None:
            profile_output.write_text(
                dataset_profile.model_dump_json(indent=2),
                encoding="utf-8",
            )

        output.write_text(
            plan.model_dump_json(indent=2),
            encoding="utf-8",
        )

    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Validated plan saved to: {output}")


@plan_app.command("evaluate")
def evaluate_plan(
    path: Annotated[Path, typer.Argument(help="Path to a saved plan JSON file.")],
    profile: Annotated[
        Path,
        typer.Option("--profile", help="Path to a dataset profile JSON file."),
    ],
    scenario: Annotated[
        str,
        typer.Option("--scenario", help="Evaluation scenario: mountains or capitals."),
    ],
) -> None:
    """Evaluate plan validity and scenario correctness."""

    # These names select fixed academic benchmarks, not arbitrary enrichment instructions.
    selected_scenario: Literal["mountains", "capitals"]

    if scenario == "mountains":
        selected_scenario = "mountains"
    elif scenario == "capitals":
        selected_scenario = "capitals"
    else:
        typer.echo(f"Error: Unsupported scenario: {scenario}", err=True)
        raise typer.Exit(code=2)

    # Load and validate the JSON contracts.
    try:
        plan = EnrichmentPlan.model_validate_json(path.read_text(encoding="utf-8"))
        dataset_profile = DatasetProfile.model_validate_json(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    # A plan can satisfy capability contracts yet solve the wrong scenario (or vice versa).
    report = evaluate_planning_case(
        plan=plan,
        dataset_profile=dataset_profile,
        registry=_build_registry(),
        scenario=selected_scenario,
    )

    # Keep both verdicts in JSON so CI and other consumers can diagnose failures.
    output = {
        "validation": report.validation.model_dump(mode="json"),
        "scenario": asdict(report.scenario),
    }

    typer.echo(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )

    if not report.validation.valid or not report.scenario.correct:
        raise typer.Exit(code=1)
