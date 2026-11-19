"""The truth table maker and the scorer, on small invented runs and on the ten fixture mails.

Nothing here reads a real mail or calls a model. Subjects, senders, values and quotes of the
invented runs carry sentinels that must never reach the console.
"""

import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from scripts import make_mail_truth, score_mail_runs
from scripts.experiment_mail_extraction import run as probe
from scripts.mail_truth_common import REPO_ROOT, relation
from scripts.make_mail_truth import AGREED, CONFLICT, EMPTY, PARTLY, decide_cell, decide_record
from scripts.score_mail_runs import read_truth
from tests.support.ollama import FakeOpener
from tests.support.web_lookup import RowApi
from tests.unit.test_experiment_mail_extraction import KEY
from tests.unit.test_experiment_mail_extraction import args as openai_args
from tests.unit.test_experiment_mail_extraction import choose as openai_choose
from tests.unit.test_experiment_mail_extraction_ollama import args as ollama_args
from tests.unit.test_experiment_mail_extraction_ollama import server as ollama_server

TARGETS = [
    {"header": "Service", "kind": "text", "options": []},
    {"header": "Amount", "kind": "number", "options": []},
    {"header": "Paid on", "kind": "date", "options": []},
    {"header": "Event", "kind": "one_of", "options": ["payment", "ended"]},
]
QUESTION = "SENTINEL-TASK invented payments"
SECRETS = ("SENTINEL-SUBJECT", "sentinel-sender", "SENTINEL-QUOTE", "SENTINEL-TASK", "SENTINEL-V")
FIRST_COLUMN = 6  # Service: after Source, Received, From, Subject, Is a record


def value(v: Any, mark: str = "literal") -> dict[str, Any]:
    return {"value": v, "mark": mark, "quote": "SENTINEL-QUOTE"}


def row(
    source: str,
    outcome: str = "FOUND",
    values: dict[str, Any] | None = None,
    dropped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "message_id": f"id-{source}@example.com",
        "received": "2026-11-03T10:00:00+00:00",
        "sender_name": "sentinel-sender",
        "sender_address": "sentinel-sender@example.com",
        "subject": "SENTINEL-SUBJECT",
        "outcome": outcome,
        "values": values or {},
        "dropped": dropped or [],
        "seconds": 1.0,
    }


def write_run(
    directory: Path,
    rows: Sequence[dict[str, Any]],
    *,
    targets: list[dict[str, Any]] = TARGETS,
    question: str = QUESTION,
    executor: str = "openai",
) -> Path:
    directory.mkdir(parents=True)
    (directory / "rows.json").write_text(json.dumps(list(rows)), encoding="utf-8")
    summary = {
        "configuration_id": f"{executor}-config",
        "executor": executor,
        "model": "m",
        "task": {"question": question, "targets": targets},
    }
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return directory


def three_runs(root: Path) -> list[Path]:
    """Three runs over six mails that show every state a cell and a record can have."""
    full = {
        "Service": value("SENTINEL-V Cloudbox Plus"),
        "Amount": value(9.99),
        "Paid on": value("2026-11-03"),
        "Event": value("payment", "derived"),
    }
    r1 = [
        row("m1.eml", values=full),
        row("m2.eml", values={"Service": value("Notes"), "Amount": value(4.5)}),
        row("m3.eml", "NOT_APPLICABLE"),
        row("m4.eml", values={"Service": value("Notes App"), "Amount": value(2.0)}),
        row("m5.eml", "NOTHING_FOUND"),
        row("m6.eml", "PROVIDER_ERROR"),
    ]
    r2 = [
        row(
            "m1.eml",
            values={
                **full,
                "Service": value("sentinel-v  CLOUDBOX plus"),
                "Amount": value(9.990001),
            },
        ),
        row("m2.eml", values={"Service": value("Notes")}),
        row("m3.eml", "NOT_APPLICABLE"),
        row("m4.eml", "PROVIDER_ERROR"),
        row("m5.eml", "NOTHING_FOUND"),
        row("m6.eml", "PROVIDER_ERROR"),
    ]
    # In another order on purpose; the first run sets the order of the table.
    r3 = list(reversed([
        row("m1.eml", values={**full, "Event": value("ended", "derived")}),
        row("m2.eml", "NOT_APPLICABLE"),
        row("m3.eml", "NOT_APPLICABLE"),
        row("m4.eml", values={"Service": value("Notes App Pro"), "Amount": value(2.0)}),
        row("m5.eml", "NOTHING_FOUND"),
        row("m6.eml", "PROVIDER_ERROR"),
    ]))  # fmt: skip
    return [
        write_run(root / "run-a", r1),
        write_run(root / "run-b", r2, executor="ollama"),
        write_run(root / "run-c", r3),
    ]


