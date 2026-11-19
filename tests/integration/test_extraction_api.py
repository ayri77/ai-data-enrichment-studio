"""The text extraction through the HTTP API and the run worker, offline.

Upload tables of mail (``/api/mail-datasets``), make a plan (``/api/extraction-plans``),
approve, run and download. The routes, the gates, the worker process, the real handler, the
reply reader and the workbook writer are real. Only the model client is fake, and it is made
INSIDE the worker (``tests.support.extraction_api``): no real OpenAI client makes a request
here. The mails are invented (``example.com``).
"""

import io
import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from tests.support.extraction_api import (
    RECEIPT,
    SENTINEL,
    LeakyExtractionFactory,
    LoggedExtractionApiFactory,
    calls,
    forbidden_extraction_api,
)
from tests.support.lookup_api import LoggedApiFactory

from ai_data_enrichment import plan_run, plan_run_worker
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.plan_run import (
    TERMINAL,
    PlanRunService,
    PlanRunSettings,
    draft_fingerprint,
)
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.run_limits import MAX_TIMEOUT_SECONDS

KEY = "sk-test-key-not-a-secret-0123456789"
CONFIGURATION = "openai-text-extraction-p1-gpt-6-luna-low"
TARGETS = [
    {"column": "Amount", "kind": "number"},
    {"column": "Paid on", "kind": "date"},
    {"column": "Renews", "kind": "yes_no"},
]
# The mails and what the fake model says about each (see ``sentinel_answer``).
FOUND = ("Receipt FIRST", "Receipt SECOND")
NOT_ABOUT_IT = ("Advertisement",)
NOTHING = ("Quiet note", "Broken quote")
PROVIDER_FAILURE = ("Server trouble",)


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("An offline test attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)
    monkeypatch.setattr(UrllibJsonTransport, "post_json", denied)
    # A run that did not get the test's fake would reach this, and fail, not the network.
    monkeypatch.setattr(plan_run, "openai_extraction_api", forbidden_extraction_api)
    monkeypatch.setattr(plan_run, "openai_api", forbidden_extraction_api)
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def mail(subject: str, number: int, *lines: str) -> bytes:
    """An invented mail whose body starts with the sentinel sentence, outside any quote."""
    message = EmailMessage()
    message["From"] = "Shop Example <shop@example.com>"
    message["To"] = "reader@example.org"
    message["Subject"] = subject
    message["Date"] = f"Mon, {number:02d} Nov 2026 10:00:00 +0000"
    message["Message-ID"] = f"<{number}@example.com>"
    message.set_content("\n".join([f"{SENTINEL}.", "", *lines]))
    return message.as_bytes()


BODIES: dict[str, tuple[str, ...]] = {
    "Receipt FIRST": (RECEIPT, "Paid on 3 Nov 2026", "Renews on 01.12.2026"),
    "Receipt SECOND": ("Total: 20,00 EUR",),
    "Advertisement": ("Big sale this week only.",),
    "Quiet note": ("Thank you for being with us.",),
    "Broken quote": ("Your order is on its way.",),
    "Server trouble": ("A mail that makes the fake service fail.",),
}


def mail_zip(subjects: tuple[str, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, subject in enumerate(subjects, 1):
            archive.writestr(f"{number:02d}.eml", mail(subject, number, *BODIES[subject]))
    return buffer.getvalue()


def plain_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Notes"
    sheet.append(["Id", "Text", "Amount"])
    sheet.append([1, f"Receipt FIRST. {SENTINEL}. {RECEIPT}. Paid on 3 Nov 2026", None])
    sheet.append([2, f"Advertisement. {SENTINEL}.", None])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@dataclass
class World:
    client: TestClient
    service: PlanRunService
    log: Path

    @property
    def directory(self) -> Path:
        return self.client.app.state.directory  # type: ignore[attr-defined]

    def mails(self, subjects: tuple[str, ...]) -> str:
        result = self.client.post(
            "/api/mail-datasets", params={"filename": "m.zip"}, content=mail_zip(subjects)
        )
        assert result.status_code == 201, result.text
        return result.json()["dataset_id"]

    def plain(self) -> str:
        result = self.client.post(
            "/api/datasets", params={"filename": "notes.xlsx"}, content=plain_workbook()
        )
        assert result.status_code == 201, result.text
        return result.json()["dataset_id"]

    def create(self, dataset_id: str, **changes: Any):
        return self.client.post("/api/extraction-plans", json=request_body(dataset_id, **changes))

    def draft(self, subjects: tuple[str, ...] = FOUND, **changes: Any) -> dict[str, Any]:
        result = self.create(self.mails(subjects), **changes)
        assert result.status_code == 201, result.text
        return result.json()

    def approve(self, draft: dict[str, Any]) -> dict[str, Any]:
        result = self.client.post(
            f"/api/plans/{draft['plan_id']}/approve",
            json={"approved": True, "fingerprint": draft["fingerprint"]},
        )
        assert result.status_code == 200, result.text
        return result.json()

    def start(self, draft: dict[str, Any], **body: Any):
        return self.client.post(f"/api/plans/{draft['plan_id']}/runs", json=body)

    def run(self, draft: dict[str, Any], **body: Any) -> dict[str, Any]:
        self.approve(draft)
        started = self.start(draft, **body)
        assert started.status_code == 202, started.text
        return self.wait(started.json())

    def wait(self, job: dict[str, Any], seconds: float = 90) -> dict[str, Any]:
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            current = self.client.get(f"/api/jobs/{job['job_id']}").json()
            if current["status"] in TERMINAL:
                return current
            sleep(0.05)
        pytest.fail("The worker did not finish in time")

    def report(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(job["artifacts"]["report"]).json()

    def download(self, job: dict[str, Any]) -> bytes:
        answer = self.client.get(job["artifacts"]["result"])
        assert answer.status_code == 200
        return answer.content


def request_body(dataset_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "question": "Payments: what was paid, when, and does it renew?",
        "text_column": "Text",
        "targets": TARGETS,
        **changes,
    }


@contextmanager
def world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mail: bool = True,
    local: bool = True,
    key: str | None = KEY,
    delay: float = 0.0,
    factory: Any = "logged",
    network: bool = False,
    offline: bool = True,
    usage_file: Path | None = None,
) -> Iterator[World]:
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    log = tmp_path / "requests.jsonl"
    if factory == "logged":
        factory = LoggedExtractionApiFactory(KEY, log, delay=delay)
    app = create_app(
        settings=PlanRunSettings(
            allow_mail=mail,
            allow_network=network,
            usage_file=str(usage_file) if usage_file else "off",
        ),
        local_browser_configuration=local,
        offline_executor_factory=offline_executor if offline else None,
        offline_extraction_api=factory,
    )
    with TestClient(app) as client:
        yield World(client, app.state.plan_run, log)


def codes_of(w: World, job: dict[str, Any]) -> dict[str, Any]:
    return {
        row["record_id"]: row["row_result"]["steps"][0]["result"]["error_code"]
        for row in w.report(job)["execution"]["records"]
    }


# --- The setting, the key and the limits ----------------------------------------------


def test_the_model_setting_is_read_like_the_web_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENRICHMENT_TEXT_EXTRACTION_MODEL", raising=False)
    assert PlanRunSettings.from_environment().text_extraction_model == "gpt-6-luna"
    monkeypatch.setenv("ENRICHMENT_TEXT_EXTRACTION_MODEL", " other-model ")
    assert PlanRunSettings.from_environment().text_extraction_model == "other-model"
    monkeypatch.setenv("ENRICHMENT_TEXT_EXTRACTION_MODEL", "two words")
    with pytest.raises(RuntimeError, match="ENRICHMENT_TEXT_EXTRACTION_MODEL"):
        PlanRunSettings.from_environment()
    with pytest.raises(ValueError, match="text extraction model"):
        PlanRunSettings(text_extraction_model="two words")


@pytest.mark.parametrize(
    "options",
    [
        {"mail": False},  # tables of mail are not offered
        {"local": False},  # a hosted app, whatever the environment says
        {"key": None},  # no OpenAI key
        {"factory": None},  # an offline application given no stand-in client
    ],
)
def test_it_is_offered_only_with_tables_of_mail_and_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    with world(tmp_path, monkeypatch, **options) as w:
        assert w.client.get("/api/limits").json()["text_extraction"] is False
        dataset_id = w.plain()

        def profiled(*args: object, **kwargs: object) -> None:
            pytest.fail("The table was read although extraction is not offered")

        monkeypatch.setattr(plan_run, "profile_worksheet", profiled)
        # An invalid body is refused with 403 too: the gate comes before the body is read.
        for body in (request_body(dataset_id), {"nonsense": 1}):
            refused = w.client.post("/api/extraction-plans", json=body)
            assert refused.status_code == 403, refused.text
            assert refused.json()["error"] == {
                "code": "extraction_not_allowed",
                "message": "Reading values out of texts is not available on this server.",
            }
        assert w.service.plans == {}
        assert not w.log.exists()


def test_limits_say_yes_with_mail_a_key_and_for_a_live_server_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        limits = w.client.get("/api/limits").json()
        assert limits["text_extraction"] is True and limits["mail"] is True
        # It is not a capability the planner or a client is told about.
        assert "text_extraction" not in limits["capabilities"]
        listed = w.client.get("/api/capabilities").json()
        assert "text_extraction" not in json.dumps(listed)
    # A live server: the network gate that every live run has applies.
    with world(tmp_path, monkeypatch, offline=False, factory=None) as w:
        assert w.client.get("/api/limits").json()["text_extraction"] is False
    with world(tmp_path, monkeypatch, offline=False, factory=None, network=True) as w:
        assert w.client.get("/api/limits").json()["text_extraction"] is True


# --- The route ------------------------------------------------------------------------


def test_a_draft_of_kind_extraction_is_built_by_the_server_for_a_table_of_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.mails(FOUND + NOT_ABOUT_IT)
        before = w.service.datasets[dataset_id].read_bytes()
        created = w.create(dataset_id)
        assert created.status_code == 201, created.text
        draft = created.json()

        assert draft["kind"] == "extraction"
        assert draft["status"] == "draft" and draft["planning_status"] == "ready"
        assert draft["planning_attempts"] == [] and draft["unfilled_targets"] == []
        assert (draft["sheet_name"], draft["range_ref"]) == ("Mail", "A1:D4")
        # The columns after the table, in the order of the request.
        assert draft["output_columns"] == {"Amount": "E", "Paid on": "F", "Renews": "G"}
        assert draft["instruction"] == "Payments: what was paid, when, and does it renew?"
        assert draft["sources_in_workbook"] == "notes" and draft["existing_outputs"] == "keep"
        # A table the application built from mail: the result has its text column emptied.
        assert draft["text_column_emptied"] is True
        assert draft["validation"]["valid"], draft["validation"]
        [step] = draft["plan"]["steps"]
        assert step["capability"] == "text_extraction"
        assert step["inputs"]["text"] == {"kind": "column", "name": "Text"}
        [assumption] = draft["plan"]["assumptions"]
        assert "one request per row" in assumption and "not verified" in assumption
        # The kind is inside what the fingerprint covers; the dataset was not touched.
        assert len(draft["fingerprint"]) == 64
        assert draft_fingerprint(draft) == draft["fingerprint"]
        assert draft_fingerprint({**draft, "kind": "fallback"}) != draft["fingerprint"]
        assert w.service.datasets[dataset_id].read_bytes() == before
        assert w.client.get(f"/api/plans/{draft['plan_id']}").json() == draft
        # Nothing was asked of any model, and the plan is counted by a fixed word.
        assert not w.log.exists()
        events = [e for e in w.client.app.state.usage.summary()["recent"] if e["kind"] == "plan"]  # type: ignore[attr-defined]
        assert [(e["source"], e["capabilities"], e["attempts"]) for e in events] == [
            ("extraction_plan", ["text_extraction"], 0)
        ]


def test_it_works_on_any_table_with_a_text_column_and_an_existing_column_is_filled_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.plain()
        draft = w.create(
            dataset_id,
            targets=[{"column": "Amount", "kind": "number"}, {"column": "Paid on", "kind": "date"}],
        ).json()
        assert draft["kind"] == "extraction" and draft["validation"]["valid"]
        assert (draft["sheet_name"], draft["range_ref"]) == ("Notes", "A1:C3")
        # "Amount" is a column of the table: it is filled where it is. A new one comes after.
        assert draft["output_columns"] == {"Amount": "C", "Paid on": "D"}
        # Not a table of mail: the result is left as the user gave it.
        assert draft["text_column_emptied"] is False
        # A range may be given, both parts or neither.
        chosen = w.create(
            dataset_id,
            sheet_name="Notes",
            range_ref="A1:C2",
            targets=[{"column": "Amount", "kind": "number"}],
        )
        assert chosen.status_code == 201 and chosen.json()["range_ref"] == "A1:C2"
        half = w.create(dataset_id, sheet_name="Notes")
        assert half.status_code == 422
        assert half.json()["error"]["code"] == "extraction_plan_invalid"


def test_a_bad_request_or_a_table_that_cannot_serve_it_is_refused_with_the_builders_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.mails(FOUND)
        cases: list[tuple[dict[str, Any], str]] = [
            # The table has no such column: the message names the column the user typed.
            ({"text_column": "Body"}, "The column 'Body' does not exist in the table."),
            # A column that is not text cannot be read.
            ({"text_column": "Received"}, "only a text column can be read"),
            ({"targets": [{"column": "Text", "kind": "text"}]}, "cannot both hold the text"),
            (
                {"targets": [{"column": "Amount", "kind": "number", "options": ["a", "b"]}]},
                "Options are only for a choice column",
            ),
            ({"targets": [{"column": "Pick", "kind": "one_of", "options": ["a"]}]}, "needs 2 to"),
            (
                {"targets": [{"column": "A", "kind": "text"}, {"column": "a", "kind": "text"}]},
                "filled twice",
            ),
            ({"question": "x" * 1001}, "at most 1000 characters"),
            ({"question": "two\nlines"}, "control characters"),
            # An existing column that does not fit the kind.
            ({"targets": [{"column": "From", "kind": "number"}]}, "From"),
        ]
        for changes, expected in cases:
            refused = w.create(dataset_id, **changes)
            assert refused.status_code == 422, (changes, refused.text)
            error = refused.json()["error"]
            assert error["code"] == "extraction_plan_invalid", error
            assert expected in error["message"], (expected, error)
        assert w.service.plans == {}
        # Shapes the model of the body refuses are the generic answer, as for every route.
        generic = w.create(dataset_id, targets=[{"column": "A", "kind": "dance"}])
        assert generic.status_code == 422
        assert generic.json()["error"]["code"] == "invalid_request"
        unknown = w.create("0" * 32)
        assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "dataset_not_found"
        too_big = w.client.post(
            "/api/extraction-plans", json=request_body(dataset_id, question="q" * 70_000)
        )
        assert too_big.status_code == 413
        assert too_big.json()["error"]["code"] == "request_too_large"


def test_a_table_over_the_row_limit_asks_for_a_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_run, "MAX_RUN_ROWS", 2)
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.mails(FOUND + NOT_ABOUT_IT)
        refused = w.create(dataset_id)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "invalid_selection"
        # The first rows, chosen on purpose.
        chosen = w.create(dataset_id, sheet_name="Mail", range_ref="A1:D3")
        assert chosen.status_code == 201, chosen.text


# --- What is still refused ------------------------------------------------------------


def test_the_planner_routes_still_refuse_a_table_of_mail_and_this_route_is_the_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.mails(FOUND)
        for path, body in (
            (
                "/api/plans",
                {
                    "dataset_id": dataset_id,
                    "sheet_name": "Mail",
                    "range_ref": "A1:D3",
                    "instruction": "Fill Amount.",
                    "output_columns": {"Amount": "E"},
                },
            ),
            (
                "/api/task-ideas",
                {"dataset_id": dataset_id, "sheet_name": "Mail", "range_ref": "A1:D3"},
            ),
            (
                "/api/fallback-plans",
                {
                    "dataset_id": dataset_id,
                    "sheet_name": "Mail",
                    "range_ref": "A1:D3",
                    "question": "Find it.",
                    "context_columns": ["From"],
                    "targets": [{"column": "Amount", "kind": "number"}],
                    "output_columns": {"Amount": "E"},
                },
            ),
        ):
            refused = w.client.post(path, json=body)
            assert refused.status_code == 409, (path, refused.text)
            assert refused.json()["error"]["code"] == "mail_dataset_no_planner"
        assert w.create(dataset_id).status_code == 201
        assert not w.log.exists()


def test_a_supplied_extraction_plan_is_refused_on_the_normal_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND)
        w.service.plans.clear()
        other = w.plain()
        refused = w.client.post(
            "/api/plans",
            json={
                "dataset_id": other,
                "sheet_name": "Notes",
                "range_ref": "A1:C3",
                "instruction": "Fill Amount.",
                "output_columns": {"Amount": "C", "Paid on": "D", "Renews": "E"},
                "plan": draft["plan"],
            },
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "extraction_plan_refused"
        assert w.service.plans == {}


def test_the_universal_search_cannot_be_put_on_top_of_an_extraction_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_run, "openai_api", forbidden_extraction_api)
    app_log = tmp_path / "lookup.jsonl"
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    app = create_app(
        settings=PlanRunSettings(allow_mail=True, allow_web_lookup=True, usage_file="off"),
        local_browser_configuration=True,
        offline_executor_factory=offline_executor,
        offline_extraction_api=LoggedExtractionApiFactory(KEY, tmp_path / "e.jsonl"),
        offline_lookup_api=LoggedApiFactory(KEY, app_log),
    )
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/datasets", params={"filename": "n.xlsx"}, content=plain_workbook()
        )
        dataset_id = uploaded.json()["dataset_id"]
        draft = client.post(
            "/api/extraction-plans",
            json=request_body(dataset_id, targets=[{"column": "Paid on", "kind": "date"}]),
        ).json()
        assert draft["kind"] == "extraction"
        refused = client.post(
            "/api/fallback-plans",
            json={
                "plan_id": draft["plan_id"],
                "question": "Find it.",
                "context_columns": ["Id"],
                "targets": [{"column": "Paid on", "kind": "text"}],
            },
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "mixed_base_not_planner"
        assert len(app.state.plan_run.plans) == 1
        assert not app_log.exists()


def test_a_draft_that_is_changed_after_approval_or_whose_kind_is_changed_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND)
        w.approve(draft)
        stored = w.service.plans[draft["plan_id"]]
        stored["kind"] = "fallback"
        refused = w.start(draft)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "approval_mismatch"
        # Even where the fingerprint is made again, the plan decides, not the kind.
        stored["fingerprint"] = draft_fingerprint(stored)
        refused = w.start(draft)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "extraction_plan_refused"
        stored["kind"] = "planner"
        stored["fingerprint"] = draft_fingerprint(stored)
        assert w.start(draft).json()["error"]["code"] == "extraction_plan_refused"
        assert not w.log.exists()


