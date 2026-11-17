"""A plan for the columns that have a source: draft, approval, run, result and report.

The planner is a scripted fake; no network is used. The prepared capitals plan writes
`distance`; the chosen column `mayor` (or `age`) has no source, so the answer lists it as
unfilled and the server offers a runnable plan for the others.
"""

import copy
import json
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from tests.integration.test_plan_run_api import approve, plan_payload, start, upload, wait
from tests.support.planning import CAPITALS_MAYOR_INSTRUCTION, ScriptedPlanClient, ready_response

from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.plan_run import PlanRunSettings, draft_fingerprint
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport


@pytest.fixture(autouse=True)
def no_parent_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline integration tests attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)
    monkeypatch.setattr(UrllibJsonTransport, "post_json", denied)


def partial_answer(unfilled: list[str]) -> dict[str, Any]:
    return {**ready_response(), "unfilled_targets": unfilled}


def app_with(*answers: dict[str, Any] | str) -> tuple[TestClient, ScriptedPlanClient]:
    llm = ScriptedPlanClient(*answers)
    return (
        TestClient(
            create_app(
                settings=PlanRunSettings(),
                offline_plan_client=llm,
                offline_executor_factory=offline_executor,
            )
        ),
        llm,
    )


def request(dataset_id: str, columns: dict[str, str], range_ref: str, **extra: Any) -> dict:
    return {
        "dataset_id": dataset_id,
        "sheet_name": "Sheet1",
        "range_ref": range_ref,
        "instruction": CAPITALS_MAYOR_INSTRUCTION,
        "output_columns": columns,
        **extra,
    }


