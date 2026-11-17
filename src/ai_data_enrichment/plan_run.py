"""Minimal source-bound plan, approval, run and artifact API for local use."""

import asyncio
import json
import logging
import multiprocessing
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shutil import rmtree
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from openai import OpenAI
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from ai_data_enrichment.capabilities.fallback import (
    FallbackPlanError,
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    fallback_registry,
    is_fallback_plan,
)
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import JobExecutionResult, classify_row_outcome
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import build_output_header_writes
from ai_data_enrichment.plan_run_worker import (
    MAX_PLAN_STEPS,
    MAX_PROVIDER_REQUESTS,
    MAX_RUN_ROWS,
    ApiFactory,
    ExecutorFactory,
    LookupSettings,
    MountainPolicy,
    RunTask,
    builtin_registry,
    execute_workbook,
    live_executor,
    lookup_deadline_seconds,
    offered_descriptions,
    openai_api,
    progress_slots,
    read_lookup_usage,
    read_progress,
)
from ai_data_enrichment.planning.llm_planner import (
    LLMPlanner,
    PlanCompletionClient,
    PlanGenerationError,
)
from ai_data_enrichment.planning.model_catalog import model_client as catalog_client
from ai_data_enrichment.provider_settings import ProviderConfiguration
from ai_data_enrichment.providers.web_lookup import configuration_id
from ai_data_enrichment.resolution.input_errors import MAX_NAME_LENGTH
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionQuery,
    ResolutionReport,
)
from ai_data_enrichment.resolution.other_name import OtherNameCapability, search_under
from ai_data_enrichment.resolution.resume import (
    build_other_names,
    build_resume_selections,
    build_searched_selections,
    other_name_capability,
    usable_other_name,
)
from ai_data_enrichment.review import (
    ReviewDecision,
    approvals_from_decisions,
    auto_accepted_steps,
    names_from_decisions,
    other_name_audit,
    review_payload,
    searched_from_decisions,
    searched_item,
    searched_steps,
    user_chosen_steps,
)
from ai_data_enrichment.run_limits import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TABLE_CELLS,
    MAX_TABLE_ROWS,
    MAX_TIMEOUT_SECONDS,
)
from ai_data_enrichment.spellings import SuggestionError, suggest_names
from ai_data_enrichment.task_ideas import TaskIdeaError, propose_tasks
from ai_data_enrichment.usage import UsageLog, parse_usage_file
from ai_data_enrichment.validation.models import ValidationResult
from ai_data_enrichment.validation.plan_validator import (
    PlanValidationPolicy,
    PlanValidator,
    apply_unfilled_targets,
)
from ai_data_enrichment.workspace_errors import WorkspaceError

LOOKUP_NUMBERS = ("requests", "input_tokens", "output_tokens", "web_searches")
MAX_PLANS = 32
MAX_JOBS = 16
MAX_PLAN_BYTES = 64 * 1024
TERMINAL = {"completed", "needs_review", "failed", "cancelled", "timed_out"}
# The model and effort of the web lookup fallback. The model may be changed in the
# environment; the effort is fixed, as it was in the measurements of the lookup.
WEB_LOOKUP_DEFAULT_MODEL = "gpt-6-luna"
WEB_LOOKUP_REASONING_EFFORT = "low"
# A model id is one short word: it is counted in the usage summary and named in the audit.
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# A draft made by the planner (or brought along) has no "kind"; one the server built for
# the web lookup fallback says so. The kind is part of what the fingerprint covers.
FALLBACK_KIND = "fallback"
# What a draft gains after its fingerprint was made; everything else is covered by it.
_AFTER_FINGERPRINT = frozenset(
    {"plan_id", "fingerprint", "status", "created_at", "approved_at", "validation"}
)


class PlanBodyLimit:
    """Bound JSON before parsing, just as uploads are bounded before ingestion."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope["path"].startswith(("/api/plans", "/api/fallback-plans"))
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_PLAN_BYTES:
                await JSONResponse(
                    {"error": {"code": "request_too_large", "message": "Use JSON up to 64 KiB."}},
                    status_code=413,
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def _validation_policy(output_columns: dict[str, str]) -> PlanValidationPolicy:
    """One policy for planning and for the final draft validation."""
    return PlanValidationPolicy(max_steps=MAX_PLAN_STEPS, output_targets=frozenset(output_columns))


def unfilled_targets_of(draft: dict) -> list[str]:
    """The requested columns the draft's planner named as having no source; a draft
    made before the field existed, a supplied plan and a fallback draft have none."""
    return list(draft.get("unfilled_targets", []))


def planned_columns(draft: dict) -> dict[str, str]:
    """The output columns the plan writes: the chosen ones minus the unfilled targets.

    What a run creates, writes and checks is this mapping. A new column with no source is
    not created; an existing one is not read for the run and stays as it is.
    """
    unfilled = set(unfilled_targets_of(draft))
    return {
        name: letter for name, letter in draft["output_columns"].items() if name not in unfilled
    }


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def draft_fingerprint(draft: dict) -> str:
    """The fingerprint of what a draft was made of: the request, the plan and its origin."""
    covered = {key: value for key, value in draft.items() if key not in _AFTER_FINGERPRINT}
    return sha256(json.dumps(covered, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PlanRunSettings:
    allow_network: bool = False
    llm_model: str | None = None
    # The universal-search fallback: every row costs a model request and a search fee.
    allow_web_lookup: bool = False
    web_lookup_model: str = WEB_LOOKUP_DEFAULT_MODEL
    # `ENRICHMENT_USAGE_FILE`: None when unset (the entry point decides), "off", or the
    # path of the file that keeps the usage events across restarts.
    usage_file: str | None = None

    def __post_init__(self) -> None:
        if not _MODEL_ID.fullmatch(self.web_lookup_model):
            raise ValueError("The web lookup model must be one short model id.")

    @classmethod
    def from_environment(cls) -> "PlanRunSettings":
        model = (os.getenv("ENRICHMENT_WEB_LOOKUP_MODEL") or "").strip()
        if model and not _MODEL_ID.fullmatch(model):
            raise RuntimeError("ENRICHMENT_WEB_LOOKUP_MODEL must be one short model id.")
        return cls(
            allow_network=os.getenv("ENRICHMENT_ALLOW_NETWORK") == "1",
            llm_model=os.getenv("OPENAI_MODEL") or None,
            allow_web_lookup=os.getenv("ENRICHMENT_ALLOW_WEB_LOOKUP") == "1",
            web_lookup_model=model or WEB_LOOKUP_DEFAULT_MODEL,
            usage_file=parse_usage_file(os.getenv("ENRICHMENT_USAGE_FILE")),
        )


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=64)
    sheet_name: str = Field(min_length=1, max_length=31)
    range_ref: str = Field(min_length=1, max_length=32)
    instruction: str = Field(min_length=1, max_length=4000)
    output_columns: dict[str, str] = Field(min_length=1, max_length=20)
    plan: EnrichmentPlan | None = None
    allow_network: bool = False
    mountain_resolution_policy: MountainPolicy = "strict"
    # An output cell that already holds a value is kept by default. "replace"
    # writes a newly found value over it; a row with no new value keeps its own.
    existing_outputs: Literal["keep", "replace"] = "keep"
    # Whether the result workbook says where each written value came from: not at
    # all, in a note on each written cell, or on an added worksheet.
    sources_in_workbook: Literal["none", "notes", "sheet"] = "none"


class FallbackTargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=200)
    kind: Literal["text", "number", "yes_no"]
    unit: str | None = Field(default=None, max_length=200)


class FallbackPlanRequest(BaseModel):
    """Ask for the universal-search plan: what to find for each row, and where to write it.

    Only structure and size are checked here; what a lookup task may hold is checked by
    ``FallbackRequest`` and ``build_fallback_plan``. There is no plan, no kind and no
    model in the body: the server builds the plan and sets the kind.
    """

    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=64)
    sheet_name: str = Field(min_length=1, max_length=31)
    range_ref: str = Field(min_length=1, max_length=32)
    question: str = Field(min_length=1, max_length=4000)
    context_columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=100
    )
    targets: list[FallbackTargetBody] = Field(min_length=1, max_length=100)
    # As in a plan request: each column to fill, existing or new, and its column letter.
    output_columns: dict[str, str] = Field(min_length=1, max_length=20)
    existing_outputs: Literal["keep", "replace"] = "keep"
    sources_in_workbook: Literal["none", "notes", "sheet"] = "none"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: Literal[True]
    fingerprint: str = Field(min_length=64, max_length=64)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_network: bool = False
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS, strict=True
    )


class ResumeRequest(RunRequest):
    """Continue a job that needs review with explicit user choices."""

    decisions: list[ReviewDecision] = Field(min_length=1, max_length=MAX_RUN_ROWS * MAX_PLAN_STEPS)


class SearchRequest(BaseModel):
    """Search the source once for another name of a reviewed query."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    allow_network: bool = False


