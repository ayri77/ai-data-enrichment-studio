"""Describe an explicitly selected rectangular Excel table."""

from dataclasses import dataclass

from openpyxl.worksheet.cell_range import CellRange


@dataclass(frozen=True, slots=True)
class TableSelection:
    """Identify a worksheet and a bounded table range, including its header."""

    sheet_name: str
    range_ref: str

    def __post_init__(self) -> None:
        if not self.sheet_name.strip():
            raise ValueError("Worksheet name must not be empty")

        try:
            region = CellRange(self.range_ref)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid table range: {self.range_ref}") from exc

        if region.title is not None:
            raise ValueError("Range must not contain a worksheet name")

        if region.max_row > 1_048_576 or region.max_col > 16_384:
            raise ValueError("Table range exceeds Excel worksheet limits")

    @property
    def header_row(self) -> int:
        return CellRange(self.range_ref).min_row

    @property
    def first_data_row(self) -> int:
        return self.header_row + 1

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """Return min_col, min_row, max_col, max_row."""
        return CellRange(self.range_ref).bounds
