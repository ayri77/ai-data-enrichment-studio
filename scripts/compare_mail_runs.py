"""Compare two runs of the mail extraction probe: numbers only, never a mail.

Reads ``rows.json`` and ``summary.json`` of two output folders of
``scripts/experiment_mail_extraction.py`` (for instance an OpenAI run and a local-model run
on the same saved ``.eml`` files) and prints how they agree. Run it from the repository root:

    uv run python -m scripts.compare_mail_runs BASE_DIR OTHER_DIR [--out FILE] [--force]

It prints counts, codes, column headers of the task, model names and seconds. It never prints
a subject, a sender, a file name, a quote or a value. It refuses two runs that had a
different task or different target columns (it says which, not what) unless --force is given.
See docs/32-mail-extraction-probe.md.
"""

import argparse
import json
import math
import statistics
import sys
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from scripts.experiment_mail_extraction import REPO_ROOT, ignored_by_git, inside

NUMBER_TOLERANCE = 0.005
OUTCOME_GROUPS = ("found", "not_applicable", "nothing_found", "failed")
_GROUP_BY_OUTCOME = {
    "FOUND": "found",
    "NOT_APPLICABLE": "not_applicable",
    "NOTHING_FOUND": "nothing_found",
}


class CompareError(Exception):
    """A fixed sentence for the person; it holds no content of a run."""


# --- Reading a run -------------------------------------------------------------------


def load_run(directory: Path, label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        rows = json.loads((directory / "rows.json").read_text(encoding="utf-8"))
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CompareError(f"{label} has no readable rows.json and summary.json.") from None
    task = summary.get("task") if isinstance(summary, dict) else None
    ok = (
        isinstance(rows, list)
        and all(isinstance(row, dict) for row in rows)
        and isinstance(task, dict)
        and isinstance(task.get("targets"), list)
    )
    if not ok:
        raise CompareError(f"{label} does not hold the files of a probe run.")
    return rows, summary


def group_of(outcome: object) -> str:
    return _GROUP_BY_OUTCOME.get(str(outcome), "failed")


def differences(base: dict[str, Any], other: dict[str, Any]) -> list[str]:
    """Which of "task" and "columns" differ between two summaries."""
    found = []
    if base["task"].get("question") != other["task"].get("question"):
        found.append("task")
    if base["task"]["targets"] != other["task"]["targets"]:
        found.append("columns")
    return found


# --- Matching ------------------------------------------------------------------------


def match_rows(
    base: list[dict[str, Any]], other: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int, int]:
    """Pairs of rows of the same mail: by ``source``, else by ``message_id``.

    A ``message_id`` pairs two rows only when it is the only one with that id on each side
    among the rows that the file name did not pair. Returns the pairs and the number of
    rows left on each side.
    """
    other_by_source = {row.get("source"): row for row in other if row.get("source")}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    rest_base: list[dict[str, Any]] = []
    used: set[int] = set()
    for row in base:
        partner = other_by_source.get(row.get("source")) if row.get("source") else None
        if partner is not None and id(partner) not in used:
            used.add(id(partner))
            pairs.append((row, partner))
        else:
            rest_base.append(row)
    rest_other = [row for row in other if id(row) not in used]

    def by_id(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("message_id"):
                grouped.setdefault(str(row["message_id"]), []).append(row)
        return grouped

    base_ids, other_ids = by_id(rest_base), by_id(rest_other)
    for message_id, rows in base_ids.items():
        partners = other_ids.get(message_id, [])
        if len(rows) == 1 and len(partners) == 1:
            pairs.append((rows[0], partners[0]))
    return pairs, len(base) - len(pairs), len(other) - len(pairs)


# --- Values --------------------------------------------------------------------------


def _text_key(value: object) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value)).casefold().split())


def same_value(kind: str, left: object, right: object) -> bool:
    """Equal after normalising: numbers numerically, dates as dates, text case-folded with
    whitespace collapsed."""
    if kind == "number":
        try:
            a, b = float(left), float(right)
        except (TypeError, ValueError):
            return _text_key(left) == _text_key(right)
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= NUMBER_TOLERANCE
    if kind == "date":
        try:
            return date.fromisoformat(str(left).strip()) == date.fromisoformat(str(right).strip())
        except ValueError:
            return _text_key(left) == _text_key(right)
    return _text_key(left) == _text_key(right)


def _tally() -> dict[str, int]:
    return {"n": 0, "equal": 0, "differ": 0}


