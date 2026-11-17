"""One killable local worker around the existing Excel pipeline."""

import json
import logging
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string
from pydantic import SecretStr

from ai_data_enrichment.capabilities.bindings import BINDINGS, Trust, binding_for
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.fallback import fallback_registry, plan_shape
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, required_input_columns
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.models import FoundRecord
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.execution.step_tally import OUTCOME_KINDS, StepTally
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner
from ai_data_enrichment.orchestration.live_executor import WebLookupOptions, build_live_executor
from ai_data_enrichment.output.excel_output_mapping import EMPTY_FIELD_CODE, empty_output_fields
from ai_data_enrichment.output.provenance import SourcesMode
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.openrouteservice import routing_key
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.providers.web_lookup import (
    LOOKUP_CAPABILITY,
    WebLookupClient,
    WebLookupUsage,
    configuration_id,
)
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
# for its later runs passes reuse=... as a keyword. A run whose plan has a web lookup
# step (a fallback or a mixed plan) passes registry=... (the one built for that plan) and
# web_lookup=... (how to run it) as keywords; no other run passes them.
ExecutorFactory = Callable[..., JobExecutor]
ResumeSelections = Mapping[tuple[str, str], ResumeSelection | OtherName]
Reuse = tuple[SQLiteOperationCache, SQLiteResolutionCache]
MAX_PLAN_STEPS = 12
# Called INSIDE the worker with the model key; returns the object whose
# ``responses.create(**kwargs)`` the web lookup calls. Trusted application injection
# only (a test passes a fake that never leaves the process); never from a request.
ApiFactory = Callable[[str], WebLookupClient]
# One lookup request may take this long; the OpenAI client is told the same.
LOOKUP_REQUEST_SECONDS = 30.0
# What a run keeps back from its time limit: starting the worker, writing the workbook
# and the files, and the supervisor's own look. The lookup starts no request that
# could end after the limit minus this.
FINISH_RESERVE_SECONDS = 20.0
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
    with the id of the source its values come from in a live run and the kind of
    trust that stands behind them (``Trust``, as a lowercase word). Neither is in
    the contract the planner is shown: that is ``CapabilityRegistry.describe_all``
    and stays the specs' own dump. The list
    follows the registry, so a capability added to it is listed without any
    further change, and one that is not offered here is not listed.
    """
    return [
        {
            **spec.model_dump(mode="json"),
            "source": binding_for(spec.id).source_id,
            "trust": binding_for(spec.id).trust.value,
        }
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
    registry: CapabilityRegistry | None = None,
    web_lookup: WebLookupOptions | None = None,
) -> JobExecutor:
    registry = registry if registry is not None else builtin_registry()
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
        web_lookup=web_lookup,
        request_policies={
            provider: ProviderRequestPolicy(
                max_requests_per_job=MAX_PROVIDER_REQUESTS,
                max_operations_per_job=MAX_RUN_ROWS * MAX_PLAN_STEPS,
                max_total_wait_seconds=MAX_PROVIDER_WAIT_SECONDS,
            )
            for provider in remote_sources(registry)
        },
    )


def openai_api(api_key: str) -> WebLookupClient:
    """The real client, made in the worker: one attempt per request, no retries."""
    # Imported here: only a lookup run needs it, and it would slow the start of every run.
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        timeout=LOOKUP_REQUEST_SECONDS,
        max_retries=0,
    )


def lookup_deadline_seconds(timeout_seconds: float) -> float:
    """How long after its own start a worker may begin lookup requests."""
    return max(timeout_seconds - FINISH_RESERVE_SECONDS, 0.0)


@dataclass(frozen=True)
class LookupSettings:
    """What a worker needs to run the web lookup fallback.

    The key is a ``SecretStr`` and is not part of ``repr``; it reaches the worker through
    the process arguments only and is never written anywhere.
    """

    model: str
    reasoning_effort: str
    api_key: SecretStr = field(repr=False)
    # Seconds after the worker's own start after which no request is begun.
    deadline_seconds: float
    api_factory: ApiFactory = openai_api


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
    # Whether the workbook says where each written value came from.
    sources_in_workbook: SourcesMode = "none"
    # Set only for a user-approved resume of an earlier job.
    resume_selections: dict[tuple[str, str], ResumeSelection | OtherName] | None = None
    previous_job_id: str | None = None
    # Where the runs of one plan keep what a source confirmed, so that a later
    # run of the same plan does not ask for it again.
    reuse_directory: Path | None = None
    # Set only for a fallback plan the server built; None for every other plan.
    lookup: LookupSettings | None = None


# Shared progress: finished rows, all rows, a sequence number, then three counts
# (filled, failed, skipped) for each step of the plan in plan order.
PROGRESS_HEADER = 3


# After the step counts of a lookup run: requests, input tokens, output tokens and
# web searches so far. Numbers only; they survive a worker that is stopped.
LOOKUP_SLOTS = 4
_INT_MAX = 2**31 - 1


def progress_slots(step_count: int, *, lookup: bool = False) -> int:
    """How many integers the shared progress of a plan with this many steps needs."""
    return PROGRESS_HEADER + len(OUTCOME_KINDS) * step_count + (LOOKUP_SLOTS if lookup else 0)


def read_lookup_usage(shared: Sequence[int], step_count: int) -> dict[str, int]:
    """What a lookup run had spent when it stopped, from the shared numbers.

    Read after the worker is gone, so no sequence number is consulted: at worst the last
    request is counted without its tokens.
    """
    start = PROGRESS_HEADER + len(OUTCOME_KINDS) * step_count
    names = ("requests", "input_tokens", "output_tokens", "web_searches")
    return {name: int(shared[start + offset]) for offset, name in enumerate(names)}


class MirroredUsage(WebLookupUsage):
    """Counts of the lookup's requests, also written to the shared numbers after each one."""

    def __init__(self, shared: MutableSequence[int] | None, start: int) -> None:
        super().__init__()
        self._shared, self._start = shared, start

    def add(self, *, input_tokens: int = 0, output_tokens: int = 0, web_searches: int = 0) -> None:
        super().add(
            input_tokens=input_tokens, output_tokens=output_tokens, web_searches=web_searches
        )
        if self._shared is None:
            return
        totals = self.totals()
        numbers = (totals.requests, totals.input_tokens, totals.output_tokens, totals.web_searches)
        for offset, number in enumerate(numbers):
            self._shared[self._start + offset] = min(number, _INT_MAX)


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


