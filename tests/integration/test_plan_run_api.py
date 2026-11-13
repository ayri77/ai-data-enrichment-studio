"""Offline HTTP → real planner/validator/providers/executor → safe XLSX tests."""

import json
from collections.abc import Mapping
from hashlib import sha256
from io import BytesIO
from math import acos, cos, radians, sin
from pathlib import Path
from time import monotonic, sleep
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    ScriptedPlanClient,
    invalid_unit_response,
    ready_response,
)
from tests.support.wikidata import entity_statement

from ai_data_enrichment import api as api_module
from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import (
    APITransportFixture,
    FixturePlanClient,
    offline_executor,
)
from ai_data_enrichment.plan_run import TERMINAL, PlanRunSettings
from ai_data_enrichment.plan_run_worker import MountainPolicy, ResumeSelections, live_executor
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.providers.wikidata import WIKIDATA_API_URL
from ai_data_enrichment.run_limits import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RUN_ROWS,
    MAX_TIMEOUT_SECONDS,
)


def plan_payload(name="capitals") -> dict:
    return json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8"))


def upload(client, name="capitals", content=None):
    result = client.post(
        "/api/datasets",
        params={"filename": f"{name}.xlsx"},
        content=content if content is not None else Path(f"samples/{name}.xlsx").read_bytes(),
    )
    assert result.status_code == 201, result.text
    return result.json()["dataset_id"]


def request_body(dataset_id, name="capitals", *, generate=False):
    return {
        "dataset_id": dataset_id,
        "sheet_name": "Sheet1",
        "range_ref": "A1:E11" if name == "capitals" else "A1:C11",
        "instruction": "Calculate literal city distances in km."
        if name == "capitals"
        else "Fill mountain elevations in metres; leave unresolved values empty.",
        "output_columns": {"distance": "E"} if name == "capitals" else {"height": "C"},
        **({} if generate else {"plan": plan_payload(name)}),
    }