def make(out: Path, runs: Sequence[Path]) -> int:
    return make_mail_truth.run([*map(str, runs), "--out", str(out)])


def truth_rows(sheet: Worksheet) -> dict[str, dict[str, Any]]:
    headers = [str(c.value) for c in sheet[1]]
    return {
        str(r[0].value): dict(zip(headers, r, strict=False)) for r in sheet.iter_rows(min_row=2)
    }


# --- The vote and the cells ------------------------------------------------------------


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["found", "found"], "yes"),
        (["not_applicable", "not_applicable"], "no"),
        (["found", "not_applicable"], "?"),
        (["found", "failed"], "yes"),  # a failed run has no vote
        (["not_applicable", "failed", "failed"], "no"),
        (["failed", "failed"], "?"),  # nobody voted
        (["nothing_found", "nothing_found"], "?"),  # about the task, but no record
        (["found", "nothing_found"], "?"),
    ],
)
def test_the_vote_for_is_a_record(groups: list[str], expected: str) -> None:
    assert decide_record(groups) == expected


def test_every_state_of_a_value_cell() -> None:
    both = [("a", 9.99), ("b", 9.990001)]
    assert decide_cell("number", both, 2).status == AGREED
    assert decide_cell("number", both, 2).value == 9.99
    assert decide_cell("number", both[:1], 2).status == PARTLY  # the other gave nothing
    assert decide_cell("number", both[:1], 1).status == AGREED  # the other run failed
    assert decide_cell("number", [("a", 1), ("b", 2)], 2).status == CONFLICT
    assert decide_cell("text", [], 3).status == EMPTY
    # A text that holds the other is still a conflict; the person decides the wording.
    held = decide_cell("text", [("a", "Notes"), ("b", "Notes App")], 2)
    assert held.status == CONFLICT and held.value == "?"
    assert decide_cell("text", [("a", "Notes"), ("b", " notes ")], 2).status == AGREED
    date_cell = decide_cell("date", [("a", "2026-11-03")], 1)
    assert date_cell.value == "2026-11-03" and isinstance(date_cell.value, str)


# --- The workbook ----------------------------------------------------------------------


