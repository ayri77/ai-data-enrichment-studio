"""One killable local worker around the existing Excel pipeline."""

import json
import logging
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

from ai_data_enrichment.capabilities.bindings import BINDINGS, Trust, binding_for
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, required_input_columns
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.execution.step_tally import OUTCOME_KINDS, StepTally
from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner
from ai_data_enrichment.orchestration.live_executor import build_live_executor
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.openrouteservice import routing_key
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.resolution.resume import OtherName, ResumeSelection
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache
from ai_data_enrichment.run_limits import (
    MAX_PROVIDER_REQUESTS,
    MAX_PROVIDER_WAIT_SECONDS,
    MAX_RUN_ROWS,
    RESULT_REUSE_SECONDS,
)
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy

MountainPolicy = Literal["strict", "verified_only"]
# Trusted application injection only; never supplied in an HTTP request.
# Called as factory(capabilities, policy, max_rows); a resume additionally passes
# resume_selections=... as a keyword, and a run whose plan keeps confirmed values
# for its later runs passes reuse=... as a keyword.
ExecutorFactory = Callable[..., JobExecutor]
ResumeSelections = Mapping[tuple[str, str], ResumeSelection | OtherName]
Reuse = tuple[SQLiteOperationCache, SQLiteResolutionCache]
MAX_PLAN_STEPS = 12
__all__ = ["MAX_PLAN_STEPS", "MAX_PROVIDER_REQUESTS", "MAX_RUN_ROWS"]


def builtin_registry() -> CapabilityRegistry:
    """What this installation offers to a plan.

    A road distance needs a routing key in the environment; without one it is
    not registered, so the planner is not told about it and no plan can ask for
    it. The run worker is a separate process with the same environment and
    builds the same registry.
    """
    registry = CapabilityRegistry()
    for capability in offered_capabilities(road_distance=routing_key() is not None):
        registry.register(capability)
    return registry


def offered_descriptions() -> list[dict[str, Any]]:
    """What this installation offers, as a client is told about it.

    Each entry is the capability's own contract (the one the planner is shown)
    with the id of the source its values come from in a live run. The list
    follows the registry, so a capability added to it is listed without any
    further change, and one that is not offered here is not listed.
    """
    return [
        {**spec.model_dump(mode="json"), "source": binding_for(spec.id).source_id}
        for spec in builtin_registry().list_all()
    ]


def remote_sources(registry: CapabilityRegistry) -> list[str]:
    """The sources a run asks over the network, each once, for what is registered."""
    sources: list[str] = []
    for binding in BINDINGS:
        if (
            binding.trust is not Trust.COMPUTED
            and (binding.always_bound or registry.get(binding.capability_id) is not None)
            and binding.source_id not in sources
        ):
            sources.append(binding.source_id)
    return sources


