"""What the usage file holds after a whole plan, run and download: numbers and fixed words."""

import io
import json
from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.plan_run import PlanRunSettings

MARKERS = ("MARKER-FILE-NAME", "MARKER-TASK-TEXT", "MARKER-CELL-VALUE", "MARKER-HEADER")


def marked_workbook() -> bytes:
    """The Capitals example with one more column whose header and cells are markers."""
    book = openpyxl.load_workbook("samples/capitals.xlsx")
    sheet = book.active
    assert sheet is not None
    sheet["F1"] = "MARKER-HEADER"
    for row in range(2, 12):
        sheet.cell(row=row, column=6, value=f"MARKER-CELL-VALUE-{row}")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_the_usage_file_holds_nothing_but_what_the_events_hold(tmp_path: Path) -> None:
    kept = tmp_path / "usage" / "events.jsonl"
    app = create_app(
        settings=PlanRunSettings(usage_file=str(kept)),
        offline_executor_factory=offline_executor,
        local_browser_configuration=True,
    )
    with TestClient(app) as client:
        sample = client.get("/api/samples/capitals/plan").json()
        dataset = client.post(
            "/api/datasets",
            params={"filename": "MARKER-FILE-NAME.xlsx"},
            content=marked_workbook(),
        ).json()
        plan = client.post(
            "/api/plans",
            json={
                "dataset_id": dataset["dataset_id"],
                "sheet_name": "Sheet1",
                "range_ref": "A1:F11",
                "instruction": "MARKER-TASK-TEXT: how far is each capital from the other?",
                "output_columns": {"distance": "E"},
                "plan": sample,
            },
        )
        assert plan.status_code in {200, 201}, plan.text
        draft = plan.json()
        approved = client.post(
            f"/api/plans/{draft['plan_id']}/approve",
            json={"approved": True, "fingerprint": draft["fingerprint"]},
        )
        assert approved.status_code == 200, approved.text
        job = client.post(
            f"/api/plans/{draft['plan_id']}/runs", json={"timeout_seconds": 60}
        ).json()
        for _ in range(600):
            job = client.get(f"/api/jobs/{job['job_id']}").json()
            if job["status"] in {"completed", "needs_review", "failed"}:
                break
        assert job["status"] == "needs_review"
        assert client.get(job["artifacts"]["result"]).status_code == 200
        body = client.get("/api/usage").json()
        assert body["persistent"] is True

    text = kept.read_text("utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    kinds = [event["kind"] for event in events]
    assert kinds == ["upload", "plan", "run_started", "run_finished", "download"]
    # The very events the page shows, and nothing besides.
    assert events == list(reversed(body["recent"]))
    for secret in (
        *MARKERS,
        "Kyiv",
        "London",
        "Capital_From",
        ".xlsx",
        "how far",
        dataset["dataset_id"],
        job["job_id"],
        draft["plan_id"],
        draft["fingerprint"],
        "testclient",
        "127.0.0.1",
    ):
        assert secret not in text, secret
    # Every value of every line is a number, a flag, nothing or one short word.
    for event in events:
        for value in event.values():
            members = value.values() if isinstance(value, dict) else [value]
            for member in members if not isinstance(value, list) else value:
                assert (
                    member is None
                    or isinstance(member, bool | int | float)
                    or (isinstance(member, str) and len(member) <= 64 and " " not in member)
                )

    # A new process reads the same numbers from the file.
    again = create_app(
        settings=PlanRunSettings(usage_file=str(kept)), local_browser_configuration=True
    )
    with TestClient(again) as client:
        replayed = client.get("/api/usage").json()
    assert replayed["events"] == body["events"] and replayed["visitors"] == body["visitors"]
    assert (
        replayed["since"] == body["since"] and replayed["since"] <= replayed["process_started_at"]
    )
