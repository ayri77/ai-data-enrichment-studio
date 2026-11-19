"""Append new rows to a copy of a workbook, and count what happened to every candidate.

``preview_rows`` and ``append_rows`` make their decisions in one private function, so the
counts of a preview are the counts of the append. Nothing is written until the whole plan
has been checked, and the source file is never changed.
"""

from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.comments import Comment
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.mail.workbook import RECEIVED_FORMAT, cell_text
from ai_data_enrichment.output.excel_writer import check_copy_paths, publish_workbook
from ai_data_enrichment.output.provenance import NOTE_AUTHOR
from ai_data_enrichment.rows.conditions import Condition, check_conditions, passes
from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.matching import key_part
from ai_data_enrichment.rows.model import Candidate, RowSource
from ai_data_enrichment.rows.sources import source_for

_MAX_ROW = 1_048_576


@dataclass(frozen=True)
class RowsPlan:
    """What to append: from which source, where, which field to which column, how to tell
    a duplicate, and what to do with one."""

    source_id: str
    # The range includes the header row.
    selection: TableSelection
    # Field id -> column letter, for example {"received": "A"}.
    mapping: Mapping[str, str]
    # Field ids.
    key: tuple[str, ...]
    on_duplicate: Literal["skip", "add_marked"]
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class RowsCount:
    """What happened to the candidates, as numbers only."""

    in_source: int
    # Did not pass a condition.
    left_out: int
    # Passed, but a part of its key is empty: such a row is never added.
    without_key: int
    # Its key equals the key of a row already in the table.
    duplicates_in_table: int
    # Its key equals the key of an earlier candidate of this run.
    duplicates_in_source: int
    # New rows that are no duplicates.
    added: int
    # Rows actually written: ``added``, plus both duplicate counts when they are added marked.
    written: int
    # Existing rows that take no part in the comparison because a key cell is empty.
    table_rows_without_key: int

    @property
    def holds(self) -> bool:
        """Whether every candidate is accounted for exactly once."""
        return self.in_source == (
            self.left_out
            + self.without_key
            + self.duplicates_in_table
            + self.duplicates_in_source
            + self.added
        )


@dataclass(frozen=True)
class RowsResult:
    count: RowsCount
    first_row: int | None
    last_row: int | None
    # The range of the table after the append, header included.
    range_ref: str


@dataclass(frozen=True)
class _Placed:
    """A candidate that is written, and the row it duplicates (None when it is new)."""

    candidate: Candidate
    duplicate_of: int | None


@dataclass(frozen=True)
class _Decision:
    count: RowsCount
    placed: tuple[_Placed, ...]
    # Worksheet column of each mapped field.
    columns: Mapping[str, int]
    last_data_row: int


def _validate_fields(source: RowSource, plan: RowsPlan) -> None:
    named = [*plan.mapping, *plan.key, *(item.field_id for item in plan.conditions)]
    if any(source.field(field_id) is None for field_id in named):
        raise RowsError("rows_unknown_field")
    check_conditions(source, plan.conditions)


def _columns(plan: RowsPlan) -> dict[str, int]:
    """The worksheet column of each mapped field; every one must lie in the selection."""
    min_col, _, max_col, _ = plan.selection.bounds
    columns: dict[str, int] = {}
    for field_id, letter in plan.mapping.items():
        try:
            column = column_index_from_string(letter.strip().upper())
        except ValueError:
            raise RowsError("rows_column_outside_table") from None
        if not min_col <= column <= max_col:
            raise RowsError("rows_column_outside_table")
        columns[field_id] = column
    if len(set(columns.values())) != len(columns):
        raise RowsError("rows_column_used_twice")
    if not plan.key:
        raise RowsError("rows_key_empty")
    if any(field_id not in columns for field_id in plan.key):
        raise RowsError("rows_key_not_mapped")
    return columns


def _last_data_row(sheet: Worksheet, selection: TableSelection) -> int:
    """The last row of the selection with a value in its columns; the header row if none."""
    min_col, _, max_col, max_row = selection.bounds
    last = selection.header_row
    # A range may name a whole column; no row below the sheet's last one holds a value.
    for row in range(selection.first_data_row, min(max_row, sheet.max_row) + 1):
        if any(
            sheet.cell(row=row, column=col).value is not None for col in range(min_col, max_col + 1)
        ):
            last = row
    return last


