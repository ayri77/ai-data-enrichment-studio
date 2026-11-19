"""Build samples/probe/rows-register.xlsx and rows-register-text-dates.xlsx.

A small register for the scenario "add rows from e-mail files" (frontend/e2e/rows.spec.ts):
Received, From, Subject as the application writes them for a table of mails, plus an Amount
column. Its three rows are the first, the third and the sixth of the ten invented mails in
samples/probe/mail, so that a run on those ten files finds three rows already in the table
and seven new ones. The second file is the same register with the dates written as text, which
the plan refuses (a date that is not a date cell cannot be compared).

    uv run python -m scripts.make_rows_register
"""

import io
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from ai_data_enrichment.mail import upload as mail_upload
from ai_data_enrichment.mail.workbook import RECEIVED_FORMAT
from ai_data_enrichment.rows.mail_files import mail_candidates

MAIL_DIRECTORY = Path("samples/probe/mail")
TARGET = Path("samples/probe")
# Positions among the ten mails, in file-name order; the amounts are invented.
PICKS = (0, 2, 5)
AMOUNTS = (10, 20, 30)
HEADERS = ["Received", "From", "Subject", "Amount"]


def register(*, text_dates: bool = False, directory: Path = MAIL_DIRECTORY) -> Workbook:
    """The register as a workbook, its rows read from the mails with ``mail_candidates``."""
    messages, _ = mail_upload.read_mail_upload(
        _zip(directory),
        archive=True,
    )
    candidates = mail_candidates(messages)
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Register"
    sheet.append(HEADERS)
    for number, (pick, amount) in enumerate(zip(PICKS, AMOUNTS, strict=True), start=2):
        values = candidates[pick].values
        received = values["received"]
        assert isinstance(received, datetime)
        sheet.append(
            [
                received.strftime("%Y-%m-%d %H:%M") if text_dates else received,
                values["sender"],
                values["subject"],
                amount,
            ]
        )
        if not text_dates:
            sheet.cell(row=number, column=1).number_format = RECEIVED_FORMAT
    for letter, width in zip("ABCD", (20, 24, 46, 10), strict=True):
        sheet.column_dimensions[letter].width = width
    return workbook


def _zip(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(directory.glob("*.eml")):
            archive.write(path, path.name)
    return buffer.getvalue()


def build(target: Path = TARGET) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text_dates in (
        ("rows-register.xlsx", False),
        ("rows-register-text-dates.xlsx", True),
    ):
        path = target / name
        register(text_dates=text_dates).save(path)
        written.append(path)
    return written


if __name__ == "__main__":
    for path in build():
        print(path)