def create(client: TestClient, body: dict) -> dict:
    response = client.post("/api/plans", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def capitals_with_existing_column() -> bytes:
    """The capitals table with a filled column F, "age", after the output column E."""
    book = load_workbook("samples/capitals.xlsx")
    sheet = book["Sheet1"]
    sheet["F1"] = "age"
    for row in range(2, 12):
        sheet[f"F{row}"] = row * 10 if row % 3 else f"=A{row}&B{row}"
    sheet["F2"].number_format = "0.00"
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def result_book(client: TestClient, job: dict):
    output = client.get(job["artifacts"]["result"])
    assert output.status_code == 200
    return load_workbook(BytesIO(output.content))


def test_the_draft_carries_the_unfilled_targets_and_is_runnable_for_the_others() -> None:
    client, llm = app_with(partial_answer(["mayor"]))
    with client:
        columns = {"distance": "E", "mayor": "F"}
        plan = create(client, request(upload(client), columns, "A1:E11"))
        assert plan["unfilled_targets"] == ["mayor"]
        assert plan["output_columns"] == columns  # what was chosen is kept as it was
        assert plan["planning_status"] == "ready" and plan["validation"]["valid"]
        assert [o["target_column"] for o in plan["plan"]["outputs"]] == ["distance"]
        assert json.loads(llm.calls[0][0][1]["content"])["output_targets"] == ["distance", "mayor"]
        assert plan["fingerprint"] == draft_fingerprint(plan)
        assert client.post(f"/api/plans/{plan['plan_id']}/validate").json()["valid"]

        approve(client, plan)
        job = wait(client, start(client, plan))
        assert job["status"] == "needs_review", job
        sheet = result_book(client, job)["Sheet1"]
        # The column with no source is not created: the sheet ends at E (read before any
        # cell is looked up, which would itself make openpyxl extend the sheet).
        assert sheet.max_column == 5
        assert sheet["E1"].value == "distance" and sheet["E3"].value is not None
        assert sheet["F1"].value is None

        report = client.get(job["artifacts"]["report"]).json()
        assert report["unfilled_targets"] == ["mayor"]
        assert report["approved_plan"]["unfilled_targets"] == ["mayor"]
        assert len(report["rows"]) == 10


def test_an_unfilled_existing_column_is_left_exactly_as_it_is() -> None:
    client, _ = app_with(partial_answer(["age"]))
    with client:
        source = capitals_with_existing_column()
        dataset = upload(client, content=source)
        plan = create(client, request(dataset, {"distance": "E", "age": "F"}, "A1:F11"))
        assert plan["unfilled_targets"] == ["age"] and plan["validation"]["valid"], plan[
            "validation"
        ]
        approve(client, plan)
        job = wait(client, start(client, plan))
        assert job["status"] == "needs_review", job
        before, after = load_workbook(BytesIO(source))["Sheet1"], result_book(client, job)["Sheet1"]
        for row in range(1, 12):
            was, now = before[f"F{row}"], after[f"F{row}"]
            assert now.value == was.value and now.number_format == was.number_format
            assert now.data_type == was.data_type and repr(now.font) == repr(was.font)
        assert after["E3"].value is not None
        report = client.get(job["artifacts"]["report"]).json()
        assert report["unfilled_targets"] == ["age"]
        # No row records an outcome for a column the plan does not write.
        assert all("age" not in (r.get("kept_outputs") or []) for r in report["rows"])


def test_a_row_is_not_skipped_or_kept_because_of_an_unfilled_column() -> None:
    """Only the columns the plan writes decide whether a row is already filled."""
    client, _ = app_with(partial_answer(["age"]))
    with client:
        dataset = upload(client, content=capitals_with_existing_column())
        plan = create(client, request(dataset, {"distance": "E", "age": "F"}, "A1:F11"))
        approve(client, plan)
        job = wait(client, start(client, plan))
        report = client.get(job["artifacts"]["report"]).json()
        assert job.get("rows_skipped", 0) == 0 and report["skipped_rows"] == []
        assert report["metrics"]["total_rows"] == 10


def test_the_fingerprint_covers_the_unfilled_targets() -> None:
    client, _ = app_with(partial_answer(["mayor"]))
    with client:
        plan = create(client, request(upload(client), {"distance": "E", "mayor": "F"}, "A1:E11"))
        approve(client, plan)
        stored = client.app.state.plan_run.plans[plan["plan_id"]]  # type: ignore[attr-defined]
        original = copy.deepcopy(stored)

        def refused(code: str, status: int = 409) -> None:
            response = client.post(f"/api/plans/{plan['plan_id']}/runs", json={})
            assert response.status_code == status, response.text
            assert response.json()["error"]["code"] == code
            assert client.app.state.plan_run.jobs == {}  # type: ignore[attr-defined]

        stored["unfilled_targets"] = []
        refused("approval_mismatch")
        stored["unfilled_targets"] = ["mayor", "distance"]
        refused("approval_mismatch")
        del stored["unfilled_targets"]
        refused("approval_mismatch")
        # With a fingerprint made to fit, validation still refuses a name that is not a
        # requested target, and the draft cannot be approved again or run as it is.
        stored.update(unfilled_targets=["bogus"], status="draft")
        stored["fingerprint"] = draft_fingerprint(stored)
        checked = client.post(f"/api/plans/{plan['plan_id']}/validate").json()
        assert not checked["valid"]
        assert "UNKNOWN_UNFILLED_TARGET" in [e["code"] for e in checked["errors"]]
        denied = client.post(
            f"/api/plans/{plan['plan_id']}/approve",
            json={"approved": True, "fingerprint": stored["fingerprint"]},
        )
        assert denied.status_code == 422 and denied.json()["error"]["code"] == "invalid_plan"
        stored.clear()
        stored.update(original)
        assert client.post(f"/api/plans/{plan['plan_id']}/runs", json={}).status_code == 202


def test_a_full_draft_has_an_empty_list_and_a_stable_fingerprint() -> None:
    client, _ = app_with(partial_answer([]))
    with client:
        columns = {"distance": "E"}
        plan = create(client, request(upload(client), columns, "A1:E11"))
        assert plan["unfilled_targets"] == [] and plan["validation"]["valid"]
        stored = dict(plan)
        # The same content gives the same fingerprint, and the list is part of the content.
        assert draft_fingerprint(stored) == plan["fingerprint"]
        assert (
            draft_fingerprint({**stored, "unfilled_targets": ["distance"]}) != plan["fingerprint"]
        )
        approve(client, plan)
        job = wait(client, start(client, plan))
        assert client.get(job["artifacts"]["report"]).json()["unfilled_targets"] == []


def test_a_draft_stored_without_the_field_keeps_working() -> None:
    client, _ = app_with(partial_answer([]))
    with client:
        plan = create(client, request(upload(client), {"distance": "E"}, "A1:E11"))
        stored = client.app.state.plan_run.plans[plan["plan_id"]]  # type: ignore[attr-defined]
        del stored["unfilled_targets"]
        stored["fingerprint"] = draft_fingerprint(stored)
        assert client.post(f"/api/plans/{plan['plan_id']}/validate").json()["valid"]
        approve(client, stored)
        job = wait(client, start(client, stored))
        assert client.get(job["artifacts"]["report"]).json()["unfilled_targets"] == []


def test_a_supplied_plan_must_still_write_every_chosen_column() -> None:
    client, llm = app_with()
    with client:
        body = request(
            upload(client), {"distance": "E", "mayor": "F"}, "A1:E11", plan=plan_payload()
        )
        plan = create(client, body)
        assert llm.calls == [] and plan["unfilled_targets"] == []
        assert not plan["validation"]["valid"]
        assert "OUTPUT_TARGET_MISMATCH" in [e["code"] for e in plan["validation"]["errors"]]
        denied = client.post(
            f"/api/plans/{plan['plan_id']}/approve",
            json={"approved": True, "fingerprint": plan["fingerprint"]},
        )
        assert denied.status_code == 422 and denied.json()["error"]["code"] == "invalid_plan"
        # The request body has no such field: a client cannot hand a column away.
        refused = client.post("/api/plans", json={**body, "unfilled_targets": ["mayor"]})
        assert refused.status_code == 422


def test_a_planner_answer_that_names_a_wrong_target_is_repaired_before_a_draft_exists() -> None:
    wrong = partial_answer(["bogus"])
    client, llm = app_with(wrong, partial_answer(["mayor"]))
    with client:
        plan = create(client, request(upload(client), {"distance": "E", "mayor": "F"}, "A1:E11"))
        assert len(llm.calls) == 2
        assert plan["unfilled_targets"] == ["mayor"] and plan["validation"]["valid"]
        # The unknown name reduces nothing, so the missing `mayor` is reported as well.
        assert plan["planning_attempts"][0]["diagnostic_codes"] == [
            "UNKNOWN_UNFILLED_TARGET",
            "OUTPUT_TARGET_MISMATCH",
        ]


def test_a_chosen_new_column_that_no_output_writes_is_refused_by_the_binding_check() -> None:
    """What happened before: the binding check refuses it; no empty header was ever made."""
    from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
    from ai_data_enrichment.ingestion.table_selection import TableSelection
    from ai_data_enrichment.output.excel_output_mapping import build_output_header_writes

    plan = EnrichmentPlan.model_validate(plan_payload())
    selection = TableSelection("Sheet1", "A1:E11")
    with pytest.raises(ValueError, match="must match plan output targets"):
        build_output_header_writes(plan, selection, output_columns={"distance": "E", "mayor": "F"})
