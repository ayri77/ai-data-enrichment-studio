"""What the truth table maker and the scorer share: reading runs and comparing values.

Not a script. ``scripts/make_mail_truth.py`` and ``scripts/score_mail_runs.py`` import it;
the rules for what counts as the same value are those of ``scripts/compare_mail_runs.py``.
Nothing here prints; every error is a fixed sentence that names no content.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scripts.compare_mail_runs import CompareError, differences, group_of, load_run, same_value
from scripts.compare_mail_runs import text_key as text_key
from scripts.experiment_mail_extraction import REPO_ROOT, ignored_by_git, inside

# The sheets of a truth workbook and the columns that come before the target columns.
TRUTH_SHEET = "Truth"
CANDIDATES_SHEET = "Candidates"
INFO_SHEET = "Info"
SOURCE, RECEIVED, FROM, SUBJECT, IS_RECORD = "Source", "Received", "From", "Subject", "Is a record"
FIXED_HEADERS = (SOURCE, RECEIVED, FROM, SUBJECT, IS_RECORD)
YES, NO, UNSURE = "yes", "no", "?"
# Typed by the person in a value cell: the mail gives no such value. An empty cell means the same.
NO_VALUE = "-"
# The labels the scorer finds on the Info sheet.
INFO_TASK, INFO_COLUMN, INFO_KIND, INFO_OPTIONS = "Task", "Column", "Kind", "Options"


class TruthError(CompareError):
    """A fixed sentence for the person; it holds no content of a run or of the truth."""


@dataclass
class Run:
    """One output folder of the probe, read."""

    name: str
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def targets(self) -> list[dict[str, Any]]:
        return normal_targets(self.summary["task"]["targets"])

    @property
    def question(self) -> object:
        return self.summary["task"].get("question")


def normal_targets(targets: Sequence[Any]) -> list[dict[str, Any]]:
    """The target columns as header, kind and options, whatever else a summary holds."""
    return [
        {
            "header": str(t.get("header")),
            "kind": str(t.get("kind")),
            "options": [str(o) for o in t.get("options") or []],
        }
        for t in targets
        if isinstance(t, dict)
    ]


def load_runs(directories: Sequence[Path]) -> list[Run]:
    """Read the runs; a run is named by its folder name, which must be unique."""
    runs: list[Run] = []
    for directory in directories:
        name = directory.resolve().name
        rows, summary = load_run(directory, name)
        if any(r.name == name for r in runs):
            raise TruthError("Two runs have the same folder name; rename one of the folders.")
        by_source: dict[str, dict[str, Any]] = {}
        for row in rows:
            source = row.get("source")
            if not isinstance(source, str) or not source or source in by_source:
                raise TruthError(f"{name} has a mail without a file name or the same one twice.")
            by_source[source] = row
        runs.append(Run(name, rows, summary, by_source))
    return runs


def check_same_task(runs: Sequence[Run]) -> None:
    """Refuse runs of another task or other columns; says which, never what."""
    first = runs[0]
    for run in runs[1:]:
        found = differences(first.summary, run.summary)
        if found:
            raise TruthError(
                f"{run.name} differs from {first.name} in: {', '.join(found)}. Nothing was done."
            )


def check_out_path(path: Path) -> str | None:
    """A reason to refuse PATH for a file that holds mail content, else None."""
    if inside(path, REPO_ROOT) and not ignored_by_git(path):
        return (
            f"{path}: inside the repository and not ignored by git; "
            "use a folder under artifacts/ or outside the repository"
        )
    return None


# --- Values --------------------------------------------------------------------------


def outcome_group(row: dict[str, Any]) -> str:
    """found, not_applicable, nothing_found or failed."""
    return group_of(row.get("outcome"))


def given(row: dict[str, Any], header: str) -> dict[str, Any] | None:
    """The entry (value, mark, quote) a run wrote for a column of a mail, or None."""
    entry = (row.get("values") or {}).get(header)
    if isinstance(entry, dict) and entry.get("value") is not None:
        return entry
    return None


def relation(kind: str, left: object, right: object) -> str:
    """equal, close or differ. Close is for text columns only: one wording holds the other."""
    if same_value(kind, left, right):
        return "equal"
    if kind == "text":
        a, b = text_key(left), text_key(right)
        if a and b and (a in b or b in a):
            return "close"
    return "differ"


def canonical(kind: str, value: object) -> object:
    """A value as the truth sheet holds it: a number, an ISO date as text, or text."""
    if kind == "number" and not isinstance(value, bool):
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return str(value).strip()
        return int(number) if number.is_integer() else number
    if kind == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            return str(value).strip()
    return str(value).strip()