def test_a_draft_is_not_run_where_extraction_stopped_being_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND)
        w.approve(draft)
        # The setting is switched off after the draft was made.
        w.service.mail_available = False
        refused = w.start(draft)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "extraction_not_allowed"
        w.service.mail_available = True
        # The key is removed.
        w.service.provider.remove_key()
        assert w.service.provider.key_present is False
        refused = w.start(draft)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "extraction_not_allowed"
        assert not w.log.exists()
        assert w.service.jobs == {}


# --- The cycle, the status rule and the report ----------------------------------------


def test_a_run_in_which_every_row_is_found_or_not_about_the_task_is_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND + NOT_ABOUT_IT, sources_in_workbook="sheet")
        job = w.run(draft)
        assert job["kind"] == "extraction" and job["status"] == "completed", job
        assert job["error"] is None and job["rows_skipped"] == 0
        assert job["rows_not_applicable"] == 1
        # The time limit is the server's maximum unless the request names one.
        assert job["timeout_seconds"] == MAX_TIMEOUT_SECONDS
        # The steps' own counts: the text that is not about the task is its own number.
        assert job["progress"] == {
            "rows_done": 3,
            "rows_total": 3,
            "steps": {"extract": {"filled": 2, "failed": 0, "skipped": 0, "not_applicable": 1}},
        }
        report = w.report(job)
        assert report["kind"] == "extraction" and report["status"] == "completed"
        assert codes_of(w, job) == {
            "Mail!2": None,
            "Mail!3": None,
            "Mail!4": "NOT_APPLICABLE",
        }
        # The executor's own classification is left as it is: the row failed its step.
        assert report["execution"]["metrics"]["failed_rows"] == 1
        assert report["text_extraction"] == {
            "configuration_id": CONFIGURATION,
            "requests": 3,
            "input_tokens": 3 * 700,
            "output_tokens": 3 * 90,
            "answers_kept": 0,
            "rows": {"found": 2, "nothing_found": 0, "not_applicable": 1, "failed": 0},
            "values_kept": {"literal": 3, "derived": 1},
            "values_dropped": {},
            "text_column_emptied": True,
        }
        assert "web_lookup" not in report
        assert [call["model"] for call in calls(w.log)] == ["gpt-6-luna"] * 3
        assert all(call["tools"] is None and call["key_ok"] for call in calls(w.log))

        # The values written, typed, and where each came from; the quote is the evidence.
        workbook = load_workbook(io.BytesIO(w.download(job)))
        try:
            sheet = workbook["Mail"]
            assert [c.value for c in sheet[1]] == [
                "Received",
                "From",
                "Subject",
                "Text",
                "Amount",
                "Paid on",
                "Renews",
            ]
            assert sheet["E2"].value == 9.99 and sheet["F2"].value == "2026-11-03"
            assert sheet["G2"].value == "Yes" and sheet["E3"].value == 20
            assert all(sheet[f"{c}4"].value is None for c in "EFG")
            sources = [list(row) for row in workbook["Sources"].iter_rows(values_only=True)]
            assert any(f"Quote: {RECEIPT}" in str(row[6]) for row in sources[1:])
        finally:
            workbook.close()

        # The usage events: counts and fixed words.
        recent = w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
        model = [e for e in recent if e["kind"] == "model"]
        assert [(e["purpose"], e["model"], e["requests"]) for e in model] == [
            ("text_extraction", "gpt-6-luna", 3)
        ]
        [finished] = [e for e in recent if e["kind"] == "run_finished"]
        assert (finished["status"], finished["rows"], finished["rows_filled"]) == (
            "completed",
            3,
            2,
        )


