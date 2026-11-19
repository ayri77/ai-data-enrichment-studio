"""The committed registers of the browser scenario are what their generator makes."""

from pathlib import Path

import pytest
from openpyxl import load_workbook
from scripts.make_rows_register import register

SAMPLES = Path(__file__).parents[2] / "samples" / "probe"


def cells(workbook) -> list[tuple[object, ...]]:
    sheet = workbook["Register"]
    return [
        tuple((cell.value, cell.number_format) for cell in row)
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row)
    ]


@pytest.mark.parametrize(
    ("name", "text_dates"),
    [("rows-register.xlsx", False), ("rows-register-text-dates.xlsx", True)],
)
def test_the_committed_file_equals_what_the_generator_makes(name: str, text_dates: bool) -> None:
    committed = load_workbook(SAMPLES / name)
    made = register(text_dates=text_dates, directory=SAMPLES / "mail")
    assert committed.sheetnames == made.sheetnames == ["Register"]
    assert cells(committed) == cells(made)
    assert committed["Register"].max_row == 4


def test_the_register_holds_three_mails_and_text_dates_are_text() -> None:
    sheet = load_workbook(SAMPLES / "rows-register.xlsx")["Register"]
    assert [c.value for c in sheet[1]] == ["Received", "From", "Subject", "Amount"]
    assert [sheet.cell(r, 4).value for r in (2, 3, 4)] == [10, 20, 30]
    assert not isinstance(sheet["A2"].value, str)
    text = load_workbook(SAMPLES / "rows-register-text-dates.xlsx")["Register"]
    assert isinstance(text["A2"].value, str) and text["A2"].value[:2] == "20"
