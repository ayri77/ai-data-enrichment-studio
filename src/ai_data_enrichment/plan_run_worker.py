"""One killable local worker around the existing Excel pipeline."""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner
from ai_data_enrichment.orchestration.live_executor import build_live_executor
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.resolution.resume import ResumeSelection
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy

MountainPolicy = Literal["strict", "verified_only"]
# Trusted application injection only; never supplied in an HTTP request.
# Called as factory(capabilities, policy, max_rows); a resume additionally passes
# resume_selections=... as a keyword.
ExecutorFactory = Callable[..., JobExecutor]
ResumeSelections = Mapping[tuple[str, str], ResumeSelection]
MAX_RUN_ROWS = 20
MAX_PLAN_STEPS = 12
MAX_PROVIDER_REQUESTS = 400


def builtin_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)
    return registry


def live_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    transport: JsonTransport | None = None,
    resume_selections: ResumeSelections | None = None,
) -> JobExecutor:
    return build_live_executor(
        builtin_registry(),
        max_rows=max_rows,
        resume_selections=resume_selections,
        audit_capitals="geocode" in capabilities,
        audit_mountains="resolve_entity" in capabilities,
        mountain_resolution_policy=policy if "resolve_entity" in capabilities else "strict",
        transport=transport,
        request_policies={
            provider: ProviderRequestPolicy(
                max_requests_per_job=MAX_PROVIDER_REQUESTS,
                max_operations_per_job=MAX_RUN_ROWS * MAX_PLAN_STEPS,
                max_total_wait_seconds=15,
            )
            for provider in ("open-meteo", "wikidata")
        },
    )


@dataclass(frozen=True)
class RunTask:
    job_id: str
    dataset_id: str
    source_path: Path
    source_sha256: str
    directory: Path
    selection: TableSelection
    plan: EnrichmentPlan
    output_columns: dict[str, str]
    mountain_policy: MountainPolicy
    # Set only for a user-approved resume of an earlier job.
    resume_selections: dict[tuple[str, str], ResumeSelection] | None = None
    previous_job_id: str | None = None


def execute_workbook(task: RunTask, factory: ExecutorFactory) -> None:
    """No API state is shared. Parent publishes only after a successful exit.

    A terminated worker's files are discarded; partial files are never offered
    as a completed result, and no checkpoint/resume contract is implied.
    """
    try:
        if sha256(task.source_path.read_bytes()).hexdigest() != task.source_sha256:
            raise ValueError("Source changed")
        capabilities = frozenset(step.capability for step in task.plan.steps)
        runner = ExcelEnrichmentRunner(
            registry=builtin_registry(),
            executor=(
                factory(
                    capabilities,
                    task.mountain_policy,
                    MAX_RUN_ROWS,
                    resume_selections=task.resume_selections,
                )
                if task.resume_selections
                else factory(capabilities, task.mountain_policy, MAX_RUN_ROWS)
            ),
            max_rows=MAX_RUN_ROWS,
            validation_policy=PlanValidationPolicy(max_steps=MAX_PLAN_STEPS),
        )
        result = runner.run(
            source_path=task.source_path,
            output_path=task.directory / "result.xlsx",
            selection=task.selection,
            plan=task.plan,
            output_columns=task.output_columns,
            dataset_id=task.dataset_id,
            job_id=task.job_id,
            previous_job_id=task.previous_job_id,
            preserve_existing_outputs=True,
        )
        if sha256(task.source_path.read_bytes()).hexdigest() != task.source_sha256:
            raise ValueError("Source changed")
        payload = {
            "execution": result.job_result.model_dump(mode="json"),
            "source_unchanged": True,
            "preserved_cells": result.preserved_cells,
            "result_sha256": sha256(result.output_path.read_bytes()).hexdigest(),
        }
        # Operation failure codes are useful; arbitrary handler messages are not
        # a public exception channel. Candidate evidence remains source data.
        for record in payload["execution"]["records"]:
            for step in record["row_result"]["steps"]:
                if step["result"]["status"] == "failure":
                    step["result"]["message"] = "Operation could not produce a verified value."
        (task.directory / "execution.json").write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        logging.getLogger(__name__).exception("Workbook worker failed")
        # Unexpected exceptions must not leak paths, credentials or raw responses.
        (task.directory / "error.json").write_text(
            json.dumps({"code": "execution_failed", "message": "The workbook run failed."}),
            encoding="utf-8",
        )