def test_every_other_outcome_counts_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        subjects = FOUND + NOT_ABOUT_IT + NOTHING + PROVIDER_FAILURE
        job = w.run(w.draft(subjects))
        # "Nothing found", a dropped value and a provider error are not settled outcomes.
        assert job["status"] == "needs_review", job
        assert job["rows_not_applicable"] == 1
        assert job["progress"]["steps"]["extract"] == {
            "filled": 2,
            "failed": 3,
            "skipped": 0,
            "not_applicable": 1,
        }
        assert list(codes_of(w, job).values()) == [
            None,
            None,
            "NOT_APPLICABLE",
            "NOTHING_FOUND",
            "NOTHING_FOUND",
            "PROVIDER_ERROR",
        ]
        report = w.report(job)
        assert report["text_extraction"]["rows"] == {
            "found": 2,
            "nothing_found": 2,
            "not_applicable": 1,
            "failed": 1,
        }
        # The value whose quote is not in the text is dropped, by its reason code.
        assert report["text_extraction"]["values_dropped"] == {"quote_not_in_text": 1}
        # The executor's own classification: every row that did not fill its step.
        assert job["metrics"]["failed_rows"] == 4 and job["metrics"]["successful_rows"] == 2


def test_a_run_of_only_texts_that_are_not_about_the_task_is_completed_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        job = w.run(w.draft(NOT_ABOUT_IT))
        assert job["status"] == "completed" and job["rows_not_applicable"] == 1
        workbook = load_workbook(io.BytesIO(w.download(job)))
        try:
            assert all(workbook["Mail"][f"{c}2"].value is None for c in "EFG")
        finally:
            workbook.close()