def compare_columns(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]], targets: Sequence[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for target in targets:
        header, kind = target["header"], target["kind"]
        entry: dict[str, Any] = {
            "kind": kind,
            "both": _tally(),
            "both_literal_in_base": _tally(),
            "only_in_base": 0,
            "only_in_other": 0,
        }
        for base_row, other_row in pairs:
            left = (base_row.get("values") or {}).get(header)
            right = (other_row.get("values") or {}).get(header)
            if left is not None and right is not None:
                equal = same_value(kind, left.get("value"), right.get("value"))
                tallies = [entry["both"]]
                if left.get("mark") == "literal":
                    tallies.append(entry["both_literal_in_base"])
                for tally in tallies:
                    tally["n"] += 1
                    tally["equal" if equal else "differ"] += 1
            elif left is not None:
                entry["only_in_base"] += 1
            elif right is not None:
                entry["only_in_other"] += 1
        out[header] = entry
    return out


# --- One side ------------------------------------------------------------------------


def side_numbers(rows: Sequence[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    seconds = [float(r.get("seconds") or 0) for r in rows]
    return {
        "configuration_id": summary.get("configuration_id"),
        "executor": summary.get("executor", "openai"),
        "model": summary.get("model"),
        "mails": len(rows),
        "values_kept_by_mark": dict(
            Counter(v.get("mark") for r in rows for v in (r.get("values") or {}).values())
        ),
        "values_dropped_by_reason": dict(
            Counter(d.get("reason") for r in rows for d in (r.get("dropped") or []))
        ),
        "prompt_did_not_fit": sum(1 for r in rows if r.get("prompt_did_not_fit")),
        "seconds_median": round(statistics.median(seconds), 2) if seconds else 0.0,
        "seconds_total": round(sum(seconds), 2),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }


def outcome_table(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, dict[str, int]]:
    table = {a: dict.fromkeys(OUTCOME_GROUPS, 0) for a in OUTCOME_GROUPS}
    for base_row, other_row in pairs:
        table[group_of(base_row.get("outcome"))][group_of(other_row.get("outcome"))] += 1
    return table


def compare(
    base_rows: list[dict[str, Any]],
    base_summary: dict[str, Any],
    other_rows: list[dict[str, Any]],
    other_summary: dict[str, Any],
) -> dict[str, Any]:
    pairs, only_base, only_other = match_rows(base_rows, other_rows)
    targets = list(base_summary["task"]["targets"])
    known = {t["header"] for t in targets}
    # Under --force the other run may have columns that BASE has not.
    targets += [t for t in other_summary["task"]["targets"] if t["header"] not in known]
    return {
        "different": differences(base_summary, other_summary),
        "mails": {"matched": len(pairs), "only_in_base": only_base, "only_in_other": only_other},
        "outcomes_base_by_other": outcome_table(pairs),
        "columns": compare_columns(pairs, targets),
        "base": side_numbers(base_rows, base_summary),
        "other": side_numbers(other_rows, other_summary),
    }


# --- Printing ------------------------------------------------------------------------


def _counts(counts: dict[Any, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "none"


def render(result: dict[str, Any]) -> str:
    lines: list[str] = []
    mails = result["mails"]
    lines.append(
        f"Mails matched: {mails['matched']}; only in BASE: {mails['only_in_base']}; "
        f"only in OTHER: {mails['only_in_other']}"
    )
    lines += ["", "Outcomes (rows: BASE, columns: OTHER)"]
    width = max(len(name) for name in OUTCOME_GROUPS) + 2
    lines.append(" " * width + "".join(f"{name:>16}" for name in OUTCOME_GROUPS))
    for name in OUTCOME_GROUPS:
        row = result["outcomes_base_by_other"][name]
        lines.append(f"{name:<{width}}" + "".join(f"{row[other]:>16}" for other in OUTCOME_GROUPS))
    lines += ["", "Target columns (mails matched)"]
    for header, col in result["columns"].items():
        both, lit = col["both"], col["both_literal_in_base"]
        lines.append(
            f"- {header} ({col['kind']}): filled in both {both['n']}, only in BASE "
            f"{col['only_in_base']}, only in OTHER {col['only_in_other']}; of both: "
            f"{both['equal']} equal, {both['differ']} differ; literal in BASE: {lit['n']} "
            f"({lit['equal']} equal, {lit['differ']} differ)"
        )
    for label, key in (("BASE", "base"), ("OTHER", "other")):
        side = result[key]
        lines += [
            "",
            f"{label}: {side['executor']}, {side['configuration_id']}, {side['mails']} mails",
            f"  values kept, by mark: {_counts(side['values_kept_by_mark'])}",
            f"  values dropped, by reason: {_counts(side['values_dropped_by_reason'])}",
            f"  prompts that did not fit: {side['prompt_did_not_fit']}",
            f"  seconds per mail (median): {side['seconds_median']}; total: "
            f"{side['seconds_total']}",
            f"  tokens in / out: {side['input_tokens']} / {side['output_tokens']}",
        ]
    return "\n".join(lines)


# --- Command line --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("base", type=Path, metavar="BASE_DIR", help="Output folder of the base run")
    parser.add_argument("other", type=Path, metavar="OTHER_DIR", help="Output folder of the other")
    parser.add_argument("--out", type=Path, metavar="FILE", help="Also write the numbers as JSON")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Compare although the task or the target columns differ",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out is not None and inside(args.out, REPO_ROOT) and not ignored_by_git(args.out):
        parser.error(
            f"--out {args.out}: inside the repository and not ignored by git; "
            "use a folder under artifacts/ or outside the repository"
        )
    try:
        base_rows, base_summary = load_run(args.base, "BASE_DIR")
        other_rows, other_summary = load_run(args.other, "OTHER_DIR")
    except CompareError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = compare(base_rows, base_summary, other_rows, other_summary)
    if result["different"] and not args.force:
        print(
            f"The two runs differ in: {', '.join(result['different'])}. "
            "Nothing was compared; use --force to compare anyway.",
            file=sys.stderr,
        )
        return 2
    if result["different"]:
        print(f"Warning: the two runs differ in: {', '.join(result['different'])}.")
    print(render(result))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nNumbers written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
