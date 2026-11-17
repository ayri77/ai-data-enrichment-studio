"""The planner measurement script: what it records besides the model's answers."""

from pathlib import Path
from typing import Any, cast

import pytest
from scripts.experiment_planner import (
    INSTRUCTIONS,
    SERVER_REQUEST_TIMEOUT_SECONDS,
    RecordingClient,
    request_timing,
)
from tests.support.planning import workbook_profile

from ai_data_enrichment import sample_catalog
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


def test_request_timing_counts_every_request_and_those_the_server_would_drop() -> None:
    slow = SERVER_REQUEST_TIMEOUT_SECONDS + 1.5
    records: list[dict[str, object]] = [
        {"request_seconds": [1.0, slow]},  # a run with one repair
        {"request_seconds": [2.0]},
        {},  # a run that never reached the model
    ]
    assert request_timing(records) == {
        "requests": 3,
        "median_seconds": 2.0,
        "max_seconds": slow,
        "over_limit": 1,
    }
    assert request_timing([]) == {
        "requests": 0,
        "median_seconds": None,
        "max_seconds": None,
        "over_limit": 0,
    }


class _Client:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        if self.answer is None:
            raise TimeoutError("model did not answer")
        return self.answer


def test_a_failed_request_is_timed_too() -> None:
    answered = RecordingClient(cast(OpenAIPlanClient, _Client("{}")))
    assert answered.complete(messages=[], response_schema={}) == "{}"
    assert cast(float, answered.completions[0]["seconds"]) >= 0

    silent = RecordingClient(cast(OpenAIPlanClient, _Client(None)))
    with pytest.raises(TimeoutError):
        silent.complete(messages=[], response_schema={})
    # A timeout is exactly the request whose duration matters.
    assert silent.completions[0]["error_type"] == "TimeoutError"
    assert cast(float, silent.completions[0]["seconds"]) >= 0


def test_every_measured_case_is_an_example_with_its_own_instruction_and_table() -> None:
    # These are measurement cases, not demo examples: two on the Capitals table, and the two
    # with a column no capability can fill.
    assert set(INSTRUCTIONS) - {
        "capitals_coordinates",
        "capitals_population",
        "mountains_age",
        "capitals_mayor",
    } == {sample.sample_id for sample in sample_catalog.SAMPLES}
    for sample in sample_catalog.SAMPLES:
        # Measured with the words a visitor of the demo gets.
        assert INSTRUCTIONS[sample.sample_id] == sample.instruction
        columns = [column.name for column in workbook_profile(sample.sample_id).columns]
        # The columns the example fills are the last ones of its table.
        names = [name for name, _ in sample.outputs]
        assert columns[-len(names) :] == names


def test_the_image_carries_every_example_and_its_prepared_plan() -> None:
    image = Path("Dockerfile").read_text(encoding="utf-8")
    for sample in sample_catalog.SAMPLES:
        assert sample.workbook in image and sample.plan in image


# --- the route check in the measurement -------------------------------------------------

import copy  # noqa: E402
import json  # noqa: E402

from scripts import experiment_planner  # noqa: E402
from scripts.experiment_planner import (  # noqa: E402
    has_literal_input,
    judge_route,
    print_route_summary,
    rejudge,
    route_line,
    route_summary,
)
from tests.support.planning import ScriptedPlanClient, ready_response  # noqa: E402


def _plan(case: str = "capitals") -> dict[str, Any]:
    return cast(dict[str, Any], ready_response(case)["plan"])


def _record(
    run: int, status: str, valid: bool, plan: dict[str, Any] | None = None
) -> dict[str, object]:
    return {
        "run": run,
        "status": status,
        "valid": valid,
        "attempts": [],
        "request_seconds": [1.5],
        "plan": plan,
    }


def _deviating(case: str = "capitals") -> dict[str, Any]:
    plan = _plan(case)
    # Types and names all check out, so this passes validation; it asks for the wrong column.
    plan["steps"][1]["inputs"]["country"]["name"] = "Country_From"
    return plan


def _unused() -> dict[str, Any]:
    plan = _plan()
    plan["steps"].append(copy.deepcopy(plan["steps"][0]) | {"id": "idle"})
    return plan