# How many times one job's names may be searched under another name. A person
# trying spellings stays far below it; a script does not get an open relay to
# the data sources.
MAX_REVIEW_SEARCHES = 100
# How many times one job may ask the model for other names. Each is a paid
# request; answers are kept, so a person asks once per name.
MAX_REVIEW_SUGGESTIONS = 40
# How many times the model may be asked for task ideas for one workbook.
MAX_TASK_IDEA_REQUESTS = 20


class TaskIdeasRequest(BaseModel):
    """Ask the model what could be asked for with a table."""

    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sheet_name: str = Field(min_length=1, max_length=31)
    range_ref: str = Field(min_length=1, max_length=32)
    language: Literal["en", "uk", "de"] = "en"
    allow_network: bool = False
    # Ask the model again although an answer for the same table, language,
    # columns and model is kept; the new answer replaces the kept one.
    again: bool = False
    # Headers of the columns chosen to be filled. With several of them the
    # model phrases one task that fills them all instead of proposing a few.
    fill_columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )


class SuggestionRequest(BaseModel):
    """Ask the model for other names of a reviewed query."""

    model_config = ConfigDict(extra="forbid")
    allow_network: bool = False


ModelPurpose = Literal["plan", "task_ideas", "other_names"]


class _CountedClient:
    """Pass requests to a model client and count them."""

    def __init__(self, client: PlanCompletionClient) -> None:
        self._client = client
        self.requests = 0

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict) -> str:
        self.requests += 1
        return self._client.complete(messages=messages, response_schema=response_schema)