def test_the_status_rule_is_narrow() -> None:
    from ai_data_enrichment.execution.models import OperationResult
    from ai_data_enrichment.execution.step_tally import is_settled_not_applicable

    refused = OperationResult(status="failure", error_code="NOT_APPLICABLE", message="x")
    assert is_settled_not_applicable("text_extraction", refused)
    # Only that capability...
    assert not is_settled_not_applicable("web_lookup", refused)
    assert not is_settled_not_applicable("geocode", refused)
    # ...and only that code.
    for code in ("NOTHING_FOUND", "MISSING_VALUE", "DEADLINE_REACHED", "PROVIDER_ERROR"):
        other = OperationResult(status="failure", error_code=code, message="x")
        assert not is_settled_not_applicable("text_extraction", other)


def test_running_the_plan_again_asks_only_what_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND + NOT_ABOUT_IT + NOTHING + PROVIDER_FAILURE)
        first = w.run(draft)
        assert len(calls(w.log)) == 6
        assert w.report(first)["text_extraction"]["answers_kept"] == 0
        first_cells = w.download(first)

        second = w.wait(w.start(draft).json())
        assert second["job_id"] != first["job_id"]
        # Found texts and "nothing found" are kept; "not about the task" and the provider
        # error are asked again (never kept).
        asked = calls(w.log)[6:]
        assert len(asked) == 2
        assert {("Advertisement" in c["input"], "Server trouble" in c["input"]) for c in asked} == {
            (True, False),
            (False, True),
        }
        report = w.report(second)["text_extraction"]
        assert report["answers_kept"] == 4 and report["requests"] == 2
        assert report["rows"] == {
            "found": 2,
            "nothing_found": 2,
            "not_applicable": 1,
            "failed": 1,
        }
        assert second["status"] == "needs_review" and second["rows_not_applicable"] == 1
        assert _cells(w.download(second)) == _cells(first_cells)