def _existing_keys(
    values: Worksheet,
    source: RowSource,
    plan: RowsPlan,
    columns: Mapping[str, int],
    last_data_row: int,
) -> tuple[dict[tuple[object, ...], int], int]:
    """Key -> first row of the rows in the table, and how many rows have no key."""
    kinds = [source.field(field_id) for field_id in plan.key]
    keys: dict[tuple[object, ...], int] = {}
    without_key = 0
    not_comparable = 0
    first_bad: int | None = None
    for row in range(plan.selection.first_data_row, last_data_row + 1):
        parts: list[object] = []
        for field_id, field in zip(plan.key, kinds, strict=True):
            assert field is not None
            value = values.cell(row=row, column=columns[field_id]).value
            part = key_part(field.kind, value)
            if part is None and field.kind == "moment" and value not in (None, ""):
                not_comparable += 1
                first_bad = row if first_bad is None else first_bad
            parts.append(part)
        if None in parts:
            without_key += 1
        else:
            keys.setdefault(tuple(parts), row)
    if not_comparable:
        raise RowsError("rows_key_not_comparable", count=not_comparable, row=first_bad)
    return keys, without_key


def _check_room(sheet: Worksheet, plan: RowsPlan, first: int, last: int) -> None:
    """Every cell the new rows would take must be empty and outside a merged range."""
    if last > _MAX_ROW:
        raise RowsError("rows_no_room")
    min_col, _, max_col, _ = plan.selection.bounds
    for row in range(first, last + 1):
        for col in range(min_col, max_col + 1):
            cell = sheet.cell(row=row, column=col)
            if isinstance(cell, MergedCell) or cell.value is not None:
                raise RowsError("rows_no_room")


def _decide(
    formulas: Workbook,
    values: Workbook,
    plan: RowsPlan,
    candidates: Sequence[Candidate],
    now: datetime,
    max_last_row: int | None,
) -> _Decision:
    """Check the plan and decide what happens to every candidate. Writes nothing."""
    source = source_for(plan.source_id)
    _validate_fields(source, plan)
    columns = _columns(plan)
    name = plan.selection.sheet_name
    if name not in formulas.sheetnames:
        raise RowsError("rows_sheet_missing")
    sheet = formulas[name]
    last_data_row = _last_data_row(sheet, plan.selection)
    existing, table_rows_without_key = _existing_keys(
        values[name], source, plan, columns, last_data_row
    )
    kinds = [source.field(field_id) for field_id in plan.key]
    seen: dict[tuple[object, ...], int] = {}
    placed: list[_Placed] = []
    left_out = without_key = in_table = in_source = added = 0
    next_row = last_data_row + 1
    for candidate in candidates:
        if not passes(candidate, plan.conditions, now=now):
            left_out += 1
            continue
        parts = [
            key_part(field.kind, candidate.values.get(field_id))
            for field_id, field in zip(plan.key, kinds, strict=True)
            if field is not None
        ]
        if None in parts:
            without_key += 1
            continue
        key = tuple(parts)
        duplicate_of = existing.get(key)
        if duplicate_of is not None:
            in_table += 1
        elif key in seen:
            duplicate_of = seen[key]
            in_source += 1
        else:
            seen[key] = next_row
            added += 1
            placed.append(_Placed(candidate, None))
            next_row += 1
            continue
        if plan.on_duplicate == "add_marked":
            placed.append(_Placed(candidate, duplicate_of))
            next_row += 1
    if placed:
        if max_last_row is not None and last_data_row + len(placed) > max_last_row:
            raise RowsError("rows_too_many")
        _check_room(sheet, plan, last_data_row + 1, last_data_row + len(placed))
    count = RowsCount(
        in_source=len(candidates),
        left_out=left_out,
        without_key=without_key,
        duplicates_in_table=in_table,
        duplicates_in_source=in_source,
        added=added,
        written=len(placed),
        table_rows_without_key=table_rows_without_key,
    )
    return _Decision(count, tuple(placed), columns, last_data_row)


def _open_pair(path: Path) -> tuple[Workbook, Workbook]:
    """The workbook with its formulas and the same one with their cached results."""
    formulas = load_workbook(path, read_only=False, data_only=False)
    try:
        values = load_workbook(path, read_only=False, data_only=True)
    except BaseException:
        formulas.close()
        raise
    return formulas, values


def preview_rows(
    path: Path,
    plan: RowsPlan,
    candidates: Sequence[Candidate],
    *,
    now: datetime,
    max_last_row: int | None = None,
) -> RowsCount:
    """The counts an append of these candidates would give; nothing is written.

    ``max_last_row`` is the last worksheet row the table may reach; an append that would
    write beyond it is refused with ``rows_too_many``.
    """
    formulas, values = _open_pair(path)
    try:
        return _decide(formulas, values, plan, candidates, now, max_last_row).count
    finally:
        formulas.close()
        values.close()


