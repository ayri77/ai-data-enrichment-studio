"""Minimal source-bound plan, approval, run and artifact API for local use."""

import asyncio
import json
import logging
import multiprocessing
import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shutil import rmtree
from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from openai import OpenAI
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import JobExecutionResult, classify_row_outcome
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import build_output_header_writes
from ai_data_enrichment.plan_run_worker import (
    MAX_PLAN_STEPS,
    MAX_PROVIDER_REQUESTS,
    MAX_RUN_ROWS,
    ExecutorFactory,
    MountainPolicy,
    RunTask,
    builtin_registry,
    execute_workbook,
    live_executor,
)
from ai_data_enrichment.planning.llm_planner import (
    LLMPlanner,
    PlanCompletionClient,
    PlanGenerationError,
)
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient
from ai_data_enrichment.provider_settings import ProviderConfiguration
from ai_data_enrichment.resolution.models import ResolutionJobReport
from ai_data_enrichment.resolution.resume import build_resume_selections
from ai_data_enrichment.review import (
    ReviewDecision,
    approvals_from_decisions,
    auto_accepted_steps,
    review_payload,
    user_chosen_steps,
)
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator
from ai_data_enrichment.workspace_errors import WorkspaceError

MAX_PLANS = 32
MAX_JOBS = 16
MAX_PLAN_BYTES = 64 * 1024
TERMINAL = {"completed", "needs_review", "failed", "cancelled", "timed_out"}