def _cells(content: bytes) -> dict[str, Any]:
    workbook = load_workbook(io.BytesIO(content))
    try:
        return {
            c.coordinate: c.value
            for row in workbook["Mail"].iter_rows()
            for c in row
            if c.value is not None
        }
    finally:
        workbook.close()


def test_the_report_lists_each_value_with_its_basis_and_quote_and_no_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        job = w.run(w.draft(FOUND + NOT_ABOUT_IT))
        rows = {row["record_id"]: row for row in w.report(job)["rows"]}
        assert rows["Mail!2"]["extracted"] == [
            {"column": "Amount", "basis": "literal", "quote": RECEIPT},
            {"column": "Paid on", "basis": "literal", "quote": "Paid on 3 Nov 2026"},
            {"column": "Renews", "basis": "derived", "quote": "Renews on 01.12.2026"},
        ]
        assert rows["Mail!3"]["extracted"] == [
            {"column": "Amount", "basis": "literal", "quote": "Total: 20,00 EUR"}
        ]
        assert rows["Mail!4"]["extracted"] == []
        assert "ai_found" not in rows["Mail!2"]
        assert "links" not in json.dumps(rows["Mail!2"]["extracted"])
        provenance = w.report(job)["step_provenance"]
        assert provenance == [
            {
                "step_id": "extract",
                "capability": "text_extraction",
                "source": "openai_text_extraction",
                "trust": "extracted",
            }
        ]


