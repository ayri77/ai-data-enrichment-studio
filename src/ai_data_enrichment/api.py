"""Local, single-user dataset workspace and source-bound Plan/Run API."""

import asyncio
import logging
from collections import deque
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from hmac import compare_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4
from xml.etree.ElementTree import ParseError
from zipfile import ZipFile

from defusedxml.ElementTree import iterparse
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from openpyxl.utils.cell import coordinate_to_tuple
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from ai_data_enrichment import sample_catalog
from ai_data_enrichment.ingestion.excel_inspector import inspect_workbook
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.profiling_limits import ProfilingLimits
from ai_data_enrichment.ingestion.table_rows import read_table_rows
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.plan_run import PlanBodyLimit, PlanRunService, PlanRunSettings
from ai_data_enrichment.plan_run_worker import ExecutorFactory, builtin_registry
from ai_data_enrichment.planning.llm_planner import PlanCompletionClient
from ai_data_enrichment.provider_settings import ProviderConfiguration, configuration_router
from ai_data_enrichment.run_limits import MAX_TABLE_CELLS, MAX_TABLE_ROWS
from ai_data_enrichment.usage import VISITOR, UsageLog
from ai_data_enrichment.workspace_errors import WorkspaceError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ROWS = MAX_TABLE_ROWS + 1  # Includes the header.
MAX_COLUMNS = 100
MAX_CELLS = MAX_TABLE_CELLS
MAX_DATASETS = 8
# Hosted access code: unauthenticated paths, and limits on wrong-code attempts.
ACCESS_OPEN_PATHS = frozenset({"/api/health", "/api/access", "/api/usage"})
# The label of the one code given as `access_code`.
SHARED_VISITOR = "shared"
ACCESS_MAX_FAILURES = 20
ACCESS_WINDOW_SECONDS = 60.0
ACCESS_FAILURE_DELAY_SECONDS = 0.4
PREVIEW_LIMITS = ProfilingLimits()  # Shared defaults: 5 rows, 200 chars, 12 KB.
logger = logging.getLogger(__name__)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet_name: str = Field(min_length=1, max_length=31)
    range_ref: str = Field(min_length=1, max_length=32)


def _check_bounds(bounds: tuple[int, int, int, int]) -> None:
    min_col, min_row, max_col, max_row = bounds
    if (
        max_row > MAX_ROWS
        or max_col > MAX_COLUMNS
        or (max_col - min_col + 1) * (max_row - min_row + 1) > MAX_CELLS
    ):
        raise WorkspaceError(
            "worksheet_limit",
            "Use a table within 5,001 rows, 100 columns and 100,000 cells.",
        )


def _validate_archive(path: Path) -> None:
    """Bound decompression and sparse XML coordinates before ingestion scans rows.

    This only checks the resource envelope; Excel semantics belong to ingestion.
    Never extract archive entries or trust their filenames as filesystem paths.
    """
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise WorkspaceError("workbook_limit", "Workbook expands beyond the local size limit.")
        # Check every XML entry: relationship targets need not use conventional paths.
        sheet_count = 0
        for entry in entries:
            max_row = max_col = cells = rows = 0
            # Binary parts fail XML parsing immediately. XML relationship targets
            # are checked regardless of extension; malformed sheets fail ingestion.
            with archive.open(entry) as stream, suppress(ParseError):
                for _, element in iterparse(stream, events=("end",)):
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag == "sheet":
                        sheet_count += 1
                        if sheet_count > 16:
                            raise WorkspaceError(
                                "worksheet_limit", "Use a workbook with up to 16 worksheets."
                            )
                    elif tag == "dimension":
                        _check_bounds(TableSelection("sheet", element.attrib["ref"]).bounds)
                    elif tag == "row":
                        rows += 1
                        max_row = max(max_row, int(element.attrib.get("r", rows)), rows)
                    elif tag == "c":
                        cells += 1
                        row, col = coordinate_to_tuple(element.attrib["r"])
                        max_row, max_col = max(max_row, row), max(max_col, col)
                    if cells > MAX_CELLS:
                        raise WorkspaceError("worksheet_limit", "Worksheet exceeds 100,000 cells.")
                    _check_bounds((1, 1, max(1, max_col), max(1, max_row)))
                    element.clear()