class PlanBodyLimit:
    """Bound JSON before parsing, just as uploads are bounded before ingestion."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope["path"].startswith("/api/plans")
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


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class PlanRunSettings:
    allow_network: bool = False
    llm_model: str | None = None

    @classmethod
    def from_environment(cls) -> "PlanRunSettings":
        return cls(
            allow_network=os.getenv("ENRICHMENT_ALLOW_NETWORK") == "1",
            llm_model=os.getenv("OPENAI_MODEL") or None,
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


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: Literal[True]
    fingerprint: str = Field(min_length=64, max_length=64)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_network: bool = False
    timeout_seconds: int = Field(default=180, ge=1, le=300, strict=True)


class ResumeRequest(RunRequest):
    """Continue a job that needs review with explicit user choices."""

    decisions: list[ReviewDecision] = Field(min_length=1, max_length=MAX_RUN_ROWS * MAX_PLAN_STEPS)


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
    ) -> None:
        self.datasets, self.lock, self.settings = datasets, lock, settings
        self.provider = provider
        self.offline_plan_client = offline_plan_client
        self.offline_executor_factory = offline_executor_factory
        self.plans: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.directories: dict[str, Path] = {}
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

    def validate(self, draft: dict) -> dict:
        path = self.source(draft)
        if draft["plan"] is None:
            return {"valid": False, "errors": [{"code": "no_plan", "message": "No ready plan."}]}
        plan = EnrichmentPlan.model_validate(draft["plan"])
        selection = TableSelection(draft["sheet_name"], draft["range_ref"])
        profile = profile_worksheet(
            path, selection.sheet_name, draft["dataset_id"], selection=selection
        )
        validation = (
            PlanValidator(builtin_registry(), profile, _validation_policy(draft["output_columns"]))
            .validate(plan, instruction=draft["instruction"])
            .model_dump(mode="json")
        )
        if any(error["code"] == "OUTPUT_TARGET_MISMATCH" for error in validation["errors"]):
            # Already reported precisely; the coordinate checks below need matching names.
            return validation
        try:
            build_output_header_writes(
                plan,
                selection,
                output_columns=draft["output_columns"],
                existing_headers={
                    get_column_letter(selection.bounds[0] + index): col.name
                    for index, col in enumerate(profile.columns)
                },
            )
            # Keep outputs within the existing workspace's column envelope.
            if any(column_index_from_string(c) > 100 for c in draft["output_columns"].values()):
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

    def create_plan(self, body: PlanRequest) -> dict:
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
        plan = body.plan
        decision, reason = "ready", None
        planning_attempts = []
        if plan is None:
            api = None
            client = self.offline_plan_client
            if client is None:
                self.network_gate(body.allow_network)
                if not self.provider.model:
                    raise WorkspaceError(
                        "llm_not_configured", "Choose a model in Provider Settings."
                    )
                if not self.provider.key_present:
                    raise WorkspaceError("missing_api_key", "Add an API key in Provider Settings.")
            try:
                if client is None:
                    api = OpenAI(
                        api_key=self.provider.api_key(),
                        base_url="https://api.openai.com/v1",
                        timeout=30.0,
                        max_retries=0,
                    )
                    client = OpenAIPlanClient(
                        api=api,
                        model=self.provider.model or "",
                        max_output_tokens=4096,
                    )
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
            except PlanGenerationError as exc:
                codes = sorted(
                    {code for attempt in exc.attempts for code in attempt.diagnostic_codes}
                )
                raise WorkspaceError(
                    "planning_failed",
                    f"Planning failed after {len(exc.attempts)} completion attempts: "
                    + ", ".join(codes)
                    + ".",
                    502,
                ) from None
            except Exception as exc:
                raise WorkspaceError(
                    "planning_failed", "The model could not return a usable planning response.", 502
                ) from exc
            finally:
                if api is not None:
                    with suppress(Exception):
                        api.close()
        draft = {
            **body.model_dump(mode="json", exclude={"allow_network", "plan"}),
            "plan": plan.model_dump(mode="json") if plan else None,
            "source_sha256": sha256(path.read_bytes()).hexdigest(),
            "planning_status": decision,
            "reason": reason,
            "planning_attempts": planning_attempts,
        }
        fingerprint = sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
        draft.update(
            plan_id=uuid4().hex,
            fingerprint=fingerprint,
            status="draft",
            created_at=timestamp(),
            approved_at=None,
        )
        draft["validation"] = self.validate(draft)
        self.plans[draft["plan_id"]] = draft
        return draft

    def _routes(self) -> None:
        router = self.router

        @router.post("/plans", status_code=201)
        async def create(body: PlanRequest):
            async with self.lock:
                self.make_room_for_plan()
                return await run_in_threadpool(self.create_plan, body)

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
                    selections = (
                        build_resume_selections(
                            manifest=manifest, report=report, plan=plan, source_path=source
                        )
                        if manifest is not None
                        else {}
                    )
                except ValueError:
                    # Validation messages can name record IDs; keep the public error generic.
                    raise WorkspaceError(
                        "invalid_decision",
                        "Choose listed, selectable candidates for operations that need review.",
                    ) from None
                if not selections:
                    raise WorkspaceError(
                        "no_decisions", "Choose a candidate for at least one item."
                    )
                return self.start_job(
                    draft,
                    body,
                    resume_selections=selections,
                    previous_job_id=job_id,
                    decisions=[d.model_dump(mode="json") for d in body.decisions],
                )

        @router.get("/jobs/{job_id}/artifacts/{artifact}")
        async def artifact(job_id: str, artifact: Literal["result", "report"]):
            job = self.get_job(job_id)
            if artifact not in job["artifacts"]:
                raise WorkspaceError("artifact_not_ready", "This artifact is not available.", 409)
            directory = self.datasets[job["dataset_id"]].parent / job_id
            name = "result.xlsx" if artifact == "result" else "report.json"
            return FileResponse(directory / name, filename=f"{job_id}-{name}")

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
    ) -> dict:
        """Queue one run of an approved plan; a resume also carries user approvals."""
        if draft["status"] != "approved":
            raise WorkspaceError("approval_required", "Explicitly approve this plan first.", 409)
        source = self.source(draft)
        if self.offline_executor_factory is None:
            self.network_gate(body.allow_network)
        if any(job["status"] not in TERMINAL for job in self.jobs.values()):
            raise WorkspaceError("job_busy", "Wait for or cancel the current run.", 409)
        self.make_room_for_job()
        job_id = uuid4().hex
        directory = source.parent / job_id
        directory.mkdir()
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
            "artifacts": {},
            "execution_mode": "offline_fixture" if self.offline_executor_factory else "live",
            "timeout_seconds": body.timeout_seconds,
            # A resume is a new job; the earlier job and its report stay untouched.
            "previous_job_id": previous_job_id,
            "review_decisions": decisions,
        }
        task = RunTask(
            job_id=job_id,
            dataset_id=draft["dataset_id"],
            source_path=source,
            source_sha256=draft["source_sha256"],
            directory=directory,
            selection=TableSelection(draft["sheet_name"], draft["range_ref"]),
            plan=EnrichmentPlan.model_validate(draft["plan"]),
            output_columns=dict(draft["output_columns"]),
            mountain_policy=draft["mountain_resolution_policy"],
            resume_selections=resume_selections,
            previous_job_id=previous_job_id,
        )
        self.jobs[job_id] = job
        self.directories[job_id] = directory
        self.cancel_events[job_id] = asyncio.Event()
        self.tasks[job_id] = asyncio.create_task(self.supervise(task, draft, job))
        return dict(job)

    async def supervise(self, task: RunTask, draft: dict, job: dict) -> None:
        public_job = job
        job = {**job, "artifacts": {}}
        process = multiprocessing.get_context("spawn").Process(
            target=execute_workbook,
            args=(task, self.offline_executor_factory or live_executor),
        )
        execution: dict = {}
        try:
            job.update(status="running", started_at=timestamp())
            public_job.update(status="running", started_at=job["started_at"])
            started = monotonic()
            process.start()
            while process.is_alive():
                if self.cancel_events[task.job_id].is_set():
                    job["status"] = "cancelled"
                    break
                if monotonic() - started >= job["timeout_seconds"]:
                    job["status"] = "timed_out"
                    break
                await asyncio.sleep(0.05)
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
                # Technical completion can still contain unresolved values.
                job["status"] = (
                    "needs_review"
                    if result.metrics.successful_rows < result.metrics.total_rows
                    else "completed"
                )
                source_rows = execution.pop("source_rows", {})
                resolution = execution["execution"].get("resolution_report")
                chosen = user_chosen_steps(resolution)
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
                        # Steps accepted automatically while other matches stayed unconfirmed.
                        "auto_accepted_steps": sorted(automatic.get(record.record_id, ())),
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
                "source_sha256": task.source_sha256,
                "limits": {
                    "max_rows": MAX_RUN_ROWS,
                    "max_steps": MAX_PLAN_STEPS,
                    "max_requests_per_provider": MAX_PROVIDER_REQUESTS,
                },
                **execution,
            }
            (task.directory / "report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (task.directory / "execution.json").unlink(missing_ok=True)
            public_job.update(job)

    async def close(self) -> None:
        for event in self.cancel_events.values():
            event.set()
        for task in self.tasks.values():
            with suppress(asyncio.CancelledError):
                await task
