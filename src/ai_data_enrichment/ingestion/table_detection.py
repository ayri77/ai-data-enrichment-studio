"""Find where the table is on a worksheet that does not start in A1.

A workbook from real work often has a title or a note above the table, or
empty columns to its left. Until a person selects a range, the interface needs
a reasonable guess. This is that guess and nothing more: the selected range is
still validated when it is previewed, and a person can always change it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from openpyxl.utils import get_column_letter


@dataclass(frozen=True, slots=True)
class TableRegion:
    """One-based, inclusive bounds; the first row holds the headers."""

    first_row: int
    first_column: int
    last_row: int
    last_column: int

    @property
    def range_ref(self) -> str:
        return (
            f"{get_column_letter(self.first_column)}{self.first_row}:"
            f"{get_column_letter(self.last_column)}{self.last_row}"
        )


def _filled(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def _header_like(value: object) -> bool:
    # A header is text. A number, a date or a formula is a value, not a name.
    return isinstance(value, str) and bool(value.strip()) and not value.lstrip().startswith("=")


def _header_runs(row: Sequence[object]) -> list[tuple[int, int]]:
    """Zero-based [start, end) spans of adjacent header-like cells with unique names."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index in range(len(row) + 1):
        inside = index < len(row) and _header_like(row[index])
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            names = [str(value).strip().casefold() for value in row[start:index]]
            if index - start >= 2 and len(set(names)) == len(names):
                runs.append((start, index))
            start = None
    return runs


def detect_table(rows: Sequence[Sequence[object]]) -> TableRegion | None:
    """Return the largest block that looks like a header row with rows under it.

    A candidate header is a run of at least two adjacent text cells with unique
    names. Its table runs down to the first completely empty row and must have
    a value under the header in its first data row. Among candidates the one
    covering the most cells wins; on a tie, the one nearest the top left. A row
    of text values under a real header is itself a candidate, but always a
    smaller one.
    """
    blank = [not any(_filled(value) for value in row) for row in rows]
    # end[i]: index of the first empty row at or below i.
    end = [len(rows)] * (len(rows) + 1)
    for index in range(len(rows) - 1, -1, -1):
        end[index] = index if blank[index] else end[index + 1]

    best: tuple[int, TableRegion] | None = None
    for index, row in enumerate(rows):
        if blank[index] or index + 1 >= len(rows):
            continue
        below = rows[index + 1]
        last = end[index + 1]
        for start, stop in _header_runs(row):
            if not any(_filled(value) for value in below[start:stop]):
                continue
            area = (last - index - 1) * (stop - start)
            if best is None or area > best[0]:
                best = (area, TableRegion(index + 1, start + 1, last, stop))
    return best[1] if best else None
