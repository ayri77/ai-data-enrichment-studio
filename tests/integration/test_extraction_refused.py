"""What is still refused about a plan that reads texts.

Only ``POST /api/extraction-plans`` makes one (``tests/integration/test_extraction_api.py``).
Everywhere else a plan can come in it is still refused with a fixed sentence: the normal plan
route, a planner's reply, a draft whose kind and plan do not agree (the gate of validate,
approve and run), and a worker that was not given the extraction's settings. It must never
be taken for a plan of the planner, nor passed on to the web lookup by accident.
"""

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic import SecretStr
from tests.support.planning import ScriptedPlanClient

from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.capabilities.extraction import (
    ExtractionRequest,
    ExtractionTarget,
    build_extraction_plan,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.plan_run import PlanRunService, PlanRunSettings
from ai_data_enrichment.plan_run_worker import (
    ExtractionSettings,
    LookupSettings,
    RunTask,
    execute_workbook,
)
from ai_data_enrichment.workspace_errors import WorkspaceError

SHEET = "Mail"
CODE = "extraction_plan_refused"
MESSAGE = "A plan that reads texts can only be made by the extraction route."


def workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET
    sheet.append(["Text", "Subject"])
    sheet.append(["Total: 9,99 EUR", "Receipt"])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def extraction_plan(path: Path) -> EnrichmentPlan:
    path.write_bytes(workbook_bytes())
    profile = profile_worksheet(path, SHEET, "d", selection=TableSelection(SHEET, "A1:B2"))
    request = ExtractionRequest(
        question="Payments",
        text_column="Text",
        targets=(ExtractionTarget(column="Amount", kind="number"),),
    )
    return build_extraction_plan(request, profile.columns)


def body(dataset_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "sheet_name": SHEET,
        "range_ref": "A1:B2",
        "instruction": "Fill Amount.",
        "output_columns": {"Amount": "C"},
        **changes,
    }


@pytest.fixture
def plan(tmp_path: Path) -> EnrichmentPlan:
    return extraction_plan(tmp_path / "plain.xlsx")


def test_the_normal_route_refuses_a_supplied_plan_that_reads_texts(plan: EnrichmentPlan) -> None:
    app = create_app(settings=PlanRunSettings(), offline_executor_factory=offline_executor)
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/datasets", params={"filename": "t.xlsx"}, content=workbook_bytes()
        )
        refused = client.post(
            "/api/plans",
            json=body(uploaded.json()["dataset_id"], plan=plan.model_dump(mode="json")),
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"] == {"code": CODE, "message": MESSAGE}
        assert app.state.plan_run.plans == {}


def test_a_reply_of_the_planner_with_such_a_plan_never_becomes_a_draft(
    plan: EnrichmentPlan,
) -> None:
    reply = {"status": "ready", "plan": plan.model_dump(mode="json"), "reason": None}
    planner = ScriptedPlanClient(*([reply] * 8))
    app = create_app(
        settings=PlanRunSettings(),
        offline_plan_client=planner,
        offline_executor_factory=offline_executor,
    )
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/datasets", params={"filename": "t.xlsx"}, content=workbook_bytes()
        )
        refused = client.post("/api/plans", json=body(uploaded.json()["dataset_id"]))
        assert planner.calls  # the planner was asked and could not produce one
        assert refused.status_code >= 400, refused.text
        assert refused.json()["error"]["code"] in {"planning_failed", CODE}
        assert app.state.plan_run.plans == {}


@pytest.mark.parametrize("kind", ["planner", "fallback", "mixed", "other", None])
def test_the_gate_refuses_a_plan_that_reads_texts_in_a_draft_of_another_kind(
    plan: EnrichmentPlan, kind: str | None
) -> None:
    draft = {} if kind is None else {"kind": kind}
    with pytest.raises(WorkspaceError) as refused:
        PlanRunService.plan_gate(draft, plan)
    assert (refused.value.code, refused.value.message) == (CODE, MESSAGE)
    # The sentence is the one thing the user is told, not the web lookup's.
    assert "web" not in refused.value.message and "search" not in refused.value.message
    assert plan_run.EXTRACTION_PLAN_REFUSED == MESSAGE


