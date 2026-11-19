"""Routes for adding new rows from a source to a copy of a workbook.

The core is in ``ai_data_enrichment.rows``; this module only makes it reachable: list the
sources, take the files of a source into memory as candidates, preview a plan with its counts,
and run it into a new dataset. ``api.create_app`` passes in what it owns (the datasets, the
lock, ``_register``, the bounds check, the plan/run service and the usage log).

Candidates (date, sender and subject of each mail, never its text) live in memory in a small
store of the app: at most four sets, the oldest is dropped when a fifth comes, and they are
gone on restart. Nothing of them is written to disk, logged or counted.
"""

import asyncio
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints
from starlette.concurrency import run_in_threadpool

from ai_data_enrichment.ingestion.excel_headers import validate_excel_headers
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.mail import upload as mail_upload
from ai_data_enrichment.plan_run import PlanRunService
from ai_data_enrichment.rows.append import (
    RowsCount,
    RowsPlan,
    RowsResult,
    append_rows,
    preview_rows,
)
from ai_data_enrichment.rows.conditions import Condition
from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.mail_files import MAIL_FILES, mail_candidates
from ai_data_enrichment.rows.matching import propose_key, propose_mapping
from ai_data_enrichment.rows.sources import ROW_SOURCES, Candidate, RowSource, source_for
from ai_data_enrichment.usage import UsageLog
from ai_data_enrichment.workspace_errors import WorkspaceError

MAX_CANDIDATE_SETS = 4
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MOMENT_DATE_OPS = ("on_or_after", "on_or_before")


@dataclass(frozen=True)
class CandidateSet:
    """The candidates of one read of a source's files."""

    source_id: str
    candidates: tuple[Candidate, ...]


class RowCandidates:
    """Candidate sets in memory, at most ``MAX_CANDIDATE_SETS``; the oldest goes first."""

    def __init__(self) -> None:
        self._sets: OrderedDict[str, CandidateSet] = OrderedDict()

    def add(self, source_id: str, candidates: Sequence[Candidate]) -> str:
        """Keep a set under a new id, dropping the oldest when the store is full."""
        candidates_id = uuid4().hex
        self._sets[candidates_id] = CandidateSet(source_id, tuple(candidates))
        while len(self._sets) > MAX_CANDIDATE_SETS:
            self._sets.popitem(last=False)
        return candidates_id

    def get(self, candidates_id: str) -> CandidateSet:
        found = self._sets.get(candidates_id)
        if found is None:
            raise WorkspaceError("rows_candidates_not_found", "Choose the files again.", 404)
        return found

    def discard(self, candidates_id: str) -> None:
        self._sets.pop(candidates_id, None)

    def clear(self) -> None:
        self._sets.clear()


class Register(Protocol):
    """``create_app._register``: make room, write a workbook under a new id, register it."""

    def __call__(
        self,
        directory: Path,
        write: Callable[[Path], Awaitable[None]],
        *,
        mail: bool = False,
        keep: str | None = None,
    ) -> Awaitable[tuple[str, dict]]: ...


@dataclass(frozen=True)
class _Reader:
    """How the files of one source are read into candidates, and how much may be sent."""

    read: Callable[[bytes, bool], tuple[list[Candidate], int]]
    max_bytes: int


def _read_mail(data: bytes, archive: bool) -> tuple[list[Candidate], int]:
    messages, skipped = mail_upload.read_mail_upload(data, archive=archive)
    return mail_candidates(messages), skipped


_READERS = {MAIL_FILES.source_id: _Reader(_read_mail, mail_upload.MAX_UPLOAD_BYTES)}


class ConditionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str = Field(min_length=1, max_length=64)
    op: str = Field(min_length=1, max_length=32)
    # A number for within_last_days, YYYY-MM-DD for a date, else text.
    value: StrictInt | Annotated[str, StringConstraints(strict=True, max_length=200)]


class _RowsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates_id: str = Field(min_length=1, max_length=64)
    sheet_name: str = Field(min_length=1, max_length=31)
    range_ref: str = Field(min_length=1, max_length=32)
    on_duplicate: Literal["skip", "add_marked"] = "skip"
    conditions: list[ConditionBody] = Field(default_factory=list, max_length=16)


