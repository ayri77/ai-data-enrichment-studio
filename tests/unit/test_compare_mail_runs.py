"""The comparison of two probe runs, on two small invented runs."""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts import compare_mail_runs
from scripts.compare_mail_runs import compare, run

TARGETS = [
    {"header": "Service", "kind": "text", "options": []},
    {"header": "Amount", "kind": "number", "options": []},
    {"header": "Paid on", "kind": "date", "options": []},
    {"header": "Event", "kind": "one_of", "options": ["payment", "ended"]},
]
QUESTION = "Invented payments"


def value(v: Any, mark: str = "literal") -> dict[str, Any]:
    return {"value": v, "mark": mark, "quote": "SECRET-QUOTE"}


def row(
    source: str,
    outcome: str = "FOUND",
    values: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
    seconds: float = 1.0,
    dropped: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "source": source,
        "message_id": message_id or f"id-{source}@example.com",
        "subject": "SECRET-SUBJECT",
        "sender_address": "someone@example.com",
        "outcome": outcome,
        "values": values or {},
        "dropped": dropped or [],
        "input_tokens": 100,
        "output_tokens": 10,
        "seconds": seconds,
        **extra,
    }


def write_run(
    directory: Path,
    rows: list[dict[str, Any]],
    *,
    targets: list[dict[str, Any]] = TARGETS,
    question: str = QUESTION,
    **summary: Any,
) -> Path:
    directory.mkdir(parents=True)
    (directory / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    body = {
        "configuration_id": "openai-text-extraction-p1-m-low",
        "model": "m",
        "task": {"question": question, "targets": targets},
        **summary,
    }
    (directory / "summary.json").write_text(json.dumps(body), encoding="utf-8")
    return directory


def base_rows() -> list[dict[str, Any]]:
    return [
        row(
            "a.eml",
            values={
                "Service": value("Cloudbox  Plus"),
                "Amount": value(9.99),
                "Paid on": value("2026-11-03"),
                "Event": value("payment", "derived"),
            },
            dropped=[{"column": "Amount", "reason": "number_not_in_quote", "value": 1.0}],
        ),
        row("b.eml", values={"Service": value("Notes"), "Amount": value(4.5)}, seconds=3.0),
        row("c.eml", "NOT_APPLICABLE"),
        row("d.eml", values={"Service": value("Only in base")}),
        row("e.eml", "PROVIDER_ERROR", seconds=5.0),
    ]


def other_rows() -> list[dict[str, Any]]:
    return [
        row(
            "a.eml",
            values={
                "Service": value("cloudbox plus", "derived"),  # equal after normalising
                "Amount": value(9.990001),  # equal numerically
                "Paid on": value("2026-11-03"),
                "Event": value("ended", "derived"),  # differs
            },
        ),
        row(
            "b.eml",
            "NOT_APPLICABLE",  # the outcome differs
        ),
        row("c.eml", "NOT_APPLICABLE", seconds=2.0),
        # d.eml is missing; z.eml has no partner; the next row pairs by message id only
        row("z.eml", values={"Service": value("x")}),
        row(
            "renamed.eml",
            "FOUND",
            values={"Service": value("Elsewhere")},
            message_id="id-e.eml@example.com",
            prompt_did_not_fit=True,
        ),
    ]


@pytest.fixture
def runs(tmp_path: Path) -> tuple[Path, Path]:
    return (
        write_run(tmp_path / "base", base_rows()),
        write_run(tmp_path / "other", other_rows(), executor="ollama", model="local"),
    )


def result_of(runs: tuple[Path, Path]) -> dict[str, Any]:
    from scripts.compare_mail_runs import load_run

    base_rows_, base_summary = load_run(runs[0], "BASE_DIR")
    other_rows_, other_summary = load_run(runs[1], "OTHER_DIR")
    return compare(base_rows_, base_summary, other_rows_, other_summary)


def test_matching_counts_and_the_outcome_table(runs: tuple[Path, Path]) -> None:
    result = result_of(runs)
    # a, b, c by file name; e with renamed.eml by message id; d and z are left over.
    assert result["mails"] == {"matched": 4, "only_in_base": 1, "only_in_other": 1}
    table = result["outcomes_base_by_other"]
    assert table["found"] == {"found": 1, "not_applicable": 1, "nothing_found": 0, "failed": 0}
    assert table["not_applicable"]["not_applicable"] == 1
    assert table["failed"]["found"] == 1
    assert sum(sum(r.values()) for r in table.values()) == 4
    assert result["different"] == []


def test_columns_filled_in_both_one_side_and_equal_or_different(runs: tuple[Path, Path]) -> None:
    columns = result_of(runs)["columns"]
    assert columns["Service"]["both"] == {"n": 1, "equal": 1, "differ": 0}  # a; e has no base value
    assert columns["Service"]["only_in_base"] == 1  # b is not applicable in OTHER
    assert columns["Service"]["only_in_other"] == 1  # e is a failure in BASE, found in OTHER
    assert columns["Amount"]["both"] == {"n": 1, "equal": 1, "differ": 0}
    assert columns["Amount"]["only_in_base"] == 1
    assert columns["Paid on"]["both"]["n"] == 1 and columns["Paid on"]["both"]["equal"] == 1
    assert columns["Event"]["both"] == {"n": 1, "equal": 0, "differ": 1}
    # Marked literal in BASE: the service and the amount of a.eml, not its event.
    assert columns["Service"]["both_literal_in_base"] == {"n": 1, "equal": 1, "differ": 0}
    assert columns["Event"]["both_literal_in_base"] == {"n": 0, "equal": 0, "differ": 0}


@pytest.mark.parametrize(
    ("kind", "left", "right", "equal"),
    [
        ("number", 9.99, 9.99, True),
        ("number", 9.99, 10.0, False),
        ("number", 5.0, "5", True),
        ("date", "2026-11-03", "2026-11-03", True),
        ("date", "2026-11-03", "2026-03-11", False),
        ("date", "2026-11-03", " 2026-11-03 ", True),
        ("text", " Cloudbox   PLUS", "cloudbox plus", True),
        ("text", "Straße", "STRASSE", True),
        ("text", "Cloudbox", "Cloudbox Plus", False),
        ("one_of", "payment", "Payment", True),
        ("number", "n/a", "N/A", True),
    ],
)
def test_normalised_equality(kind: str, left: Any, right: Any, equal: bool) -> None:
    assert compare_mail_runs.same_value(kind, left, right) is equal


def test_each_side_in_numbers(runs: tuple[Path, Path]) -> None:
    result = result_of(runs)
    base, other = result["base"], result["other"]
    assert base["mails"] == 5 and other["mails"] == 5
    assert base["executor"] == "openai" and other["executor"] == "ollama"
    assert base["values_kept_by_mark"] == {"literal": 6, "derived": 1}
    assert base["values_dropped_by_reason"] == {"number_not_in_quote": 1}
    assert base["seconds_median"] == 1.0 and base["seconds_total"] == 11.0
    assert other["seconds_total"] == 6.0
    assert (base["input_tokens"], base["output_tokens"]) == (500, 50)
    assert (base["prompt_did_not_fit"], other["prompt_did_not_fit"]) == (0, 1)


def test_the_printout_holds_numbers_only(
    runs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(runs[0]), str(runs[1])]) == 0
    out = capsys.readouterr().out
    assert "Mails matched: 4; only in BASE: 1; only in OTHER: 1" in out
    assert "- Amount (number): filled in both 1, only in BASE 1, only in OTHER 0" in out
    for secret in ("SECRET-SUBJECT", "SECRET-QUOTE", "someone@example.com", "a.eml", "Cloudbox"):
        assert secret not in out