def _mixed_batch() -> list[dict[str, object]]:
    two_deviations = _plan()
    two_deviations["steps"][2]["parameters"]["unit"] = "mi"
    two_deviations["outputs"][0]["expected_unit"] = "mi"
    literal = _plan()
    literal["steps"][0]["inputs"]["city"] = {"kind": "literal", "value": "Oslo"}
    records = [
        _record(1, "ready", True, _plan()),
        _record(2, "ready", True, _unused()),
        _record(3, "ready", True, _deviating()),
        _record(4, "ready", True, two_deviations),
        _record(5, "ready", True, literal),
        _record(6, "ready", False, _plan()),  # invalid: not judged, even though the plan is fine
        _record(7, "unsupported", False, None),  # refused
        _record(8, "planning_error", False, None),
    ]
    for record in records:
        judge_route(record, "capitals")
    return records


def test_only_a_valid_ready_plan_is_judged_and_the_line_says_how() -> None:
    lines = [route_line(r) for r in _mixed_batch()]
    assert lines == [
        "route: expected",
        "route: expected (UNUSED_STEP)",
        "route: deviates (WRONG_SOURCE_COLUMN)",
        "route: deviates (WRONG_PARAMETER, WRONG_UNIT)",
        "route: deviates (LITERAL_INPUT)",
        "route: not judged (no valid plan)",
        "route: not judged (refused)",
        "route: not judged (no valid plan)",
    ]


def test_the_summary_keeps_the_validity_count_and_adds_the_route_numbers() -> None:
    summary = route_summary(_mixed_batch(), 8)
    assert summary == {
        "success_count": 5,
        "valid_on_expected_route": 2,
        "valid_deviating_route": 3,
        "valid_not_judged": 0,
        "deviation_counts": {
            "LITERAL_INPUT": 1,
            "WRONG_PARAMETER": 1,
            "WRONG_SOURCE_COLUMN": 1,
            "WRONG_UNIT": 1,
        },
        "note_counts": {"UNUSED_STEP": 1},
        "refused": 1,
        "invalid": 2,
        "plans_with_literal_inputs": 1,
        "valid_handed_away_expected_filled": 0,
        "headline": "2 of 8: valid and on the expected route",
    }


def test_the_console_summary_shows_every_number_and_warns_about_literals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_route_summary(route_summary(_mixed_batch(), 8), 8)
    out = capsys.readouterr().out
    assert "Valid ready plans: 5/8" in out
    assert "Valid plans on the expected route: 2/8" in out
    assert (
        "Valid plans that deviate: 3/8 "
        "(LITERAL_INPUT x1, WRONG_PARAMETER x1, WRONG_SOURCE_COLUMN x1, WRONG_UNIT x1)" in out
    )
    assert "Noted, not failed: UNUSED_STEP in 1 run(s)" in out
    assert "Valid plans that handed away a target the case expects to be filled: 0/8" in out
    assert "Refused: 1 | invalid: 2" in out
    assert "Headline: 2 of 8: valid and on the expected route" in out
    assert "WARNING: 1 plan(s) contain a literal input" in out


def test_a_plan_of_an_older_schema_is_neither_on_nor_off_the_route() -> None:
    record = _record(1, "ready", True, {"steps": "not a plan"})
    judge_route(record, "capitals")
    assert route_line(record) == "route: not judged (plan unreadable)"
    assert route_summary([record], 1)["valid_not_judged"] == 1


class _NoApi:
    """Stands in for the OpenAI client object; the scripted plan client never uses it."""

    def __init__(self, *args: object, **kwargs: object) -> None: ...

    def __enter__(self) -> "_NoApi":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answers: list[str | dict[str, Any]]
) -> Path:
    scripted = ScriptedPlanClient(*answers)
    monkeypatch.setattr(experiment_planner, "OpenAI", _NoApi)
    monkeypatch.setattr(experiment_planner, "OpenAIPlanClient", lambda **_: scripted)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-key")
    out = tmp_path / "out"
    experiment_planner.run(
        [
            "--case", "capitals", "--model", "fake", "--repeat", str(len(answers)),
            "--repairs", "0", "--out", str(out),
        ]
    )  # fmt: skip
    (directory,) = out.iterdir()
    return directory