class RowsPreviewRequest(_RowsBody):
    # Field id -> column letter. Null: proposed from the header row.
    mapping: dict[str, str] | None = Field(default=None, max_length=32)
    # Field ids. Null: the default key of the source.
    key: list[str] | None = Field(default=None, max_length=32)


class RowsRunRequest(_RowsBody):
    """A run is exactly what the person saw: nothing is proposed."""

    mapping: dict[str, str] = Field(max_length=32)
    key: list[str] = Field(max_length=32)


def workspace_error_for(error: RowsError) -> WorkspaceError:
    """The public error of a refused plan, one literal construction per code.

    The codes are written out so that the test of error texts finds them and requires a text
    in every language. A code of the core that is not listed is not public: it is raised again.
    """
    match error.code:
        case "rows_unknown_source":
            return WorkspaceError("rows_unknown_source", "This source of rows is not known.", 404)
        case "rows_unknown_field":
            return WorkspaceError(
                "rows_unknown_field", "A field in the plan does not exist for this source."
            )
        case "rows_bad_condition":
            return WorkspaceError(
                "rows_bad_condition", "A condition is not valid. Check its field and its value."
            )
        case "rows_column_outside_table":
            return WorkspaceError(
                "rows_column_outside_table",
                "A field goes to a column outside the table. Choose a column inside the range.",
            )
        case "rows_column_used_twice":
            return WorkspaceError(
                "rows_column_used_twice",
                "Two fields go to the same column. Give each field its own column.",
            )
        case "rows_key_empty":
            return WorkspaceError(
                "rows_key_empty", "Choose at least one field that tells one row from another."
            )
        case "rows_key_not_mapped":
            return WorkspaceError(
                "rows_key_not_mapped",
                "Every field of the key needs a column. Map it or take it out of the key.",
            )
        case "rows_key_not_comparable":
            return WorkspaceError(
                "rows_key_not_comparable",
                "A date in a key column is not a date cell. "
                "Rows cannot be compared, so no row is added.",
            )
        case "rows_no_room":
            return WorkspaceError("rows_no_room", "The cells below the table are not empty.")
        case "rows_sheet_missing":
            return WorkspaceError(
                "rows_sheet_missing", "This worksheet is not in the workbook. Choose it again."
            )
        case "rows_too_many":
            return WorkspaceError(
                "rows_too_many",
                "The table would grow beyond its limits. Add fewer rows or narrow the conditions.",
            )
    raise error


def _now() -> datetime:
    """The server's clock in UTC, naive, as the conditions expect."""
    return datetime.now(UTC).replace(tzinfo=None)


def _invalid_selection() -> WorkspaceError:
    return WorkspaceError(
        "invalid_selection",
        "Check the worksheet and A1 range. The first selected row must contain unique, "
        "non-empty text headers.",
    )


def _count_json(count: RowsCount) -> dict:
    return {**asdict(count), "holds": count.holds}


def _condition(item: ConditionBody) -> Condition:
    """A condition of the core; a date is parsed from ``YYYY-MM-DD`` and nothing else."""
    value: int | str | date = item.value
    if item.op in _MOMENT_DATE_OPS and isinstance(value, str):
        if not _DATE.fullmatch(value):
            raise RowsError("rows_bad_condition")
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise RowsError("rows_bad_condition") from None
    return Condition(item.field_id, item.op, value)


