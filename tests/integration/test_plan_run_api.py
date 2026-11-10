"""Offline HTTP → real planner/validator/providers/executor → safe XLSX tests."""

import json
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

from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import FixturePlanClient, offline_executor
from ai_data_enrichment.plan_run import TERMINAL, PlanRunSettings
from ai_data_enrichment.plan_run_worker import MountainPolicy
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport


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
    "model,code", [(None, "llm_not_configured"), ("test-model", "missing_api_key")]
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
    monkeypatch.setattr(plan_run, "MAX_PLANS", 1)
    draft(client)
    body = request_body(upload(client))
    response = client.post("/api/plans", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_full"


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
    response = client.post(f"/api/plans/{plan['plan_id']}/runs", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_full"


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
