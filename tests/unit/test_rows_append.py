"""Appending rows from a source to a copy of a workbook."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.table import Table

from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.mail.messages import MailMessage, sort_messages
from ai_data_enrichment.mail.workbook import RECEIVED_FORMAT, SHEET_NAME, write_mail_workbook
from ai_data_enrichment.rows.append import (
    RowsCount,
    RowsPlan,
    append_rows,
    preview_rows,
)
from ai_data_enrichment.rows.conditions import Condition
from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.mail_files import MAIL_FILES, mail_candidates
from ai_data_enrichment.rows.matching import propose_key, propose_mapping
from ai_data_enrichment.rows.sources import Candidate

NOW = datetime(2026, 11, 10, 12, 0)
FORMAT = "dd.mm.yyyy hh:mm"


def day(number: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, number, hour, 0)


def cand(received: datetime | None, sender: str | None, subject: str = "Hi") -> Candidate:
    return Candidate({"received": received, "sender": sender, "subject": subject})


def make_book(path: Path, rows: list[tuple[Any, Any, Any]]) -> Path:
    """Sheet ``Register`` (header in row 1, a note column D) and a second sheet ``Other``."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Register"
    for col, header in enumerate(("Received", "From", "Subject", "Note"), start=1):
        sheet.cell(row=1, column=col, value=header).font = Font(bold=True)
    for number, (received, sender, subject) in enumerate(rows, start=2):
        sheet.cell(row=number, column=1, value=received).number_format = FORMAT
        sheet.cell(row=number, column=2, value=sender)
        sheet.cell(row=number, column=3, value=subject)
        sheet.cell(row=number, column=4, value="n")
    other = book.create_sheet("Other")
    other["A1"] = "keep"
    other["B2"] = 5
    book.save(path)
    return path


def plan(
    last_row: int,
    *,
    on_duplicate: str = "skip",
    conditions: tuple[Condition, ...] = (),
    key: tuple[str, ...] = ("received", "sender"),
) -> RowsPlan:
    return RowsPlan(
        source_id="mail_files",
        selection=TableSelection("Register", f"A1:D{last_row}"),
        mapping={"received": "A", "sender": "B", "subject": "C"},
        key=key,
        on_duplicate="add_marked" if on_duplicate == "add_marked" else "skip",
        conditions=conditions,
    )


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return make_book(tmp_path / "in.xlsx", [(day(1), "Acme", "one"), (day(2), "Bolt", "two")])


def only_one_file_left(tmp_path: Path, *names: str) -> None:
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names)


def test_invariant_without_conditions_and_with_duplicates(book: Path, tmp_path: Path) -> None:
    candidates = [
        cand(day(1), "acme", "again"),  # in the table (case ignored)
        cand(day(3), "Cargo"),  # new
        cand(day(3), "CARGO"),  # same key as the one before
        cand(day(4), None),  # no key
        cand(None, "Dune"),  # no key
    ]
    result = append_rows(book, tmp_path / "out.xlsx", plan(3), candidates, now=NOW)
    count = result.count
    assert (count.in_source, count.left_out, count.without_key) == (5, 0, 2)
    assert (count.duplicates_in_table, count.duplicates_in_source, count.added) == (1, 1, 1)
    assert count.written == 1 and count.holds and count.table_rows_without_key == 0
    assert (result.first_row, result.last_row, result.range_ref) == (4, 4, "A1:D4")


def test_invariant_with_a_date_condition(book: Path, tmp_path: Path) -> None:
    conditions = (Condition("received", "on_or_after", date(2026, 11, 3)),)
    candidates = [cand(day(3), "A"), cand(day(2), "B"), cand(None, "C"), cand(day(5), None)]
    count = preview_rows(book, plan(3, conditions=conditions), candidates, now=NOW)
    # The undated one does not pass; the one without a sender passes but has no key.
    assert (count.left_out, count.without_key, count.added) == (2, 1, 1)
    assert count.holds
    count = preview_rows(book, plan(3), candidates, now=NOW)
    assert (count.left_out, count.without_key, count.added) == (0, 2, 2)
    assert count.holds