def _headers(path: Path, selection: TableSelection) -> tuple[str, ...]:
    """The header row of the selection; ``ValueError`` when the sheet or the headers are bad."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if selection.sheet_name not in workbook.sheetnames:
            raise ValueError("Worksheet not found")
        min_col, min_row, max_col, _ = selection.bounds
        row = next(
            workbook[selection.sheet_name].iter_rows(
                min_row=min_row, max_row=min_row, min_col=min_col, max_col=max_col
            ),
            (),
        )
        return validate_excel_headers(cell.value for cell in row)
    finally:
        workbook.close()


def _plan(
    source_id: str,
    selection: TableSelection,
    body: _RowsBody,
    mapping: dict[str, str],
    key: Sequence[str],
) -> RowsPlan:
    return RowsPlan(
        source_id=source_id,
        selection=selection,
        mapping=mapping,
        key=tuple(key),
        on_duplicate=body.on_duplicate,
        conditions=tuple(_condition(item) for item in body.conditions),
    )


def rows_router(
    *,
    datasets: dict[str, Path],
    lock: asyncio.Lock,
    register: Register,
    check_bounds: Callable[[tuple[int, int, int, int]], None],
    plan_run: PlanRunService,
    usage: UsageLog,
    candidates: RowCandidates,
    max_rows: int,
    max_cells: int,
) -> APIRouter:
    """The routes of this module. ``max_rows`` is the last worksheet row a table may reach and
    ``max_cells`` its cells, as ``check_bounds`` reads them."""
    router = APIRouter(prefix="/api")

    def available(source: RowSource) -> bool:
        # The one switch there is for local-only sources: ENRICHMENT_ALLOW_MAIL=1 on a local
        # installation (`mail_available`). A hosted app never has it.
        return not source.local_only or plan_run.mail_available

    def selection_of(body: _RowsBody) -> TableSelection:
        try:
            selection = TableSelection(body.sheet_name, body.range_ref)
        except ValueError:
            raise _invalid_selection() from None
        check_bounds(selection.bounds)
        return selection

    def last_row_limit(selection: TableSelection) -> int:
        """The last row the table may reach and still pass ``check_bounds``."""
        min_col, _, max_col, _ = selection.bounds
        by_cells = selection.header_row - 1 + max_cells // (max_col - min_col + 1)
        return min(max_rows, by_cells)

    @router.get("/row-sources")
    async def row_sources():
        return {
            "sources": [
                {
                    "source_id": source.source_id,
                    "label": source.label,
                    "connect": source.connect,
                    "file_suffixes": list(source.file_suffixes),
                    "fields": [
                        {"field_id": f.field_id, "kind": f.kind, "label": f.label}
                        for f in source.fields
                    ],
                    "default_key": list(source.default_key),
                    "listing": source.listing,
                    "available": available(source),
                }
                for source in ROW_SOURCES
            ]
        }

    @router.post("/row-sources/{source_id}/candidates", status_code=201)
    async def read_candidates(
        request: Request, source_id: str, filename: str = Query(min_length=1, max_length=255)
    ):
        """Read the files of a source into memory and keep only its candidates."""
        try:
            source = source_for(source_id)
        except RowsError as error:
            raise workspace_error_for(error) from None
        if not available(source):
            raise WorkspaceError(
                "mail_not_allowed", "Tables from e-mails are not available here.", 403
            )
        suffix = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
        if suffix not in source.file_suffixes:
            raise WorkspaceError(
                "invalid_mail_file_type", "Upload one .zip of .eml files, or one .eml file."
            )
        reader = _READERS[source.source_id]
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > reader.max_bytes:
                raise WorkspaceError("mail_upload_too_large", "Upload up to 32 MiB.", 413)
            chunks.append(chunk)
        data = b"".join(chunks)
        chunks.clear()
        found, skipped = await run_in_threadpool(reader.read, data, suffix == ".zip")
        del data
        return {
            "candidates_id": candidates.add(source.source_id, found),
            "source_id": source.source_id,
            "count": len(found),
            "skipped": skipped,
        }

    @router.delete("/row-sources/candidates/{candidates_id}", status_code=204)
    async def forget_candidates(candidates_id: str):
        candidates.discard(candidates_id)
        return Response(status_code=204)

    def preview_in_thread(
        path: Path, selection: TableSelection, found: CandidateSet, body: RowsPreviewRequest
    ) -> dict:
        try:
            headers = _headers(path, selection)
        except ValueError:
            raise _invalid_selection() from None
        source = source_for(found.source_id)
        mapping = body.mapping
        if mapping is None:
            first_column = selection.bounds[0]
            mapping = {
                field_id: get_column_letter(first_column + index)
                for field_id, index in propose_mapping(source, headers).items()
            }
        key = body.key if body.key is not None else list(propose_key(source, mapping))
        count: RowsCount | None = None
        refused: dict | None = None
        try:
            plan = _plan(found.source_id, selection, body, mapping, key)
            count = preview_rows(
                path,
                plan,
                found.candidates,
                now=_now(),
                max_last_row=last_row_limit(selection),
            )
        except RowsError as error:
            refused = {"code": error.code, "count": error.count, "row": error.row}
        return {
            "plan": {
                "source_id": found.source_id,
                "sheet_name": body.sheet_name,
                "range_ref": body.range_ref,
                "mapping": mapping,
                "key": key,
                "on_duplicate": body.on_duplicate,
                "conditions": [item.model_dump() for item in body.conditions],
            },
            "proposed": {"mapping": body.mapping is None, "key": body.key is None},
            "count": _count_json(count) if count is not None else None,
            "refused": refused,
        }

    @router.post("/datasets/{dataset_id}/rows/preview")
    async def preview(dataset_id: str, body: RowsPreviewRequest):
        """The plan as it would run, with its counts; a refused plan is an answer, not an error."""
        async with lock:
            path = datasets.get(dataset_id)
            if path is None:
                raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
            found = candidates.get(body.candidates_id)
            selection = selection_of(body)
            return await run_in_threadpool(preview_in_thread, path, selection, found, body)

    def append_in_thread(
        source_path: Path,
        output_path: Path,
        plan: RowsPlan,
        found: CandidateSet,
        max_last_row: int,
    ) -> RowsResult:
        try:
            return append_rows(
                source_path,
                output_path,
                plan,
                found.candidates,
                now=_now(),
                max_last_row=max_last_row,
            )
        except RowsError as error:
            raise workspace_error_for(error) from None

    def check_in_thread(path: Path, plan: RowsPlan, found: CandidateSet, max_last_row: int) -> None:
        try:
            preview_rows(path, plan, found.candidates, now=_now(), max_last_row=max_last_row)
        except RowsError as error:
            raise workspace_error_for(error) from None

    @router.post("/datasets/{dataset_id}/rows", status_code=201)
    async def append(request: Request, dataset_id: str, body: RowsRunRequest):
        """Append the new rows to a copy of the dataset; the copy is a new dataset."""
        selection = selection_of(body)
        async with lock:
            source_path = datasets.get(dataset_id)
            if source_path is None:
                raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
            found = candidates.get(body.candidates_id)
            try:
                plan = _plan(found.source_id, selection, body, body.mapping, body.key)
            except RowsError as error:
                raise workspace_error_for(error) from None
            # Making room for the copy may drop the oldest dataset. A plan the core refuses
            # must not cost another table its place, so it is checked before room is made.
            await run_in_threadpool(
                check_in_thread, source_path, plan, found, last_row_limit(selection)
            )
        results: list[RowsResult] = []

        async def write(path: Path) -> None:
            # Called with the lock held: the source cannot be removed under the append.
            source_path = datasets.get(dataset_id)
            if source_path is None:
                raise WorkspaceError("dataset_not_found", "Upload the workbook again.", 404)
            results.append(
                await run_in_threadpool(
                    append_in_thread,
                    source_path,
                    path,
                    plan,
                    found,
                    last_row_limit(selection),
                )
            )

        new_id, metadata = await register(
            request.app.state.directory,
            write,
            mail=dataset_id in plan_run.mail_datasets,
            keep=dataset_id,
        )
        result = results[0]
        count = result.count
        usage.record(
            "rows_append",
            in_source=count.in_source,
            added=count.added,
            written=count.written,
            duplicates=count.duplicates_in_table + count.duplicates_in_source,
        )
        return {
            "dataset_id": new_id,
            **metadata,
            "rows": {
                "count": _count_json(count),
                "first_row": result.first_row,
                "last_row": result.last_row,
                "sheet_name": selection.sheet_name,
                "range_ref": result.range_ref,
            },
        }

    return router
