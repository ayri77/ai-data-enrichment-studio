"""Every row of a selected table, as a person will see it."""

from datetime import date, datetime, time
from pathlib import Path

import pytest
from openpyxl import Workbook

from ai_data_enrichment.ingestion.table_rows import read_table_rows
from ai_data_enrichment.ingestion.table_selection import TableSelection


def workbook(
    tmp_path: Path, rows: list[tuple], *, first_row: int = 1, first_column: int = 1
) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "Data"
    for offset, row in enumerate(rows):
        for index, value in enumerate(row):
            sheet.cell(row=first_row + offset, column=first_column + index, value=value)
    path = tmp_path / "table.xlsx"
    book.save(path)
    return path


def test_rows_come_in_worksheet_order_with_their_row_numbers(tmp_path) -> None:
    path = workbook(
        tmp_path,
        [("city", "population"), ("Kyiv", 2952301), (None, None), ("Lviv", None), ("Odesa", 1.5)],
        first_row=3,
        first_column=2,
    )
    table = read_table_rows(path, selection=TableSelection("Data", "B3:C7"))
    # A fully empty row is not a row of the table; a partly empty one is.
    assert table.row_numbers == (4, 6, 7)
    assert table.values == (("Kyiv", 2952301), ("Lviv", None), ("Odesa", 1.5))


def test_cells_are_shown_as_plain_values(tmp_path) -> None:
    path = workbook(
        tmp_path,
        [
            ("name", "value"),
            ("long", "x" * 500),
            ("flag", True),
            ("day", date(2026, 11, 13)),
            ("moment", datetime(2026, 11, 13, 12, 30)),
            ("clock", time(9, 5)),
        ],
    )
    values = dict(read_table_rows(path, selection=TableSelection("Data", "A1:B6")).values)
    assert values["long"] == "x" * 200
    assert values["flag"] is True
    assert values["day"] == "2026-11-13"
    assert str(values["moment"]).startswith("2026-11-13")
    assert values["clock"] == "09:05:00"
    short = read_table_rows(path, selection=TableSelection("Data", "A1:B2"), max_value_length=3)
    assert short.values == (("lon", "xxx"),)


def test_a_selection_without_a_usable_header_or_sheet_is_refused(tmp_path) -> None:
    path = workbook(tmp_path, [("city", "city"), ("Kyiv", "Lviv")])
    with pytest.raises(ValueError):
        read_table_rows(path, selection=TableSelection("Data", "A1:B2"))
    with pytest.raises(ValueError):
        read_table_rows(path, selection=TableSelection("Missing", "A1:B2"))