def draft(client, name="capitals", *, content=None, generate=False):
    dataset_id = upload(client, name, content)
    response = client.post("/api/plans", json=request_body(dataset_id, name, generate=generate))
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, plan):
    response = client.post(
        f"/api/plans/{plan['plan_id']}/approve",
        json={"approved": True, "fingerprint": plan["fingerprint"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def start(client, plan, **kwargs):
    response = client.post(f"/api/plans/{plan['plan_id']}/runs", json=kwargs)
    assert response.status_code == 202, response.text
    return response.json()


def wait(client, job):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        result = client.get(f"/api/jobs/{job['job_id']}")
        assert result.status_code == 200
        if result.json()["status"] in TERMINAL:
            return result.json()
        sleep(0.05)
    pytest.fail("Worker did not finish within test deadline")


@pytest.fixture(autouse=True)
def no_parent_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Offline integration tests attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)


@pytest.fixture
def client():
    with TestClient(
        create_app(
            settings=PlanRunSettings(),
            offline_executor_factory=offline_executor,
        )
    ) as client:
        yield client


@pytest.mark.parametrize("name", ["capitals", "mountains"])
@pytest.mark.parametrize("generate", [False, True])
def test_official_workbooks_through_real_api(name, generate):
    original = Path(f"samples/{name}.xlsx").read_bytes()
    llm = FixturePlanClient(plan_payload(name))
    with TestClient(
        create_app(
            settings=PlanRunSettings(),
            offline_plan_client=llm,
            offline_executor_factory=offline_executor,
        )
    ) as client:
        plan = draft(client, name, generate=generate)
        assert plan["validation"]["valid"]
        assert plan["status"] == "draft"
        assert len(plan["plan_id"]) == 32
        assert plan["source_sha256"] == sha256(original).hexdigest()
        assert bool(llm.calls) == generate
        if generate:
            context = json.loads(llm.calls[0]["messages"][1]["content"])
            assert context["dataset_profile"]["dataset_id"] == plan["dataset_id"]
            assert context["instruction"] == plan["instruction"]
            assert len(context["dataset_profile"]["sample_records"]) <= 5
        assert client.get(f"/api/plans/{plan['plan_id']}").json() == plan
        assert client.post(f"/api/plans/{plan['plan_id']}/validate").json()["valid"]
        assert client.post(f"/api/plans/{plan['plan_id']}/runs", json={}).status_code == 409
        approve(client, plan)
        queued = start(client, plan)
        assert queued["status"] == "queued"
        job = wait(client, queued)
        assert job["status"] == "needs_review", job
        assert job["metrics"]["total_rows"] == 10
        assert job["metrics"]["successful_rows"] == 1
        assert job["metrics"]["failed_rows"] > 0
        if name == "capitals":
            assert job["metrics"]["partial_rows"] > 0
        report_response = client.get(job["artifacts"]["report"])
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["execution_mode"] == "offline_fixture"
        assert report["approved_plan"]["plan"] == plan["plan"]
        assert report["approved_plan"]["approved_at"]
        assert report["source_sha256"] == sha256(original).hexdigest()
        assert len(report["rows"]) == 10
        execution = report["execution"]
        assert execution["resolution_report"]["job_id"] == job["job_id"]
        assert execution["resolution_report"]["operations"]
        assert any(
            step["result"]["error_code"]
            for row in execution["records"]
            for step in row["row_result"]["steps"]
        )
        assert all(
            counters["transport_attempts"] <= report["limits"]["max_requests_per_provider"]
            for counters in execution["provider_requests"].values()
        )
        output = client.get(job["artifacts"]["result"])
        assert output.status_code == 200
        assert report["result_sha256"] == sha256(output.content).hexdigest()
        source_book = load_workbook(BytesIO(original))
        output_book = load_workbook(BytesIO(output.content))
        try:
            for source_sheet in source_book:
                for row in source_sheet:
                    for cell in row:
                        if cell.value is not None:
                            assert (
                                output_book[source_sheet.title][cell.coordinate].value == cell.value
                            )
            sheet = output_book["Sheet1"]
            if name == "capitals":
                # Independent spherical law of cosines for the London/Rome fixture.
                a, b = radians(51.50853), radians(41.89193)
                delta = radians(-0.12574 - 12.51133)
                expected = 6371.0 * acos(sin(a) * sin(b) + cos(a) * cos(b) * cos(delta))
                # The 6371 km rounded radius differs by ~2 m from the mean
                # Earth radius used by the provider; a 10 m tolerance covers it.
                assert sheet["E3"].value == pytest.approx(expected, abs=0.01)
                assert sheet["E2"].value is None  # Ambiguous Kyiv is never invented.
                assert sheet["D11"].value == "Turkey"
                assert sheet["C11"].value == "Istanbul"
            else:
                assert sheet["C2"].value == 8848.86
                assert sheet["C11"].value is None  # No Annapurna fixture/evidence.
        finally:
            source_book.close()
            output_book.close()
        stored = cast(FastAPI, client.app).state.directory / f"{plan['dataset_id']}.xlsx"
        assert stored.read_bytes() == original
        assert Path(f"samples/{name}.xlsx").read_bytes() == original
        assert client.delete(f"/api/datasets/{plan['dataset_id']}").status_code == 409


def test_preserves_populated_outputs_and_fills_other_cells(client):
    book = load_workbook("samples/mountains.xlsx")
    book["Sheet1"]["C3"] = 0.0
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    plan = draft(client, "mountains", content=buffer.getvalue())
    approve(client, plan)
    job = wait(client, start(client, plan))
    assert job["status"] == "needs_review"
    report = client.get(job["artifacts"]["report"]).json()
    assert report["preserved_cells"] == ["C3"]
    output = load_workbook(BytesIO(client.get(job["artifacts"]["result"]).content))
    try:
        assert output["Sheet1"]["C3"].value == 0.0
        assert output["Sheet1"]["C2"].value == 8848.86
    finally:
        output.close()


def test_successful_bounded_selection(client):
    dataset_id = upload(client, "mountains")
    body = request_body(dataset_id, "mountains")
    body["range_ref"] = "A1:C2"
    plan = client.post("/api/plans", json=body).json()
    approve(client, plan)
    job = wait(client, start(client, plan))
    assert job["status"] == "completed"
    assert job["metrics"]["successful_rows"] == 1


@pytest.mark.parametrize("change", ["capability", "reference", "mapping", "steps"])
def test_invalid_plans_cannot_be_approved_or_executed(client, change):
    body = request_body(upload(client))
    if change == "capability":
        body["plan"]["steps"][0]["capability"] = "arbitrary_python"
    elif change == "reference":
        body["plan"]["steps"][0]["inputs"]["city"]["name"] = "absent"
    elif change == "mapping":
        body["output_columns"] = {"distance": "A"}
    else:
        body["plan"]["steps"] *= 5
    response = client.post("/api/plans", json=body)
    assert response.status_code == 201
    plan = response.json()
    assert not plan["validation"]["valid"]
    endpoint = f"/api/plans/{plan['plan_id']}"
    assert (
        client.post(
            endpoint + "/approve",
            json={
                "approved": True,
                "fingerprint": plan["fingerprint"],
            },
        ).json()["error"]["code"]
        == "invalid_plan"
    )
    assert client.post(endpoint + "/runs", json={}).status_code == 409


def test_approval_is_source_and_fingerprint_bound(client):
    plan = draft(client)
    endpoint = f"/api/plans/{plan['plan_id']}"
    mismatch = client.post(endpoint + "/approve", json={"approved": True, "fingerprint": "0" * 64})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "approval_mismatch"
    assert (
        client.post(
            endpoint + "/approve",
            json={
                "approved": False,
                "fingerprint": plan["fingerprint"],
            },
        ).status_code
        == 422
    )
    approve(client, plan)
    source = client.app.state.directory / f"{plan['dataset_id']}.xlsx"
    source.write_bytes(b"tampered")
    for action in ("validate", "runs"):
        response = client.post(endpoint + "/" + action, json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "source_changed"


@pytest.mark.parametrize(
    "server_enabled,request_enabled", [(False, False), (False, True), (True, False)]
)
def test_network_requires_both_opt_ins(server_enabled, request_enabled):
    with TestClient(create_app(settings=PlanRunSettings(allow_network=server_enabled))) as client:
        body = request_body(upload(client), generate=True)
        body["allow_network"] = request_enabled
        response = client.post("/api/plans", json=body)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "network_disabled"
        body["plan"] = plan_payload()
        plan = client.post("/api/plans", json=body).json()
        approve(client, plan)
        run = client.post(
            f"/api/plans/{plan['plan_id']}/runs",
            json={
                "allow_network": request_enabled,
            },
        )
        assert run.status_code == 403
        assert run.json()["error"]["code"] == "network_disabled"


@pytest.mark.parametrize(
    "model,code",
    [
        # An explicitly configured but unsupported model is reported, not replaced.
        ("gpt-4o-mini", "llm_not_configured"),
        ("gpt-4.1-mini", "missing_api_key"),
        # No configured model falls back to the measured default.
        (None, "missing_api_key"),
    ],
)
def test_missing_llm_configuration_is_distinct(monkeypatch, model, code):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app(settings=PlanRunSettings(True, model))) as client:
        body = request_body(upload(client), generate=True)
        body["allow_network"] = True
        response = client.post("/api/plans", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("status", ["ambiguous", "unsupported"])
def test_non_ready_planning_decisions_cannot_be_approved(status):
    with TestClient(create_app(offline_plan_client=FixturePlanClient(None, status))) as client:
        plan = draft(client, generate=True)
        assert plan["planning_status"] == status
        assert plan["plan"] is None
        assert not plan["validation"]["valid"]
        assert (
            client.post(
                f"/api/plans/{plan['plan_id']}/approve",
                json={
                    "approved": True,
                    "fingerprint": plan["fingerprint"],
                },
            ).status_code
            == 422
        )


class BrokenPlanClient(FixturePlanClient):
    def complete(self, **kwargs):
        raise RuntimeError("secret-token C:/private/path provider raw payload")


def test_planning_failure_is_sanitized():
    with TestClient(create_app(offline_plan_client=BrokenPlanClient(None))) as client:
        response = client.post("/api/plans", json=request_body(upload(client), generate=True))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "planning_failed"
        assert "secret-token" not in response.text
        assert "private" not in response.text


@pytest.mark.parametrize("failure", ["repeated_invalid", "malformed_repair", "transport"])
def test_failed_generated_repairs_remain_inspectable_and_unapprovable(failure):
    invalid = invalid_unit_response()
    responses = {
        "repeated_invalid": [invalid, invalid, invalid],
        "malformed_repair": [invalid, "sensitive-repair-marker", "sensitive-repair-marker"],
        "transport": [invalid, RuntimeError("credential-marker")],
    }[failure]
    llm = ScriptedPlanClient(*responses)
    with TestClient(create_app(offline_plan_client=llm)) as client:
        body = {**request_body(upload(client), generate=True), "instruction": CAPITALS_INSTRUCTION}
        response = client.post("/api/plans", json=body)
        assert response.status_code == 201
        plan = response.json()
        assert len(llm.calls) == (2 if failure == "transport" else 3)
        assert len(plan["planning_attempts"]) == len(llm.calls)
        assert plan["plan"] == invalid["plan"]
        assert not plan["validation"]["valid"]
        assert plan["validation"]["errors"][0]["code"] == "PARAMETER_TYPE_MISMATCH"
        assert "sensitive-repair-marker" not in response.text
        assert "credential-marker" not in response.text
        assert client.get(f"/api/plans/{plan['plan_id']}").json() == plan
        endpoint = f"/api/plans/{plan['plan_id']}"
        rejected = client.post(
            endpoint + "/approve", json={"approved": True, "fingerprint": plan["fingerprint"]}
        )
        assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_plan"
        assert client.post(endpoint + "/runs", json={}).status_code == 409


def test_repair_after_malformed_json_still_requires_explicit_approval():
    llm = ScriptedPlanClient('{"status":', invalid_unit_response(), ready_response())
    with TestClient(create_app(offline_plan_client=llm)) as client:
        plan = draft(client, generate=True)
        assert plan["validation"]["valid"]
        assert len(llm.calls) == 3
        assert plan["planning_attempts"][0]["diagnostic_codes"] == ["INVALID_JSON"]
        assert plan["planning_attempts"][1]["diagnostic_codes"] == ["PARAMETER_TYPE_MISMATCH"]
        assert plan["status"] == "draft" and plan["approved_at"] is None
        assert client.post(f"/api/plans/{plan['plan_id']}/runs", json={}).status_code == 409
        approve(client, plan)
        assert len(llm.calls) == 3  # Approval revalidates independently without another LLM call.


def test_exhausted_malformed_generation_has_safe_diagnostics(caplog):
    llm = ScriptedPlanClient(*["private-source-marker" for _ in range(3)])
    with TestClient(create_app(offline_plan_client=llm)) as client:
        response = client.post("/api/plans", json=request_body(upload(client), generate=True))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "planning_failed"
        assert "3 completion attempts" in response.text and "INVALID_JSON" in response.text
        assert "private-source-marker" not in response.text + caplog.text
        assert len(llm.calls) == 3


def slow_executor(capabilities: frozenset[str], policy: MountainPolicy, max_rows: int):
    sleep(30)
    return offline_executor(capabilities, policy, max_rows)


def broken_executor(capabilities: frozenset[str], policy: MountainPolicy, max_rows: int):
    raise RuntimeError("secret-token C:/private/path provider raw payload")


@pytest.mark.parametrize("action", ["cancel", "timeout", "failure", "shutdown"])
def test_worker_stops_and_never_publishes_partial_artifacts(action):
    factory = broken_executor if action == "failure" else slow_executor
    with TestClient(create_app(offline_executor_factory=factory)) as client:
        directory = cast(FastAPI, client.app).state.directory
        plan = draft(client)
        approve(client, plan)
        job = start(client, plan, timeout_seconds=1 if action == "timeout" else 30)
        endpoint = f"/api/jobs/{job['job_id']}"
        if action == "shutdown":
            # Lifespan must terminate and reap before deleting workspace files.
            pass
        else:
            assert client.get("/api/health").json() == {"status": "ok"}
            assert client.get(endpoint + "/artifacts/result").status_code == 409
            if action == "cancel":
                assert client.post(f"/api/plans/{plan['plan_id']}/runs", json={}).status_code == 409
                assert client.post(endpoint + "/cancel").status_code == 202
            final = wait(client, job)
            expected = {"cancel": "cancelled", "timeout": "timed_out", "failure": "failed"}[action]
            assert final["status"] == expected, final
            assert final["finished_at"]
            assert "result" not in final["artifacts"]
            report = client.get(final["artifacts"]["report"])
            assert report.status_code == 200
            assert report.json()["status"] == expected
            assert "secret-token" not in report.text
            assert "private" not in report.text
            assert not (directory / job["job_id"] / "result.xlsx").exists()
    assert not directory.exists()


def test_limits_and_unknown_ids(client):
    assert client.get("/api/plans/not-an-id").status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404
    dataset_id = upload(client)
    body = request_body(dataset_id)
    body["range_ref"] = "A1:E22"
    assert client.post("/api/plans", json=body).json()["error"]["code"] == "invalid_selection"
    body["range_ref"] = "A1:E11"
    plan = client.post("/api/plans", json=body).json()
    approve(client, plan)
    for seconds in (0, 301, True):
        response = client.post(
            f"/api/plans/{plan['plan_id']}/runs", json={"timeout_seconds": seconds}
        )
        assert response.status_code == 422


def test_request_size_and_retained_plan_limit(client, monkeypatch):
    response = client.post(
        "/api/plans", content=(b" " * 65_537), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    # At the limit the oldest plan gives way; the workspace never asks for a restart.
    monkeypatch.setattr(plan_run, "MAX_PLANS", 1)
    first = draft(client)
    response = client.post("/api/plans", json=request_body(upload(client)))
    assert response.status_code == 201
    assert client.get(f"/api/plans/{first['plan_id']}").status_code == 404
    assert client.get(f"/api/plans/{response.json()['plan_id']}").status_code == 200


def test_existing_successful_output_is_preserved_and_run_limit_enforced(client, monkeypatch):
    book = load_workbook("samples/mountains.xlsx")
    book["Sheet1"]["C2"] = 123.0
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    plan = draft(client, "mountains", content=buffer.getvalue())
    approve(client, plan)
    monkeypatch.setattr(plan_run, "MAX_JOBS", 1)
    job = wait(client, start(client, plan))
    assert job["status"] == "needs_review"
    output = load_workbook(BytesIO(client.get(job["artifacts"]["result"]).content))
    try:
        assert output["Sheet1"]["C2"].value == 123.0
    finally:
        output.close()
    report = client.get(job["artifacts"]["report"]).json()
    assert report["preserved_cells"] == ["C2"]
    assert report["source_unchanged"] is True
    # At the limit the oldest finished job and its artifacts give way to the new run.
    second = wait(client, start(client, plan))
    assert second["job_id"] != job["job_id"]
    assert client.get(f"/api/jobs/{job['job_id']}").status_code == 404
    assert client.get(job["artifacts"]["report"]).status_code == 404
    assert client.get(second["artifacts"]["report"]).status_code == 200


def test_offset_selection_and_output_mapping(client):
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet["A1"] = "Unselected note"
    sheet.append([None, "Mountain", "Country", "height"])
    sheet.append([None, "Mount Everest", "Nepal", None])
    book.create_sheet("Notes")["B2"] = "=1+2"
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    dataset_id = upload(client, content=buffer.getvalue())
    body = request_body(dataset_id, "mountains")
    body.update(range_ref="B2:D3", output_columns={"height": "D"})
    plan = client.post("/api/plans", json=body).json()
    approve(client, plan)
    job = wait(client, start(client, plan))
    assert job["status"] == "completed", job
    output = load_workbook(BytesIO(client.get(job["artifacts"]["result"]).content))
    try:
        assert output["Sheet1"]["D3"].value == 8848.86
        assert output["Sheet1"]["A1"].value == "Unselected note"
        assert output["Notes"]["B2"].value == "=1+2"
    finally:
        output.close()


@pytest.mark.parametrize("generated", [False, True])
def test_intent_conflict_blocks_approval_after_independent_revalidation(generated):
    proposal = ready_response()
    for step in proposal["plan"]["steps"][:2]:
        step["parameters"] = {"required_role": "country_capital"}
    llm = ScriptedPlanClient(proposal, proposal, proposal)
    with TestClient(create_app(offline_plan_client=llm)) as client:
        body = request_body(upload(client), generate=generated)
        body["instruction"] = CAPITALS_INSTRUCTION
        if not generated:
            body["plan"] = proposal["plan"]
        response = client.post("/api/plans", json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["validation"]["contract_valid"]
        assert not plan["validation"]["valid"]
        assert len(plan["validation"]["errors"]) == 2
        assert all(e["category"] == "intent" for e in plan["validation"]["errors"])
        endpoint = f"/api/plans/{plan['plan_id']}"
        assert client.post(endpoint + "/validate").json() == plan["validation"]
        rejected = client.post(
            endpoint + "/approve", json={"approved": True, "fingerprint": plan["fingerprint"]}
        )
        assert rejected.json()["error"]["code"] == "invalid_plan"
        assert client.post(endpoint + "/runs", json={}).status_code == 409
        assert len(llm.calls) == (3 if generated else 0)


def test_explicit_capital_role_can_be_approved(client):
    body = request_body(upload(client))
    body["instruction"] = "Only accept country capitals; calculate their distances in km."
    for step in body["plan"]["steps"][:2]:
        step["parameters"] = {"required_role": "country_capital"}
    response = client.post("/api/plans", json=body)
    assert response.status_code == 201
    plan = response.json()
    assert plan["validation"]["valid"]
    assert approve(client, plan)["status"] == "approved"


def test_generated_plan_with_a_differently_named_target_is_repaired():
    """The planner sees the bound target names and their mismatch is fed back."""
    renamed = ready_response()
    renamed["plan"]["outputs"][0]["target_column"] = "Distance_km"
    llm = ScriptedPlanClient(renamed, ready_response())
    with TestClient(create_app(offline_plan_client=llm)) as client:
        plan = draft(client, generate=True)
        assert plan["validation"]["valid"]
        assert [a["diagnostic_codes"] for a in plan["planning_attempts"]] == [
            ["OUTPUT_TARGET_MISMATCH"],
            [],
        ]
        context = json.loads(llm.calls[0][0][1]["content"])
        assert context["output_targets"] == ["distance"]
        assert approve(client, plan)["status"] == "approved"


def test_supplied_plan_target_mismatch_is_reported_precisely(client):
    body = request_body(upload(client))
    body["output_columns"] = {"Distance_km": "F"}
    response = client.post("/api/plans", json=body)
    assert response.status_code == 201
    validation = response.json()["validation"]
    assert not validation["valid"] and not validation["contract_valid"]
    assert [e["code"] for e in validation["errors"]] == ["OUTPUT_TARGET_MISMATCH"]
    assert "Missing: ['Distance_km']" in validation["errors"][0]["message"]


def reviewed_job(client, name="capitals"):
    plan = approve(client, draft(client, name))
    job = wait(client, start(client, plan))
    assert job["status"] == "needs_review"
    review = client.get(f"/api/jobs/{job['job_id']}/review")
    assert review.status_code == 200
    return plan, job, review.json()


def test_review_groups_an_ambiguous_city_once_for_all_affected_rows(client):
    _, job, review = reviewed_job(client)
    assert review["job_id"] == job["job_id"] and review["previous_job_id"] is None
    assert [item["query"]["name"] for item in review["items"]] == ["Kyiv"]
    kyiv = review["items"][0]
    assert kyiv["capability"] == "geocode" and kyiv["provider_id"] == "open-meteo"
    assert kyiv["query"]["context"] == {"country_code": "UA"}
    assert kyiv["status"] == "needs_review"
    # One decision covers both rows that mention Kyiv, in either column.
    assert sorted((link["row"], link["step_id"]) for link in kyiv["affected"]) == [
        (2, "from_city"),
        (6, "to_city"),
    ]
    assert review["affected_rows"] == 2
    assert {c["entity_id"] for c in kyiv["candidates"]} == {"703448", "704213"}
    assert all(c["selectable"] and c["blocker"] is None for c in kyiv["candidates"])
    assert {"latitude", "longitude", "country_code"} <= set(kyiv["candidates"][0]["attributes"])
    # Two equally ranked capitals: the API does not invent a preference.
    assert not any(c["recommended"] for c in kyiv["candidates"])
    # Cities the provider did not find are reported, but offer nothing to decide.
    unresolved = {item["query"]["name"] for item in review["informational"]}
    assert "Berlin" in unresolved and "Kyiv" not in unresolved
    assert all(not item["candidates"] for item in review["informational"])


def test_resume_fills_rows_from_a_user_choice_as_a_new_audited_job(client):
    plan, first, review = reviewed_job(client)
    kyiv = review["items"][0]
    decision = {"operation_id": kyiv["operation_id"], "source": "open-meteo", "entity_id": "703448"}
    response = client.post(f"/api/jobs/{first['job_id']}/resume", json={"decisions": [decision]})
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["job_id"] != first["job_id"]
    assert queued["previous_job_id"] == first["job_id"]
    assert queued["plan_id"] == plan["plan_id"]
    second = wait(client, queued)
    # Kyiv-Paris is now complete; Warsaw-Kyiv still lacks Warsaw.
    assert second["status"] == "needs_review"
    assert second["metrics"]["successful_rows"] == first["metrics"]["successful_rows"] + 1
    report = client.get(second["artifacts"]["report"]).json()
    assert report["previous_job_id"] == first["job_id"]
    # Rows carry what they were computed from and which steps the user decided.
    rows = {row["row"]: row for row in report["rows"]}
    assert rows[2]["inputs"] == {
        "Capital_From": "Kyiv",
        "Country_From": "Ukraine",
        "Capital_To": "Paris",
        "Country_To": "France",
    }
    assert rows[2]["status"] == "success" and rows[2]["user_chosen_steps"] == ["from_city"]
    assert rows[6]["status"] != "success" and rows[6]["user_chosen_steps"] == ["to_city"]
    assert rows[3]["user_chosen_steps"] == []
    earlier = client.get(first["artifacts"]["report"]).json()["rows"]
    assert all(row["user_chosen_steps"] == [] for row in earlier)
    assert "source_rows" not in report
    assert report["review_decisions"] == [decision]
    audit = report["execution"]["resolution_report"]
    assert audit["previous_job_id"] == first["job_id"]
    chosen = [
        operation["report"]["decision"]
        for operation in audit["operations"]
        if operation["report"]["candidate_set"]["query"]["name"] == "Kyiv"
    ]
    assert chosen and all(d["basis"] == "user_override" for d in chosen)
    assert all(d["selected"]["entity_id"] == "703448" for d in chosen)
    workbook = load_workbook(BytesIO(client.get(second["artifacts"]["result"]).content))
    try:
        assert isinstance(workbook["Sheet1"]["E2"].value, int | float)
        assert workbook["Sheet1"]["E6"].value is None
    finally:
        workbook.close()
    # The first job and its artifacts are untouched and can be resumed differently.
    assert client.get(f"/api/jobs/{first['job_id']}").json() == first
    assert client.get(first["artifacts"]["report"]).json()["previous_job_id"] is None
    # Nothing about Kyiv is left to decide in the new job.
    follow_up = client.get(f"/api/jobs/{second['job_id']}/review").json()
    assert follow_up["previous_job_id"] == first["job_id"]
    assert "Kyiv" not in {item["query"]["name"] for item in follow_up["items"]}


@pytest.mark.parametrize(
    "change,code",
    [
        ({"operation_id": "unknown"}, "invalid_decision"),
        ({"entity_id": "999"}, "invalid_decision"),
        ({"source": "wikidata", "entity_id": "Q1899"}, "invalid_decision"),
        ({"source": None, "entity_id": None}, "no_decisions"),
    ],
)
def test_resume_rejects_decisions_it_cannot_verify(client, change, code):
    _, job, review = reviewed_job(client)
    decision = {
        "operation_id": review["items"][0]["operation_id"],
        "source": "open-meteo",
        "entity_id": "703448",
        **change,
    }
    response = client.post(f"/api/jobs/{job['job_id']}/resume", json={"decisions": [decision]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert "Sheet1" not in response.text  # No record identifiers in public errors.
    assert len(client.get(f"/api/jobs/{job['job_id']}/review").json()["items"]) == 1


def test_resume_request_shape_and_job_state_are_checked(client):
    _, job, review = reviewed_job(client)
    endpoint = f"/api/jobs/{job['job_id']}/resume"
    operation_id = review["items"][0]["operation_id"]
    chosen = {"operation_id": operation_id, "source": "open-meteo", "entity_id": "703448"}
    for body in (
        {},
        {"decisions": []},
        {"decisions": [{"operation_id": operation_id, "source": "open-meteo"}]},
        {"decisions": [chosen], "unexpected": True},
    ):
        assert client.post(endpoint, json=body).status_code == 422
    duplicate = client.post(endpoint, json={"decisions": [chosen, chosen]})
    assert duplicate.json()["error"]["code"] == "invalid_decision"
    assert client.get("/api/jobs/missing/review").status_code == 404
    assert client.post("/api/jobs/missing/resume", json={"decisions": [chosen]}).status_code == 404


class AnnapurnaTransport(APITransportFixture):
    """Adds one verified summit and one unchecked namesake to the sparse fixture."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url == WIKIDATA_API_URL and params["action"] == "wbsearchentities":
            if params["search"] == "Annapurna":
                return {"search": [{"id": qid, "label": "Annapurna"} for qid in ("Q9001", "Q9002")]}
        if url == WIKIDATA_API_URL and params["action"] == "wbgetentities":
            known = {
                "Q9001": {
                    "P31": [entity_statement("Q8502")],
                    "P17": [entity_statement("Q837")],
                    "P2044": [quantity_statement("+8091")],
                },
                "Q9002": {},  # Never positively verified: visible, not approvable.
            }
            ids = params["ids"].split("|")
            if any(qid in known for qid in ids):
                return {"entities": {q: {"id": q, "claims": known.get(q, {})} for q in ids}}
        return super().get_json(url=url, params=params, timeout_seconds=timeout_seconds)


def quantity_statement(amount: str) -> dict[str, object]:
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "quantity",
                "value": {"amount": amount, "unit": "http://www.wikidata.org/entity/Q11573"},
            },
        },
    }


def annapurna_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    resume_selections: ResumeSelections | None = None,
):
    return live_executor(
        capabilities,
        policy,
        max_rows,
        transport=AnnapurnaTransport(),
        resume_selections=resume_selections,
    )


def test_single_confirmed_match_is_accepted_without_review_when_asked_and_marked():
    with TestClient(create_app(offline_executor_factory=annapurna_executor)) as client:
        body = {
            **request_body(upload(client, "mountains"), "mountains"),
            "mountain_resolution_policy": "verified_only",
        }
        plan = approve(client, client.post("/api/plans", json=body).json())
        job = wait(client, start(client, plan))
        report = client.get(job["artifacts"]["report"]).json()
        rows = {row["row"]: row for row in report["rows"]}
        # One confirmed summit, one unconfirmed namesake: filled, and flagged as such.
        assert rows[11]["status"] == "success"
        assert rows[11]["auto_accepted_steps"] == ["resolve"]
        assert rows[11]["user_chosen_steps"] == []
        # A clean unique match is an ordinary resolution and carries no flag.
        assert rows[2]["status"] == "success" and rows[2]["auto_accepted_steps"] == []
        review = client.get(f"/api/jobs/{job['job_id']}/review").json()
        assert "Annapurna" not in {item["query"]["name"] for item in review["items"]}
        workbook = load_workbook(BytesIO(client.get(job["artifacts"]["result"]).content))
        try:
            assert workbook["Sheet1"]["C11"].value == 8091
        finally:
            workbook.close()


def test_mountains_use_the_same_review_and_resume_routes():
    with TestClient(create_app(offline_executor_factory=annapurna_executor)) as client:
        _, first, review = reviewed_job(client, "mountains")
        assert [item["query"]["name"] for item in review["items"]] == ["Annapurna"]
        item = review["items"][0]
        assert item["capability"] == "resolve_entity" and item["provider_id"] == "wikidata"
        assert item["status"] == "incomplete_verification"
        assert [(link["row"], link["step_id"]) for link in item["affected"]] == [(11, "resolve")]
        listed = {c["entity_id"]: c for c in item["candidates"]}
        assert listed["Q9001"]["selectable"] and listed["Q9001"]["recommended"]
        assert listed["Q9001"]["recommendation_reasons"] == ["only_selectable_candidate"]
        assert not listed["Q9002"]["selectable"] and listed["Q9002"]["blocker"] == "unverified"
        endpoint = f"/api/jobs/{first['job_id']}/resume"
        unverified = {"operation_id": item["operation_id"], "source": "wikidata"}
        rejected = client.post(endpoint, json={"decisions": [{**unverified, "entity_id": "Q9002"}]})
        assert rejected.json()["error"]["code"] == "invalid_decision"
        accepted = client.post(endpoint, json={"decisions": [{**unverified, "entity_id": "Q9001"}]})
        assert accepted.status_code == 202, accepted.text
        second = wait(client, accepted.json())
        assert second["metrics"]["successful_rows"] == first["metrics"]["successful_rows"] + 1
        workbook = load_workbook(BytesIO(client.get(second["artifacts"]["result"]).content))
        try:
            assert workbook["Sheet1"]["C11"].value == 8091
        finally:
            workbook.close()


def test_a_running_job_is_never_evicted(monkeypatch):
    monkeypatch.setattr(plan_run, "MAX_JOBS", 1)
    monkeypatch.setattr(plan_run, "MAX_PLANS", 1)
    monkeypatch.setattr(api_module, "MAX_DATASETS", 1)
    with TestClient(create_app(offline_executor_factory=slow_executor)) as client:
        plan = approve(client, draft(client))
        running = start(client, plan)
        # Neither its plan nor its workbook can be pushed out while it runs.
        workbook = Path("samples/capitals.xlsx").read_bytes()
        blocked = client.post("/api/datasets", params={"filename": "next.xlsx"}, content=workbook)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "workspace_full"
        body = request_body(plan["dataset_id"])
        assert client.post("/api/plans", json=body).json()["error"]["code"] == "workspace_full"
        assert client.get(f"/api/plans/{plan['plan_id']}").status_code == 200
        client.post(f"/api/jobs/{running['job_id']}/cancel")
        wait(client, running)
        # Once it has finished, a new workbook replaces it with its plan and job.
        upload(client)
        assert client.get(f"/api/plans/{plan['plan_id']}").status_code == 404
        assert client.get(f"/api/jobs/{running['job_id']}").status_code == 404


def test_limits_state_what_one_run_accepts(client):
    limits = client.get("/api/limits").json()
    assert limits == {
        "max_run_rows": MAX_RUN_ROWS,
        "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
        "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
    # The stated bound is the enforced one.
    plan = draft(client)
    approve(client, plan)
    over = client.post(
        f"/api/plans/{plan['plan_id']}/runs",
        json={"timeout_seconds": limits["max_timeout_seconds"] + 1},
    )
    assert over.status_code == 422


def table(header, *rows):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), f"A1:{'ABCDE'[len(header) - 1]}{len(rows) + 1}"


def run_table(client, name, header, *rows):
    content, range_ref = table(header, *rows)
    dataset_id = upload(client, name, content)
    response = client.post(
        "/api/plans", json={**request_body(dataset_id, name), "range_ref": range_ref}
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    approve(client, plan)
    job = wait(client, start(client, plan))
    report = client.get(f"/api/jobs/{job['job_id']}/artifacts/report").json()
    codes = [
        {step["step_id"]: step["result"]["error_code"] for step in record["row_result"]["steps"]}
        for record in report["execution"]["records"]
    ]
    return job, report, codes


CAPITALS_HEADER = ("Capital_From", "Country_From", "Capital_To", "Country_To", "distance")
KNOWN_PAIR = ("London", "United Kingdom", "Rome", "Italy")


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (("London", "Atlantis", "Rome", "Italy"), "UNRECOGNIZED_COUNTRY"),
        (("London", "Unted Kingdom", "Rome", "Italy"), "UNRECOGNIZED_COUNTRY"),
        (("London", "Italy, France", "Rome", "Italy"), "UNRECOGNIZED_COUNTRY"),
        (("L" * 600, "Italy", "Rome", "Italy"), "NAME_TOO_LONG"),
        (("   ", "Italy", "Rome", "Italy"), "MISSING_VALUE"),
        (("London", " \t", "Rome", "Italy"), "MISSING_VALUE"),
    ],
)
def test_one_unusable_cell_costs_its_row_not_the_run(client, row, code):
    job, report, codes = run_table(client, "capitals", CAPITALS_HEADER, KNOWN_PAIR, row)
    assert job["status"] in {"completed", "needs_review"}
    assert job["metrics"]["total_rows"] == 2
    # The good row is computed as before, and the workbook is published.
    assert codes[0] == {"from_city": None, "to_city": None, "distance": None}
    assert "result" in job["artifacts"]
    # The other row says what was wrong with it; its other lookup still ran.
    assert codes[1] == {"from_city": code, "to_city": None, "distance": "DEPENDENCY_FAILED"}
    assert [entry["status"] for entry in report["rows"]] == ["success", "partial"]


def test_a_country_is_recognized_in_common_and_non_english_spellings(client):
    # The same known pair, with the countries written as people write them.
    job, _, codes = run_table(
        client,
        "capitals",
        CAPITALS_HEADER,
        ("London", "UK", "Rome", "Italien"),
        ("London", "Велика Британія", "Rome", "Італія"),
        ("London", "GBR", "Rome", " it "),
    )
    assert job["metrics"]["successful_rows"] == 3, codes


def test_an_unrecognized_country_is_audited_without_asking_the_source(client):
    job, report, _ = run_table(
        client, "capitals", CAPITALS_HEADER, KNOWN_PAIR, ("London", "Atlantis", "Rome", "Italy")
    )
    operations = report["execution"]["resolution_report"]["operations"]
    rejected = [
        operation
        for operation in operations
        if operation["report"]["decision"]["reason_codes"] == ["country_not_recognized"]
    ]
    assert len(rejected) == 1
    entry = rejected[0]["report"]
    assert entry["candidate_set"]["query"]["name"] == "London"
    context = entry["candidate_set"]["query"]["context"]
    assert [(item["name"], item["value"]) for item in context] == [("country", "Atlantis")]
    # Two requests for the good row; none for the rejected cell, and the
    # repeated "Rome" is answered from the run's memory.
    assert report["execution"]["provider_requests"]["open-meteo"]["transport_attempts"] == 2
    assert entry["candidate_set"]["candidates"] == []
    assert entry["candidate_set"]["provider_error_code"] is None
    assert entry["decision"]["status"] == "incomplete_verification"
    assert rejected[0]["record_links"] == [{"record_id": "Sheet1!3", "step_id": "from_city"}]
    # The review lists it for information: there is nothing to choose.
    review = client.get(f"/api/jobs/{job['job_id']}/review").json()
    listed = [item for item in review["informational"] if item["query"]["name"] == "London"]
    assert [item["reason_codes"] for item in listed] == [["country_not_recognized"]]
    assert all(item["query"]["name"] != "London" for item in review["items"])


def test_an_unusable_mountain_cell_costs_its_row_not_the_run(client):
    job, report, codes = run_table(
        client,
        "mountains",
        ("Mountain", "Country", "height"),
        ("Mount Everest", "Nepal/China"),
        ("Mount Elbrus", "Narnia"),
        ("K2", "Pakistan/"),
        ("M" * 600, "Nepal"),
    )
    assert job["status"] in {"completed", "needs_review"}
    assert codes[0]["resolve"] is None and codes[0]["elevation"] is None
    assert [row["resolve"] for row in codes[1:]] == [
        "UNRECOGNIZED_COUNTRY",
        "UNRECOGNIZED_COUNTRY",
        "NAME_TOO_LONG",
    ]
    assert "result" in job["artifacts"]


class PacedTransport(APITransportFixture):
    """The offline answers, each a little late, so a run can be watched."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        sleep(0.15)
        return super().get_json(url=url, params=params, timeout_seconds=timeout_seconds)


def paced_executor(capabilities: frozenset[str], policy: MountainPolicy, max_rows: int):
    return live_executor(capabilities, policy, max_rows, transport=PacedTransport())


def test_a_run_reports_how_many_rows_are_finished():
    with TestClient(create_app(offline_executor_factory=paced_executor)) as client:
        plan = draft(client)
        approve(client, plan)
        job = start(client, plan)
        # Nothing is known until the worker has read the table.
        assert job["progress"] is None
        seen = []
        current = job
        deadline = monotonic() + 30
        while monotonic() < deadline:
            current = client.get(f"/api/jobs/{job['job_id']}").json()
            if current["progress"] and current["progress"] not in seen:
                seen.append(current["progress"])
            if current["status"] in TERMINAL:
                break
            sleep(0.02)
        assert current["status"] in {"completed", "needs_review"}, current
        total = current["metrics"]["total_rows"]
        assert {step["rows_total"] for step in seen} == {total}
        done = [step["rows_done"] for step in seen]
        assert done == sorted(done)
        # Seen on the way, not only at the end.
        assert any(0 < value < total for value in done), done
        assert current["progress"] == {"rows_done": total, "rows_total": total}
        report = client.get(current["artifacts"]["report"]).json()
        assert report["progress"] == current["progress"]


def test_a_stopped_run_says_how_far_it_got():
    with TestClient(create_app(offline_executor_factory=paced_executor)) as client:
        plan = draft(client)
        approve(client, plan)
        job = start(client, plan)
        endpoint = f"/api/jobs/{job['job_id']}"
        deadline = monotonic() + 30
        while monotonic() < deadline:
            progress = client.get(endpoint).json()["progress"]
            if progress and progress["rows_done"] >= 1:
                break
            sleep(0.02)
        assert client.post(endpoint + "/cancel").status_code == 202
        final = wait(client, job)
        assert final["status"] == "cancelled"
        # The results are discarded; how far the run got is still true.
        assert "result" not in final["artifacts"]
        assert 1 <= final["progress"]["rows_done"] < final["progress"]["rows_total"]


class TwoAmbiguousCities(APITransportFixture):
    """Rome is ambiguous as well as Kyiv, so a review can take two rounds."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        payload = super().get_json(url=url, params=params, timeout_seconds=timeout_seconds)
        if isinstance(payload, dict) and params.get("name") == "Rome" and payload["results"]:
            twin = {**payload["results"][0], "id": 3169071, "latitude": 40.0, "longitude": 14.0}
            return {"results": [*payload["results"], twin]}
        return payload


def two_ambiguous_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    resume_selections=None,
):
    return live_executor(
        capabilities,
        policy,
        max_rows,
        transport=TwoAmbiguousCities(),
        resume_selections=resume_selections,
    )


def test_a_second_round_of_review_keeps_the_choices_of_the_first():
    with TestClient(create_app(offline_executor_factory=two_ambiguous_executor)) as client:
        content, range_ref = table(
            CAPITALS_HEADER,
            ("Kyiv", "Ukraine", "Paris", "France"),
            ("London", "United Kingdom", "Rome", "Italy"),
            ("Kyiv", "Ukraine", "Rome", "Italy"),
        )
        dataset_id = upload(client, "capitals", content)
        response = client.post(
            "/api/plans", json={**request_body(dataset_id, "capitals"), "range_ref": range_ref}
        )
        plan = approve(client, response.json())
        first = wait(client, start(client, plan))
        assert first["metrics"]["successful_rows"] == 0

        def items(job):
            listed = client.get(f"/api/jobs/{job['job_id']}/review").json()["items"]
            return {item["query"]["name"]: item for item in listed}

        def resume(job, name, entity_id):
            decision = {
                "operation_id": items(job)[name]["operation_id"],
                "source": "open-meteo",
                "entity_id": entity_id,
            }
            response = client.post(
                f"/api/jobs/{job['job_id']}/resume", json={"decisions": [decision]}
            )
            assert response.status_code == 202, response.text
            return wait(client, response.json())

        assert set(items(first)) == {"Kyiv", "Rome"}
        # Round one settles Kyiv only; Rome is left for later.
        second = resume(first, "Kyiv", "703448")
        assert second["status"] == "needs_review" and set(items(second)) == {"Rome"}
        assert second["metrics"]["successful_rows"] == 1
        # Round two settles Rome. Kyiv must still be the user's choice, not undecided again.
        third = resume(second, "Rome", "3169070")
        assert third["status"] == "completed", third
        assert third["metrics"]["successful_rows"] == 3
        assert third["previous_job_id"] == second["job_id"]
        audit = client.get(third["artifacts"]["report"]).json()["execution"]["resolution_report"]
        chosen = {
            operation["report"]["candidate_set"]["query"]["name"]: (
                operation["report"]["decision"]["basis"],
                operation["report"]["decision"]["selected"]["entity_id"],
            )
            for operation in audit["operations"]
            if operation["report"]["decision"]["basis"] == "user_override"
        }
        assert chosen == {"Kyiv": ("user_override", "703448"), "Rome": ("user_override", "3169070")}
        rows = {row["row"]: row for row in client.get(third["artifacts"]["report"]).json()["rows"]}
        assert rows[4]["user_chosen_steps"] == ["from_city", "to_city"]
        # Each job still starts from one earlier job and can be resumed on its own:
        # choosing differently from the first job does not inherit the later rounds.
        other = resume(first, "Rome", "3169071")
        assert other["metrics"]["successful_rows"] == 1 and set(items(other)) == {"Kyiv"}


def test_a_table_below_a_title_is_found_on_upload_and_runs_from_there(client):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet["B1"] = "REGIONAL DISTANCES"
    sheet["B2"] = "G6 and G7 are occupied on purpose."
    rows = (
        CAPITALS_HEADER,
        (*KNOWN_PAIR, None),
        ("London", "United Kingdom", "Paris", "France", 99999),
        ("Rome", "Italy", "Paris", "France", "=1+1"),
    )
    for offset, values in enumerate(rows):
        for column, value in zip("CDEFG", values, strict=True):
            sheet[f"{column}{4 + offset}"] = value
    buffer = BytesIO()
    workbook.save(buffer)

    uploaded = client.post(
        "/api/datasets", params={"filename": "offset.xlsx"}, content=buffer.getvalue()
    ).json()
    found = uploaded["worksheets"][0]
    assert found["table_range"] == "C4:G7"
    assert found["table"] == {"first_row": 4, "first_column": 3, "last_row": 7, "last_column": 7}
    assert found["columns"] == list(CAPITALS_HEADER) and found["row_count"] == 3

    # The found range is an ordinary selection: preview, plan and run accept it as it is.
    selection = {"sheet_name": "Sheet1", "range_ref": found["table_range"]}
    preview = client.post(f"/api/datasets/{uploaded['dataset_id']}/preview", json=selection)
    assert preview.status_code == 200, preview.text
    body = {
        **request_body(uploaded["dataset_id"]),
        **selection,
        "output_columns": {"distance": "G"},
    }
    plan = client.post("/api/plans", json=body).json()
    assert plan["validation"]["valid"], plan["validation"]
    job = wait(client, start(client, approve(client, plan)))
    assert job["status"] in {"completed", "needs_review"}, job
    result = load_workbook(BytesIO(client.get(job["artifacts"]["result"]).content))
    try:
        filled = result["Sheet1"]
        assert isinstance(filled["G5"].value, int | float)
        # Occupied output cells are kept as they were, wrong value and formula alike.
        assert filled["G6"].value == 99999 and filled["G7"].value == "=1+1"
        assert filled["B1"].value == "REGIONAL DISTANCES" and filled["A5"].value is None
    finally:
        result.close()


class OtherSpellings(APITransportFixture):
    """Warsaw is known under its English name only."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if params.get("name") == "Warsaw" and params.get("countryCode") == "PL":
            return {
                "results": [
                    {
                        "id": 756135,
                        "name": "Warsaw",
                        "country_code": "PL",
                        "latitude": 52.22977,
                        "longitude": 21.01178,
                        "feature_code": "PPLC",
                    }
                ]
            }
        return super().get_json(url=url, params=params, timeout_seconds=timeout_seconds)


def other_spellings_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    resume_selections=None,
):
    return live_executor(
        capabilities,
        policy,
        max_rows,
        transport=OtherSpellings(),
        resume_selections=resume_selections,
    )


@pytest.fixture
def other_names():
    """A job whose two names the source does not know as written."""
    with TestClient(create_app(offline_executor_factory=other_spellings_executor)) as client:
        content, range_ref = table(
            CAPITALS_HEADER,
            ("Varsovie", "Poland", "Paris", "France"),
            ("Kiev", "Ukraine", "London", "United Kingdom"),
            ("Paris", "France", "Varsovie", "Poland"),
        )
        dataset_id = upload(client, "capitals", content)
        response = client.post(
            "/api/plans", json={**request_body(dataset_id, "capitals"), "range_ref": range_ref}
        )
        plan = approve(client, response.json())
        job = wait(client, start(client, plan))
        yield client, job


def listed(client, job, group):
    review = client.get(f"/api/jobs/{job['job_id']}/review").json()
    return {item["query"]["name"]: item for item in review[group]}


def test_a_name_the_source_does_not_know_can_be_searched_under_another(other_names):
    client, first = other_names
    assert first["status"] == "needs_review" and first["metrics"]["successful_rows"] == 0
    # Nothing to choose from, yet both names can be given another spelling.
    assert listed(client, first, "items") == {}
    unknown = listed(client, first, "informational")
    assert set(unknown) == {"Varsovie", "Kiev"}
    assert all(
        item["other_name"] and item["query"]["searched_as"] is None for item in unknown.values()
    )

    decisions = [
        {"operation_id": unknown["Varsovie"]["operation_id"], "name": " Warsaw "},
        {"operation_id": unknown["Kiev"]["operation_id"], "name": "Kyiv"},
    ]
    response = client.post(f"/api/jobs/{first['job_id']}/resume", json={"decisions": decisions})
    assert response.status_code == 202, response.text
    second = wait(client, response.json())

    # Warsaw has one match and is filled in both rows; Kyiv has two and waits for a choice.
    assert second["status"] == "needs_review" and second["metrics"]["successful_rows"] == 2
    report = client.get(second["artifacts"]["report"]).json()
    assert report["review_decisions"] == [
        {**decision, "source": None, "entity_id": None} for decision in decisions
    ]
    audit = {
        operation["report"]["candidate_set"]["query"]["name"]: operation
        for operation in report["execution"]["resolution_report"]["operations"]
    }
    warsaw = audit["Varsovie"]
    # The audit keeps the row's own value and the name the source was asked for.
    assert warsaw["report"]["candidate_set"]["query"]["searched_as"] == "Warsaw"
    assert warsaw["report"]["decision"]["basis"] == "unique_verified"
    assert warsaw["report"]["decision"]["selected"]["entity_id"] == "756135"
    assert len(warsaw["record_links"]) == 2
    rows = {row["row"]: row for row in report["rows"]}
    # A value found under the user's name is marked as the user's decision.
    assert rows[2]["user_chosen_steps"] == ["from_city"]
    assert rows[4]["user_chosen_steps"] == ["to_city"]
    assert rows[3]["user_chosen_steps"] == []

    kiev = listed(client, second, "items")["Kiev"]
    assert kiev["query"]["searched_as"] == "Kyiv" and kiev["status"] == "needs_review"
    assert {c["entity_id"] for c in kiev["candidates"]} == {"703448", "704213"}

    # Round two: a candidate found under the other name is chosen like any other,
    # and the name given for Warsaw is still in force.
    chosen = {"operation_id": kiev["operation_id"], "source": "open-meteo", "entity_id": "703448"}
    response = client.post(f"/api/jobs/{second['job_id']}/resume", json={"decisions": [chosen]})
    assert response.status_code == 202, response.text
    third = wait(client, response.json())
    assert third["status"] == "completed" and third["metrics"]["successful_rows"] == 3
    final = client.get(third["artifacts"]["report"]).json()
    decided = {
        operation["report"]["candidate_set"]["query"]["name"]: (
            operation["report"]["candidate_set"]["query"]["searched_as"],
            operation["report"]["decision"]["basis"],
        )
        for operation in final["execution"]["resolution_report"]["operations"]
        if operation["report"]["candidate_set"]["query"]["searched_as"]
    }
    assert decided == {"Varsovie": ("Warsaw", "unique_verified"), "Kiev": ("Kyiv", "user_override")}


def test_another_name_that_finds_nothing_leaves_the_row_empty(other_names):
    client, first = other_names
    unknown = listed(client, first, "informational")
    decision = {"operation_id": unknown["Varsovie"]["operation_id"], "name": "Warschau"}
    response = client.post(f"/api/jobs/{first['job_id']}/resume", json={"decisions": [decision]})
    second = wait(client, response.json())
    assert second["status"] == "needs_review" and second["metrics"]["successful_rows"] == 0
    again = listed(client, second, "informational")["Varsovie"]
    # Still the row's value, with the name that was tried; another one can be given.
    assert again["query"]["searched_as"] == "Warschau" and again["other_name"]
    assert again["status"] == "unresolved"


@pytest.mark.parametrize(
    "decision",
    [
        {"name": "varsovie"},  # The row's own value.
        {"name": "   "},
        {"operation_id": "unknown", "name": "Warsaw"},
    ],
)
def test_another_name_is_checked_before_a_job_starts(other_names, decision):
    client, first = other_names
    operation_id = listed(client, first, "informational")["Varsovie"]["operation_id"]
    endpoint = f"/api/jobs/{first['job_id']}/resume"
    response = client.post(
        endpoint, json={"decisions": [{"operation_id": operation_id, **decision}]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_decision"
    assert "Sheet1" not in response.text


def test_another_name_needs_its_own_search_and_cannot_reopen_a_settled_value(client):
    _, job, review = reviewed_job(client)
    endpoint = f"/api/jobs/{job['job_id']}/resume"
    kyiv = review["items"][0]["operation_id"]
    # A candidate with a name must come from a search under that name.
    both = {"operation_id": kyiv, "source": "open-meteo", "entity_id": "703448", "name": "Kiev"}
    unsearched = client.post(endpoint, json={"decisions": [both]})
    assert unsearched.status_code == 422
    assert unsearched.json()["error"]["code"] == "invalid_decision"
    report = client.get(job["artifacts"]["report"]).json()
    settled = next(
        operation["operation_id"]
        for operation in report["execution"]["resolution_report"]["operations"]
        if operation["report"]["decision"]["status"] == "resolved"
    )
    response = client.post(endpoint, json={"decisions": [{"operation_id": settled, "name": "X"}]})
    assert response.json()["error"]["code"] == "invalid_decision"


def search(client, job, item, name):
    return client.post(
        f"/api/jobs/{job['job_id']}/review/{item['operation_id']}/search", json={"name": name}
    )


def test_a_name_is_searched_at_once_and_the_chosen_match_fills_the_rows_in_one_round(other_names):
    client, first = other_names
    unknown = listed(client, first, "informational")
    before = client.get(f"/api/jobs/{first['job_id']}/review").json()

    kyiv = search(client, first, unknown["Kiev"], " Kyiv ")
    assert kyiv.status_code == 200, kyiv.text
    kyiv = kyiv.json()
    # The answer has the shape of a review item: the row's value, the name
    # searched and what the source confirmed for it.
    assert kyiv["operation_id"] == unknown["Kiev"]["operation_id"]
    assert kyiv["query"]["name"] == "Kiev" and kyiv["query"]["searched_as"] == "Kyiv"
    assert kyiv["status"] == "needs_review" and kyiv["affected"] == unknown["Kiev"]["affected"]
    assert {c["entity_id"] for c in kyiv["candidates"] if c["selectable"]} == {"703448", "704213"}

    warsaw = search(client, first, unknown["Varsovie"], "Warsaw").json()
    assert warsaw["status"] == "resolved"
    assert [(c["entity_id"], c["selectable"]) for c in warsaw["candidates"]] == [("756135", True)]

    nothing = search(client, first, unknown["Varsovie"], "Warschau").json()
    assert nothing["status"] == "unresolved" and nothing["candidates"] == []
    # A search decides nothing: the job and its review are as they were.
    assert client.get(f"/api/jobs/{first['job_id']}/review").json() == before

    endpoint = f"/api/jobs/{first['job_id']}/resume"
    pick_warsaw = {
        "operation_id": unknown["Varsovie"]["operation_id"],
        "source": "open-meteo",
        "entity_id": "756135",
        "name": "Warsaw",
    }
    # "Warschau" replaced the search for "Warsaw": only the latest one counts.
    stale = client.post(endpoint, json={"decisions": [pick_warsaw]})
    assert stale.status_code == 422 and stale.json()["error"]["code"] == "invalid_decision"
    search(client, first, unknown["Varsovie"], "Warsaw")

    decisions = [
        pick_warsaw,
        {
            "operation_id": unknown["Kiev"]["operation_id"],
            "source": "open-meteo",
            "entity_id": "703448",
            "name": "Kyiv",
        },
    ]
    response = client.post(endpoint, json={"decisions": decisions})
    assert response.status_code == 202, response.text
    second = wait(client, response.json())
    assert second["status"] == "completed" and second["metrics"]["successful_rows"] == 3
    report = client.get(second["artifacts"]["report"]).json()
    assert report["review_decisions"] == decisions
    decided = {
        operation["report"]["candidate_set"]["query"]["name"]: (
            operation["report"]["candidate_set"]["query"]["searched_as"],
            operation["report"]["decision"]["basis"],
            operation["report"]["decision"]["selected"]["entity_id"],
        )
        for operation in report["execution"]["resolution_report"]["operations"]
        if operation["report"]["candidate_set"]["query"]["searched_as"]
    }
    # Both are the user's choices, verified again by the job under the given name.
    assert decided == {
        "Varsovie": ("Warsaw", "user_override", "756135"),
        "Kiev": ("Kyiv", "user_override", "703448"),
    }
    rows = {row["row"]: row for row in report["rows"]}
    assert rows[2]["user_chosen_steps"] == ["from_city"]
    assert rows[3]["user_chosen_steps"] == ["from_city"]


@pytest.mark.parametrize("name", ["varsovie", "   ", "W" * 513])
def test_a_search_is_refused_for_a_name_that_cannot_help(other_names, name):
    client, first = other_names
    response = search(client, first, listed(client, first, "informational")["Varsovie"], name)
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"invalid_search", "invalid_request"}


def test_a_search_needs_a_reviewable_item_and_stays_within_its_limit(other_names, monkeypatch):
    client, first = other_names
    varsovie = listed(client, first, "informational")["Varsovie"]
    unknown = search(client, first, {"operation_id": "unknown"}, "Warsaw")
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "invalid_search"
    assert search(client, {"job_id": "missing"}, varsovie, "Warsaw").status_code == 404
    monkeypatch.setattr(plan_run, "MAX_REVIEW_SEARCHES", 1)
    assert search(client, first, varsovie, "Warsaw").status_code == 200
    limited = search(client, first, varsovie, "Warszawa")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "search_limit"