def test_the_gate_lets_an_extraction_draft_with_an_extraction_plan_through(
    plan: EnrichmentPlan,
) -> None:
    PlanRunService.plan_gate({"kind": "extraction"}, plan)
    assert "extraction" in plan_run.PLAN_KINDS


@pytest.mark.parametrize("shape", ["planner", "fallback"])
def test_the_gate_refuses_an_extraction_draft_whose_plan_is_of_another_shape(shape: str) -> None:
    other = (
        EnrichmentPlan(version="1.0", steps=[], outputs=[], assumptions=[])
        if shape == "planner"
        else EnrichmentPlan.model_validate(
            {
                "version": "1.0",
                "steps": [
                    {
                        "id": "lookup",
                        "capability": "web_lookup",
                        "inputs": {"task": {"kind": "literal", "value": "x"}},
                        "parameters": {},
                    }
                ],
                "outputs": [],
                "assumptions": [],
            }
        )
    )
    with pytest.raises(WorkspaceError) as refused:
        PlanRunService.plan_gate({"kind": "extraction"}, other)
    assert (refused.value.code, refused.value.message) == (CODE, MESSAGE)


def test_the_planner_gate_still_refuses_the_lookup_with_its_own_sentence() -> None:
    # Not changed: a plan of the planner is let through, a draft of another kind is not.
    empty = EnrichmentPlan(version="1.0", steps=[], outputs=[], assumptions=[])
    PlanRunService.plan_gate({"kind": "planner"}, empty)
    with pytest.raises(WorkspaceError) as refused:
        PlanRunService.plan_gate({"kind": "fallback"}, empty)
    assert refused.value.code == "web_lookup_plan_refused"


def run_task(tmp_path: Path, plan: EnrichmentPlan, **changes: Any) -> RunTask:
    source = tmp_path / "source.xlsx"
    source.write_bytes(workbook_bytes())
    directory = tmp_path / "job"
    directory.mkdir()
    return RunTask(
        job_id="job",
        dataset_id="d",
        source_path=source,
        source_sha256=sha256(source.read_bytes()).hexdigest(),
        directory=directory,
        selection=TableSelection(SHEET, "A1:B2"),
        plan=plan,
        output_columns={"Amount": "C"},
        mountain_policy="strict",
        **changes,
    )


def test_the_worker_refuses_it_without_the_extraction_settings_and_writes_only_the_error(
    tmp_path: Path, plan: EnrichmentPlan, caplog: pytest.LogCaptureFixture
) -> None:
    task = run_task(tmp_path, plan)
    execute_workbook(task, offline_executor)
    assert sorted(path.name for path in task.directory.iterdir()) == ["error.json"]
    assert json.loads((task.directory / "error.json").read_text("utf-8")) == {
        "code": "execution_failed",
        "message": "The workbook run failed.",
    }
    # Refused by name, not by the web lookup's own registry check.
    assert "run for an extraction plan only" in caplog.text
    assert "fallback plan" not in caplog.text


def test_the_worker_refuses_the_lookup_settings_for_it(
    tmp_path: Path, plan: EnrichmentPlan, caplog: pytest.LogCaptureFixture
) -> None:
    lookup = LookupSettings(
        model="m", reasoning_effort="low", api_key=SecretStr("sk-test"), deadline_seconds=60.0
    )
    task = run_task(tmp_path, plan, lookup=lookup)
    execute_workbook(task, offline_executor)
    assert sorted(path.name for path in task.directory.iterdir()) == ["error.json"]
    assert "run for an extraction plan only" in caplog.text


def test_the_worker_refuses_the_extraction_settings_for_any_other_plan(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ordinary = EnrichmentPlan(version="1.0", steps=[], outputs=[], assumptions=[])
    settings = ExtractionSettings(
        model="m", reasoning_effort="low", api_key=SecretStr("sk-test"), deadline_seconds=60.0
    )
    task = run_task(tmp_path, ordinary, extraction=settings)
    execute_workbook(task, offline_executor)
    assert sorted(path.name for path in task.directory.iterdir()) == ["error.json"]
    # A run that holds the extraction's settings logs the class of an error and nothing else.
    assert "Workbook worker failed (ValueError)" in caplog.text
    assert "extraction plan only" not in caplog.text