def _inspect(path: Path) -> dict:
    try:
        _validate_archive(path)
        summary = inspect_workbook(path)
        if sum(len(name.encode("utf-8")) for ws in summary.worksheets for name in ws.columns) > (
            PREVIEW_LIMITS.max_profile_bytes
        ):
            raise WorkspaceError(
                "workbook_limit", "Worksheet headers exceed the metadata size limit."
            )
        return asdict(summary)
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError(
            "invalid_workbook", "Cannot read this workbook. Upload a valid, unencrypted .xlsx file."
        ) from exc


def _preview(path: Path, dataset_id: str, body: PreviewRequest) -> dict:
    try:
        selection = TableSelection(body.sheet_name, body.range_ref)
        _check_bounds(selection.bounds)
        profile = profile_worksheet(
            path, body.sheet_name, dataset_id, PREVIEW_LIMITS, selection=selection
        )
        table = read_table_rows(
            path, selection=selection, max_value_length=PREVIEW_LIMITS.max_value_length
        )
        return {
            "dataset_id": dataset_id,
            "sheet_name": body.sheet_name,
            "range_ref": body.range_ref,
            "sample_limit": PREVIEW_LIMITS.max_sample_records,
            "profile": profile.model_dump(mode="json"),
            # Every row of the selection, for the person to look through. The
            # profile above is what a planning request may carry; these rows
            # never leave the workspace.
            "rows": {"row_numbers": table.row_numbers, "values": table.values},
        }
    except WorkspaceError:
        raise
    except ValueError as exc:
        # Ingestion errors can include paths or cell contents. Keep the API path-free.
        raise WorkspaceError(
            "invalid_selection",
            "Check the worksheet and A1 range. The first selected row must contain unique, "
            "non-empty text headers.",
        ) from exc


