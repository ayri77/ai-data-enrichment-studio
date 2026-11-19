"""Score runs of the mail extraction probe against a truth table the owner has corrected.

    uv run python -m scripts.score_mail_runs TRUTH.xlsx RUN_DIR [RUN_DIR ...] [--out FILE.json]

TRUTH.xlsx is made by ``scripts/make_mail_truth.py`` and settled by hand. The scorer refuses
while a ``?`` is left in it, and a run whose task or columns differ from the truth's, or
that lacks a mail of the truth (or has one the truth lacks). It prints numbers, column
names and run folder names only, never a subject, a sender, a value or a quote. See
docs/32-mail-extraction-probe.md, "Truth table and scoring".
"""

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from scripts.compare_mail_runs import CompareError
from scripts.mail_truth_common import (
    FIXED_HEADERS,
    INFO_COLUMN,
    INFO_KIND,
    INFO_SHEET,
    INFO_TASK,
    IS_RECORD,
    NO,
    NO_VALUE,
    TRUTH_SHEET,
    UNSURE,
    YES,
    Run,
    TruthError,
    check_out_path,
    given,
    load_runs,
    normal_targets,
    outcome_group,
    relation,
)

VERDICTS = ("correct", "close", "wrong", "missing", "extra")


@dataclass
class Truth:
    question: str
    targets: list[dict[str, Any]]
    # source -> (is a record, {header: value or None})
    mails: dict[str, tuple[str, dict[str, Any]]]
    unsure_records: int
    unsure_values: int


# --- Reading the truth ---------------------------------------------------------------


def _cell_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _read_info(sheet: Any) -> tuple[str, list[dict[str, Any]]]:
    question: str | None = None
    targets: list[dict[str, Any]] = []
    in_columns = False
    for first, second, third in sheet.iter_rows(max_col=3, values_only=True):
        a = _cell_text(first)
        if in_columns:
            if not a:
                break
            options = [o for o in _cell_text(third).split("|") if o]
            targets.append({"header": a, "kind": _cell_text(second), "options": options})
        elif a == INFO_TASK and question is None:
            question = _cell_text(second)
        elif a == INFO_COLUMN and _cell_text(second) == INFO_KIND:
            in_columns = True
    if question is None or not targets:
        raise TruthError("The Info sheet of the truth table has no task or no columns.")
    return question, targets


