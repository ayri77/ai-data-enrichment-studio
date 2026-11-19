"""What the usage file holds after a whole plan, run and download: numbers and fixed words."""

import io
import json
import logging
import zipfile
from email.message import EmailMessage
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_extraction_api import FOUND, NOT_ABOUT_IT, mail_zip
from tests.support.extraction_api import RECEIPT, SENTINEL, LoggedExtractionApiFactory

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


MAIL_MARKERS = (
    "MARKER-MAIL-SUBJECT",
    "MARKER-MAIL-SENDER",
    "MARKER-MAIL-BODY",
    "MARKER-MAIL-FILE",
    "MARKER-MAIL-ID",
    "MARKER-MAIL-ATTACHMENT",
    "MARKER-MAIL-HEADER",
    "marker-mail-address",
)


def marked_mail(number: int) -> bytes:
    message = EmailMessage()
    message["From"] = f"MARKER-MAIL-SENDER <marker-mail-address-{number}@example.com>"
    message["To"] = "owner@example.org"
    message["Subject"] = f"MARKER-MAIL-SUBJECT {number}"
    message["Date"] = f"Mon, 0{number} Nov 2026 10:00:00 +0000"
    message["Message-ID"] = f"<MARKER-MAIL-ID-{number}@example.com>"
    message["X-Marker"] = "MARKER-MAIL-HEADER"
    message.set_content(f"MARKER-MAIL-BODY {number}")
    message.add_attachment(
        b"MARKER-MAIL-ATTACHMENT", maintype="application", subtype="pdf", filename="a.pdf"
    )
    return message.as_bytes()


def marked_zip(count: int = 2) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for number in range(1, count + 1):
            archive.writestr(f"MARKER-MAIL-FILE-{number}.eml", marked_mail(number))
        archive.writestr("MARKER-MAIL-FILE-broken.eml", b"\x00\x01")
        archive.writestr("MARKER-MAIL-FILE-other.txt", b"x")
    return buffer.getvalue()


def test_a_mail_import_leaves_no_trace_of_the_mails_in_usage_logs_errors_or_files(
    tmp_path: Path, caplog
) -> None:
    kept = tmp_path / "usage" / "events.jsonl"
    app = create_app(
        settings=PlanRunSettings(allow_mail=True, usage_file=str(kept)),
        local_browser_configuration=True,
    )
    caplog.set_level(logging.DEBUG)
    answers = []
    with TestClient(app) as client:
        imported = client.post(
            "/api/mail-datasets", params={"filename": "MARKER-MAIL-FILE.zip"}, content=marked_zip()
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["mail"]["read"] == 2 and imported.json()["mail"]["skipped"] == 2
        answers.append(imported.text)
        # Every refusal of the route, each with marked names inside the upload.
        refusals = [
            ("MARKER-MAIL-FILE.zip", b"MARKER-MAIL-BODY not a zip"),
            ("MARKER-MAIL-FILE.eml", b"MARKER-MAIL-BODY"),
            ("MARKER-MAIL-FILE.txt", marked_mail(1)),
            ("MARKER-MAIL-FILE.zip", zip_bytes({"MARKER-MAIL-FILE.eml": b"\0" * 11_000_000})),
            ("MARKER-MAIL-FILE.zip", zip_bytes({"MARKER-MAIL-FILE.txt": b"MARKER-MAIL-BODY"})),
            (
                "MARKER-MAIL-FILE.zip",
                zip_bytes({f"MARKER-MAIL-FILE-{i}.eml": b"\0" * 9_000_000 for i in range(8)}),
            ),
            (
                "MARKER-MAIL-FILE.zip",
                zip_bytes({f"MARKER-MAIL-FILE-{i}.eml": marked_mail(1) for i in range(501)}),
            ),
        ]
        for name, content in refusals:
            answer = client.post("/api/mail-datasets", params={"filename": name}, content=content)
            assert answer.status_code >= 400
            answers.append(answer.text)
        summary = client.get("/api/usage").text
        directory = app.state.directory
        files = [p for p in directory.rglob("*") if p.is_file()]
        assert len(files) == 1 and files[0].suffix == ".xlsx"
        with zipfile.ZipFile(files[0]) as workbook:
            stored = b"".join(workbook.read(name) for name in workbook.namelist())

    # In the workbook: the four columns only. Nothing of the headers, id or attachment.
    for absent in (
        "MARKER-MAIL-ID",
        "MARKER-MAIL-HEADER",
        "MARKER-MAIL-ATTACHMENT",
        "MARKER-MAIL-FILE",
    ):
        assert absent.encode() not in stored, absent
    assert b"MARKER-MAIL-SUBJECT" in stored and b"MARKER-MAIL-BODY" in stored

    usage = kept.read_text("utf-8")
    events = [json.loads(line) for line in usage.splitlines()]
    assert [event["kind"] for event in events] == ["mail_import"]
    # The test client logs the URL it was given itself; the application's own lines are meant.
    logged = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )
    for text in (usage, summary, logged, *answers):
        for marker in MAIL_MARKERS:
            assert marker not in text, marker
        assert "owner@example.org" not in text


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


