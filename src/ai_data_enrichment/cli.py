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
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner
from ai_data_enrichment.orchestration.live_executor import build_live_executor
from ai_data_enrichment.planning.llm_planner import (
    LLMPlanner,
    PlanGenerationError,
)
from ai_data_enrichment.planning.model_catalog import DEFAULT_MODEL, model_client
from ai_data_enrichment.planning.openai_client import OpenAICompletionError
from ai_data_enrichment.resolution.resume import load_resume_selections
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


@app.command("run")
def run_enrichment(
    source: Annotated[Path, typer.Argument(help="Source XLSX workbook.")],
    plan_path: Annotated[Path, typer.Option("--plan")],
    sheet: Annotated[str, typer.Option("--sheet")],
    range_ref: Annotated[str, typer.Option("--range")],
    columns_file: Annotated[Path, typer.Option("--columns-file")],
    output: Annotated[Path, typer.Option("--output")],
    allow_network: Annotated[
        bool,
        typer.Option("--allow-network"),
    ] = False,
    max_rows: Annotated[
        int,
        typer.Option("--max-rows"),
    ] = 20,
    approvals_file: Annotated[
        Path | None, typer.Option("--approvals-file", help="Validated user selection manifest.")
    ] = None,
    source_report: Annotated[
        Path | None, typer.Option("--source-report", help="Original audited resolution report.")
    ] = None,
    mountain_resolution_policy: Annotated[
        Literal["strict", "verified_only"],
        typer.Option(
            "--mountain-resolution-policy",
            help="Mountain policy; verified_only retains uncertainty about alternatives.",
        ),
    ] = "strict",
) -> None:
    """Execute a saved plan and create an enriched XLSX workbook."""

    if (approvals_file is None) != (source_report is None):
        typer.echo(
            "Error: --approvals-file and --source-report must be supplied together.", err=True
        )
        raise typer.Exit(code=2)

    if not allow_network:
        typer.echo(
            "Error: Live providers require --allow-network.",
            err=True,
        )
        raise typer.Exit(code=2)

    if not 1 <= max_rows <= 1000:
        typer.echo(
            "Error: max_rows must be between 1 and 1000.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        plan = EnrichmentPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

        bindings = json.loads(columns_file.read_text(encoding="utf-8"))

        if not isinstance(bindings, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in bindings.items()
        ):
            raise ValueError("Columns file must contain a JSON object of string mappings")

        selection = TableSelection(
            sheet_name=sheet,
            range_ref=range_ref,
        )

        # Approval input is bound to the original workbook, plan and audit
        # report; it is not a free-form override of arbitrary operations.
        resume_selections = {}
        previous_job_id = None
        if approvals_file is not None and source_report is not None:
            resume_selections, previous_job_id = load_resume_selections(
                source_path=source,
                plan=plan,
                approvals_path=approvals_file,
                source_report_path=source_report,
            )

        # A new audit must never replace a report retained for review/resume.
        report_path = output.with_name(f"{output.stem}.resolution.json")
        if report_path.exists():
            raise FileExistsError(report_path)

        registry = _build_registry()

        # Provider access is explicitly enabled above. Resolution auditing
        # is configured from the saved plan, not decided by the LLM at runtime.
        executor = build_live_executor(
            registry,
            max_rows=max_rows,
            audit_mountains=any(step.capability == "resolve_entity" for step in plan.steps),
            audit_capitals=any(
                step.capability == "geocode"
                and step.parameters.get("required_role") == "country_capital"
                for step in plan.steps
            ),
            resume_selections=resume_selections,
            mountain_resolution_policy=mountain_resolution_policy,
        )

        enrichment_runner = ExcelEnrichmentRunner(
            registry=registry,
            executor=executor,
            max_rows=max_rows,
        )

        # The runner handles preflight, deterministic execution and writing
        # a NEW workbook. Do not mutate the source in the CLI.
        result = enrichment_runner.run(
            source_path=source,
            output_path=output,
            selection=selection,
            plan=plan,
            output_columns=bindings,
            dataset_id=source.stem,
            previous_job_id=previous_job_id,
        )

        # Publish a separate immutable audit artifact for later review and
        # workbook-bound resume; the destination is checked for conflicts.
        report = result.job_result.resolution_report
        if report is not None:
            with report_path.open("x", encoding="utf-8") as stream:
                stream.write(report.model_dump_json(indent=2))

    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    metrics = result.job_result.metrics

    typer.echo(f"Enriched workbook: {result.output_path}")
    typer.echo(
        f"Rows: {metrics.total_rows}, "
        f"success: {metrics.successful_rows}, "
        f"partial: {metrics.partial_rows}, "
        f"failed: {metrics.failed_rows}"
    )

    for record in result.job_result.records:
        for step in record.row_result.steps:
            operation = step.result

            if operation.status == "failure":
                typer.echo(
                    f"Record: {record.record_id} | "
                    f"Step: {step.step_id} | "
                    f"Error: {operation.error_code} | "
                    f"Message: {operation.message}",
                    err=True,
                )

    report = result.job_result.resolution_report

    if report is not None:
        typer.echo(f"Resolution report: {report_path}")

        for item in report.operations:
            decision = item.report.decision

            typer.echo(f"Resolution status: {decision.status}")
            if decision.basis is not None:
                typer.echo(f"Selection basis: {decision.basis}")

            for candidate in item.report.candidate_set.candidates:
                if candidate.verification == "verified":
                    typer.echo(
                        f"Verified candidate: {candidate.label} ({candidate.reference.entity_id})"
                    )


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
        client = model_client(
            api=OpenAI(timeout=30.0, max_retries=0),
            model_id=os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
            answer_tokens=4096,
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