def test_a_rerun_on_the_output_adds_nothing(book: Path, tmp_path: Path) -> None:
    candidates = [cand(day(3), "A"), cand(day(4), "B"), cand(day(1), "Acme"), cand(None, "X")]
    first = append_rows(book, tmp_path / "one.xlsx", plan(3), candidates, now=NOW)
    assert first.count.added == 2
    selection = TableSelection("Register", first.range_ref)
    again = RowsPlan("mail_files", selection, plan(3).mapping, ("received", "sender"), "skip")
    second = append_rows(tmp_path / "one.xlsx", tmp_path / "two.xlsx", again, candidates, now=NOW)
    assert (second.count.added, second.count.written) == (0, 0)
    assert second.count.duplicates_in_table == 3
    assert second.count.holds
    assert (second.first_row, second.last_row, second.range_ref) == (None, None, "A1:D5")
    cells = lambda p: [[c.value for c in r] for r in load_workbook(p)["Register"].iter_rows()]  # noqa: E731
    assert cells(tmp_path / "one.xlsx") == cells(tmp_path / "two.xlsx")


def test_nothing_old_changes_and_the_source_stays(book: Path, tmp_path: Path) -> None:
    before = book.read_bytes()
    append_rows(book, tmp_path / "out.xlsx", plan(3), [cand(day(5), "New")], now=NOW)
    assert book.read_bytes() == before
    old, new = load_workbook(book), load_workbook(tmp_path / "out.xlsx")
    assert old.sheetnames == new.sheetnames
    for name in old.sheetnames:
        for row in old[name].iter_rows():
            for cell in row:
                other = new[name][cell.coordinate]
                assert (cell.value, cell.number_format) == (other.value, other.number_format)
    assert new["Other"]["A1"].value == "keep"
    assert new["Register"].max_row == 4


def test_a_subject_starting_with_equals_stays_text(book: Path, tmp_path: Path) -> None:
    append_rows(book, tmp_path / "out.xlsx", plan(3), [cand(day(5), "X", "=1+1")], now=NOW)
    cell = load_workbook(tmp_path / "out.xlsx")["Register"]["C4"]
    assert cell.value == "=1+1" and cell.data_type == "s"
    assert load_workbook(tmp_path / "out.xlsx", data_only=False)["Register"]["C4"].data_type == "s"


def test_a_text_date_in_a_key_column_is_refused(tmp_path: Path) -> None:
    path = make_book(tmp_path / "in.xlsx", [(day(1), "Acme", "x"), ("2026-11-02", "Bolt", "y")])
    path2 = make_book(tmp_path / "in2.xlsx", [("a", "A", "x"), (day(2), "B", "y"), ("b", "C", "z")])
    with pytest.raises(RowsError) as error:
        append_rows(path, tmp_path / "out.xlsx", plan(3), [cand(day(5), "N")], now=NOW)
    assert (error.value.code, error.value.count, error.value.row) == (
        "rows_key_not_comparable",
        1,
        3,
    )
    with pytest.raises(RowsError) as error:
        preview_rows(path2, plan(4), [], now=NOW)
    assert (error.value.count, error.value.row) == (2, 2)
    only_one_file_left(tmp_path, "in.xlsx", "in2.xlsx")