def create_app(
    *,
    settings: PlanRunSettings | None = None,
    local_browser_configuration: bool = False,
    offline_plan_client: PlanCompletionClient | None = None,
    offline_executor_factory: ExecutorFactory | None = None,
    access_code: str | None = None,
    access_codes: Mapping[str, str] | None = None,
    admin_code: str | None = None,
    usage_lines: bool = False,
    static_dir: Path | None = None,
) -> FastAPI:
    """Create an isolated workspace whose uploads are removed on shutdown.

    `access_code` and `access_codes` protect every API route of a publicly
    reachable deployment: the first is one code under the label "shared", the
    second maps a label to the code of one visitor, so that what is used can be
    counted per visitor. `admin_code` opens the usage summary and nothing else;
    without it there is no such route. `usage_lines` also writes each counted
    event to standard output. `static_dir` serves the built frontend from the
    same origin. All are unset for the local launcher.
    """
    datasets: dict[str, Path] = {}
    lock = asyncio.Lock()
    settings = settings or PlanRunSettings.from_environment()
    provider = ProviderConfiguration.from_environment(settings.llm_model)
    usage = UsageLog(emit=usage_lines)
    codes = {
        **({SHARED_VISITOR: access_code} if access_code is not None else {}),
        **(access_codes or {}),
    }
    plan_run = PlanRunService(
        datasets,
        lock,
        settings,
        offline_plan_client,
        offline_executor_factory,
        provider,
        usage,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with TemporaryDirectory(prefix="enrichment-workspace-") as directory:
            app.state.directory = Path(directory)
            try:
                yield
            finally:
                await plan_run.close()
                datasets.clear()
                provider.remove_key()

    app = FastAPI(title="Data Enrichment Workspace", lifespan=lifespan)
    app.state.usage = usage
    app.add_middleware(PlanBodyLimit)
    app.include_router(plan_run.router)
    app.include_router(
        configuration_router(
            provider,
            lock,
            settings=settings,
            offline=offline_plan_client is not None,
            enabled=local_browser_configuration,
        )
    )

    @app.middleware("http")
    async def private_configuration_response(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # Consume unexpected errors here: Starlette's outer server-error handler
            # otherwise re-raises them to Uvicorn, which could log credential text.
            response = await internal_error(request, exc)
        if request.url.path.startswith("/api/provider-settings"):
            response.headers["Cache-Control"] = "no-store"
        return response

    failed_attempts: deque[float] = deque()

    def _visitor(request: Request) -> str | None:
        """The label of the offered code, or None when it is none of the codes."""
        offered = request.headers.get("x-access-code", "").encode()
        found = None
        # Every code is compared, whichever matches, so that the time taken
        # says nothing about which labels exist.
        for label, code in codes.items():
            if compare_digest(offered, code.encode()):
                found = label
        return found

    def _granted(request: Request) -> bool:
        return not codes or _visitor(request) is not None

    async def _refuse(request: Request) -> JSONResponse | None:
        """Return a refusal for a request without the access code, else None.

        A wrong code is slowed down and counted, so the code cannot be guessed
        by rapid retries; a missing code is simply refused.
        """
        if _granted(request):
            return None
        now = monotonic()
        while failed_attempts and now - failed_attempts[0] > ACCESS_WINDOW_SECONDS:
            failed_attempts.popleft()
        if len(failed_attempts) >= ACCESS_MAX_FAILURES:
            return JSONResponse(
                {"error": {"code": "access_throttled", "message": "Too many attempts. Wait."}},
                status_code=429,
            )
        if request.headers.get("x-access-code"):
            failed_attempts.append(now)
            await asyncio.sleep(ACCESS_FAILURE_DELAY_SECONDS)
        return JSONResponse(
            {"error": {"code": "access_required", "message": "Enter the access code."}},
            status_code=401,
        )

    @app.middleware("http")
    async def require_access_code(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in ACCESS_OPEN_PATHS:
            refusal = await _refuse(request)
            if refusal is not None:
                return refusal
        # Whatever this request counts is counted for the holder of its code.
        marked = VISITOR.set((_visitor(request) if codes else None) or VISITOR.get())
        try:
            return await call_next(request)
        finally:
            VISITOR.reset(marked)

    @app.get("/api/access")
    async def access(request: Request):
        """Tell the frontend whether a code is needed and whether the offered one works."""
        refusal = await _refuse(request) if request.headers.get("x-access-code") else None
        if refusal is not None and refusal.status_code == 429:
            return refusal
        granted = _granted(request)
        if granted:
            # The studio asks this once when its page is opened.
            usage.record("visit")
        return {"required": bool(codes), "granted": granted}

    @app.get("/api/usage")
    async def usage_summary(request: Request):
        """What was used since this process started; for the holder of the admin code."""
        if admin_code is None:
            raise WorkspaceError("usage_disabled", "No admin code is set on this server.", 404)
        offered = request.headers.get("x-admin-code", "")
        if not compare_digest(offered.encode(), admin_code.encode()):
            # Guessing is slowed and limited together with the visitor codes.
            now = monotonic()
            while failed_attempts and now - failed_attempts[0] > ACCESS_WINDOW_SECONDS:
                failed_attempts.popleft()
            if len(failed_attempts) >= ACCESS_MAX_FAILURES:
                raise WorkspaceError("access_throttled", "Too many attempts. Wait.", 429)
            if offered:
                failed_attempts.append(now)
                await asyncio.sleep(ACCESS_FAILURE_DELAY_SECONDS)
            raise WorkspaceError("admin_required", "Enter the admin code.", 401)
        return JSONResponse(
            {**usage.summary(), "labels": sorted(codes)},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(WorkspaceError)
    async def workspace_error(_request: Request, exc: WorkspaceError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Check the request fields.",
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        logger.error("Workspace request failed (%s)", type(exc).__name__)
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "The local request failed. Try again.",
                }
            },
            status_code=500,
        )

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/datasets", status_code=201)
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=255)):
        # Raw streaming avoids an unbounded multipart parser before our size check.
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if Path(safe_name).suffix.lower() != ".xlsx":
            raise WorkspaceError("invalid_file_type", "Only .xlsx workbooks are supported.")
        async with lock:
            if len(datasets) >= MAX_DATASETS:
                # Drop the oldest workbook together with its plans and finished jobs.
                oldest = next((i for i in datasets if plan_run.release_dataset(i)), None)
                if oldest is None:
                    raise WorkspaceError(
                        "workspace_full", "Wait for the current run to finish.", 409
                    )
                datasets.pop(oldest).unlink(missing_ok=True)
            dataset_id = uuid4().hex
            path = request.app.state.directory / f"{dataset_id}.xlsx"
            try:
                size = 0
                with path.open("xb") as target:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise WorkspaceError(
                                "upload_too_large", "Upload a file up to 10 MiB.", 413
                            )
                        target.write(chunk)
                metadata = await run_in_threadpool(_inspect, path)
                datasets[dataset_id] = path
                sheets = metadata.get("worksheets") or []
                usage.record(
                    "upload",
                    kilobytes=round(size / 1024),
                    sheets=len(sheets),
                    rows=max((sheet.get("row_count") or 0 for sheet in sheets), default=0),
                )
                return {"dataset_id": dataset_id, "filename": safe_name, **metadata}
            finally:
                if dataset_id not in datasets:
                    path.unlink(missing_ok=True)

    @app.post("/api/datasets/{dataset_id}/preview")
    async def preview(dataset_id: str, body: PreviewRequest):
        async with lock:
            path = datasets.get(dataset_id)
            if path is None:
                raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
            return await run_in_threadpool(_preview, path, dataset_id, body)

    @app.delete("/api/datasets/{dataset_id}", status_code=204)
    async def remove(dataset_id: str):
        async with lock:
            if plan_run.dataset_in_use(dataset_id):
                raise WorkspaceError(
                    "dataset_in_use",
                    "Dataset is bound to retained plans. Restart to clear it.",
                    409,
                )
            path = datasets.pop(dataset_id, None)
            if path is not None:
                path.unlink(missing_ok=True)
        return Response(status_code=204)

    @app.get("/api/samples")
    async def samples():
        """Example workbooks a visitor can try without a file of their own."""
        offered = (spec.id for spec in builtin_registry().list_all())
        return {"samples": sample_catalog.available(offered)}

    @app.get("/api/samples/{sample_id}/workbook")
    async def sample_workbook(sample_id: str):
        sample = sample_catalog.find(sample_id)
        path = sample_catalog.workbook_path(sample) if sample else None
        if sample is None or path is None:
            raise WorkspaceError("sample_not_found", "This example is not available.", 404)
        # A known id of the catalog, never a name the visitor typed.
        usage.record("example", sample=sample.sample_id)
        # The browser uploads these bytes through the ordinary upload route, so an
        # example passes exactly the same checks as a visitor's own workbook.
        return Response(
            await run_in_threadpool(path.read_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.get("/api/samples/{sample_id}/plan")
    async def sample_plan(sample_id: str):
        sample = sample_catalog.find(sample_id)
        plan = await run_in_threadpool(sample_catalog.prepared_plan, sample) if sample else None
        if plan is None:
            raise WorkspaceError("sample_not_found", "This example has no prepared plan.", 404)
        return plan

    if static_dir is not None:
        # Mounted last: API routes win, everything else is the single-page frontend.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    return app


app = create_app()