def _copy_style(target: object, above: object) -> None:
    if isinstance(above, MergedCell):
        return
    for name in ("number_format", "font", "alignment", "border", "fill", "protection"):
        setattr(target, name, copy(getattr(above, name)))


def _write_row(
    sheet: Worksheet,
    source: RowSource,
    plan: RowsPlan,
    decision: _Decision,
    row: int,
    placed: _Placed,
) -> None:
    min_col, _, max_col, _ = plan.selection.bounds
    # A table that has only its header gives no row to take a style from.
    if decision.last_data_row > plan.selection.header_row:
        for col in range(min_col, max_col + 1):
            cell = sheet.cell(row=row, column=col)
            _copy_style(cell, sheet.cell(row=decision.last_data_row, column=col))
    for field_id, col in decision.columns.items():
        field = source.field(field_id)
        value = placed.candidate.values.get(field_id)
        if field is None or value is None:
            continue
        cell = sheet.cell(row=row, column=col)
        if isinstance(cell, MergedCell):
            continue
        if field.kind == "moment":
            if isinstance(value, datetime):
                # Assigning a datetime changes a General format on its own; set ours after.
                general = cell.number_format == "General"
                cell.value = value
                if general:
                    cell.number_format = RECEIVED_FORMAT
        else:
            text = cell_text(str(value))
            if text:
                cell.value = text
                # Text that starts with = + - @ stays text and is never a formula.
                cell.data_type = "s"
    if placed.duplicate_of is not None:
        key_cell = sheet.cell(row=row, column=decision.columns[plan.key[0]])
        key_cell.comment = Comment(f"Possible duplicate of row {placed.duplicate_of}", NOTE_AUTHOR)


def _extend_ranges(
    sheet: Worksheet, selection: TableSelection, old_last: int, new_last: int
) -> None:
    """Extend an Excel table or the auto filter that spans the selection down to ``new_last``."""
    min_col, _, max_col, _ = selection.bounds

    def spans(ref: str | None) -> CellRange | None:
        if not ref:
            return None
        region = CellRange(ref)
        if (
            region.min_row == selection.header_row
            and region.min_col <= min_col
            and region.max_col >= max_col
            and region.max_row == old_last
        ):
            return region
        return None

    def extended(region: CellRange) -> str:
        return (
            f"{get_column_letter(region.min_col)}{region.min_row}:"
            f"{get_column_letter(region.max_col)}{new_last}"
        )

    for table in sheet.tables.values():
        region = spans(table.ref)
        if region is not None:
            table.ref = extended(region)
            if table.autoFilter is not None:
                table.autoFilter.ref = table.ref
    region = spans(sheet.auto_filter.ref)
    if region is not None:
        sheet.auto_filter.ref = extended(region)


def _range_after(selection: TableSelection, last: int) -> str:
    min_col, _, max_col, _ = selection.bounds
    return f"{get_column_letter(min_col)}{selection.header_row}:{get_column_letter(max_col)}{last}"


def append_rows(
    source_path: Path,
    output_path: Path,
    plan: RowsPlan,
    candidates: Sequence[Candidate],
    *,
    now: datetime,
    max_last_row: int | None = None,
) -> RowsResult:
    """Write the new rows to a copy of the workbook and return the counts.

    ``max_last_row`` is as for ``preview_rows``; the check comes before anything is written.

    The copy is published the way ``write_excel_cells`` publishes: a new file, never an
    existing one. No cell that existed before is changed; the source is not touched.
    """
    check_copy_paths(source_path, output_path)
    formulas, values = _open_pair(source_path)
    try:
        decision = _decide(formulas, values, plan, candidates, now, max_last_row)
        source = source_for(plan.source_id)
        sheet = formulas[plan.selection.sheet_name]
        first = decision.last_data_row + 1
        for offset, placed in enumerate(decision.placed):
            _write_row(sheet, source, plan, decision, first + offset, placed)
        last = decision.last_data_row + len(decision.placed)
        if decision.placed:
            _extend_ranges(sheet, plan.selection, decision.last_data_row, last)
        publish_workbook(formulas, output_path, plan.selection.sheet_name)
    finally:
        formulas.close()
        values.close()
    return RowsResult(
        count=decision.count,
        first_row=first if decision.placed else None,
        last_row=last if decision.placed else None,
        range_ref=_range_after(plan.selection, last),
    )