def test_a_named_time_limit_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND)
        w.approve(draft)
        started = w.start(draft, timeout_seconds=120)
        assert started.status_code == 202 and started.json()["timeout_seconds"] == 120
        assert w.wait(started.json())["status"] == "completed"


def test_there_is_nothing_to_review_and_a_resume_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        job = w.run(w.draft(FOUND + NOTHING))
        assert job["status"] == "needs_review"
        review = w.client.get(f"/api/jobs/{job['job_id']}/review").json()
        assert review["items"] == [] and review["informational"] == []
        resumed = w.client.post(
            f"/api/jobs/{job['job_id']}/resume",
            json={"decisions": [{"operation_id": "op", "name": "x"}]},
        )
        assert resumed.status_code == 422, resumed.text
        assert list(w.service.jobs) == [job["job_id"]]


def test_rows_after_the_deadline_fail_and_earlier_rows_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The run's limit is 52 s, so no request is begun 32 s after the worker's start;
    # a request takes 3 s here and the handler keeps 30 s for one.
    monkeypatch.setattr(plan_run, "MAX_TIMEOUT_SECONDS", 52)
    with world(tmp_path, monkeypatch, delay=3.0) as w:
        draft = w.draft(FOUND)
        job = w.run(draft)
        assert job["timeout_seconds"] == 52
        assert job["status"] == "needs_review", job  # not "timed_out": the run finished
        assert list(codes_of(w, job).values()) == [None, "DEADLINE_REACHED"]
        assert len(calls(w.log)) == 1
        assert w.report(job)["text_extraction"]["requests"] == 1
        # A failure by the deadline is a failure, not a settled outcome.
        assert job["rows_not_applicable"] == 0


