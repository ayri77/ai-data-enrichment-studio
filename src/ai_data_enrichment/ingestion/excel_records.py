"""Convert Excel worksheet rows into typed execution records."""

from math import isfinite
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.execution.models import RuntimeValue
from ai_data_enrichment.ingestion.excel_headers import validate_excel_headers
from ai_data_enrichment.ingestion.table_selection import TableSelection


def _to_runtime_value(
    value: object,
    coordinate: str,
) -> str | float | None:
    """Convert a supported Excel scalar to an execution value."""

    if value is None or isinstance(value, str):
        return value

    # bool is a subclass of int in Python.
    if isinstance(value, bool):
        raise ValueError(f"Unsupported Boolean in {coordinate}")

    if isinstance(value, int):
        if abs(value) > 2**53:
            raise ValueError(f"Integer exceeds exact float range in {coordinate}")
        return float(value)

    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"Non-finite number in {coordinate}")
        return value

    raise ValueError(f"Unsupported input type in {coordinate}: {type(value).__name__}")


def read_input_records(
    path: Path,
    *,
    selection: TableSelection,
    input_columns: tuple[str, ...],
    max_rows: int = 1000,
) -> tuple[InputRecord, ...]:
    """Read worksheet records without modifying the source workbook."""

    if max_rows < 1:
        raise ValueError("max_rows must be positive")

    workbook = load_workbook(
        path,
        read_only=True,
        data_only=False,
    )

    try:
        if selection.sheet_name not in workbook.sheetnames:
            raise ValueError(f"Worksheet '{selection.sheet_name}' not found")

        worksheet = workbook[selection.sheet_name]
        min_col, min_row, max_col, max_row = selection.bounds

        rows = worksheet.iter_rows(
            min_row=min_row,
            max_row=max_row,
            min_col=min_col,
            max_col=max_col,
            values_only=False,
        )

        header = next(rows, ())
        columns = validate_excel_headers(cell.value for cell in header)

        # Map column names to their positions within the selected table.
        column_positions = {name: index for index, name in enumerate(columns)}

        if not input_columns or len(set(input_columns)) != len(input_columns):
            raise ValueError("Input columns must be non-empty and unique")

        for name in input_columns:
            if name not in column_positions:
                raise ValueError(f"Input column not found: {name}")

        records: list[InputRecord] = []

        for row_number, row in enumerate(
            rows,
            start=selection.first_data_row,
        ):
            if all(cell.value is None for cell in row):
                continue

            values: dict[str, RuntimeValue | None] = {}

            for name in input_columns:
                position = column_positions[name]
                cell = row[position]

                # Derive the coordinate independently of the cell object.
                coordinate = f"{get_column_letter(min_col + position)}{row_number}"

                if cell.data_type in {"f", "e"}:
                    raise ValueError(f"Unsupported formula or error in {coordinate}")

                values[name] = _to_runtime_value(
                    cell.value,
                    coordinate,
                )

            if len(records) >= max_rows:
                raise ValueError(f"Input exceeds max_rows={max_rows}")

            records.append(
                InputRecord(
                    record_id=f"{selection.sheet_name}!{row_number}",
                    values=values,
                )
            )

        return tuple(records)

    finally:
        workbook.close()