def test_no_room_leaves_no_file(book: Path, tmp_path: Path) -> None:
    below = load_workbook(book)
    below["Register"]["B5"] = "stray"
    below.save(tmp_path / "below.xlsx")
    merged = load_workbook(book)
    merged["Register"].merge_cells("B5:C5")
    merged.save(tmp_path / "merged.xlsx")
    candidates = [cand(day(5), "A"), cand(day(6), "B")]
    for name in ("below.xlsx", "merged.xlsx"):
        with pytest.raises(RowsError) as error:
            append_rows(tmp_path / name, tmp_path / "out.xlsx", plan(3), candidates, now=NOW)
        assert error.value.code == "rows_no_room"
    # One row fits above the stray value.
    count = append_rows(
        tmp_path / "below.xlsx", tmp_path / "out.xlsx", plan(3), candidates[:1], now=NOW
    )
    assert count.count.added == 1
    only_one_file_left(tmp_path, "in.xlsx", "below.xlsx", "merged.xlsx", "out.xlsx")


def test_no_rows_left_on_the_sheet(tmp_path: Path) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Register"
    sheet["A1"], sheet["B1"] = "Received", "From"
    sheet.cell(row=1_048_576, column=1, value=day(1))
    sheet.cell(row=1_048_576, column=2, value="A")
    book.save(tmp_path / "full.xlsx")
    selection = TableSelection("Register", "A1:B1048576")
    full = RowsPlan(
        "mail_files", selection, {"received": "A", "sender": "B"}, ("received",), "skip"
    )
    with pytest.raises(RowsError) as error:
        append_rows(
            tmp_path / "full.xlsx", tmp_path / "out.xlsx", full, [cand(day(2), "B")], now=NOW
        )
    assert error.value.code == "rows_no_room"


def refused(book: Path, tmp_path: Path, bad: RowsPlan) -> str:
    with pytest.raises(RowsError) as error:
        append_rows(book, tmp_path / "out.xlsx", bad, [cand(day(5), "A")], now=NOW)
    assert not (tmp_path / "out.xlsx").exists()
    return error.value.code


def test_the_other_error_codes(book: Path, tmp_path: Path) -> None:
    good = plan(3)

    def changed(**kwargs: object) -> RowsPlan:
        values = {**good.__dict__, **kwargs}
        return RowsPlan(**values)  # type: ignore[arg-type]

    assert refused(book, tmp_path, changed(mapping={"nope": "A"})) == "rows_unknown_field"
    assert refused(book, tmp_path, changed(key=("nope",))) == "rows_unknown_field"
    unknown_in_condition = (Condition("nope", "contains", "x"),)
    assert refused(book, tmp_path, changed(conditions=unknown_in_condition)) == "rows_unknown_field"
    wrong_op = (Condition("sender", "within_last_days", 3),)
    assert refused(book, tmp_path, changed(conditions=wrong_op)) == "rows_bad_condition"
    assert refused(book, tmp_path, changed(mapping={"received": "A", "sender": "F"})) == (
        "rows_column_outside_table"
    )
    assert refused(book, tmp_path, changed(mapping={"received": "A", "sender": "1"})) == (
        "rows_column_outside_table"
    )
    assert refused(book, tmp_path, changed(mapping={"received": "A", "sender": "A"})) == (
        "rows_column_used_twice"
    )
    assert refused(book, tmp_path, changed(key=())) == "rows_key_empty"
    assert refused(book, tmp_path, changed(mapping={"received": "A"})) == "rows_key_not_mapped"
    assert refused(book, tmp_path, changed(source_id="nope")) == "rows_unknown_source"
    missing = TableSelection("Gone", "A1:D3")
    assert refused(book, tmp_path, changed(selection=missing)) == "rows_sheet_missing"