EXTRACTION_MARKERS = (
    "MARKER-TASK-TEXT",
    "MARKER-COLUMN-AMOUNT",
    "MARKER-COLUMN-PAID",
    "MARKER-COLUMN-RENEWS",
    "MARKER-FILE-NAME",
    SENTINEL,
    RECEIPT,
    "Paid on 3 Nov 2026",
    "Receipt FIRST",
    "shop@example.com",
)


def test_an_extraction_run_leaves_only_counts_and_fixed_words_in_the_usage_file_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-not-a-secret-0123456789")
    caplog.set_level(logging.DEBUG)
    kept = tmp_path / "usage" / "events.jsonl"
    app = create_app(
        settings=PlanRunSettings(allow_mail=True, usage_file=str(kept)),
        local_browser_configuration=True,
        offline_executor_factory=offline_executor,
        offline_extraction_api=LoggedExtractionApiFactory(
            "sk-test-key-not-a-secret-0123456789", tmp_path / "requests.jsonl"
        ),
        usage_lines=True,
    )
    with TestClient(app) as client:
        dataset = client.post(
            "/api/mail-datasets",
            params={"filename": "MARKER-FILE-NAME.zip"},
            content=mail_zip(FOUND + NOT_ABOUT_IT),
        ).json()
        created = client.post(
            "/api/extraction-plans",
            json={
                "dataset_id": dataset["dataset_id"],
                "question": "MARKER-TASK-TEXT: what was paid?",
                "text_column": "Text",
                "targets": [
                    {"column": "MARKER-COLUMN-AMOUNT", "kind": "number"},
                    {"column": "MARKER-COLUMN-PAID", "kind": "date"},
                    {"column": "MARKER-COLUMN-RENEWS", "kind": "yes_no"},
                ],
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        approved = client.post(
            f"/api/plans/{draft['plan_id']}/approve",
            json={"approved": True, "fingerprint": draft["fingerprint"]},
        )
        assert approved.status_code == 200, approved.text
        job = client.post(f"/api/plans/{draft['plan_id']}/runs", json={}).json()
        for _ in range(1200):
            job = client.get(f"/api/jobs/{job['job_id']}").json()
            if job["status"] in {"completed", "needs_review", "failed"}:
                break
        assert job["status"] == "completed", job
        assert client.get(job["artifacts"]["result"]).status_code == 200
        body = client.get("/api/usage").json()

    text = kept.read_text("utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    assert [event["kind"] for event in events] == [
        "mail_import",
        "plan",
        "run_started",
        "model",
        "run_finished",
        "download",
    ]
    assert events == list(reversed(body["recent"]))
    plan, model, finished = events[1], events[3], events[4]
    assert (plan["source"], plan["capabilities"]) == ("extraction_plan", ["text_extraction"])
    assert (model["purpose"], model["model"], model["requests"]) == (
        "text_extraction",
        "gpt-6-luna",
        3,
    )
    assert (finished["status"], finished["rows"], finished["rows_filled"]) == ("completed", 3, 2)
    logged = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )
    for secret in (
        *EXTRACTION_MARKERS,
        dataset["dataset_id"],
        job["job_id"],
        draft["plan_id"],
        draft["fingerprint"],
        "sk-test-key",
        "testclient",
    ):
        assert secret not in text, secret
        assert secret not in logged, secret
        assert secret not in json.dumps(body), secret
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