def test_a_measurement_with_a_fake_model_reports_and_saves_the_route_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ready = lambda plan: {"status": "ready", "plan": plan, "reason": None}  # noqa: E731
    directory = _fake_measurement(
        monkeypatch,
        tmp_path,
        [
            ready(_plan()),
            ready(_deviating()),
            ready(_unused()),
            {"status": "unsupported", "plan": None, "reason": "no"},
            "this is not json",
        ],
    )
    out = capsys.readouterr().out
    assert out.count("route: expected") == 2
    assert "route: deviates (WRONG_SOURCE_COLUMN)" in out
    assert "route: not judged (refused)" in out
    assert "Headline: 2 of 5: valid and on the expected route" in out

    saved = json.loads((directory / "summary.json").read_text("utf-8"))
    assert saved["success_count"] == 3  # unchanged meaning: valid ready plans
    assert saved["valid_on_expected_route"] == 2
    assert saved["valid_deviating_route"] == 1
    assert saved["deviation_counts"] == {"WRONG_SOURCE_COLUMN": 1}
    assert saved["headline"] == "2 of 5: valid and on the expected route"
    runs = saved["runs"]
    assert [r["route"]["verdict"] for r in runs] == [
        "expected", "deviates", "expected", "not_judged", "not_judged",
    ]  # fmt: skip
    assert runs[1]["route"]["deviations"][0]["code"] == "WRONG_SOURCE_COLUMN"
    assert runs[1]["route"]["deviations"][0]["blocking"] is True
    assert runs[2]["route"]["deviations"][0]["blocking"] is False

    # The same directory judged again later, offline: the same verdicts, a separate file.
    capsys.readouterr()
    experiment_planner.run(["--routes-only", str(directory)])
    again = capsys.readouterr().out
    assert "Headline: 2 of 5: valid and on the expected route" in again
    routes = json.loads((directory / "routes.json").read_text("utf-8"))
    assert routes["valid_deviating_route"] == 1
    assert [r["route"]["verdict"] for r in routes["runs"]] == [r["route"]["verdict"] for r in runs]
    assert json.loads((directory / "summary.json").read_text("utf-8")) == saved


