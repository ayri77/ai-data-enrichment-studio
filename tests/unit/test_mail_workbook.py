"""The generated workbook of an application table of e-mails."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from openpyxl import load_workbook

from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.mail.documents import prepare
from ai_data_enrichment.mail.messages import MailMessage, read_eml_folder
from ai_data_enrichment.mail.workbook import (
    HEADERS,
    MAX_CELL_CHARS,
    SHEET_NAME,
    write_mail_workbook,
)

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"


def mail(
    *,
    subject: str = "Hello",
    name: str = "Cloudbox",
    address: str = "billing@cloudbox.example.com",
    text: str = "Body",
    received: datetime | None = datetime(
        2026, 11, 1, 6, 12, 44, tzinfo=timezone(timedelta(hours=1))
    ),
    source: str = "a.eml",
    truncated: bool = False,
) -> MailMessage:
    return MailMessage(
        "id", received, name, address, "me@example.org", subject, text, truncated, source
    )


def rows(path: Path) -> list:
    return list(load_workbook(path)[SHEET_NAME].iter_rows())


def test_one_sheet_four_columns_text_last_and_rows_in_the_order_of_the_dates(
    tmp_path: Path,
) -> None:
    messages = [
        mail(subject="late", received=datetime(2026, 11, 9, tzinfo=UTC), source="a"),
        mail(subject="undated", received=None, source="b"),
        mail(subject="early", received=datetime(2026, 11, 2, tzinfo=UTC), source="c"),
    ]
    summary = write_mail_workbook(messages, tmp_path / "m.xlsx")
    book = load_workbook(tmp_path / "m.xlsx")
    assert book.sheetnames == ["Mail"]
    cells = rows(tmp_path / "m.xlsx")
    assert tuple(c.value for c in cells[0]) == ("Received", "From", "Subject", "Text") == HEADERS
    assert [r[2].value for r in cells[1:]] == ["early", "late", "undated"]
    assert summary.read == 3 and summary.skipped == 0


def test_received_is_a_date_time_in_utc_without_a_time_zone(tmp_path: Path) -> None:
    write_mail_workbook([mail(), mail(received=None, source="b")], tmp_path / "m.xlsx")
    cells = rows(tmp_path / "m.xlsx")
    assert cells[1][0].value == datetime(2026, 11, 1, 5, 12)
    assert cells[1][0].is_date and cells[1][0].number_format == "yyyy-mm-dd hh:mm"
    assert cells[2][0].value is None


def test_a_date_excel_cannot_hold_leaves_the_cell_empty(tmp_path: Path) -> None:
    old = mail(received=datetime(1850, 1, 1, tzinfo=UTC))
    write_mail_workbook([old], tmp_path / "m.xlsx")
    assert rows(tmp_path / "m.xlsx")[1][0].value is None


def test_from_is_the_name_or_else_the_domain(tmp_path: Path) -> None:
    named = mail(source="a")
    nameless = mail(name="", address="x@hosthill.example.org", source="b")
    write_mail_workbook([named, nameless], tmp_path / "m.xlsx")
    assert [r[1].value for r in rows(tmp_path / "m.xlsx")[1:]] == [
        "Cloudbox",
        "hosthill.example.org",
    ]


def test_text_that_looks_like_a_formula_stays_a_string_cell(tmp_path: Path) -> None:
    row: tuple = ()
    for start in "=+-@":
        message = mail(subject=f"{start}1+1", name=f"{start}sum", text=f"{start}cmd|' /C calc'!A0")
        write_mail_workbook([message], tmp_path / "m.xlsx")
        row = rows(tmp_path / "m.xlsx")[1]
        for cell in row[1:]:
            assert cell.data_type == "s", (start, cell.coordinate)
        assert row[2].value == f"{start}1+1" and row[1].value == f"{start}sum"
    # The body is read inside the document; it starts with "Subject:", never with the text.
    assert row[3].value.startswith("Subject: ")


def test_control_characters_are_removed_and_a_long_text_is_cut(tmp_path: Path) -> None:
    message = mail(subject="Sub\x01ject", name="Na\x01me", text="A\x02B" + "x" * 40_000)
    write_mail_workbook([message], tmp_path / "m.xlsx")
    row = rows(tmp_path / "m.xlsx")[1]
    assert row[1].value == "Name" and row[2].value == "Subject"
    assert len(row[3].value) == MAX_CELL_CHARS and "\x02" not in row[3].value


def test_an_empty_body_and_no_subject_still_make_a_row(tmp_path: Path) -> None:
    write_mail_workbook([mail(subject="", text="")], tmp_path / "m.xlsx")
    row = rows(tmp_path / "m.xlsx")[1]
    assert row[2].value is None
    assert row[3].value.startswith("Subject: \nFrom: Cloudbox")


def test_masking_is_applied_to_from_subject_and_text(tmp_path: Path) -> None:
    message = mail(
        subject="Receipt for me@example.org",
        name="+1 555 010 0199",
        text="Card ending in 1234, sent to me@example.org",
    )
    summary = write_mail_workbook([message], tmp_path / "m.xlsx")
    row = rows(tmp_path / "m.xlsx")[1]
    assert row[1].value == "[phone]" and row[2].value == "Receipt for [me]"
    assert "[card]" in row[3].value and "me@example.org" not in row[3].value
    assert row[3].value == prepare([message], message, True)[0]
    assert (
        summary.masked["card"] == 1 and summary.masked["me"] >= 2 and summary.masked["phone"] == 1
    )


def test_without_masking_nothing_is_replaced(tmp_path: Path) -> None:
    message = mail(subject="me@example.org", text="x me@example.org")
    summary = write_mail_workbook([message], tmp_path / "m.xlsx", mask=False)
    row = rows(tmp_path / "m.xlsx")[1]
    assert row[2].value == "me@example.org" and "me@example.org" in row[3].value
    assert summary.masked == {"card": 0, "phone": 0, "me": 0}


def test_the_text_cell_is_the_masked_document(tmp_path: Path) -> None:
    messages = read_eml_folder(FIXTURES)
    write_mail_workbook(messages, tmp_path / "m.xlsx")
    ordered = rows(tmp_path / "m.xlsx")[1:]
    assert len(ordered) == 10
    assert {r[3].value for r in ordered} == {prepare(messages, m, True)[0] for m in messages}


def test_the_summary_counts_truncated_mails_and_masking_of_the_fixtures(tmp_path: Path) -> None:
    messages = read_eml_folder(FIXTURES)
    summary = write_mail_workbook(
        [*messages, mail(truncated=True, source="z")], tmp_path / "m.xlsx"
    )
    assert summary.read == 11 and summary.truncated == 1 and summary.skipped == 0
    assert (
        summary.masked["card"] >= 1 and summary.masked["phone"] >= 1 and summary.masked["me"] >= 1
    )


def test_the_workbook_can_be_read_back_as_a_table(tmp_path: Path) -> None:
    write_mail_workbook(
        [mail(subject="=1+1", text="=2+2"), mail(subject="x", source="b")], tmp_path / "m.xlsx"
    )
    batch = read_input_batch(
        tmp_path / "m.xlsx",
        selection=TableSelection("Mail", "A1:D3"),
        input_columns=("Subject", "Text"),
    )
    assert len(batch.records) == 2
