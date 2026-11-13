"""The scale experiment's own parts; its live run is not a test."""

from argparse import Namespace

import pytest
from openpyxl import load_workbook
from scripts.experiment_scale import failure_codes, run, summarize
from scripts.scale_names import CAPITALS, MOUNTAINS
from scripts.scale_workbook import (
    build_rows,
    distinct_lookups,
    left_out,
    names,
    recognized,
    write_workbook,
)


def test_the_name_lists_hold_no_duplicates() -> None:
    assert len(set(CAPITALS)) == len(CAPITALS)
    assert len(set(MOUNTAINS)) == len(MOUNTAINS)
    assert len({name for name, _ in MOUNTAINS}) == len(MOUNTAINS)


def test_capital_rows_are_different_pairs_and_repeatable() -> None:
    rows = build_rows("capitals", 400)
    assert len(rows) == len(set(rows)) == 400
    assert all(row[:2] != row[2:] for row in rows)
    assert rows == build_rows("capitals", 400)
    # Fewer capitals, same number of rows: fewer different places to look up.
    narrow = build_rows("capitals", 400, distinct=40)
    assert distinct_lookups("capitals", narrow) == 40
    assert distinct_lookups("capitals", rows) > 100


def test_more_rows_than_the_names_allow_is_refused_with_the_numbers() -> None:
    with pytest.raises(ValueError, match="45 different pairs"):
        build_rows("capitals", 46, distinct=10)
    with pytest.raises(ValueError, match="--repeat"):
        build_rows("mountains", len(MOUNTAINS) + 1)
    repeated = build_rows("mountains", 400, repeat=True)
    assert len(repeated) == 400
    assert distinct_lookups("mountains", repeated) == len(names("mountains"))


def test_unrecognized_countries_are_left_out_unless_asked_for() -> None:
    assert recognized("Nepal/China")
    assert recognized("Georgia/Russia")
    # Kosovo has no ISO 3166-1 code, so no spelling of it can be recognized.
    assert not recognized("Kosovo")
    assert not recognized("Georgia/Kosovo")
    assert left_out("capitals") == ["Kosovo"]
    assert all(recognized(country) for _, country in names("capitals"))
    assert all(recognized(country) for _, country in names("mountains"))
    assert len(names("capitals", keep_unrecognized=True)) == len(CAPITALS)


def test_the_workbook_has_the_layout_of_the_bundled_sample(tmp_path) -> None:
    for case, sample in (
        ("capitals", "samples/capitals.xlsx"),
        ("mountains", "samples/mountains.xlsx"),
    ):
        rows = build_rows(case, 30)
        path = tmp_path / f"{case}.xlsx"
        range_ref = write_workbook(path, case, rows)
        made, original = load_workbook(path), load_workbook(sample)
        try:
            sheet, expected = made["Sheet1"], original.active
            assert expected is not None
            assert [c.value for c in sheet[1]] == [c.value for c in expected[1]]
            assert sheet.dimensions == range_ref
            assert sheet.max_row == 31
            # The output column is left empty for the run to fill.
            assert all(row[-1].value is None for row in sheet.iter_rows(min_row=2))
        finally:
            made.close()
            original.close()


def test_failure_codes_count_steps_without_a_value() -> None:
    def step(step_id: str, status: str, code: str | None = None) -> dict:
        return {"step_id": step_id, "result": {"status": status, "error_code": code}}

    report = {
        "execution": {
            "records": [
                {"row_result": {"steps": [step("a", "success"), step("b", "failed", "NOT_FOUND")]}},
                {
                    "row_result": {
                        "steps": [
                            step("a", "failed", "AMBIGUOUS"),
                            step("b", "failed", "NOT_FOUND"),
                        ]
                    }
                },
            ]
        }
    }
    assert failure_codes(report) == {"b: NOT_FOUND": 2, "a: AMBIGUOUS": 1}
    assert failure_codes({}) == {}


def test_a_run_that_did_not_finish_is_summarized_without_a_report() -> None:
    summary = summarize(
        case="capitals",
        rows=200,
        lookups=120,
        seconds=300.4,
        job={
            "status": "timed_out",
            "error": {"code": "timeout"},
            "metrics": None,
            "timeout_seconds": 300,
        },
        report=None,
        review=None,
        sizes={},
    )
    assert summary["status"] == "timed_out"
    assert summary["seconds_per_row"] == 1.502
    assert summary["requests_total"] == 0 and summary["failure_codes"] == {}


def test_the_experiment_drives_the_api_end_to_end_on_fixture_answers(tmp_path) -> None:
    # Within the default 20-row bound, so no limit has to be raised in this process.
    args = Namespace(
        case="capitals",
        rows=12,
        distinct=None,
        repeat=False,
        keep_unrecognized=False,
        policy="verified_only",
        timeout=120,
        offline=True,
    )
    summary = run(args, tmp_path)
    assert summary["status"] in {"completed", "needs_review"}
    assert summary["metrics"]["total_rows"] == 12
    assert summary["rows"] == 12 and summary["distinct_lookups"] > 12
    assert (
        summary["requests_total"]
        == summary["provider_requests"]["open-meteo"]["transport_attempts"]
    )
    assert summary["bytes"]["report"] > 0 and summary["bytes"]["result"] > 0
    assert summary["limits"]["max_rows"] >= 20
