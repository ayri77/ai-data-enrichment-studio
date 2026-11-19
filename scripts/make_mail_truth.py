"""Merge runs of the mail extraction probe into one workbook the owner corrects.

    uv run python -m scripts.make_mail_truth RUN_DIR [RUN_DIR ...] --out FILE.xlsx

The runs must have the same task and the same target columns. The workbook has one row per
mail with what the runs agree on already filled in, what they partly agree on lightly
coloured, and what they disagree on marked ``?`` with the candidates in a cell comment. The
person settles every ``?``; ``scripts/score_mail_runs.py`` then scores any run against the
result. See docs/32-mail-extraction-probe.md, "Truth table and scoring".

It prints counts and the path only. The workbook holds subjects, senders, values and quotes
of the mails, so it is written only into a folder git ignores (for instance ``artifacts/``) or
outside the repository.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet
from scripts.compare_mail_runs import CompareError, same_value
from scripts.experiment_mail_extraction import _header_row, _text
from scripts.mail_truth_common import (
    CANDIDATES_SHEET,
    FIXED_HEADERS,
    INFO_COLUMN,
    INFO_KIND,
    INFO_OPTIONS,
    INFO_SHEET,
    INFO_TASK,
    NO,
    NO_VALUE,
    SOURCE,
    TRUTH_SHEET,
    UNSURE,
    YES,
    Run,
    TruthError,
    canonical,
    check_out_path,
    check_same_task,
    given,
    load_runs,
    outcome_group,
)

AGREED, PARTLY, CONFLICT, EMPTY = "agreed", "partly", "conflict", "empty"
PARTLY_FILL = PatternFill("solid", start_color="FFF2CC")
CONFLICT_FILL = PatternFill("solid", start_color="F4B183")
# The columns of the Truth sheet that identify the mail: locked, so that Source stays the key.
LOCKED_COLUMNS = 4


@dataclass
class Cell:
    status: str
    value: object = None
    candidates: list[tuple[str, object]] = field(default_factory=list)


@dataclass
class MailRow:
    source: str
    received: str
    sender: str
    subject: str
    is_record: str
    cells: dict[str, Cell]


# --- Merging -------------------------------------------------------------------------


def decide_record(groups: Sequence[str]) -> str:
    """yes when every run that voted found values, no when every one said not applicable.

    A run that failed on the mail has no vote. A run that found nothing, or no run with a
    vote, leaves the question open.
    """
    votes = [g for g in groups if g != "failed"]
    if votes and all(g == "found" for g in votes):
        return YES
    if votes and all(g == "not_applicable" for g in votes):
        return NO
    return UNSURE


def decide_cell(kind: str, answers: Sequence[tuple[str, Any]], voters: int) -> Cell:
    """The state of one value cell from what the runs that voted wrote there.

    Agreed: every run that voted gave an equal value. Partly: some gave equal values and the
    others gave nothing. Conflict: two values differ after normalising (for text, one holding
    the other is a difference too). Empty: nobody gave one.
    """
    if not answers:
        return Cell(EMPTY)
    first = answers[0][1]
    if any(not same_value(kind, first, value) for _, value in answers[1:]):
        return Cell(CONFLICT, UNSURE, list(answers))
    status = AGREED if len(answers) == voters else PARTLY
    return Cell(status, canonical(kind, first), list(answers))


def sender_text(row: dict[str, Any]) -> str:
    name = row.get("sender_name")
    if name:
        return str(name)
    address = str(row.get("sender_address") or "")
    return address.rpartition("@")[2] or address


def merge(runs: Sequence[Run]) -> list[MailRow]:
    """One row per mail, in the order of the first run."""
    first = runs[0]
    if any(set(run.by_source) != set(first.by_source) for run in runs[1:]):
        raise TruthError("The runs do not hold the same mails.")
    if not first.rows:
        raise TruthError("The runs hold no mails.")
    targets = first.targets
    clash = {t["header"] for t in targets} & set(FIXED_HEADERS)
    if clash:
        raise TruthError("A target column is named like a fixed column of the truth sheet.")
    merged: list[MailRow] = []
    for base in first.rows:
        source = base["source"]
        rows = [(run.name, run.by_source[source]) for run in runs]
        voters = sum(1 for _, row in rows if outcome_group(row) != "failed")
        cells: dict[str, Cell] = {}
        for target in targets:
            header = target["header"]
            answers = [
                (name, entry["value"])
                for name, row in rows
                if (entry := given(row, header)) is not None
            ]
            cells[header] = decide_cell(target["kind"], answers, voters)
        received = base.get("received")
        merged.append(
            MailRow(
                source=source,
                received=str(received)[:10] if received else "",
                sender=sender_text(base),
                subject=str(base.get("subject") or ""),
                is_record=decide_record([outcome_group(row) for _, row in rows]),
                cells=cells,
            )
        )
    return merged


def counts_of(merged: Sequence[MailRow]) -> tuple[Counter[str], Counter[str]]:
    """Cells by status, and the rows by the value of ``Is a record``."""
    cells = Counter(cell.status for row in merged for cell in row.cells.values())
    records = Counter(row.is_record for row in merged)
    return cells, records


# --- The workbook --------------------------------------------------------------------

INSTRUCTIONS = (
    "A ? in 'Is a record' or in a value cell is for you to settle: the runs disagree (the "
    "candidates are in the cell's comment and on the Candidates sheet). The scorer refuses "
    "until no ? is left.",
    "Type - (or leave the cell empty) in a value cell when the mail gives no such value.",
    "A row whose 'Is a record' is no has its value cells ignored.",
)


def _write_info(
    sheet: Worksheet,
    runs: Sequence[Run],
    cell_counts: Counter[str],
    record_counts: Counter[str],
) -> dict[str, str]:
    """The Info sheet; returns, for each choice column, the range of its choices on it."""
    first = runs[0]
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 36
    sheet.column_dimensions["C"].width = 60
    bold = Font(bold=True)
    row = 1

    def line(*values: object, header: bool = False) -> None:
        nonlocal row
        for index, value in enumerate(values, start=1):
            cell = _text(sheet, row, index, str(value))
            if header:
                cell.font = bold
        row += 1

    line("Truth table of a mail extraction probe", header=True)
    row += 1
    line(INFO_TASK, first.question)
    row += 1
    line(INFO_COLUMN, INFO_KIND, INFO_OPTIONS, header=True)
    for target in first.targets:
        line(target["header"], target["kind"], "|".join(target["options"]))
    row += 1
    line("Run merged (folder name)", "Executor", "Configuration", header=True)
    for run in runs:
        line(run.name, run.summary.get("executor", "openai"), run.summary.get("configuration_id"))
    row += 1
    line("Value cells", "Count", header=True)
    line("agreed (all runs equal; not coloured)", cell_counts[AGREED])
    line("partly (some runs equal, others nothing; light yellow)", cell_counts[PARTLY])
    line("conflict (? to settle; orange)", cell_counts[CONFLICT])
    line("empty (no run gave a value)", cell_counts[EMPTY])
    line("? in 'Is a record'", record_counts[UNSURE])
    row += 1
    line("What to do", header=True)
    for text in INSTRUCTIONS:
        line(text)
    row += 1
    line("The Candidates sheet lists only the values the runs wrote, one row each.")

    choices: dict[str, str] = {}
    for target in first.targets:
        options = list(target["options"]) or (["Yes", "No"] if target["kind"] == "yes_no" else [])
        if not options:
            continue
        row += 1
        line(f"Choices of: {target['header']}", header=True)
        start = row
        for option in [*options, NO_VALUE]:
            line(option)
        choices[target["header"]] = f"{INFO_SHEET}!$A${start}:$A${row - 1}"
    return choices


def _write_truth(
    sheet: Worksheet,
    merged: Sequence[MailRow],
    runs: Sequence[Run],
    choices: dict[str, str],
) -> None:
    targets = runs[0].targets
    headers = [*FIXED_HEADERS, *[t["header"] for t in targets]]
    _header_row(sheet, headers, [34, 12, 22, 40, 12, *[20] * len(targets)])
    note = Comment(
        "The key of the mail. It is locked: do not change it, the scorer finds the mail by it.",
        "mail truth",
    )
    note.width, note.height = 300, 80
    sheet["A1"].comment = note

    for number, mail in enumerate(merged, start=2):
        for column, value in enumerate((mail.source, mail.received, mail.sender, mail.subject), 1):
            _text(sheet, number, column, value)
        record = _text(sheet, number, 5, mail.is_record)
        if mail.is_record == UNSURE:
            record.fill = CONFLICT_FILL
        for index, target in enumerate(targets):
            cell = mail.cells[target["header"]]
            column = len(FIXED_HEADERS) + 1 + index
            if cell.status == EMPTY:
                out = sheet.cell(row=number, column=column)
            elif isinstance(cell.value, int | float) and not isinstance(cell.value, bool):
                out = sheet.cell(row=number, column=column, value=cell.value)
            else:
                out = _text(sheet, number, column, str(cell.value))
                if target["kind"] == "date":
                    out.number_format = "@"  # typed dates stay text
            if cell.status == PARTLY:
                out.fill = PARTLY_FILL
            elif cell.status == CONFLICT:
                out.fill = CONFLICT_FILL
                out.comment = _candidates_note(cell.candidates)
        for column in range(1, len(headers) + 1):
            sheet.cell(row=number, column=column).protection = Protection(
                locked=column <= LOCKED_COLUMNS
            )

    last = len(merged) + 1
    record_choice = DataValidation(
        type="list", formula1=f'"{YES},{NO},{UNSURE}"', allow_blank=False
    )
    record_choice.add(f"E2:E{last}")
    sheet.add_data_validation(record_choice)
    for index, target in enumerate(targets):
        if target["header"] in choices:
            letter = get_column_letter(len(FIXED_HEADERS) + 1 + index)
            choice = DataValidation(
                type="list", formula1=f"={choices[target['header']]}", allow_blank=True
            )
            choice.add(f"{letter}2:{letter}{last}")
            sheet.add_data_validation(choice)
    # Locks Source and its neighbours only; the person may still size, sort and filter.
    sheet.protection.sheet = True
    for allowed in ("formatCells", "formatColumns", "formatRows", "sort", "autoFilter"):
        setattr(sheet.protection, allowed, False)
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last}"


def _candidates_note(candidates: Sequence[tuple[str, object]]) -> Comment:
    text = "\n".join(f"{name}: {value}" for name, value in candidates)
    note = Comment(ILLEGAL_CHARACTERS_RE.sub("", text), "mail truth")
    note.width, note.height = 320, 40 + 20 * len(candidates)
    return note


def _write_candidates(sheet: Worksheet, runs: Sequence[Run]) -> None:
    _header_row(
        sheet, [SOURCE, "Column", "Run", "Value", "Mark", "Quote"], [34, 18, 24, 24, 10, 70]
    )
    targets = runs[0].targets
    for base in runs[0].rows:
        for target in targets:
            for run in runs:
                entry = given(run.by_source[base["source"]], target["header"])
                if entry is None:
                    continue
                number = sheet.max_row + 1
                _text(sheet, number, 1, base["source"])
                _text(sheet, number, 2, target["header"])
                _text(sheet, number, 3, run.name)
                value = canonical(target["kind"], entry["value"])
                if isinstance(value, int | float):
                    sheet.cell(row=number, column=4, value=value)
                else:
                    _text(sheet, number, 4, str(value))
                _text(sheet, number, 5, str(entry.get("mark") or ""))
                _text(sheet, number, 6, str(entry.get("quote") or ""))
    sheet.auto_filter.ref = f"A1:F{max(sheet.max_row, 1)}"


def write_truth_workbook(path: Path, runs: Sequence[Run], merged: Sequence[MailRow]) -> None:
    workbook = Workbook()
    truth = workbook.active
    assert truth is not None
    truth.title = TRUTH_SHEET
    candidates = workbook.create_sheet(CANDIDATES_SHEET)
    info = workbook.create_sheet(INFO_SHEET)
    cell_counts, record_counts = counts_of(merged)
    choices = _write_info(info, runs, cell_counts, record_counts)
    _write_truth(truth, merged, runs, choices)
    _write_candidates(candidates, runs)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


# --- Command line --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("runs", type=Path, nargs="+", metavar="RUN_DIR", help="Output folders")
    parser.add_argument("--out", type=Path, required=True, metavar="FILE.xlsx", help="The workbook")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if reason := check_out_path(args.out):
        parser.error(f"--out {reason}")
    try:
        runs = load_runs(args.runs)
        check_same_task(runs)
        merged = merge(runs)
    except CompareError as exc:  # TruthError is one
        print(str(exc), file=sys.stderr)
        return 1
    write_truth_workbook(args.out, runs, merged)
    cells, records = counts_of(merged)
    print(f"Mails: {len(merged)}; runs merged: {len(runs)}")
    print(
        f"Is a record: yes {records[YES]}, no {records[NO]}, ? {records[UNSURE]}; "
        f"value cells: agreed {cells[AGREED]}, partly {cells[PARTLY]}, "
        f"conflict {cells[CONFLICT]}, empty {cells[EMPTY]}"
    )
    print(f"Truth table written to {args.out.resolve()}")
    print("It holds subjects, senders and quotes from the mails; do not commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