def live_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    transport: JsonTransport | None = None,
    resume_selections: ResumeSelections | None = None,
    reuse: Reuse | None = None,
) -> JobExecutor:
    registry = builtin_registry()
    return build_live_executor(
        registry,
        routing_key=routing_key(),
        max_rows=max_rows,
        resume_selections=resume_selections,
        reuse=reuse,
        reuse_seconds=RESULT_REUSE_SECONDS,
        audit_capitals="geocode" in capabilities,
        audit_mountains="resolve_entity" in capabilities,
        mountain_resolution_policy=policy if "resolve_entity" in capabilities else "strict",
        transport=transport,
        request_policies={
            provider: ProviderRequestPolicy(
                max_requests_per_job=MAX_PROVIDER_REQUESTS,
                max_operations_per_job=MAX_RUN_ROWS * MAX_PLAN_STEPS,
                max_total_wait_seconds=MAX_PROVIDER_WAIT_SECONDS,
            )
            for provider in remote_sources(registry)
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
    # What to do with an output cell that already holds a value.
    existing_outputs: Literal["keep", "replace"] = "keep"
    # Set only for a user-approved resume of an earlier job.
    resume_selections: dict[tuple[str, str], ResumeSelection | OtherName] | None = None
    previous_job_id: str | None = None
    # Where the runs of one plan keep what a source confirmed, so that a later
    # run of the same plan does not ask for it again.
    reuse_directory: Path | None = None


# Shared progress: finished rows, all rows, a sequence number, then three counts
# (filled, failed, skipped) for each step of the plan in plan order.
PROGRESS_HEADER = 3


def progress_slots(step_count: int) -> int:
    """How many integers the shared progress of a plan with this many steps needs."""
    return PROGRESS_HEADER + len(OUTCOME_KINDS) * step_count


def read_progress(
    shared: Sequence[int], step_ids: Sequence[str]
) -> tuple[int, int, dict[str, dict[str, int]]] | None:
    """Finished rows, all rows and the counts per step, or None if the worker is mid-write.

    The worker makes the sequence number odd while it writes and even when done,
    so a reader never takes counts from two different moments; it just looks again.
    """
    before = shared[2]
    if before % 2:
        return None
    done, total = shared[0], shared[1]
    steps: dict[str, dict[str, int]] = {}
    if done > 0:
        for index, step_id in enumerate(step_ids):
            start = PROGRESS_HEADER + len(OUTCOME_KINDS) * index
            steps[step_id] = {
                kind: shared[start + offset] for offset, kind in enumerate(OUTCOME_KINDS)
            }
    return None if shared[2] != before else (done, total, steps)


def execute_workbook(
    task: RunTask,
    factory: ExecutorFactory,
    progress: MutableSequence[int] | None = None,
) -> None:
    """No API state is shared. Parent publishes only after a successful exit.

    A terminated worker's files are discarded; partial files are never offered
    as a completed result, and no checkpoint/resume contract is implied.

    What the sources confirmed is kept apart from those files, in the plan's
    reuse directory: a later run of the same plan takes it over instead of
    asking again. That holds lookups only, each written whole, never a row's
    result, so a terminated run leaves nothing half-done there either.

    ``progress`` is the one exception to "nothing shared": integers, finished
    rows and all rows and, per step, how many finished rows it filled, failed or
    skipped (see ``read_progress``), that the parent only reads to tell a person
    how far the run is. They are counts, carry no result and are never used to
    decide anything.
    """
    tally = StepTally(step.id for step in task.plan.steps)

    def report(done: int, total: int) -> None:
        assert progress is not None
        progress[2] += 1
        # Total first, so a reader never sees more finished rows than rows.
        progress[1] = total
        progress[0] = done
        for index, counts in enumerate(tally.snapshot().values()):
            start = PROGRESS_HEADER + len(OUTCOME_KINDS) * index
            for offset, kind in enumerate(OUTCOME_KINDS):
                progress[start + offset] = counts[kind]
        progress[2] += 1

    try:
        if sha256(task.source_path.read_bytes()).hexdigest() != task.source_sha256:
            raise ValueError("Source changed")
        capabilities = frozenset(step.capability for step in task.plan.steps)
        options: dict[str, Any] = {}
        if task.resume_selections:
            options["resume_selections"] = task.resume_selections
        with ExitStack() as opened:
            if task.reuse_directory is not None:
                options["reuse"] = (
                    opened.enter_context(
                        SQLiteOperationCache(task.reuse_directory / "values.sqlite")
                    ),
                    opened.enter_context(
                        SQLiteResolutionCache(task.reuse_directory / "entities.sqlite")
                    ),
                )
            runner = ExcelEnrichmentRunner(
                registry=builtin_registry(),
                executor=factory(capabilities, task.mountain_policy, MAX_RUN_ROWS, **options),
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
                replace_existing_outputs=task.existing_outputs == "replace",
                on_record_done=report if progress is not None else None,
                on_row_result=tally.add if progress is not None else None,
            )
        if sha256(task.source_path.read_bytes()).hexdigest() != task.source_sha256:
            raise ValueError("Source changed")
        # The values each row was computed from, so a result can be read without
        # opening the workbook. Only columns the plan actually uses are included.
        batch = read_input_batch(
            task.source_path,
            selection=task.selection,
            input_columns=required_input_columns(task.plan),
            max_rows=MAX_RUN_ROWS,
        )

        def cells(row: int) -> list[tuple[str, str]]:
            """Each output target with its cell in this row."""
            return [
                (target, f"{get_column_letter(column_index_from_string(letter))}{row}")
                for target, letter in task.output_columns.items()
            ]

        payload = {
            "execution": result.job_result.model_dump(mode="json"),
            "source_rows": {
                record.record_id: {
                    "row": batch.row_by_record_id[record.record_id],
                    "inputs": record.model_dump(mode="json")["values"],
                    # What happened to an output cell that already held a value:
                    # kept as it was, or replaced (with what it held).
                    "kept_outputs": sorted(
                        target
                        for target, cell in cells(batch.row_by_record_id[record.record_id])
                        if cell in result.preserved_cells
                    ),
                    "replaced_outputs": {
                        target: result.replaced_cells[cell]
                        for target, cell in cells(batch.row_by_record_id[record.record_id])
                        if cell in result.replaced_cells
                    },
                }
                for record in batch.records
            },
            "source_unchanged": True,
            "preserved_cells": result.preserved_cells,
            "replaced_cells": dict(result.replaced_cells),
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