def test_paths_are_checked_like_write_excel_cells(book: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        append_rows(book, book, plan(3), [], now=NOW)
    with pytest.raises(ValueError):
        append_rows(book, tmp_path / "out.csv", plan(3), [], now=NOW)
    (tmp_path / "out.xlsx").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        append_rows(book, tmp_path / "out.xlsx", plan(3), [], now=NOW)
    assert (tmp_path / "out.xlsx").read_bytes() == b"keep"


def test_text_key_matches_across_case_and_spacing_and_moments_to_the_minute(
    tmp_path: Path,
) -> None:
    path = make_book(tmp_path / "t.xlsx", [(datetime(2026, 11, 1, 9, 0, 40), "Acme  Corp", "x")])
    zone = timezone(timedelta(hours=2))
    same = cand(datetime(2026, 11, 1, 11, 0, tzinfo=zone), " acme corp")  # type: ignore[arg-type]
    later = cand(datetime(2026, 11, 1, 9, 1), "Acme Corp")
    count = preview_rows(path, plan(2), [same, later], now=NOW)
    assert (count.duplicates_in_table, count.added) == (1, 1)


def test_the_mail_workbook_is_recognised_by_its_own_rows(tmp_path: Path) -> None:
    def message(subject: str, sender: str, received: datetime | None, source: str) -> MailMessage:
        return MailMessage(
            "id", received, sender, f"{sender}@example.com", "me@example.org",
            subject, "Body", False, source,
        )  # fmt: skip

    messages = [
        message("one", "Acme", datetime(2026, 11, 1, 6, 12, 44, tzinfo=UTC), "a"),
        message(
            "two", "Bolt", datetime(2026, 11, 2, 8, 0, tzinfo=timezone(timedelta(hours=3))), "b"
        ),
        message("none", "Cargo", None, "c"),
    ]
    path = tmp_path / "mail.xlsx"
    write_mail_workbook(messages, path)
    mapping = {"received": "A", "sender": "B", "subject": "C"}
    headers = [str(c.value) for c in load_workbook(path)[SHEET_NAME][1]]
    proposed = propose_mapping(MAIL_FILES, headers)
    assert {k: "ABCD"[i] for k, i in proposed.items()} == mapping
    key = propose_key(MAIL_FILES, set(proposed))
    assert key == ("received", "sender")
    selection = TableSelection(SHEET_NAME, "A1:D4")
    mail_plan = RowsPlan("mail_files", selection, mapping, key, "skip")
    candidates = mail_candidates(messages)
    assert len(candidates) == len(sort_messages(messages))
    count = preview_rows(path, mail_plan, candidates, now=NOW)
    assert count.added == 0 and count.holds
    assert count.duplicates_in_table == 2 and count.without_key == 1
    # The cells of a candidate are the cells of the workbook.
    sheet = load_workbook(path)[SHEET_NAME]
    for row, item in enumerate(candidates, start=2):
        assert item.values["received"] == sheet.cell(row=row, column=1).value
        assert item.values["sender"] == sheet.cell(row=row, column=2).value
        assert item.values["subject"] == sheet.cell(row=row, column=3).value


def test_add_marked_writes_duplicates_with_a_comment(book: Path, tmp_path: Path) -> None:
    candidates = [
        cand(day(2), "Bolt", "again"),  # duplicates row 3
        cand(day(6), "New"),  # row 5
        cand(day(6), "new"),  # duplicates row 5
    ]
    result = append_rows(
        book, tmp_path / "out.xlsx", plan(3, on_duplicate="add_marked"), candidates, now=NOW
    )
    count = result.count
    assert (count.added, count.duplicates_in_table, count.duplicates_in_source) == (1, 1, 1)
    assert count.written == 3 and count.holds
    sheet = load_workbook(tmp_path / "out.xlsx")["Register"]
    assert (result.first_row, result.last_row, result.range_ref) == (4, 6, "A1:D6")
    assert sheet["B4"].value == "Bolt" and sheet["C4"].value == "again"
    assert sheet["A4"].comment is not None
    assert sheet["A4"].comment.text == "Possible duplicate of row 3"
    assert sheet["A5"].comment is None
    assert sheet["A6"].comment is not None
    assert sheet["A6"].comment.text == "Possible duplicate of row 5"
    assert sheet["B6"].comment is None
    # The preview says the same.
    assert preview_rows(book, plan(3, on_duplicate="add_marked"), candidates, now=NOW) == count


def test_a_table_and_an_auto_filter_on_the_region_are_extended(tmp_path: Path) -> None:
    path = make_book(tmp_path / "in.xlsx", [(day(1), "A", "x"), (day(2), "B", "y")])
    book = load_workbook(path)
    sheet = book["Register"]
    sheet.add_table(Table(displayName="Register1", ref="A1:D3"))
    sheet["G1"], sheet["G2"], sheet["H1"], sheet["H2"] = "k", 1, "v", 2
    sheet.add_table(Table(displayName="Elsewhere", ref="G1:H2"))
    book.save(path)
    result = append_rows(path, tmp_path / "out.xlsx", plan(3), [cand(day(5), "C")], now=NOW)
    out = load_workbook(tmp_path / "out.xlsx")["Register"]
    assert out.tables["Register1"].ref == "A1:D4"
    assert out.tables["Elsewhere"].ref == "G1:H2"
    assert result.range_ref == "A1:D4"

    filtered = load_workbook(path)
    filtered["Register"].tables.clear()
    filtered["Register"].auto_filter.ref = "A1:D3"
    filtered.save(tmp_path / "f.xlsx")
    append_rows(tmp_path / "f.xlsx", tmp_path / "fout.xlsx", plan(3), [cand(day(5), "C")], now=NOW)
    assert load_workbook(tmp_path / "fout.xlsx")["Register"].auto_filter.ref == "A1:D4"
    # A filter on another region is left alone.
    narrow = load_workbook(path)
    narrow["Register"].tables.clear()
    narrow["Register"].auto_filter.ref = "A1:B3"
    narrow.save(tmp_path / "n.xlsx")
    append_rows(tmp_path / "n.xlsx", tmp_path / "nout.xlsx", plan(3), [cand(day(5), "C")], now=NOW)
    assert load_workbook(tmp_path / "nout.xlsx")["Register"].auto_filter.ref == "A1:B3"


def test_new_cells_take_the_style_of_the_cell_above(tmp_path: Path) -> None:
    path = make_book(tmp_path / "in.xlsx", [(day(1), "A", "x")])
    book = load_workbook(path)
    book["Register"]["B2"].font = Font(italic=True)
    book["Register"]["D2"].number_format = "0.00"
    book["Register"]["A2"].value = None
    book["Register"]["A2"].number_format = "General"
    book.save(path)
    append_rows(path, tmp_path / "out.xlsx", plan(2), [cand(day(5), "C")], now=NOW)
    sheet = load_workbook(tmp_path / "out.xlsx")["Register"]
    assert sheet["B3"].font.italic is True
    assert sheet["D3"].number_format == "0.00" and sheet["D3"].value is None
    # A moment on a General cell gets the format of the mail workbook.
    assert sheet["A3"].number_format == RECEIVED_FORMAT
    other = make_book(tmp_path / "in2.xlsx", [(day(1), "A", "x")])
    append_rows(other, tmp_path / "out2.xlsx", plan(2), [cand(day(5), "C")], now=NOW)
    assert load_workbook(tmp_path / "out2.xlsx")["Register"]["A3"].number_format == FORMAT


def test_preview_and_append_give_the_same_counts(book: Path, tmp_path: Path) -> None:
    candidates = [cand(day(1), "Acme"), cand(day(7), "Z"), cand(day(7), "z"), cand(None, "Q")]
    conditions = (Condition("sender", "contains", "z"),)
    for mode in ("skip", "add_marked"):
        wanted = plan(3, on_duplicate=mode, conditions=conditions)
        preview = preview_rows(book, wanted, candidates, now=NOW)
        out = tmp_path / f"{mode}.xlsx"
        assert append_rows(book, out, wanted, candidates, now=NOW).count == preview
        assert isinstance(preview, RowsCount) and preview.holds


def test_table_rows_without_a_key_and_a_selection_longer_than_the_data(tmp_path: Path) -> None:
    path = make_book(
        tmp_path / "in.xlsx", [(day(1), "A", "x"), (None, "B", "y"), (day(3), None, "z")]
    )
    # The range runs to row 8; the data ends at row 4. New rows start at row 5.
    result = append_rows(path, tmp_path / "out.xlsx", plan(8), [cand(day(9), "N")], now=NOW)
    assert result.count.table_rows_without_key == 2
    assert (result.first_row, result.range_ref) == (5, "A1:D5")
    assert load_workbook(tmp_path / "out.xlsx")["Register"]["B5"].value == "N"


def test_a_table_with_only_its_header_gets_plain_rows(tmp_path: Path) -> None:
    source = make_book(tmp_path / "in.xlsx", [])
    result = append_rows(source, tmp_path / "out.xlsx", plan(1), [cand(day(3), "Shop")], now=NOW)
    assert (result.first_row, result.last_row, result.range_ref) == (2, 2, "A1:D2")
    sheet = load_workbook(tmp_path / "out.xlsx")["Register"]
    assert sheet["A1"].font.bold and not sheet["A2"].font.bold
    assert sheet["A2"].value == day(3) and sheet["A2"].number_format == RECEIVED_FORMAT


def test_a_selection_that_names_whole_columns_reads_only_the_filled_rows(tmp_path: Path) -> None:
    source = make_book(tmp_path / "in.xlsx", [(day(1), "Shop", "a")])
    whole = RowsPlan(
        source_id="mail_files",
        selection=TableSelection("Register", "A1:D1048576"),
        mapping={"received": "A", "sender": "B", "subject": "C"},
        key=("received", "sender"),
        on_duplicate="skip",
    )
    result = append_rows(
        source, tmp_path / "out.xlsx", whole, [cand(day(1), "shop"), cand(day(2), "Shop")], now=NOW
    )
    assert (result.count.duplicates_in_table, result.count.added) == (1, 1)
    assert (result.first_row, result.range_ref) == (3, "A1:D3")


def test_a_merged_cell_in_the_last_row_does_not_stop_the_append(tmp_path: Path) -> None:
    source = make_book(tmp_path / "in.xlsx", [(day(1), "Shop", "a")])
    book = load_workbook(source)
    book["Register"]["D2"] = None
    book["Register"].merge_cells("C2:D2")
    book.save(source)
    result = append_rows(
        source, tmp_path / "out.xlsx", plan(2), [cand(day(2), "Shop", "b")], now=NOW
    )
    assert result.count.added == 1
    assert load_workbook(tmp_path / "out.xlsx")["Register"]["C3"].value == "b"


def test_max_last_row_stops_an_append_before_anything_is_written(
    book: Path, tmp_path: Path
) -> None:
    candidates = [cand(day(5), "A"), cand(day(6), "B"), cand(day(7), "C")]
    # The table ends in row 3; three new rows reach row 6.
    for limit in (5, 3):
        with pytest.raises(RowsError) as error:
            append_rows(
                book, tmp_path / "out.xlsx", plan(3), candidates, now=NOW, max_last_row=limit
            )
        assert error.value.code == "rows_too_many"
        assert not (tmp_path / "out.xlsx").exists()
        with pytest.raises(RowsError) as previewed:
            preview_rows(book, plan(3), candidates, now=NOW, max_last_row=limit)
        assert previewed.value.code == "rows_too_many"
    # Exactly at the limit is allowed, and so is a run that adds nothing.
    result = append_rows(book, tmp_path / "out.xlsx", plan(3), candidates, now=NOW, max_last_row=6)
    assert result.last_row == 6
    assert preview_rows(book, plan(3), candidates, now=NOW, max_last_row=6).added == 3
    assert preview_rows(book, plan(3), [cand(day(1), "Acme")], now=NOW, max_last_row=1).added == 0
    assert preview_rows(book, plan(3), candidates, now=NOW) == result.count