def test_a_run_that_is_stopped_discards_the_result_but_keeps_the_answers_it_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No reserve, so the supervisor's own limit is what stops the run.
    monkeypatch.setattr(plan_run_worker, "FINISH_RESERVE_SECONDS", -1000.0)
    with world(tmp_path, monkeypatch, delay=4.0) as w:
        draft = w.draft(FOUND + NOT_ABOUT_IT)
        job = w.run(draft, timeout_seconds=11)
        assert job["status"] == "timed_out", job
        assert "result" not in job["artifacts"]
        report = w.report(job)
        # Only what was spent: no row or value counts of a result that was discarded.
        assert set(report["text_extraction"]) == {
            "configuration_id",
            "requests",
            "input_tokens",
            "output_tokens",
            "answers_kept",
        }
        spent = report["text_extraction"]
        assert 1 <= spent["requests"] <= 3 and spent["answers_kept"] == 0
        assert "execution" not in report
        events = [
            e
            for e in w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
            if e["kind"] == "model"
        ]
        assert [e["requests"] for e in events] == [spent["requests"]]
        asked_first = len(calls(w.log))

        # Running the plan again takes over the rows that were answered before the stop.
        again = w.wait(w.start(draft, timeout_seconds=200).json())
        assert again["status"] == "completed", again
        kept = w.report(again)["text_extraction"]["answers_kept"]
        assert kept >= 1
        assert len(calls(w.log)) - asked_first == 3 - kept


def test_a_workbook_without_the_gate_cannot_reach_the_real_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stand-in that fails when it is made: the run fails, and the real factory is never used.
    with world(tmp_path, monkeypatch, factory=forbidden_extraction_api) as w:
        job = w.run(w.draft(FOUND))
        assert job["status"] == "failed" and job["error"]["code"] == "execution_failed"


# --- The text does not spread ---------------------------------------------------------