def test_different_tasks_or_columns_are_refused_unless_forced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = write_run(tmp_path / "b", base_rows())
    task = write_run(tmp_path / "t", other_rows(), question="Another invented question")
    without_event = [
        {**r, "values": {k: v for k, v in r["values"].items() if k != "Event"}}
        for r in other_rows()
    ]
    columns = write_run(tmp_path / "c", without_event, targets=TARGETS[:3])
    both = write_run(tmp_path / "x", without_event, targets=TARGETS[:3], question="Other")

    assert run([str(base), str(task)]) == 2
    assert "differ in: task." in capsys.readouterr().err
    assert run([str(base), str(columns)]) == 2
    assert "differ in: columns." in capsys.readouterr().err
    assert run([str(base), str(both)]) == 2
    assert "differ in: task, columns." in capsys.readouterr().err

    assert run([str(base), str(task), "--force"]) == 0
    out = capsys.readouterr().out
    assert "Warning: the two runs differ in: task." in out and "Mails matched" in out
    # A column the other run lacks is compared as "only in BASE".
    assert run([str(base), str(columns), "--force"]) == 0
    assert "- Event (one_of): filled in both 0, only in BASE 1" in capsys.readouterr().out


def test_unreadable_runs_give_one_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_run(tmp_path / "g", base_rows())
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run([str(good), str(empty)]) == 1
    assert capsys.readouterr().err == "OTHER_DIR has no readable rows.json and summary.json.\n"
    odd = write_run(tmp_path / "odd", [])
    (odd / "summary.json").write_text('{"task": 1}', encoding="utf-8")
    assert run([str(odd), str(good)]) == 1
    assert capsys.readouterr().err == "BASE_DIR does not hold the files of a probe run.\n"


def test_out_writes_the_same_numbers_as_json(
    runs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "numbers" / "compare.json"
    assert run([str(runs[0]), str(runs[1]), "--out", str(target)]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == result_of(runs)
    assert "SECRET" not in target.read_text(encoding="utf-8")
    assert "Numbers written to" in capsys.readouterr().out


def test_out_inside_the_repository_is_refused_unless_git_ignores_it(
    runs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root = compare_mail_runs.REPO_ROOT
    refused = root / "compare-numbers-test.json"
    with pytest.raises(SystemExit) as stop:
        run([str(runs[0]), str(runs[1]), "--out", str(refused)])
    assert stop.value.code == 2 and "not ignored by git" in capsys.readouterr().err
    assert not refused.exists()

    ignored = root / "artifacts" / "compare-numbers-test" / "compare.json"
    try:
        assert run([str(runs[0]), str(runs[1]), "--out", str(ignored)]) == 0
        assert ignored.is_file()
    finally:
        ignored.unlink(missing_ok=True)
        ignored.parent.rmdir()