def test_a_measurement_of_a_partial_case_records_the_unfilled_targets_and_counts_handing_away(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mountains = json.loads(Path("demo/mountains_api_plan.json").read_text("utf-8"))
    renamed = copy.deepcopy(mountains)
    renamed["outputs"][0]["target_column"] = "age"
    answers: list[str | dict[str, Any]] = [
        # The expected answer: the height is written, the age is named as unfilled.
        {"status": "ready", "plan": mountains, "reason": None, "unfilled_targets": ["age"]},
        # The height is handed away although a capability can produce it.
        {"status": "ready", "plan": renamed, "reason": None, "unfilled_targets": ["height"]},
        # Both targets written: the age is not handed away.
        {"status": "unsupported", "plan": None, "reason": "no", "unfilled_targets": []},
    ]
    both = copy.deepcopy(mountains)
    both["steps"].append(copy.deepcopy(both["steps"][-1]))
    both["steps"][-1]["id"] = "age_step"
    both["outputs"].append({**both["outputs"][0], "step_id": "age_step", "target_column": "age"})
    answers.insert(2, {"status": "ready", "plan": both, "reason": None})
    scripted = ScriptedPlanClient(*answers)
    monkeypatch.setattr(experiment_planner, "OpenAI", _NoApi)
    monkeypatch.setattr(experiment_planner, "OpenAIPlanClient", lambda **_: scripted)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-key")
    out = tmp_path / "out"
    experiment_planner.run(
        [
            "--case", "mountains_age", "--model", "fake", "--repeat", "4", "--repairs", "0",
            "--output-targets", "height,age", "--out", str(out),
        ]
    )  # fmt: skip
    (directory,) = out.iterdir()
    saved = json.loads((directory / "summary.json").read_text("utf-8"))
    runs = saved["runs"]
    assert [r["unfilled_targets"] for r in runs[:2]] == [["age"], ["height"]]
    assert [r["route"]["verdict"] for r in runs] == [
        "expected",
        "deviates",
        "deviates",
        "not_judged",
    ]
    codes = [{d["code"] for d in r["route"]["deviations"]} for r in runs[1:3]]
    assert "TARGET_HANDED_AWAY" in codes[0] and "TARGET_NOT_HANDED_AWAY" in codes[0]
    assert codes[1] == {"TARGET_NOT_HANDED_AWAY"}
    assert saved["valid_handed_away_expected_filled"] == 1
    assert saved["valid_on_expected_route"] == 1
    printed = capsys.readouterr().out
    assert "unfilled=['age']" in printed and "unfilled=['height']" in printed
    assert "handed away a target the case expects to be filled: 1/4" in printed
    # Judged again offline: the unfilled targets are read from the saved runs.
    experiment_planner.run(["--routes-only", str(directory)])
    again = json.loads((directory / "routes.json").read_text("utf-8"))
    assert [r["unfilled_targets"] for r in again["runs"][:2]] == [["age"], ["height"]]
    assert again["valid_handed_away_expected_filled"] == 1


def test_routes_only_judges_a_run_saved_before_the_route_check_existed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = {
        "case": "capitals",
        "success_count": 2,
        "runs": [
            {k: v for k, v in _record(1, "ready", True, _plan()).items()},
            {k: v for k, v in _record(2, "ready", True, _deviating()).items()},
        ],
    }
    (tmp_path / "summary.json").write_text(json.dumps(old), encoding="utf-8")
    experiment_planner.run(["--routes-only", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Headline: 1 of 2: valid and on the expected route" in out
    assert "route: deviates (WRONG_SOURCE_COLUMN)" in out


def test_routes_only_refuses_what_it_cannot_judge(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):  # no summary.json
        experiment_planner.run(["--routes-only", str(tmp_path)])
    (tmp_path / "summary.json").write_text(json.dumps({"case": "other", "runs": []}), "utf-8")
    with pytest.raises(SystemExit):  # a case without an expected route
        experiment_planner.run(["--routes-only", str(tmp_path)])
    (tmp_path / "summary.json").write_text(json.dumps({"case": "capitals", "runs": []}), "utf-8")
    with pytest.raises(SystemExit):  # another case than the one named
        experiment_planner.run(["--routes-only", str(tmp_path), "--case", "mountains"])
    with pytest.raises(SystemExit):  # neither a case nor a directory
        experiment_planner.run([])


def test_rejudge_returns_the_numbers(tmp_path: Path) -> None:
    saved = {"case": "capitals", "runs": [_record(1, "ready", True, _plan())]}
    (tmp_path / "summary.json").write_text(json.dumps(saved), encoding="utf-8")
    assert rejudge(tmp_path)["valid_on_expected_route"] == 1


# --- what a saved plan can hold ---------------------------------------------------------


def _cell_values(workbook: str) -> set[str]:
    from openpyxl import load_workbook

    book = load_workbook(workbook, read_only=True)
    try:
        return {
            str(cell)
            for sheet in book.worksheets
            for row in list(sheet.iter_rows(values_only=True))[1:]
            for cell in row
            if cell is not None
        }
    finally:
        book.close()


@pytest.mark.parametrize("sample", sample_catalog.SAMPLES, ids=lambda s: s.sample_id)
def test_the_structure_of_a_prepared_plan_holds_no_cell_value_but_its_assumptions_can(
    sample: sample_catalog.Sample,
) -> None:
    plan = sample_catalog.prepared_plan(sample)
    assert plan is not None
    structure = json.dumps({k: v for k, v in plan.items() if k != "assumptions"})
    values = _cell_values(sample.workbook)
    assert values
    for value in values:
        assert f'"{value}"' not in structure, value
        if len(value) >= 4:
            assert value not in structure, value
    assert not has_literal_input(_record(1, "ready", True, plan))
    # The assumptions are free text, and the known-good plans of the Capitals tables quote
    # a city of the table ("including Istanbul") in them.
    quoted = [v for v in values if v in " ".join(plan["assumptions"])]
    assert quoted == (["Istanbul"] if sample.sample_id.startswith("capitals") else [])


def test_a_plan_can_hold_a_cell_value_through_a_literal_and_the_script_counts_such_plans() -> None:
    plan = _plan()
    cell = sorted(_cell_values("samples/capitals.xlsx"))[0]
    plan["steps"][0]["inputs"]["city"] = {"kind": "literal", "value": cell}
    assert json.dumps(cell) in json.dumps(plan)
    assert has_literal_input(_record(1, "ready", True, plan))
