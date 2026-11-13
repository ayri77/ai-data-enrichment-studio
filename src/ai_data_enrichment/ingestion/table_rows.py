"""Read every row of a selected table for display.

The profile sent to the planner keeps a few sample rows on purpose. A person
checking what they uploaded needs the whole table: to scroll it, filter it and
sort it. This reader serves only that view. It is lenient where the reader for
execution is strict, because showing a cell decides nothing about using it.
"""

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from ai_data_enrichment.ingestion.excel_headers import validate_excel_headers
from ai_data_enrichment.ingestion.excel_profiler import JsonValue, to_json_value
from ai_data_enrichment.ingestion.table_selection import TableSelection


@dataclass(frozen=True, slots=True)
class TableRows:
    """The non-empty data rows of a selection, each with its worksheet row."""

    row_numbers: tuple[int, ...]
    values: tuple[tuple[JsonValue, ...], ...]


def _shown(value: object, number_format: str | None, max_value_length: int) -> JsonValue:
    try:
        return to_json_value(value, number_format, max_value_length=max_value_length)
    except ValueError:
        # A cell of a type the profile does not describe is still shown as text.
        return str(value)[:max_value_length]


def read_table_rows(
    path: Path, *, selection: TableSelection, max_value_length: int = 200
) -> TableRows:
    """Return the selection's rows in worksheet order, skipping fully empty ones.

    Formulas show their cached results, as in the profile. Long text is cut to
    ``max_value_length`` characters.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if selection.sheet_name not in workbook.sheetnames:
            raise ValueError(f"Worksheet '{selection.sheet_name}' not found")
        min_col, min_row, max_col, max_row = selection.bounds
        rows = workbook[selection.sheet_name].iter_rows(
            min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col
        )
        validate_excel_headers(cell.value for cell in next(rows, ()))
        numbers: list[int] = []
        values: list[tuple[JsonValue, ...]] = []
        for row_number, row in enumerate(rows, start=selection.first_data_row):
            if all(cell.value is None for cell in row):
                continue
            numbers.append(row_number)
            values.append(
                tuple(_shown(cell.value, cell.number_format, max_value_length) for cell in row)
            )
        return TableRows(tuple(numbers), tuple(values))
    finally:
        workbook.close()