class PlanRunService:
    """In-memory identities with temporary on-disk artifacts; one active run."""

    def __init__(
        self,
        datasets: dict[str, Path],
        lock: asyncio.Lock,
        settings: PlanRunSettings,
        offline_plan_client: PlanCompletionClient | None,
        offline_executor_factory: ExecutorFactory | None,
        provider: ProviderConfiguration,
        usage: UsageLog | None = None,
        offline_lookup_api: ApiFactory | None = None,
    ) -> None:
        self.datasets, self.lock, self.settings = datasets, lock, settings
        # Trusted injection for tests: made inside the worker in place of the real client.
        self.offline_lookup_api = offline_lookup_api
        self.provider = provider
        # Counted events of this workspace: kinds and numbers, never content.
        self.usage = usage or UsageLog()
        self.offline_plan_client = offline_plan_client
        self.offline_executor_factory = offline_executor_factory
        self.plans: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.directories: dict[str, Path] = {}
        # The user's choices each job ran with, so that a later resume keeps them.
        self.selections: dict[str, dict] = {}
        # The latest search under another name for each operation of a job, so
        # that a candidate chosen from it is checked against the server's own
        # record, and how many such searches the job has had.
        self.searches: dict[str, dict[str, ResolutionReport]] = {}
        self.search_counts: dict[str, int] = {}
        self.searching = False
        # Names a model proposed for an operation, so that asking again costs
        # nothing, and how many times a job has asked.
        self.suggestions: dict[str, dict[str, tuple[str, ...]]] = {}
        # Every request to the model for other names during a job's review,
        # answered or not; a resume writes them into the new job's audit.
        self.model_requests: dict[str, list[dict]] = {}
        # What a resumed job's audit says about its review: the other names
        # used, who gave each, and the model requests made on the way.
        self.assistance: dict[str, dict[str, list[dict]]] = {}
        self.suggestion_counts: dict[str, int] = {}
        self.suggesting = False
        # Task ideas a model proposed for a table and a language, kept per model
        # (an answer of another model is not served), and how many times a
        # workbook has asked, across models. The reasoning effort follows from
        # the model id in the catalog, so the id is all the key needs.
        self.task_ideas: dict[
            str, dict[tuple[str, str, str, str, tuple[str, ...]], tuple[str, ...]]
        ] = {}
        self.task_idea_counts: dict[str, int] = {}
        self.router = APIRouter(prefix="/api")
        self._routes()

    def dataset_in_use(self, dataset_id: str) -> bool:
        return any(p["dataset_id"] == dataset_id for p in self.plans.values())

    # Retention: a long-running process (a hosted demo, a local session with many
    # runs) must not end in "restart to continue". When a limit is reached the
    # oldest finished item is dropped. Nothing with a running job is ever dropped.
    def _active(self, **match: str) -> bool:
        return any(
            job["status"] not in TERMINAL and all(job[k] == v for k, v in match.items())
            for job in self.jobs.values()
        )

    def _drop_job(self, job_id: str) -> None:
        del self.jobs[job_id]
        self.selections.pop(job_id, None)
        self.searches.pop(job_id, None)
        self.search_counts.pop(job_id, None)
        self.suggestions.pop(job_id, None)
        self.suggestion_counts.pop(job_id, None)
        self.model_requests.pop(job_id, None)
        self.assistance.pop(job_id, None)
        self.tasks.pop(job_id, None)
        self.cancel_events.pop(job_id, None)
        rmtree(self.directories.pop(job_id), ignore_errors=True)

    def make_room_for_job(self) -> None:
        if len(self.jobs) < MAX_JOBS:
            return
        oldest = next((i for i, job in self.jobs.items() if job["status"] in TERMINAL), None)
        if oldest is None:
            raise WorkspaceError("workspace_full", "Wait for the current run to finish.", 409)
        self._drop_job(oldest)

    def make_room_for_plan(self) -> None:
        if len(self.plans) < MAX_PLANS:
            return
        oldest = next((i for i in self.plans if not self._active(plan_id=i)), None)
        if oldest is None:
            raise WorkspaceError("workspace_full", "Wait for the current run to finish.", 409)
        del self.plans[oldest]

    def release_dataset(self, dataset_id: str) -> bool:
        """Forget plans and finished jobs bound to a dataset; False while it is running."""
        if self._active(dataset_id=dataset_id):
            return False
        for job_id in [i for i, job in self.jobs.items() if job["dataset_id"] == dataset_id]:
            self._drop_job(job_id)
        for plan_id in [i for i, p in self.plans.items() if p["dataset_id"] == dataset_id]:
            del self.plans[plan_id]
        return True

    def network_gate(self, requested: bool) -> None:
        if not requested or not self.settings.allow_network:
            raise WorkspaceError(
                "network_disabled", "Enable network in server configuration and this request.", 403
            )

    def model_gate(self, allow_network: bool) -> None:
        """Refuse a model request that lacks consent, a model or a key."""
        if self.offline_plan_client is not None:
            return
        self.network_gate(allow_network)
        if not self.provider.model:
            raise WorkspaceError("llm_not_configured", "Choose a model in Provider Settings.")
        if not self.provider.key_present:
            raise WorkspaceError("missing_api_key", "Add an API key in Provider Settings.")

    @contextmanager
    def model_client(
        self, *, answer_tokens: int, purpose: ModelPurpose
    ) -> Iterator[PlanCompletionClient]:
        """The configured model for one request; call model_gate first.

        What the request was for, how many times the model was asked and the
        tokens the provider reported are counted when the client is released,
        whether or not an answer could be used.
        """
        if self.offline_plan_client is not None:
            counted = _CountedClient(self.offline_plan_client)
            try:
                yield counted
            finally:
                self.usage.record(
                    "model", purpose=purpose, model="offline", requests=counted.requests
                )
            return
        api = OpenAI(
            api_key=self.provider.api_key(),
            base_url="https://api.openai.com/v1",
            timeout=30.0,
            max_retries=0,
        )
        client = catalog_client(
            api=api, model_id=self.provider.model or "", answer_tokens=answer_tokens
        )
        counted = _CountedClient(client)
        try:
            yield counted
        finally:
            with suppress(Exception):
                api.close()
            self.usage.record(
                "model",
                purpose=purpose,
                model=self.provider.model or "",
                requests=counted.requests,
                input_tokens=client.input_tokens,
                output_tokens=client.output_tokens,
            )

    def get_plan(self, plan_id: str) -> dict:
        if plan_id not in self.plans:
            raise WorkspaceError("plan_not_found", "Plan was not found in this workspace.", 404)
        return self.plans[plan_id]

    def get_job(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            raise WorkspaceError("job_not_found", "Job was not found in this workspace.", 404)
        return self.jobs[job_id]

    def source(self, plan: dict) -> Path:
        path = self.datasets.get(plan["dataset_id"])
        if path is None:
            raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
        if sha256(path.read_bytes()).hexdigest() != plan["source_sha256"]:
            raise WorkspaceError(
                "source_changed", "Source workbook changed. Create a new plan.", 409
            )
        return path

    def lookup_available(self) -> bool:
        """Whether a fallback plan could be created and run now: no promise about a request."""
        return (
            self.settings.allow_web_lookup
            and self.provider.key_present
            and not self._lookup_without_stand_in()
            and (self.settings.allow_network or self.offline_executor_factory is not None)
        )

    def _lookup_without_stand_in(self) -> bool:
        """An application assembled offline (tests) that was given no stand-in client.

        Its runs skip the network gate, so a lookup would reach the real service; it is
        refused instead. A normally started server has no offline executor and is not
        affected.
        """
        return self.offline_executor_factory is not None and self.offline_lookup_api is None

    def lookup_gate(self) -> None:
        """Refuse the web lookup unless it is switched on and the server has a key for it.

        The key is the planner's: the one in the environment or given in the provider
        settings. The planner's model is not needed, the lookup has its own.
        """
        if not self.settings.allow_web_lookup or self._lookup_without_stand_in():
            raise WorkspaceError(
                "web_lookup_disabled", "The universal search is not enabled on this server.", 403
            )
        if not self.provider.key_present:
            raise WorkspaceError(
                "web_lookup_no_key", "The universal search needs an OpenAI API key on the server."
            )

    @staticmethod
    def plan_gate(draft: dict, plan: EnrichmentPlan) -> None:
        """A plan with the web lookup is a fallback draft and nothing else is.

        Whatever the draft says, the plan decides: a draft of another kind, or one whose
        kind and plan do not agree, is not run, validated or approved.
        """
        kind = draft.get("kind", "planner")
        if kind not in ("planner", FALLBACK_KIND) or is_fallback_plan(plan) != (
            kind == FALLBACK_KIND
        ):
            raise WorkspaceError(
                "web_lookup_plan_refused",
                "A plan that searches the web can only be made by the universal search.",
            )

    def validate(self, draft: dict) -> dict:
        path = self.source(draft)
        if draft["plan"] is None:
            return {"valid": False, "errors": [{"code": "no_plan", "message": "No ready plan."}]}
        plan = EnrichmentPlan.model_validate(draft["plan"])
        try:
            self.plan_gate(draft, plan)
        except WorkspaceError as exc:
            return {"valid": False, "errors": [{"code": exc.code, "message": exc.message}]}
        selection = TableSelection(draft["sheet_name"], draft["range_ref"])
        profile = profile_worksheet(
            path, selection.sheet_name, draft["dataset_id"], selection=selection
        )
        try:
            registry = (
                fallback_registry(plan, profile.columns)
                if draft.get("kind") == FALLBACK_KIND
                else builtin_registry()
            )
        except FallbackPlanError as exc:
            return {
                "valid": False,
                "errors": [{"code": "fallback_plan_invalid", "message": str(exc)}],
            }
        # The same reduction the planner's loop applied: the plan writes the chosen columns
        # minus the unfilled ones, and a wrongly named unfilled target is reported here too.
        policy, unfilled_errors = apply_unfilled_targets(
            _validation_policy(draft["output_columns"]), unfilled_targets_of(draft), plan
        )
        result = PlanValidator(registry, profile, policy).validate(
            plan, instruction=draft["instruction"]
        )
        if unfilled_errors:
            result = ValidationResult(errors=[*unfilled_errors, *result.errors])
        validation = result.model_dump(mode="json")
        if unfilled_errors or any(
            error["code"] == "OUTPUT_TARGET_MISMATCH" for error in validation["errors"]
        ):
            # Already reported precisely; the coordinate checks below need matching names.
            return validation
        try:
            build_output_header_writes(
                plan,
                selection,
                output_columns=planned_columns(draft),
                existing_headers={
                    get_column_letter(selection.bounds[0] + index): col.name
                    for index, col in enumerate(profile.columns)
                },
            )
            # Keep outputs within the existing workspace's column envelope.
            if any(column_index_from_string(c) > 100 for c in planned_columns(draft).values()):
                raise ValueError("Output column limit")
        except ValueError:
            validation["errors"].append(
                {
                    "code": "invalid_output_mapping",
                    "message": "Map outputs to distinct matching or empty columns within A:CV.",
                }
            )
            validation["valid"] = False
            validation["contract_valid"] = False
        return validation

    def _selected_table(self, body: PlanRequest) -> tuple[Path, DatasetProfile]:
        """The workbook and the profile of the selected table, within the run's limits."""
        path = self.datasets.get(body.dataset_id)
        if path is None:
            raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
        if len(body.model_dump_json().encode()) > MAX_PLAN_BYTES:
            raise WorkspaceError("plan_limit", "Plan request exceeds 64 KiB.")
        try:
            selection = TableSelection(body.sheet_name, body.range_ref)
            min_col, min_row, max_col, max_row = selection.bounds
            if max_row - min_row > MAX_RUN_ROWS or max_col > 100 or min_col < 1:
                raise ValueError("Selection limit")
            profile = profile_worksheet(path, body.sheet_name, body.dataset_id, selection=selection)
            if profile.row_count is None or profile.row_count < 1:
                raise ValueError("Empty selection")
        except ValueError as exc:
            raise WorkspaceError(
                "invalid_selection", "Select a valid table with 1–20 data rows within A:CV."
            ) from exc
        return path, profile

    def _store_draft(
        self,
        body: PlanRequest,
        path: Path,
        plan: EnrichmentPlan | None,
        *,
        decision: str,
        reason: str | None,
        planning_attempts: list,
        source: str,
        kind: str | None = None,
        unfilled_targets: Sequence[str] = (),
    ) -> dict:
        """Make, fingerprint, validate and keep the draft; the same for every kind."""
        draft = {
            **body.model_dump(mode="json", exclude={"allow_network", "plan"}),
            "plan": plan.model_dump(mode="json") if plan else None,
            "source_sha256": sha256(path.read_bytes()).hexdigest(),
            "planning_status": decision,
            "reason": reason,
            "planning_attempts": planning_attempts,
            # The chosen columns the planner found no source for. Inside what the
            # fingerprint covers, so a client cannot change it after approval.
            "unfilled_targets": list(unfilled_targets),
            # Only a fallback draft has a kind; it is inside what the fingerprint covers.
            **({"kind": kind} if kind else {}),
        }
        fingerprint = draft_fingerprint(draft)
        draft.update(
            plan_id=uuid4().hex,
            fingerprint=fingerprint,
            status="draft",
            created_at=timestamp(),
            approved_at=None,
        )
        draft["validation"] = self.validate(draft)
        self.plans[draft["plan_id"]] = draft
        self.usage.record(
            "plan",
            # Asked from the model, brought along (an example or a saved plan), or built
            # by the server for the web lookup.
            source=source,
            status=decision,
            valid=bool(draft["validation"]["valid"]),
            attempts=len(planning_attempts),
            steps=len(plan.steps) if plan else 0,
            outputs=len(body.output_columns),
            capabilities=sorted({step.capability for step in plan.steps}) if plan else [],
        )
        return draft

    def create_plan(self, body: PlanRequest) -> dict:
        if body.plan is not None and is_fallback_plan(body.plan):
            # Wherever a plan comes from, the web lookup only comes from its own route.
            raise WorkspaceError(
                "web_lookup_plan_refused",
                "A plan that searches the web can only be made by the universal search.",
            )
        path, profile = self._selected_table(body)
        plan = body.plan
        decision, reason = "ready", None
        planning_attempts = []
        # A supplied plan has none: it must write every chosen column.
        unfilled: list[str] = []
        if plan is None:
            self.model_gate(body.allow_network)
            try:
                with self.model_client(answer_tokens=4096, purpose="plan") as client:
                    generated = LLMPlanner(client=client, registry=builtin_registry()).generate(
                        instruction=body.instruction,
                        dataset_profile=profile,
                        # The same policy as the final validation, so its diagnostics
                        # (step limit, requested output targets) can still be repaired.
                        validation_policy=_validation_policy(body.output_columns),
                    )
                plan = generated.response.plan
                decision, reason = generated.response.status, generated.response.reason
                planning_attempts = [asdict(attempt) for attempt in generated.attempts]
                unfilled = list(generated.unfilled_targets)
            except PlanGenerationError as exc:
                codes = sorted(
                    {code for attempt in exc.attempts for code in attempt.diagnostic_codes}
                )
                self.usage.record("plan_failed", attempts=len(exc.attempts))
                raise WorkspaceError(
                    "planning_failed",
                    f"Planning failed after {len(exc.attempts)} completion attempts: "
                    + ", ".join(codes)
                    + ".",
                    502,
                ) from None
            except Exception as exc:
                self.usage.record("plan_failed", attempts=0)
                raise WorkspaceError(
                    "planning_failed", "The model could not return a usable planning response.", 502
                ) from exc
            if plan is not None and is_fallback_plan(plan):
                # Not reachable while the planner is not offered the capability; kept so
                # that what the model returns can never become a lookup plan.
                self.usage.record("plan_failed", attempts=len(planning_attempts))
                raise WorkspaceError(
                    "web_lookup_plan_refused",
                    "A plan that searches the web can only be made by the universal search.",
                )
        return self._store_draft(
            body,
            path,
            plan,
            decision=decision,
            reason=reason,
            planning_attempts=planning_attempts,
            source="model" if body.plan is None else "supplied",
            unfilled_targets=unfilled,
        )

    def create_fallback_plan(self, body: FallbackPlanRequest) -> dict:
        """The one-step universal-search plan the server builds, kept as a draft."""
        self.lookup_gate()
        # The same request fields and the same checks of the table as a normal plan.
        as_plan_request = PlanRequest(
            dataset_id=body.dataset_id,
            sheet_name=body.sheet_name,
            range_ref=body.range_ref,
            instruction=body.question,
            output_columns=body.output_columns,
            existing_outputs=body.existing_outputs,
            sources_in_workbook=body.sources_in_workbook,
        )
        path, profile = self._selected_table(as_plan_request)
        try:
            request = FallbackRequest(
                question=body.question,
                context_columns=tuple(body.context_columns),
                targets=tuple(
                    FallbackTarget(column=item.column, kind=item.kind, unit=item.unit)
                    for item in body.targets
                ),
            )
            plan = build_fallback_plan(request, profile.columns)
        except ValidationError as exc:
            message = str(exc.errors()[0]["msg"]).removeprefix("Value error, ")
            raise WorkspaceError("fallback_plan_invalid", message) from None
        except FallbackPlanError as exc:
            raise WorkspaceError("fallback_plan_invalid", str(exc)) from None
        return self._store_draft(
            as_plan_request,
            path,
            plan,
            decision="ready",
            reason=None,
            planning_attempts=[],
            source="fallback",
            kind=FALLBACK_KIND,
        )

    def _routes(self) -> None:
        router = self.router

        @router.get("/limits")
        async def limits():
            # What one run accepts, so a client can say so before it is refused.
            return {
                # A table of up to this size can be read and looked through;
                # one run fills at most max_run_rows of its rows.
                "max_table_rows": MAX_TABLE_ROWS,
                "max_table_cells": MAX_TABLE_CELLS,
                "max_run_rows": MAX_RUN_ROWS,
                "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
                "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                # What a plan may use here; a road distance only with its key.
                "capabilities": sorted(spec.id for spec in builtin_registry().list_all()),
                # Whether the universal search can be asked for here: switched on, with a
                # key and, for a live run, the network gate open.
                "web_lookup": self.lookup_available(),
            }

        @router.get("/capabilities")
        async def capabilities():
            # What a plan may be built from here, with each contract and source:
            # the list a client shows instead of keeping one of its own.
            return {"capabilities": offered_descriptions()}

        @router.post("/task-ideas")
        async def task_ideas(body: TaskIdeasRequest):
            async with self.lock:
                path = self.datasets.get(body.dataset_id)
                if path is None:
                    raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
                # Workbooks come and go; what was kept for a removed one goes too.
                for kept in (self.task_ideas, self.task_idea_counts):
                    for stale in set(kept) - set(self.datasets):
                        del kept[stale]
                # One chosen column asks the same question as none.
                chosen = tuple(body.fill_columns) if len(body.fill_columns) > 1 else ()
                model = self.provider.model or ""
                key = (model, body.sheet_name, body.range_ref, body.language, chosen)
                known = None if body.again else self.task_ideas.get(body.dataset_id, {}).get(key)
                if known is not None:
                    return {"tasks": list(known)}
                self.model_gate(body.allow_network)
                if self.suggesting:
                    raise WorkspaceError("suggestions_busy", "Wait for the current answer.", 409)
                if self.task_idea_counts.get(body.dataset_id, 0) >= MAX_TASK_IDEA_REQUESTS:
                    raise WorkspaceError(
                        "task_idea_limit", "This workbook has asked the model too many times.", 429
                    )
                self.task_idea_counts[body.dataset_id] = (
                    self.task_idea_counts.get(body.dataset_id, 0) + 1
                )
                self.suggesting = True
            try:
                ideas = await run_in_threadpool(self.task_ideas_once, path, body)
            except TaskIdeaError:
                raise WorkspaceError(
                    "task_ideas_failed", "The model did not return usable tasks.", 502
                ) from None
            finally:
                self.suggesting = False
            if body.dataset_id in self.datasets:
                self.task_ideas.setdefault(body.dataset_id, {})[key] = ideas
            return {"tasks": list(ideas)}

        @router.post("/plans", status_code=201)
        async def create(body: PlanRequest):
            async with self.lock:
                self.make_room_for_plan()
                return await run_in_threadpool(self.create_plan, body)

        @router.post("/fallback-plans", status_code=201)
        async def create_fallback(body: FallbackPlanRequest):
            async with self.lock:
                # Before anything is dropped or read.
                self.lookup_gate()
                self.make_room_for_plan()
                return await run_in_threadpool(self.create_fallback_plan, body)

        @router.get("/plans/{plan_id}")
        async def read(plan_id: str):
            return self.get_plan(plan_id)

        @router.post("/plans/{plan_id}/validate")
        async def validate(plan_id: str):
            async with self.lock:
                draft = self.get_plan(plan_id)
                draft["validation"] = await run_in_threadpool(self.validate, draft)
                return draft["validation"]

        @router.post("/plans/{plan_id}/approve")
        async def approve(plan_id: str, body: ApprovalRequest):
            async with self.lock:
                draft = self.get_plan(plan_id)
                if body.fingerprint != draft["fingerprint"]:
                    raise WorkspaceError(
                        "approval_mismatch", "Approve the displayed fingerprint.", 409
                    )
                draft["validation"] = await run_in_threadpool(self.validate, draft)
                if not draft["validation"]["valid"]:
                    raise WorkspaceError("invalid_plan", "Repair plan validation errors first.")
                draft["status"] = "approved"
                draft["approved_at"] = draft["approved_at"] or timestamp()
                return draft

        @router.post("/plans/{plan_id}/runs", status_code=202)
        async def run(plan_id: str, body: RunRequest):
            async with self.lock:
                draft = self.get_plan(plan_id)
                return self.start_job(draft, body)

        @router.get("/jobs/{job_id}")
        async def status(job_id: str):
            return self.get_job(job_id)

        @router.post("/jobs/{job_id}/cancel", status_code=202)
        async def cancel(job_id: str):
            job = self.get_job(job_id)
            if job["status"] not in TERMINAL:
                self.cancel_events[job_id].set()
            return job

        @router.get("/jobs/{job_id}/review")
        async def review(job_id: str):
            job = self.get_job(job_id)
            report, draft = self.resolution_report(job)
            return review_payload(job_id, report, EnrichmentPlan.model_validate(draft["plan"]))

        @router.post("/jobs/{job_id}/review/{operation_id}/search")
        async def search(job_id: str, operation_id: str, body: SearchRequest):
            async with self.lock:
                job = self.get_job(job_id)
                if job["status"] != "needs_review":
                    raise WorkspaceError(
                        "review_not_available", "This job has nothing left to review.", 409
                    )
                report, draft = self.resolution_report(job)
                plan = EnrichmentPlan.model_validate(draft["plan"])
                operation = (
                    next(
                        (item for item in report.operations if item.operation_id == operation_id),
                        None,
                    )
                    if report
                    else None
                )
                try:
                    if operation is None:
                        raise ValueError("Unknown operation.")
                    capability = other_name_capability(operation, plan)
                    query = operation.report.candidate_set.query
                    searched_as = usable_other_name(body.name, query.name)
                except ValueError:
                    raise WorkspaceError(
                        "invalid_search", "This name cannot be searched for this item."
                    ) from None
                if self.offline_executor_factory is None:
                    self.network_gate(body.allow_network)
                if self.searching:
                    raise WorkspaceError("search_busy", "Wait for the current search.", 409)
                if self.search_counts.get(job_id, 0) >= MAX_REVIEW_SEARCHES:
                    raise WorkspaceError("search_limit", "This run has had too many searches.", 429)
                self.search_counts[job_id] = self.search_counts.get(job_id, 0) + 1
                self.usage.record("name_search")
                self.searching = True
            try:
                found = await run_in_threadpool(
                    self.search_once,
                    capability,
                    draft["mountain_resolution_policy"],
                    query,
                    searched_as,
                )
            except Exception:
                logging.getLogger(__name__).exception("Search under another name failed")
                raise WorkspaceError(
                    "search_failed", "The search could not be completed.", 502
                ) from None
            finally:
                self.searching = False
            # The job may have been dropped while the source was being asked.
            if job_id in self.jobs:
                self.searches.setdefault(job_id, {})[operation_id] = found
            return searched_item(operation, found, plan)

        @router.post("/jobs/{job_id}/review/{operation_id}/suggestions")
        async def suggestions(job_id: str, operation_id: str, body: SuggestionRequest):
            async with self.lock:
                job = self.get_job(job_id)
                if job["status"] != "needs_review":
                    raise WorkspaceError(
                        "review_not_available", "This job has nothing left to review.", 409
                    )
                report, draft = self.resolution_report(job)
                plan = EnrichmentPlan.model_validate(draft["plan"])
                operation = (
                    next(
                        (item for item in report.operations if item.operation_id == operation_id),
                        None,
                    )
                    if report
                    else None
                )
                try:
                    if operation is None:
                        raise ValueError("Unknown operation.")
                    capability = other_name_capability(operation, plan)
                except ValueError:
                    raise WorkspaceError(
                        "invalid_search", "Other names cannot be searched for this item."
                    ) from None
                known = self.suggestions.get(job_id, {}).get(operation_id)
                if known is not None:
                    return {"operation_id": operation_id, "names": list(known)}
                self.model_gate(body.allow_network)
                if self.suggesting:
                    raise WorkspaceError("suggestions_busy", "Wait for the current answer.", 409)
                if self.suggestion_counts.get(job_id, 0) >= MAX_REVIEW_SUGGESTIONS:
                    raise WorkspaceError(
                        "suggestion_limit", "This run has asked the model too many times.", 429
                    )
                self.suggestion_counts[job_id] = self.suggestion_counts.get(job_id, 0) + 1
                self.suggesting = True
            query = operation.report.candidate_set.query
            # The request is audited whether or not it is answered.
            asked = {
                "job_id": job_id,
                "operation_id": operation_id,
                "name": query.name,
                "model": "offline_fixture" if self.offline_plan_client else self.provider.model,
                "asked_at": timestamp(),
                "outcome": "failed",
                "proposed": [],
            }
            if job_id in self.jobs:
                self.model_requests.setdefault(job_id, []).append(asked)
            try:
                names = await run_in_threadpool(self.suggest_once, query, capability)
            except SuggestionError:
                raise WorkspaceError(
                    "suggestions_failed", "The model did not return usable names.", 502
                ) from None
            finally:
                self.suggesting = False
            asked.update(outcome="answered", proposed=list(names))
            if job_id in self.jobs:
                self.suggestions.setdefault(job_id, {})[operation_id] = names
            return {"operation_id": operation_id, "names": list(names)}

        @router.post("/jobs/{job_id}/resume", status_code=202)
        async def resume(job_id: str, body: ResumeRequest):
            async with self.lock:
                job = self.get_job(job_id)
                if job["status"] != "needs_review":
                    raise WorkspaceError(
                        "review_not_available", "This job has nothing left to review.", 409
                    )
                report, draft = self.resolution_report(job)
                source = self.source(draft)
                plan = EnrichmentPlan.model_validate(draft["plan"])
                try:
                    if report is None:
                        raise ValueError("No audited resolution report.")
                    manifest = approvals_from_decisions(
                        body.decisions, report, workbook_sha256=draft["source_sha256"]
                    )
                    selections = {
                        **(
                            build_resume_selections(
                                manifest=manifest, report=report, plan=plan, source_path=source
                            )
                            if manifest is not None
                            else {}
                        ),
                        # A name the user gave is searched by the new job, under
                        # its limits; nothing is looked up here.
                        **build_other_names(
                            names=names_from_decisions(body.decisions), report=report, plan=plan
                        ),
                        # A candidate chosen from a search under another name is
                        # checked against the server's record of that search.
                        **build_searched_selections(
                            choices=searched_from_decisions(body.decisions),
                            searches=self.searches.get(job_id, {}),
                            report=report,
                            plan=plan,
                        ),
                    }
                except ValueError:
                    # Validation messages can name record IDs; keep the public error generic.
                    raise WorkspaceError(
                        "invalid_decision",
                        "Choose listed, selectable candidates, or give another name to "
                        "search for, for operations that need review.",
                    ) from None
                if not selections:
                    raise WorkspaceError(
                        "no_decisions", "Choose a candidate or give another name for one item."
                    )
                # A resume starts from the source workbook again, so the choices
                # the reviewed job itself ran with are part of the next one: a
                # second round of review must not undo the first.
                selections = {**self.selections.get(job_id, {}), **selections}
                # The same holds for the audit of the names behind them: who
                # gave each name, and what the model was asked on the way.
                given = other_name_audit(
                    body.decisions, report, self.suggestions.get(job_id, {}), job_id
                )
                earlier = self.assistance.get(job_id, {"other_names": [], "model_requests": []})
                renamed = {entry["name"] for entry in given}
                assistance = {
                    "other_names": [
                        *(e for e in earlier["other_names"] if e["name"] not in renamed),
                        *given,
                    ],
                    "model_requests": [
                        *earlier["model_requests"],
                        *self.model_requests.get(job_id, []),
                    ],
                }
                return self.start_job(
                    draft,
                    body,
                    resume_selections=selections,
                    previous_job_id=job_id,
                    assistance=assistance if any(assistance.values()) else None,
                    decisions=[
                        # A name is recorded only where one was given.
                        d.model_dump(mode="json", exclude=set() if d.name else {"name"})
                        for d in body.decisions
                    ],
                )

        @router.get("/jobs/{job_id}/artifacts/{artifact}")
        async def artifact(job_id: str, artifact: Literal["result", "report"]):
            job = self.get_job(job_id)
            if artifact not in job["artifacts"]:
                raise WorkspaceError("artifact_not_ready", "This artifact is not available.", 409)
            directory = self.datasets[job["dataset_id"]].parent / job_id
            name = "result.xlsx" if artifact == "result" else "report.json"
            # The interface reads the report by itself after a run; only the
            # filled table is something a visitor takes away.
            if artifact == "result":
                self.usage.record("download")
            return FileResponse(directory / name, filename=f"{job_id}-{name}")

    def task_ideas_once(self, path: Path, body: TaskIdeasRequest) -> tuple[str, ...]:
        """Profile the table as planning does and ask the model once."""
        try:
            selection = TableSelection(body.sheet_name, body.range_ref)
            min_col, min_row, max_col, max_row = selection.bounds
            if max_row - min_row > MAX_TABLE_ROWS or max_col > 100 or min_col < 1:
                raise ValueError("Selection limit")
            profile = profile_worksheet(path, body.sheet_name, body.dataset_id, selection=selection)
        except ValueError as exc:
            raise WorkspaceError(
                "invalid_selection", "Select a valid table within the size the server reads."
            ) from exc
        with self.model_client(answer_tokens=1024, purpose="task_ideas") as client:
            proposal = propose_tasks(
                client,
                profile=profile,
                registry=builtin_registry(),
                language=body.language,
                fill_columns=body.fill_columns,
            )
        # Why nothing is shown, when nothing is: as numbers, without the text.
        self.usage.record(
            "task_ideas",
            language=body.language,
            again=int(body.again),
            columns=len(body.fill_columns) if len(body.fill_columns) > 1 else 0,
            proposed=proposal.proposed,
            too_long=proposal.too_long,
            incomplete=proposal.incomplete,
            internal_names=proposal.internal_names,
            shown=len(proposal.tasks),
        )
        return proposal.tasks

    def suggest_once(self, query: ResolutionQuery, capability: str) -> tuple[str, ...]:
        """Ask the model once for other names; nothing is searched here."""
        with self.model_client(answer_tokens=1024, purpose="other_names") as client:
            return suggest_names(client, query=query, capability=capability)

    def search_once(
        self,
        capability: OtherNameCapability,
        mountain_policy: MountainPolicy,
        query: ResolutionQuery,
        searched_as: str,
    ) -> ResolutionReport:
        """Ask the source for one name, with the lookup and limits a job would use."""
        factory = self.offline_executor_factory or live_executor
        executor = factory(frozenset({capability}), mountain_policy, MAX_RUN_ROWS)
        service, policy = executor.audited_resolution(capability)
        return search_under(
            service, policy, capability=capability, query=query, searched_as=searched_as
        )

    def resolution_report(self, job: dict) -> tuple[ResolutionJobReport | None, dict]:
        """Read the audited decisions of a finished job from its private report."""
        if job["status"] not in {"completed", "needs_review"}:
            raise WorkspaceError(
                "review_not_available", "Review is available after a finished run.", 409
            )
        directory = self.datasets[job["dataset_id"]].parent / job["job_id"]
        stored = json.loads((directory / "report.json").read_text("utf-8"))
        payload = stored["execution"].get("resolution_report")
        report = ResolutionJobReport.model_validate_json(json.dumps(payload)) if payload else None
        return report, stored["approved_plan"]

    def start_job(
        self,
        draft: dict,
        body: RunRequest,
        *,
        resume_selections: dict | None = None,
        previous_job_id: str | None = None,
        decisions: list[dict] | None = None,
        assistance: dict[str, list[dict]] | None = None,
    ) -> dict:
        """Queue one run of an approved plan; a resume also carries user approvals."""
        if draft["status"] != "approved":
            raise WorkspaceError("approval_required", "Explicitly approve this plan first.", 409)
        source = self.source(draft)
        plan = EnrichmentPlan.model_validate(draft["plan"])
        # What was approved is what runs: the draft must still be what its fingerprint
        # covers (request, plan and kind), and the kind must agree with the plan.
        if draft["fingerprint"] != draft_fingerprint(draft):
            raise WorkspaceError("approval_mismatch", "Approve the displayed fingerprint.", 409)
        self.plan_gate(draft, plan)
        fallback = draft.get("kind") == FALLBACK_KIND
        if fallback:
            # Switched off, or the key removed, since the draft was made: not run.
            self.lookup_gate()
            if resume_selections is not None or previous_job_id is not None:
                raise WorkspaceError(
                    "review_not_available", "This job has nothing left to review.", 409
                )
        if self.offline_executor_factory is None:
            self.network_gate(body.allow_network)
        if any(job["status"] not in TERMINAL for job in self.jobs.values()):
            raise WorkspaceError("job_busy", "Wait for or cancel the current run.", 409)
        self.make_room_for_job()
        job_id = uuid4().hex
        directory = source.parent / job_id
        directory.mkdir()
        # Runs of one plan share what the sources confirmed: a resume after
        # review asks only for what the earlier run could not settle.
        reuse_directory = source.parent / f"plan-{draft['plan_id']}"
        reuse_directory.mkdir(exist_ok=True)
        job = {
            "job_id": job_id,
            "plan_id": draft["plan_id"],
            "dataset_id": draft["dataset_id"],
            "fingerprint": draft["fingerprint"],
            "status": "queued",
            "created_at": timestamp(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "metrics": None,
            # Finished and total rows, and what each step did over the finished
            # rows (counts only), once the worker has read the table.
            "progress": None,
            "artifacts": {},
            "execution_mode": "offline_fixture" if self.offline_executor_factory else "live",
            # A fallback run has the longest limit a run may have unless the request names
            # a shorter one: its rows are asked one after another and each takes seconds.
            "timeout_seconds": (
                MAX_TIMEOUT_SECONDS
                if fallback and "timeout_seconds" not in body.model_fields_set
                else body.timeout_seconds
            ),
            # A resume is a new job; the earlier job and its report stay untouched.
            "previous_job_id": previous_job_id,
            "review_decisions": decisions,
            **({"kind": FALLBACK_KIND} if fallback else {}),
        }
        lookup = None
        if fallback:
            lookup = LookupSettings(
                model=self.settings.web_lookup_model,
                reasoning_effort=WEB_LOOKUP_REASONING_EFFORT,
                api_key=SecretStr(self.provider.api_key() or ""),
                deadline_seconds=lookup_deadline_seconds(job["timeout_seconds"]),
                api_factory=self.offline_lookup_api or openai_api,
            )
        if assistance is not None:
            self.assistance[job_id] = assistance
        task = RunTask(
            job_id=job_id,
            dataset_id=draft["dataset_id"],
            source_path=source,
            source_sha256=draft["source_sha256"],
            directory=directory,
            selection=TableSelection(draft["sheet_name"], draft["range_ref"]),
            plan=plan,
            # What the plan writes; an unfilled target is neither created nor touched.
            output_columns=planned_columns(draft),
            mountain_policy=draft["mountain_resolution_policy"],
            # Drafts made before the option existed keep what they had.
            existing_outputs=draft.get("existing_outputs", "keep"),
            sources_in_workbook=draft.get("sources_in_workbook", "none"),
            resume_selections=resume_selections,
            previous_job_id=previous_job_id,
            reuse_directory=reuse_directory,
            lookup=lookup,
        )
        self.jobs[job_id] = job
        self.selections[job_id] = dict(resume_selections or {})
        self.directories[job_id] = directory
        self.cancel_events[job_id] = asyncio.Event()
        self.usage.record(
            "run_started", resume=previous_job_id is not None, mode=job["execution_mode"]
        )
        # The task inherits the visitor of this request, so the end of the run
        # is counted for the same visitor.
        self.tasks[job_id] = asyncio.create_task(self.supervise(task, draft, job))
        return dict(job)

    async def supervise(self, task: RunTask, draft: dict, job: dict) -> None:
        public_job = job
        job = {**job, "artifacts": {}}
        context = multiprocessing.get_context("spawn")
        # Two counters the worker writes and this side only reads: how far the
        # run is. Results still arrive only through the worker's files.
        step_ids = [step.id for step in task.plan.steps]
        counters = context.Array(
            "i", progress_slots(len(step_ids), lookup=task.lookup is not None), lock=False
        )
        process = context.Process(
            target=execute_workbook,
            args=(task, self.offline_executor_factory or live_executor, counters),
        )

        def publish_progress() -> None:
            seen = read_progress(counters, step_ids)
            if seen is None:
                return
            done, total, steps = seen
            if total > 0 and (job["progress"] or {}).get("rows_done") != done:
                # Counts only: per step, the finished rows it filled, failed or skipped.
                job["progress"] = {
                    "rows_done": min(done, total),
                    "rows_total": total,
                    "steps": steps,
                }
                public_job["progress"] = job["progress"]

        execution: dict = {}
        started = monotonic()
        try:
            job.update(status="running", started_at=timestamp())
            public_job.update(status="running", started_at=job["started_at"])
            process.start()
            while process.is_alive():
                if self.cancel_events[task.job_id].is_set():
                    job["status"] = "cancelled"
                    break
                if monotonic() - started >= job["timeout_seconds"]:
                    job["status"] = "timed_out"
                    break
                publish_progress()
                await asyncio.sleep(0.05)
            # The last rows may have finished between two looks.
            publish_progress()
            # Cancellation wins even if requested just as the worker exits.
            if self.cancel_events[task.job_id].is_set():
                job["status"] = "cancelled"
            elif monotonic() - started >= job["timeout_seconds"]:
                job["status"] = "timed_out"
            if job["status"] in {"cancelled", "timed_out"}:
                job["error"] = {"code": job["status"], "message": "Run stopped; results discarded."}
            elif process.exitcode != 0 or (task.directory / "error.json").exists():
                raise RuntimeError("Worker failed")
            else:
                execution = json.loads((task.directory / "execution.json").read_text("utf-8"))
                result = JobExecutionResult.model_validate_json(json.dumps(execution["execution"]))
                job["metrics"] = result.metrics.model_dump(mode="json")
                # Rows not looked up because every chosen cell already had a
                # value. Everything above and below counts the other rows only.
                job["rows_skipped"] = len(execution.get("skipped_rows", []))
                # Technical completion can still contain unresolved values.
                job["status"] = (
                    "needs_review"
                    if result.metrics.successful_rows < result.metrics.total_rows
                    else "completed"
                )
                source_rows = execution.pop("source_rows", {})
                resolution = execution["execution"].get("resolution_report")
                chosen = user_chosen_steps(resolution)
                searched = searched_steps(
                    resolution, self.assistance.get(task.job_id, {}).get("other_names", [])
                )
                automatic = auto_accepted_steps(resolution)
                execution["rows"] = [
                    {
                        "record_id": record.record_id,
                        "status": classify_row_outcome(record.row_result),
                        # Excel row number and the source values the plan used.
                        "row": source_rows.get(record.record_id, {}).get("row"),
                        "inputs": source_rows.get(record.record_id, {}).get("inputs", {}),
                        # Steps whose entity was chosen by the user during review.
                        "user_chosen_steps": sorted(chosen.get(record.record_id, ())),
                        # Steps whose value was found under another name: the name
                        # the source was asked for and who gave it (user or model).
                        "other_names": searched.get(record.record_id, {}),
                        # Steps accepted automatically while other matches stayed unconfirmed.
                        "auto_accepted_steps": sorted(automatic.get(record.record_id, ())),
                        # Output columns whose cell already held a value: kept as
                        # it was, or replaced (with the value it held).
                        "kept_outputs": source_rows.get(record.record_id, {}).get(
                            "kept_outputs", []
                        ),
                        "replaced_outputs": source_rows.get(record.record_id, {}).get(
                            "replaced_outputs", {}
                        ),
                        # Output columns left empty because the source holds no
                        # value for the field, although the step succeeded.
                        "empty_outputs": source_rows.get(record.record_id, {}).get(
                            "empty_outputs", {}
                        ),
                        # A fallback run: the values written from a web search, each
                        # as its column, its basis and its number of links.
                        **(
                            {"ai_found": source_rows.get(record.record_id, {}).get("ai_found", [])}
                            if task.lookup is not None
                            else {}
                        ),
                    }
                    for record in result.records
                ]
                job["artifacts"]["result"] = f"/api/jobs/{task.job_id}/artifacts/result"
        except Exception:
            logging.getLogger(__name__).exception("Local job supervision failed")
            job.update(
                status="failed",
                error={"code": "execution_failed", "message": "The workbook run failed."},
            )
        finally:
            # Stop and reap before cleanup or publishing any terminal artifacts.
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                await run_in_threadpool(process.join, 2)
                if process.is_alive():
                    process.kill()
                    await run_in_threadpool(process.join)
                process.close()
            if self.cancel_events[task.job_id].is_set() and job["status"] in {
                "running",
                "completed",
                "needs_review",
            }:
                job.update(
                    status="cancelled",
                    error={"code": "cancelled", "message": "Run stopped; results discarded."},
                )
            # What a lookup run asked of the API, whatever became of the run: what the
            # worker reported, or else the numbers it had shared when it stopped.
            spent: dict | None = None
            if task.lookup is not None:
                reported = execution.get("web_lookup")
                spent = {
                    "configuration_id": configuration_id(
                        task.lookup.model, task.lookup.reasoning_effort
                    ),
                    **(
                        {key: int(reported[key]) for key in LOOKUP_NUMBERS}
                        if reported
                        else read_lookup_usage(counters, len(step_ids))
                    ),
                }
            job["finished_at"] = timestamp()
            if job["status"] not in {"completed", "needs_review"}:
                execution = {}
                job["artifacts"].pop("result", None)
                # Only this worker's flat, private artifact directory is cleaned.
                for artifact in task.directory.iterdir():
                    artifact.unlink()
            job["artifacts"]["report"] = f"/api/jobs/{task.job_id}/artifacts/report"
            report = {
                "schema_version": "1.0",
                **job,
                "approved_plan": draft,
                # The chosen columns the planner found no source for: not written, not created.
                "unfilled_targets": unfilled_targets_of(draft),
                "source_sha256": task.source_sha256,
                # A resumed job: the other names it searched under, who gave
                # each, and every model request made during the review.
                "review_assistance": self.assistance.get(task.job_id),
                "limits": {
                    "max_rows": MAX_RUN_ROWS,
                    "max_steps": MAX_PLAN_STEPS,
                    "max_requests_per_provider": MAX_PROVIDER_REQUESTS,
                },
                **execution,
                **({"web_lookup": spent} if spent else {}),
            }
            (task.directory / "report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (task.directory / "execution.json").unlink(missing_ok=True)
            public_job.update(job)
            finished = execution.get("execution") or {}
            metrics = job["metrics"] or {}
            if task.lookup is not None and spent and spent["requests"]:
                # Failed requests, and those of a run that was stopped, are counted too.
                self.usage.record(
                    "model",
                    purpose="web_lookup",
                    model=task.lookup.model,
                    requests=spent["requests"],
                    input_tokens=spent["input_tokens"],
                    output_tokens=spent["output_tokens"],
                    web_searches=spent["web_searches"],
                )
            self.usage.record(
                "run_finished",
                status=job["status"],
                resume=task.previous_job_id is not None,
                rows=metrics.get("total_rows", 0),
                rows_filled=metrics.get("successful_rows", 0),
                rows_skipped=job.get("rows_skipped", 0),
                seconds=round(monotonic() - started, 1),
                # Requests that left for each source, repeats included.
                source_requests={
                    str(source): int(stats.get("transport_attempts", 0))
                    for source, stats in (finished.get("provider_requests") or {}).items()
                    if stats.get("transport_attempts")
                },
                taken_over=(finished.get("metrics") or {}).get("persistent_cache_hits", 0),
            )

    async def close(self) -> None:
        for event in self.cancel_events.values():
            event.set()
        for task in self.tasks.values():
            with suppress(asyncio.CancelledError):
                await task
