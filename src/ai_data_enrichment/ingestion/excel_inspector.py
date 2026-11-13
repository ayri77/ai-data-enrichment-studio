"""Read-only XLSX inspection for the initial project bootstrap.

This is intentionally limited to worksheet names, first-row headers, and
non-empty data row counts. Full DatasetProfile modeling follows in M1.
"""

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from ai_data_enrichment.ingestion.table_detection import TableRegion, detect_table


@dataclass(frozen=True, slots=True)
class WorksheetSummary:
    """Small, serialization-friendly summary of a worksheet."""

    name: str
    columns: tuple[str, ...]
    row_count: int
    # Where the table most likely is, when the sheet does not simply start in A1.
    table: TableRegion | None = None
    table_range: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbookSummary:
    """Read-only summary of an XLSX workbook."""

    worksheets: tuple[WorksheetSummary, ...]


def inspect_workbook(path: Path) -> WorkbookSummary:
    """Describe each worksheet by the table found on it, or by its first row."""
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Only .xlsx files are supported")
    if not path.is_file():
        raise FileNotFoundError(f"Workbook not found: {path}")

    summaries: list[WorksheetSummary] = []
    # Inspect formula cells as populated rather than relying on cached results.
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        for worksheet in workbook.worksheets:
            # Anchored at A1 whatever the sheet's own dimension says, so that
            # positions in the rows are positions on the sheet.
            cells = [
                tuple(row) for row in worksheet.iter_rows(min_row=1, min_col=1, values_only=True)
            ]
            table = detect_table(cells)
            if table is not None:
                header = cells[table.first_row - 1][table.first_column - 1 : table.last_column]
                columns = tuple(str(value).strip() for value in header)
                row_count = table.last_row - table.first_row
            else:
                # Nothing that looks like a table: describe the sheet as before,
                # first row as the header, and let the selection be made by hand.
                first = cells[0] if cells else ()
                columns = tuple(str(value).strip() if value is not None else "" for value in first)
                # Exclude blank or whitespace-only rows from the data count.
                row_count = sum(
                    1
                    for row in cells[1:]
                    if any(value is not None and str(value).strip() for value in row)
                )
            summaries.append(
                WorksheetSummary(
                    name=worksheet.title,
                    columns=columns,
                    row_count=row_count,
                    table=table,
                    table_range=table.range_ref if table else None,
                )
            )
    finally:
        workbook.close()
    return WorkbookSummary(worksheets=tuple(summaries))