def _value_of(kind: str, options: Sequence[str], raw: object) -> tuple[str, object]:
    """(state, value): state is ok, empty, unsure or bad."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "empty", None
    if isinstance(raw, str):
        text = raw.strip()
        if text == NO_VALUE:
            return "empty", None
        if text == UNSURE:
            return "unsure", None
    else:
        text = ""
    if kind == "number":
        if isinstance(raw, bool):
            return "bad", None
        try:
            number = float(raw) if not isinstance(raw, str) else float(text)
        except (TypeError, ValueError):
            return "bad", None
        return ("ok", number) if math.isfinite(number) else ("bad", None)
    if kind == "date":
        if isinstance(raw, datetime):
            return "ok", raw.date().isoformat()
        if isinstance(raw, date):
            return "ok", raw.isoformat()
        try:
            return "ok", date.fromisoformat(text).isoformat()
        except ValueError:
            return "bad", None
    if isinstance(raw, str):
        text = raw.strip()
    else:
        text = str(raw)
    allowed = options if kind == "one_of" else ("Yes", "No") if kind == "yes_no" else ()
    if allowed:
        matches = [o for o in allowed if o.casefold() == text.casefold()]
        return ("ok", matches[0]) if matches else ("bad", None)
    return "ok", text


def read_truth(path: Path) -> Truth:
    try:
        workbook = load_workbook(path, data_only=True)
        truth_sheet, info_sheet = workbook[TRUTH_SHEET], workbook[INFO_SHEET]
    except (OSError, KeyError, ValueError, BadZipFile, InvalidFileException):
        raise TruthError(
            "The file is not a readable truth table made by make_mail_truth."
        ) from None
    question, targets = _read_info(info_sheet)
    rows = truth_sheet.iter_rows(values_only=True)
    header = [_cell_text(v) for v in next(rows, ())]
    expected = [*FIXED_HEADERS, *[t["header"] for t in targets]]
    if header[: len(expected)] != expected:
        raise TruthError("The columns of the Truth sheet differ from the Info sheet: columns.")
    mails: dict[str, tuple[str, dict[str, Any]]] = {}
    unsure_records = unsure_values = bad_records = bad_values = 0
    for row in rows:
        cells = list(row) + [None] * (len(expected) - len(row))
        if all(_cell_text(v) == "" for v in cells[: len(expected)]):
            continue
        source = _cell_text(cells[0])
        if not source or source in mails:
            raise TruthError("A row of the Truth sheet has no Source or a Source twice.")
        record = _cell_text(cells[FIXED_HEADERS.index(IS_RECORD)]).casefold()
        if record in ("", UNSURE):
            unsure_records += 1
            record = UNSURE
        elif record not in (YES, NO):
            bad_records += 1
        values: dict[str, Any] = {}
        for index, target in enumerate(targets, start=len(FIXED_HEADERS)):
            state, value = _value_of(target["kind"], target["options"], cells[index])
            if state == "unsure" and record != NO:
                unsure_values += 1
            elif state == "bad" and record != NO:
                bad_values += 1
            values[target["header"]] = value
        mails[source] = (record, values)
    if bad_records or bad_values:
        raise TruthError(
            f"The truth table holds cells that are not valid: {bad_records} in 'Is a record' "
            f"(yes, no or ?) and {bad_values} value cells that do not fit their column."
        )
    return Truth(question, targets, mails, unsure_records, unsure_values)


def check_truth_against(truth: Truth, runs: Sequence[Run]) -> None:
    """Refuse a run of another task or columns, or with other mails than the truth has."""
    for run in runs:
        found = []
        if str(run.question).strip() != truth.question:
            found.append("task")
        if run.targets != normal_targets(truth.targets):
            found.append("columns")
        if found:
            raise TruthError(
                f"{run.name} differs from the truth table in: {', '.join(found)}. Nothing scored."
            )
    for run in runs:
        only_run = len(set(run.by_source) - set(truth.mails))
        only_truth = len(set(truth.mails) - set(run.by_source))
        if only_run or only_truth:
            raise TruthError(
                f"{run.name} and the truth table do not hold the same mails by Source: "
                f"{only_truth} of the truth table are missing in the run and {only_run} of the "
                "run are missing in the truth table. Nothing scored."
            )


# --- Scoring -------------------------------------------------------------------------


def _verdict(kind: str, truth_value: object, run_entry: dict[str, Any] | None) -> str | None:
    if truth_value is None and run_entry is None:
        return None
    if run_entry is None:
        return "missing"
    if truth_value is None:
        return "extra"
    return {"equal": "correct", "close": "close", "differ": "wrong"}[
        relation(kind, truth_value, run_entry["value"])
    ]


def _share(part: int, whole: int) -> dict[str, Any]:
    return {"count": part, "of": whole, "share": round(part / whole, 4) if whole else None}


def score_run(truth: Truth, run: Run) -> dict[str, Any]:
    """The numbers of one run. A mail the run failed on gives none of its values: those the
    truth holds are ``missing``, like those of a mail the run did not find."""
    kinds = {t["header"]: t["kind"] for t in truth.targets}
    records = Counter[str]()
    false_values = written = 0
    by_column = {h: dict.fromkeys(VERDICTS, 0) for h in kinds}
    by_mark: dict[str, dict[str, int]] = {}
    truth_values = 0
    for source, (is_record, expected) in truth.mails.items():
        row = run.by_source[source]
        group = outcome_group(row)
        written += sum(1 for h in kinds if given(row, h) is not None)
        if group == "failed":
            records["failed"] += 1
        if is_record == NO:
            if group == "found":
                records["false"] += 1
                false_values += sum(1 for h in kinds if given(row, h) is not None)
            elif group != "failed":
                records["correct_rejections"] += 1
            continue
        if group == "found":
            records["found"] += 1
        elif group != "failed":
            records["missed"] += 1
        for header, kind in kinds.items():
            entry = given(row, header)
            verdict = _verdict(kind, expected[header], entry)
            if expected[header] is not None:
                truth_values += 1
            if verdict is None:
                continue
            by_column[header][verdict] += 1
            if entry is not None:
                marks = by_mark.setdefault(
                    str(entry.get("mark")), dict.fromkeys(("correct", "close", "wrong", "extra"), 0)
                )
                marks[verdict] += 1
    total = {v: sum(c[v] for c in by_column.values()) for v in VERDICTS}
    dropped = Counter(str(d.get("reason")) for r in run.rows for d in (r.get("dropped") or []))
    return {
        "name": run.name,
        "executor": run.summary.get("executor", "openai"),
        "configuration_id": run.summary.get("configuration_id"),
        "records": {
            "found": records["found"],
            "missed": records["missed"],
            "false": records["false"],
            "values_in_false": false_values,
            "correct_rejections": records["correct_rejections"],
            "failed": records["failed"],
        },
        "values": {"total": total, "by_column": by_column, "by_mark": by_mark},
        "dropped_by_reason": dict(dropped),
        "shares": {
            "correct_of_written": _share(total["correct"], written),
            "given_correctly_of_truth": _share(total["correct"], truth_values),
        },
        "written": written,
        "truth_values": truth_values,
    }


def truth_numbers(truth: Truth) -> dict[str, int]:
    yes = [values for record, values in truth.mails.values() if record == YES]
    return {
        "mails": len(truth.mails),
        "records": len(yes),
        "not_records": sum(1 for record, _ in truth.mails.values() if record == NO),
        "values": sum(v is not None for values in yes for v in values.values()),
    }


# --- Printing ------------------------------------------------------------------------


def _pct(share: dict[str, Any]) -> str:
    if share["share"] is None:
        return f"{share['count']}/{share['of']}"
    return f"{share['count']}/{share['of']} = {share['share'] * 100:.1f}%"


def _slash(counts: dict[str, int], keys: Sequence[str]) -> str:
    return "/".join(str(counts.get(k, 0)) for k in keys)


def render(
    numbers: dict[str, int], scores: Sequence[dict[str, Any]], targets: Sequence[str]
) -> str:
    rows: list[tuple[str, list[str]]] = []

    def add(label: str, getter: Any) -> None:
        rows.append((label, [str(getter(s)) for s in scores]))

    rec = "records"
    add("Records found", lambda s: s[rec]["found"])
    add("Records missed", lambda s: s[rec]["missed"])
    add(
        "False records (values in them)",
        lambda s: f"{s[rec]['false']} ({s[rec]['values_in_false']})",
    )
    add("Correct rejections", lambda s: s[rec]["correct_rejections"])
    add("Failed mails", lambda s: s[rec]["failed"])
    for verdict in VERDICTS:
        add(f"Values {verdict}", lambda s, v=verdict: s["values"]["total"][v])
    for header in targets:
        add(
            f"  {header} (c/cl/w/m/e)",
            lambda s, h=header: _slash(s["values"]["by_column"][h], VERDICTS),
        )
    marks = sorted({m for s in scores for m in s["values"]["by_mark"]})
    for mark in marks:
        add(
            f"  mark {mark} (c/cl/w/e)",
            lambda s, m=mark: _slash(
                s["values"]["by_mark"].get(m, {}), ("correct", "close", "wrong", "extra")
            ),
        )
    for reason in sorted({r for s in scores for r in s["dropped_by_reason"]}):
        add(f"Dropped: {reason}", lambda s, r=reason: s["dropped_by_reason"].get(r, 0))
    add("Correct, of all written", lambda s: _pct(s["shares"]["correct_of_written"]))
    add("Given correctly, of truth", lambda s: _pct(s["shares"]["given_correctly_of_truth"]))

    label_width = max(len(label) for label, _ in rows) + 2
    widths = [
        max(len(s["name"]), *(len(cells[i]) for _, cells in rows)) + 2 for i, s in enumerate(scores)
    ]
    lines = [
        f"Truth: {numbers['mails']} mails, {numbers['records']} records, "
        f"{numbers['not_records']} not records, {numbers['values']} values",
        "Values over mails the truth calls a record; c = correct, cl = close, w = wrong, "
        "m = missing, e = extra.",
        "",
        " " * label_width
        + "".join(f"{s['name']:>{w}}" for s, w in zip(scores, widths, strict=True)),
    ]
    for label, cells in rows:
        lines.append(
            f"{label:<{label_width}}"
            + "".join(f"{c:>{w}}" for c, w in zip(cells, widths, strict=True))
        )
    return "\n".join(lines)


# --- Command line --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("truth", type=Path, metavar="TRUTH.xlsx", help="The corrected truth table")
    parser.add_argument("runs", type=Path, nargs="+", metavar="RUN_DIR", help="Output folders")
    parser.add_argument("--out", type=Path, metavar="FILE.json", help="Also write the numbers")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out is not None and (reason := check_out_path(args.out)):
        parser.error(f"--out {reason}")
    try:
        truth = read_truth(args.truth)
        runs = load_runs(args.runs)
        check_truth_against(truth, runs)
        if truth.unsure_records or truth.unsure_values:
            raise TruthError(
                f"The truth table still holds ?: {truth.unsure_records} in 'Is a record' and "
                f"{truth.unsure_values} in value cells. Settle them first; nothing scored."
            )
    except CompareError as exc:  # TruthError is one
        print(str(exc), file=sys.stderr)
        return 1
    numbers = truth_numbers(truth)
    scores = [score_run(truth, r) for r in runs]
    print(render(numbers, scores, [t["header"] for t in truth.targets]))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"truth": numbers, "runs": scores}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nNumbers written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