def stored_parts(directory: Path, *, skip: set[Path]) -> list[tuple[str, bytes]]:
    """Every file under a directory, a workbook also as each of its unzipped parts."""
    found: list[tuple[str, bytes]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path in skip:
            continue
        data = path.read_bytes()
        found.append((str(path), data))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                found += [(f"{path}:{name}", archive.read(name)) for name in archive.namelist()]
    return found


def test_the_text_of_a_mail_reaches_no_report_usage_file_log_or_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    usage_file = tmp_path / "usage" / "events.jsonl"
    subjects = FOUND + NOT_ABOUT_IT + NOTHING
    with world(tmp_path, monkeypatch, usage_file=usage_file) as w:
        dataset_id = w.mails(subjects)
        dataset = w.service.datasets[dataset_id]
        # The dataset workbook is the one place the text lives; the preview is local display.
        preview = w.client.post(
            f"/api/datasets/{dataset_id}/preview",
            json={"sheet_name": "Mail", "range_ref": "A1:D6"},
        ).json()
        shown = [cell for row in preview["rows"]["values"] for cell in row if cell]
        assert any(SENTINEL in cell for cell in shown)
        stored = load_workbook(dataset)
        try:
            texts = [str(row[3].value) for row in stored["Mail"].iter_rows(min_row=2)]
        finally:
            stored.close()
        assert all(SENTINEL in text for text in texts)

        draft = w.create(dataset_id).json()
        job = w.run(draft)
        assert job["status"] == "needs_review", job
        # The model was sent the text: that is the point of the plan, and it is not stored.
        assert all(SENTINEL in call["input"] for call in calls(w.log))

        report_text = w.client.get(job["artifacts"]["report"]).text
        report = json.loads(report_text)
        assert SENTINEL not in report_text
        # In place of the text of each row: a fixed marker and its length.
        inputs = {row["record_id"]: row["inputs"] for row in report["rows"]}
        assert inputs == {
            f"Mail!{index + 2}": {"Text": f"[text not kept: {len(text)} characters]"}
            for index, text in enumerate(texts)
        }
        assert report["text_extraction"]["text_column_emptied"] is True
        # The user's own task, and the quotes, are in the report by design.
        assert report["approved_plan"]["instruction"] == draft["instruction"]
        assert RECEIPT in report_text

        downloaded = w.download(job)
        with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
            assert all(SENTINEL.encode() not in archive.read(name) for name in archive.namelist())
        result = load_workbook(io.BytesIO(downloaded))
        try:
            sheet = result["Mail"]
            assert sheet["D1"].value == "Text"  # the header stays
            assert all(sheet[f"D{row}"].value is None for row in range(2, len(subjects) + 2))
            # The other columns of the table are as they were; the quote is in the note.
            assert sheet["C2"].value == "Receipt FIRST"
            assert RECEIPT in sheet["E2"].comment.text
        finally:
            result.close()

        summary = w.client.app.state.usage.summary()  # type: ignore[attr-defined]
        # Everything the application wrote, but the dataset workbook: the one place the text
        # lives (see the test after this one).
        files = stored_parts(w.directory, skip={dataset})

    for name, data in files:
        assert SENTINEL.encode() not in data, name
    assert SENTINEL not in json.dumps(summary)
    assert SENTINEL not in usage_file.read_text("utf-8")
    assert SENTINEL not in caplog.text
    # The kept answers hold the quotes in clear (by design) and a digest of the text.
    assert any(name.endswith("values.sqlite") for name, _ in files)


def test_the_dataset_itself_is_the_one_stored_place_of_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.mails(FOUND)
        dataset = w.service.datasets[dataset_id]
        w.run(w.create(dataset_id).json())
        holding = [
            name
            for name, data in stored_parts(w.directory, skip=set())
            if SENTINEL.encode() in data
        ]
        assert holding and all(name.startswith(str(dataset)) for name in holding), holding


def test_an_error_of_the_worker_names_its_class_and_nothing_of_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    # The worker is another process: its log goes to the standard error that capfd reads.
    with world(tmp_path, monkeypatch, factory=LeakyExtractionFactory()) as w:
        job = w.run(w.draft(FOUND))
        assert job["status"] == "failed"
        assert job["error"] == {"code": "execution_failed", "message": "The workbook run failed."}
        report_text = w.client.get(job["artifacts"]["report"]).text
    seen = capfd.readouterr()
    assert SENTINEL not in seen.err + seen.out + report_text and KEY not in seen.err + report_text
    assert "RuntimeError" in seen.err


# --- A table that is not mail ---------------------------------------------------------


def test_the_text_column_of_a_table_that_is_not_mail_is_left_as_the_user_gave_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.plain()
        draft = w.create(dataset_id).json()
        assert draft["output_columns"] == {"Amount": "C", "Paid on": "D", "Renews": "E"}
        job = w.run(draft)
        assert job["status"] == "completed", job
        report = w.report(job)
        assert report["text_extraction"]["text_column_emptied"] is False
        result = load_workbook(io.BytesIO(w.download(job)))
        try:
            sheet = result["Notes"]
            assert SENTINEL in sheet["B2"].value and SENTINEL in sheet["B3"].value
            assert sheet["C2"].value == 9.99 and sheet["D2"].value == "2026-11-03"
        finally:
            result.close()
        # What the report keeps of the text is still only its length.
        assert SENTINEL not in w.client.get(job["artifacts"]["report"]).text