def test_the_truth_workbook(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "truth.xlsx"
    assert make(out, three_runs(tmp_path)) == 0
    console = capsys.readouterr()
    assert not any(s in console.out + console.err for s in SECRETS)
    assert str(out.resolve()) in console.out
    assert "Mails: 6; runs merged: 3" in console.out

    workbook = load_workbook(out)
    assert workbook.sheetnames == ["Truth", "Candidates", "Info"]
    truth = workbook["Truth"]
    assert [c.value for c in truth[1]] == [
        "Source", "Received", "From", "Subject", "Is a record",
        "Service", "Amount", "Paid on", "Event",
    ]  # fmt: skip
    rows = truth_rows(truth)
    assert list(rows) == [f"m{i}.eml" for i in range(1, 7)]  # the order of the first run
    assert rows["m1.eml"]["Received"].value == "2026-11-03"
    assert rows["m1.eml"]["From"].value == "sentinel-sender"
    assert rows["m1.eml"]["Subject"].value == "SENTINEL-SUBJECT"

    # Is a record: yes, ? (found/found/not applicable), no, yes (one failed), ?, ? (all failed)
    assert [rows[f"m{i}.eml"]["Is a record"].value for i in range(1, 7)] == [
        "yes", "?", "no", "yes", "?", "?",
    ]  # fmt: skip

    def state(source: str, header: str) -> tuple[Any, str, str | None]:
        cell = rows[source][header]
        fill = cell.fill.start_color.rgb if cell.fill.fill_type else None
        return cell.value, (fill or "")[-6:], cell.comment.text if cell.comment else None

    # m1: agreed text (case and spacing aside), agreed number, agreed date, a conflict
    assert state("m1.eml", "Service") == ("SENTINEL-V Cloudbox Plus", "", None)
    assert state("m1.eml", "Amount") == (9.99, "", None)
    assert state("m1.eml", "Paid on") == ("2026-11-03", "", None)
    event, fill, comment = state("m1.eml", "Event")
    assert (event, fill) == ("?", "F4B183")
    assert comment == "run-a: payment\nrun-b: payment\nrun-c: ended"
    # m2: two of three runs say "Notes" and the third found no record: partly
    assert state("m2.eml", "Service") == ("Notes", "FFF2CC", None)
    assert state("m2.eml", "Amount") == (4.5, "FFF2CC", None)
    assert state("m2.eml", "Paid on")[0] is None
    # m3: nothing; m4: a text that holds the other is a conflict, the number is agreed
    assert state("m3.eml", "Service")[0] is None
    service, fill, comment = state("m4.eml", "Service")
    assert (service, fill) == ("?", "F4B183")
    assert comment == "run-a: Notes App\nrun-c: Notes App Pro"
    assert state("m4.eml", "Amount") == (2, "", None)  # an integer stays a number
    # the record cell that is open is coloured too
    assert rows["m2.eml"]["Is a record"].fill.start_color.rgb.endswith("F4B183")

    # A number is a number, a date is text, and typed dates stay text in Excel.
    assert isinstance(rows["m1.eml"]["Amount"].value, float)
    assert rows["m1.eml"]["Paid on"].number_format == "@"

    # Drop-downs: the record and the choice column (its options and "-" live on Info).
    validations = {str(v.sqref): v for v in truth.data_validations.dataValidation}
    assert validations["E2:E7"].formula1 == '"yes,no,?"'
    event_choice = validations["I2:I7"]
    assert event_choice.formula1.startswith("=Info!$A$")
    info = workbook["Info"]
    found = re.fullmatch(r"=Info!\$A\$(\d+):\$A\$(\d+)", event_choice.formula1)
    assert found is not None
    first, last = int(found[1]), int(found[2])
    assert [info.cell(row=r, column=1).value for r in range(first, last + 1)] == [
        "payment", "ended", "-",
    ]  # fmt: skip

    # Source is locked: the sheet is protected and only the cells after it are unlocked.
    assert truth.protection.sheet
    assert truth["A2"].protection.locked and not truth["E2"].protection.locked
    assert not truth["I7"].protection.locked
    assert "locked" in truth["A1"].comment.text


def test_candidates_and_info(tmp_path: Path) -> None:
    out = tmp_path / "truth.xlsx"
    assert make(out, three_runs(tmp_path)) == 0
    workbook = load_workbook(out)
    candidates = [[c.value for c in r] for r in workbook["Candidates"].iter_rows()]
    assert candidates[0] == ["Source", "Column", "Run", "Value", "Mark", "Quote"]
    body = candidates[1:]
    # One line for every value a run wrote: m1 has 4 x 3, m2 3, m4 2 x 2.
    assert len(body) == 12 + 3 + 4
    assert ["m1.eml", "Event", "run-c", "ended", "derived", "SENTINEL-QUOTE"] in body
    assert ["m1.eml", "Amount", "run-b", 9.990001, "literal", "SENTINEL-QUOTE"] in body

    info = [
        [None if c.value is None else str(c.value) for c in r] for r in workbook["Info"].iter_rows()
    ]
    flat = [v for r in info for v in r if v is not None]
    assert ["Task", QUESTION, None] in info
    assert ["Column", "Kind", "Options"] in info and ["Event", "one_of", "payment|ended"] in info
    assert ["run-b", "ollama", "ollama-config"] in info
    counts = {r[0].split(" (")[0]: r[1] for r in info if r[0] and r[1] is not None}
    # 24 value cells: agreed Service, Amount, Paid on of m1 and the Amount of m4; partly ...
    assert counts["agreed"] == "4" and counts["partly"] == "2" and counts["conflict"] == "2"
    assert counts["empty"] == "16" and counts["? in 'Is a record'"] == "3"
    assert sum(int(counts[k]) for k in ("agreed", "partly", "conflict", "empty")) == 6 * 4
    assert sum("?" in line or "-" in line for line in flat) >= 3  # the three instructions


# --- Refusals of the maker -------------------------------------------------------------


def test_runs_of_another_task_or_columns_are_not_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b, _ = three_runs(tmp_path)
    other_task = write_run(tmp_path / "other-task", [row("m1.eml")], question="SENTINEL-OTHER")
    assert make(tmp_path / "t.xlsx", [a, other_task]) == 1
    err = capsys.readouterr().err
    assert "task" in err and "columns" not in err and "SENTINEL" not in err

    fewer = write_run(tmp_path / "fewer", [row("m1.eml")], targets=TARGETS[:2])
    assert make(tmp_path / "t.xlsx", [a, fewer]) == 1
    err = capsys.readouterr().err
    assert "columns" in err and "task" not in err and "Amount" not in err
    assert not (tmp_path / "t.xlsx").exists() and b.is_dir()


def test_runs_that_do_not_hold_the_same_mails_or_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_run(tmp_path / "a", [row("m1.eml"), row("m2.eml")])
    b = write_run(tmp_path / "b", [row("m1.eml"), row("m9.eml")])
    assert make(tmp_path / "t.xlsx", [a, b]) == 1
    assert capsys.readouterr().err == "The runs do not hold the same mails.\n"
    same_name = write_run(tmp_path / "x" / "a", [row("m1.eml"), row("m2.eml")])
    assert make(tmp_path / "t.xlsx", [a, same_name]) == 1
    assert "same folder name" in capsys.readouterr().err


def test_a_target_named_like_a_fixed_column_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clash = [{"header": "Source", "kind": "text", "options": []}]
    a = write_run(tmp_path / "a", [row("m1.eml")], targets=clash)
    assert make(tmp_path / "t.xlsx", [a]) == 1
    assert "fixed column" in capsys.readouterr().err


@pytest.mark.parametrize("tool", ["make", "score"])
def test_output_in_a_tracked_folder_is_refused(
    tool: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = three_runs(tmp_path)
    target = REPO_ROOT / "docs" / "must-not-exist.xlsx"
    with pytest.raises(SystemExit) as stop:
        if tool == "make":
            make_mail_truth.run([str(runs[0]), "--out", str(target)])
        else:
            score_mail_runs.run(
                [str(tmp_path / "t.xlsx"), str(runs[0]), "--out", str(target.with_suffix(".json"))]
            )
    assert stop.value.code == 2 and "not ignored by git" in capsys.readouterr().err
    assert not target.exists() and not target.with_suffix(".json").exists()

    ignored = REPO_ROOT / "artifacts" / "truth-test" / "truth.xlsx"
    try:
        assert make(ignored, runs) == 0 and ignored.is_file()
    finally:
        ignored.unlink(missing_ok=True)
        ignored.parent.rmdir()


# --- Reading the truth back ------------------------------------------------------------


def test_a_workbook_written_by_the_tool_is_read_back_by_the_scorer(tmp_path: Path) -> None:
    out = tmp_path / "truth.xlsx"
    assert make(out, [three_runs(tmp_path)[0]]) == 0
    workbook = load_workbook(out)
    sheet = workbook["Truth"]
    rows = truth_rows(sheet)
    # What a person does: types in cells, as Excel would store them.
    rows["m1.eml"]["Amount"].value = 12  # an integer
    rows["m1.eml"]["Paid on"].value = date(2026, 11, 5)  # a date typed into a cell that lost "@"
    rows["m1.eml"]["Event"].value = "-"
    rows["m1.eml"]["Service"].value = None  # empty means the same as "-"
    rows["m2.eml"]["Is a record"].value = "YES "
    rows["m2.eml"]["Paid on"].value = "2026-11-06"
    for source in ("m3.eml", "m4.eml", "m5.eml", "m6.eml"):
        rows[source]["Is a record"].value = "no"
    workbook.save(out)

    truth = read_truth(out)  # after a round trip through openpyxl
    record, values = truth.mails["m1.eml"]
    assert record == "yes"
    assert values["Amount"] == 12.0 and isinstance(values["Amount"], float)
    assert values["Paid on"] == "2026-11-05" and isinstance(values["Paid on"], str)
    assert values["Event"] is None and values["Service"] is None
    assert truth.mails["m2.eml"][0] == "yes"
    assert truth.mails["m2.eml"][1]["Paid on"] == "2026-11-06"
    assert truth.mails["m2.eml"][1]["Amount"] == 4.5
    assert (truth.unsure_records, truth.unsure_values) == (0, 0)
    assert truth.question == QUESTION and [t["header"] for t in truth.targets] == [
        "Service", "Amount", "Paid on", "Event",
    ]  # fmt: skip


def test_cells_that_do_not_fit_their_column_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = three_runs(tmp_path)[0]
    out = tmp_path / "truth.xlsx"
    make(out, [run_dir])
    workbook = load_workbook(out)
    rows = truth_rows(workbook["Truth"])
    for source in rows:
        rows[source]["Is a record"].value = "yes"
        for header in ("Service", "Amount", "Paid on", "Event"):
            rows[source][header].value = None
    rows["m1.eml"]["Amount"].value = "SENTINEL-V ten"
    rows["m2.eml"]["Paid on"].value = "3 Nov"
    rows["m3.eml"]["Event"].value = "paid"
    rows["m4.eml"]["Is a record"].value = "maybe"
    workbook.save(out)
    assert score_mail_runs.run([str(out), str(run_dir)]) == 1
    err = capsys.readouterr().err
    assert "1 in 'Is a record'" in err and "3 value cells" in err and "SENTINEL" not in err


# --- Scoring ---------------------------------------------------------------------------


def hand_made(root: Path) -> tuple[Path, list[Path]]:
    """A truth of eight mails and two runs whose scores can be counted by hand."""
    a_rows = [
        row(  # right Service, right Amount, wrong date, no Event: 2 correct, 1 wrong, 1 missing
            "t1.eml",
            values={
                "Service": value("cloudbox  PLUS"),
                "Amount": value(10.0),
                "Paid on": value("2026-11-02"),
            },
            dropped=[{"column": "Event", "reason": "quote_not_in_text"}],
        ),
        row(  # close Service, an Amount the truth does not have, Event right (derived)
            "t2.eml",
            values={
                "Service": value("Notes App"),
                "Amount": value(4.5),
                "Event": value("ended", "derived"),
            },
            dropped=[
                {"column": "Paid on", "reason": "number_not_in_quote"},
                {"column": "Paid on", "reason": "quote_not_in_text"},
            ],
        ),
        row("t3.eml", "NOT_APPLICABLE"),  # a record that was missed
        row("t4.eml", values={"Service": value("Ad"), "Amount": value(1.0)}),  # a false record
        row("t5.eml", "NOT_APPLICABLE"),  # correctly rejected
        row("t6.eml", "PROVIDER_ERROR"),  # failed
        row("t7.eml", "NOTHING_FOUND"),  # truth says no record: nothing written, rejected
        row("t8.eml", values={"Service": value("Z"), "Amount": value(1.5)}),
    ]
    b_rows = [row(f"t{i}.eml", "NOT_APPLICABLE") for i in range(1, 9)]
    runs = [write_run(root / "run-a", a_rows), write_run(root / "run-b", b_rows)]

    truth_path = root / "truth.xlsx"
    assert make(truth_path, runs) == 0
    workbook = load_workbook(truth_path)
    rows = truth_rows(workbook["Truth"])
    truth: dict[str, tuple[str, list[Any]]] = {
        "t1.eml": ("yes", ["Cloudbox Plus", 10, "2026-11-01", "payment"]),
        "t2.eml": ("yes", ["Notes", "-", "2026-11-03", "ended"]),
        "t3.eml": ("yes", ["X", None, None, None]),
        "t4.eml": ("no", [None] * 4),
        "t5.eml": ("no", [None] * 4),
        "t6.eml": ("yes", ["Y", None, None, None]),
        "t7.eml": ("no", [None] * 4),
        "t8.eml": ("yes", ["Z", 1.5, None, None]),
    }
    for source, (record, cells) in truth.items():
        rows[source]["Is a record"].value = record
        for header, cell in zip(("Service", "Amount", "Paid on", "Event"), cells, strict=True):
            rows[source][header].value = cell
    workbook.save(truth_path)
    return truth_path, runs


def test_every_scoring_category_on_a_hand_made_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    truth_path, runs = hand_made(tmp_path)
    capsys.readouterr()
    numbers = tmp_path / "numbers.json"
    assert score_mail_runs.run([str(truth_path), *map(str, runs), "--out", str(numbers)]) == 0
    console = capsys.readouterr().out
    assert not any(s in console for s in SECRETS)

    result = json.loads(numbers.read_text(encoding="utf-8"))
    assert result["truth"] == {"mails": 8, "records": 5, "not_records": 3, "values": 11}
    a, b = result["runs"]
    assert (a["name"], b["name"]) == ("run-a", "run-b")

    assert a["records"] == {
        "found": 3, "missed": 1, "false": 1, "values_in_false": 2,
        "correct_rejections": 2, "failed": 1,
    }  # fmt: skip
    columns = a["values"]["by_column"]
    assert columns["Service"] == {"correct": 2, "close": 1, "wrong": 0, "missing": 2, "extra": 0}
    assert columns["Amount"] == {"correct": 2, "close": 0, "wrong": 0, "missing": 0, "extra": 1}
    assert columns["Paid on"] == {"correct": 0, "close": 0, "wrong": 1, "missing": 1, "extra": 0}
    assert columns["Event"] == {"correct": 1, "close": 0, "wrong": 0, "missing": 1, "extra": 0}
    assert a["values"]["total"] == {"correct": 5, "close": 1, "wrong": 1, "missing": 4, "extra": 1}
    assert a["values"]["by_mark"] == {
        "literal": {"correct": 4, "close": 1, "wrong": 1, "extra": 1},
        "derived": {"correct": 1, "close": 0, "wrong": 0, "extra": 0},
    }
    assert a["dropped_by_reason"] == {"quote_not_in_text": 2, "number_not_in_quote": 1}
    # Written: 3 + 3 + 2 (false record) + 2 = 10, 5 of them correct; the truth holds 11.
    assert a["shares"]["correct_of_written"] == {"count": 5, "of": 10, "share": 0.5}
    assert a["shares"]["given_correctly_of_truth"] == {"count": 5, "of": 11, "share": 0.4545}

    # A run that says "not applicable" to everything: all five records missed, 11 values missing.
    assert b["records"] == {
        "found": 0, "missed": 5, "false": 0, "values_in_false": 0,
        "correct_rejections": 3, "failed": 0,
    }  # fmt: skip
    assert b["values"]["total"] == {"correct": 0, "close": 0, "wrong": 0, "missing": 11, "extra": 0}
    assert b["shares"]["correct_of_written"]["share"] is None  # nothing written

    # One compact table, runs as columns.
    lines = console.splitlines()
    assert lines[0] == "Truth: 8 mails, 5 records, 3 not records, 11 values"
    header = next(line for line in lines if "run-a" in line)
    assert header.split() == ["run-a", "run-b"]
    assert any(
        line.startswith("False records (values in them)") and "1 (2)" in line for line in lines
    )
    assert any(line.startswith("  Service (c/cl/w/m/e)") and "2/1/0/2/0" in line for line in lines)
    assert any(
        line.startswith("Correct, of all written") and "5/10 = 50.0%" in line for line in lines
    )


def test_only_text_columns_have_close() -> None:
    assert relation("text", "Notes", "Notes App") == "close"
    assert relation("text", "notes  app", "Notes App") == "equal"
    assert relation("text", "Notes", "Other") == "differ"
    assert relation("one_of", "payment", "payment plan") == "differ"
    assert relation("number", 10, "10.0") == "equal"
    assert relation("number", 10, 100) == "differ"
    assert relation("date", "2026-11-03", "20261103") == "equal"
    assert relation("date", "2026-11-03", "2026-11-04") == "differ"


# --- Refusals of the scorer ------------------------------------------------------------


def test_the_scorer_refuses_while_a_question_mark_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = three_runs(tmp_path)
    truth_path = tmp_path / "truth.xlsx"
    make(truth_path, runs)
    capsys.readouterr()
    assert score_mail_runs.run([str(truth_path), *map(str, runs)]) == 1
    captured = capsys.readouterr()
    # Three open records, and two conflicting value cells in rows that are not "no".
    assert captured.out == ""
    assert captured.err == (
        "The truth table still holds ?: 3 in 'Is a record' and 2 in value cells. "
        "Settle them first; nothing scored.\n"
    )


def test_a_question_mark_in_a_row_that_is_no_is_ignored(tmp_path: Path) -> None:
    runs = three_runs(tmp_path)
    truth_path = tmp_path / "truth.xlsx"
    make(truth_path, runs)
    workbook = load_workbook(truth_path)
    rows = truth_rows(workbook["Truth"])
    for source, record in zip(rows, ("yes", "no", "no", "no", "no", "no"), strict=True):
        rows[source]["Is a record"].value = record
    rows["m1.eml"]["Event"].value = "payment"
    rows["m2.eml"]["Service"].value = "Notes"
    rows["m4.eml"]["Service"].value = "?"  # in a row that is "no" now
    workbook.save(truth_path)
    assert score_mail_runs.run([str(truth_path), *map(str, runs)]) == 0


def test_runs_that_do_not_fit_the_truth_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    truth_path, runs = hand_made(tmp_path)
    capsys.readouterr()
    other_task = write_run(
        tmp_path / "t", [row(f"t{i}.eml") for i in range(1, 9)], question="SENTINEL-OTHER"
    )
    assert score_mail_runs.run([str(truth_path), str(runs[0]), str(other_task)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("t differs from the truth table in: task.") and "SENTINEL" not in err

    other_columns = write_run(
        tmp_path / "c", [row(f"t{i}.eml") for i in range(1, 9)], targets=TARGETS[:3]
    )
    assert score_mail_runs.run([str(truth_path), str(other_columns)]) == 1
    assert "differs from the truth table in: columns." in capsys.readouterr().err

    both = write_run(
        tmp_path / "both", [row("t1.eml")], targets=TARGETS[:3], question="SENTINEL-OTHER"
    )
    assert score_mail_runs.run([str(truth_path), str(both)]) == 1
    assert "task, columns" in capsys.readouterr().err


def test_a_source_missing_on_either_side_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    truth_path, _ = hand_made(tmp_path)
    capsys.readouterr()
    short = write_run(tmp_path / "short", [row(f"t{i}.eml") for i in range(1, 7)])
    assert score_mail_runs.run([str(truth_path), str(short)]) == 1
    assert "2 of the truth table are missing in the run and 0 of the run" in capsys.readouterr().err
    extra = write_run(tmp_path / "extra", [row(f"t{i}.eml") for i in range(1, 10)])
    assert score_mail_runs.run([str(truth_path), str(extra)]) == 1
    assert "0 of the truth table are missing in the run and 1 of the run" in capsys.readouterr().err


def test_a_file_that_is_no_truth_table_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = three_runs(tmp_path)
    plain = tmp_path / "plain.xlsx"
    Workbook().save(plain)
    junk = tmp_path / "junk.xlsx"
    junk.write_text("not a workbook", encoding="utf-8")
    for path in (plain, junk, tmp_path / "missing.xlsx"):
        assert score_mail_runs.run([str(path), str(runs[0])]) == 1
        assert capsys.readouterr().err.startswith("The file is not a readable truth table")


# --- The whole way over the ten fixture mails ------------------------------------------


def test_two_runs_on_the_fixture_mails_are_merged_settled_and_scored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    openai_dir, local_dir = tmp_path / "openai-run", tmp_path / "local-run"
    assert probe(openai_args(openai_dir), client=RowApi(openai_choose, api_key=KEY)) == 0
    assert probe(ollama_args(local_dir), ollama_opener=FakeOpener(choose=ollama_server)) == 0
    capsys.readouterr()

    truth_path = tmp_path / "truth.xlsx"
    assert make(truth_path, [openai_dir, local_dir]) == 0
    capsys.readouterr()

    # Not settled yet: refused.
    assert score_mail_runs.run([str(truth_path), str(openai_dir), str(local_dir)]) == 1
    assert "still holds ?" in capsys.readouterr().err

    # The person settles every ?: a record if it says so, "-" for a value no run agrees on.
    workbook = load_workbook(truth_path)
    rows = truth_rows(workbook["Truth"])
    assert len(rows) == 10
    opened = 0
    for cells in rows.values():
        for header, cell in cells.items():
            if header != "Source" and cell.value == "?":
                opened += 1
                cell.value = "yes" if header == "Is a record" else "-"
    assert opened > 0
    workbook.save(truth_path)

    numbers = tmp_path / "numbers.json"
    code = score_mail_runs.run(
        [str(truth_path), str(openai_dir), str(local_dir), "--out", str(numbers)]
    )
    console = capsys.readouterr().out
    assert code == 0
    for secret in ("Cloudbox", "Rechnung", "example.com", "Streamly", "HostHill", "Gesamt"):
        assert secret not in console
    result = json.loads(numbers.read_text(encoding="utf-8"))
    truth = result["truth"]
    assert truth["mails"] == 10 and truth["records"] + truth["not_records"] == 10
    for scored in result["runs"]:
        records = scored["records"]
        found_or_missed = records["found"] + records["missed"]
        # Every truth record is found, missed or failed on; every other mail is rejected,
        # wrongly taken for a record, or failed on.
        assert found_or_missed + records["failed"] >= truth["records"]
        total = scored["values"]["total"]
        assert (
            total["correct"] + total["close"] + total["wrong"] + total["missing"] == truth["values"]
        )
    # The two runs agree where both found a value, so the settled truth scores both well.
    assert result["runs"][0]["values"]["total"]["wrong"] == 0