def _close(api: object) -> None:
    """Close what the factory made, where it can be closed; never raises."""
    close = getattr(api, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logging.getLogger(__name__).warning("Closing the lookup client failed")


def lookup_numbers(usage: WebLookupUsage) -> dict[str, int]:
    totals = usage.totals()
    return {
        "requests": totals.requests,
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "web_searches": totals.web_searches,
    }


def _found_values(
    task: RunTask,
    job_result: Any,
    row_by_record_id: Mapping[str, int],
    kept: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """For each record, the values written from a web search: column, basis, link count.

    No link, value or quote is repeated: those are in the step evidence and the provenance.
    A cell that kept its earlier value, and a column nothing was found for, are not listed.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    for record in job_result.records:
        row = row_by_record_id[record.record_id]
        results = {step.step_id: step.result for step in record.row_result.steps}
        entries: list[dict[str, Any]] = []
        for output in task.plan.outputs:
            result = results.get(output.step_id)
            letter = task.output_columns[output.target_column]
            cell = f"{get_column_letter(column_index_from_string(letter))}{row}"
            if result is None or not isinstance(result.value, FoundRecord) or cell in kept:
                continue
            slot = result.value.slots.get(output.value_field)
            if slot is None or slot.value is None:
                continue
            entries.append(
                {"column": output.target_column, "basis": slot.basis, "links": len(slot.sources)}
            )
        found[record.record_id] = entries
    return found


def _open_reuse(opened: ExitStack, task: RunTask) -> Reuse | None:
    """The plan's two caches, closed with the run; None where the task has no directory."""
    if task.reuse_directory is None:
        return None
    return (
        opened.enter_context(SQLiteOperationCache(task.reuse_directory / "values.sqlite")),
        opened.enter_context(SQLiteResolutionCache(task.reuse_directory / "entities.sqlite")),
    )


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
    started = monotonic()
    tally = StepTally(step.id for step in task.plan.steps)
    lookup = task.lookup
    usage: WebLookupUsage = WebLookupUsage()

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
        # A plan with the web lookup runs with the lookup's own registry and options, and
        # only then; no other plan may, and a plan with the lookup may not run without them.
        if (plan_shape(task.plan) != "planner") != (lookup is not None):
            raise ValueError("The web lookup is run for a fallback or mixed plan only.")
        options: dict[str, Any] = {}
        if task.resume_selections:
            options["resume_selections"] = task.resume_selections
        with ExitStack() as opened:
            # The plan's own caches, as for every plan: what a source confirmed, and an
            # answer a model gave for a row, is taken over by a later run of the plan for
            # the same time, before a budget or an API is touched.
            reuse = _open_reuse(opened, task)
            if reuse is not None:
                options["reuse"] = reuse
            if lookup is None:
                registry = builtin_registry()
            else:
                profile = profile_worksheet(
                    task.source_path,
                    task.selection.sheet_name,
                    task.dataset_id,
                    selection=task.selection,
                )
                registry = fallback_registry(task.plan, profile.columns, builtin_registry())
                usage = MirroredUsage(
                    progress, PROGRESS_HEADER + len(OUTCOME_KINDS) * len(task.plan.steps)
                )
                # The client is made here, from the key this process was given.
                api = lookup.api_factory(lookup.api_key.get_secret_value())
                opened.callback(_close, api)
                options["registry"] = registry
                options["web_lookup"] = WebLookupOptions(
                    api=api,
                    model=lookup.model,
                    reasoning_effort=lookup.reasoning_effort,
                    usage=usage,
                    timeout_seconds=LOOKUP_REQUEST_SECONDS,
                    # From this process's own start, on its own clock.
                    deadline=started + lookup.deadline_seconds,
                )
            executor = factory(capabilities, task.mountain_policy, MAX_RUN_ROWS, **options)
            runner = ExcelEnrichmentRunner(
                registry=registry,
                executor=executor,
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
                sources_in_workbook=task.sources_in_workbook,
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

        # Output cells left empty because the source holds no value for the field
        # (its step succeeded): per record, the column, the field and why.
        gaps = empty_output_fields(task.plan, result.job_result)
        found_values = (
            _found_values(task, result.job_result, batch.row_by_record_id, result.preserved_cells)
            if lookup is not None
            else {}
        )
        payload: dict[str, Any] = {
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
                    "empty_outputs": {
                        target: {"field": field, "code": EMPTY_FIELD_CODE}
                        for target, field in gaps.get(record.record_id, {}).items()
                    },
                    # A lookup run only: each value written from a model's web search,
                    # as its column, its basis and how many links it rests on.
                    **(
                        {"ai_found": found_values.get(record.record_id, [])}
                        if lookup is not None
                        else {}
                    ),
                }
                for record in batch.records
            },
            # Rows left out of the run because every chosen cell already held a
            # value: no step ran and no source was asked for them.
            "skipped_rows": [
                {
                    "record_id": record.record_id,
                    "row": batch.row_by_record_id[record.record_id],
                    "inputs": record.model_dump(mode="json")["values"],
                    "reason": "already_filled",
                    "kept_outputs": [
                        target
                        for target, cell in cells(batch.row_by_record_id[record.record_id])
                        if cell in result.skipped_records[record.record_id]
                    ],
                    "kept_values": {
                        target: result.skipped_records[record.record_id][cell]
                        for target, cell in cells(batch.row_by_record_id[record.record_id])
                        if cell in result.skipped_records[record.record_id]
                    },
                }
                for record in batch.records
                if record.record_id in result.skipped_records
            ],
            # What each step of the plan is, where its values come from and what
            # stands behind them: once per step, in plan order, not per row.
            "step_provenance": [
                {
                    "step_id": step.id,
                    "capability": step.capability,
                    "source": binding_for(step.capability).source_id,
                    "trust": binding_for(step.capability).trust.value,
                }
                for step in task.plan.steps
            ],
            # Which choice the run was asked for, and whether the workbook got
            # it: False when adding the notes or the sheet failed.
            "sources_in_workbook": {
                "mode": task.sources_in_workbook,
                "applied": result.sources_applied,
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
        if lookup is not None:
            # Numbers only: what the run asked of the API, failed requests included.
            payload["web_lookup"] = {
                "configuration_id": configuration_id(lookup.model, lookup.reasoning_effort),
                **lookup_numbers(usage),
                # Rows an earlier run's kept answer settled: no request, no budget.
                "answers_kept": result.job_result.kept_answers.get(LOOKUP_CAPABILITY, 0),
            }
        (task.directory / "execution.json").write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:
        if lookup is None:
            logging.getLogger(__name__).exception("Workbook worker failed")
        else:
            # No traceback: a library's message could quote the request, and with it the key.
            secret = lookup.api_key.get_secret_value()
            logging.getLogger(__name__).error(
                "Workbook worker failed (%s): %s",
                type(exc).__name__,
                str(exc).replace(secret, "[key]") if secret else str(exc),
            )
        # Unexpected exceptions must not leak paths, credentials or raw responses.
        (task.directory / "error.json").write_text(
            json.dumps({"code": "execution_failed", "message": "The workbook run failed."}),
            encoding="utf-8",
        )
