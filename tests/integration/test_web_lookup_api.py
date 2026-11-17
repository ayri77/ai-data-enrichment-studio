"""The universal search through the HTTP API and the run worker, offline.

The routes, the gates, the worker process, the real handler, the reply reader and the
workbook writer are all real. Only the model client is fake, and it is made INSIDE the
worker (``tests.support.lookup_api``): no real OpenAI client makes a request here.
"""

import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from tests.support.lookup_api import (
    LeakyFactory,
    LoggedApiFactory,
    calls,
    forbidden_api,
)
from tests.support.planning import ScriptedPlanClient
from tests.support.web_lookup import PAGE, PAGE_2

from ai_data_enrichment import plan_run, plan_run_worker
from ai_data_enrichment.api import create_app
from ai_data_enrichment.capabilities.fallback import (
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
)
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.plan_run import (
    TERMINAL,
    PlanRunService,
    PlanRunSettings,
    draft_fingerprint,
)
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.run_limits import MAX_TIMEOUT_SECONDS

KEY = "sk-test-key-not-a-secret-0123456789"
ADMIN = "admin-code-for-tests"
SHEET = "Mountains"
CONFIGURATION = "openai-web-lookup-p1-gpt-6-luna-low"
OUTPUT_COLUMNS = {"Notes": "D", "Height": "E", "Climbed in winter": "F"}
ROWS = [
    ["Mountain", "Country", "Rank", "Notes"],
    ["Everest", "Nepal", 1, None],
    ["Lhotse", "Nepal", None, None],  # the numeric context cell is empty
    ["K2", "Pakistan", 2, "keep me"],  # the existing target is filled
    ["Nowhere", "Nowhere", 3, None],
    ["Everest", "Nepal", 1, None],  # the same row again
    ["Broken", "Nowhere", 4, None],
]
FOUND_ROWS = [
    ["Mountain", "Country", "Rank", "Notes"],
    ["Everest", "Nepal", 1, None],
    ["Lhotse", "Nepal", 4, None],
    ["Makalu", "Nepal", 5, None],
]


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("An offline test attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)
    monkeypatch.setattr(UrllibJsonTransport, "post_json", denied)
    # A run that did not get the test's fake would reach this, and fail, not the network.
    monkeypatch.setattr(plan_run, "openai_api", forbidden_api)
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def workbook_bytes(rows: list[list[Any]] = ROWS) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@dataclass
class World:
    client: TestClient
    service: PlanRunService
    log: Path
    bodies: list[bytes]

    @property
    def directory(self) -> Path:
        return self.client.app.state.directory  # type: ignore[attr-defined]

    def upload(self, rows: list[list[Any]] = ROWS) -> str:
        result = self.client.post(
            "/api/datasets", params={"filename": "mountains.xlsx"}, content=workbook_bytes(rows)
        )
        assert result.status_code == 201, result.text
        return result.json()["dataset_id"]

    def create(self, dataset_id: str, **changes: Any):
        return self.client.post("/api/fallback-plans", json=request_body(dataset_id, **changes))

    def draft(self, rows: list[list[Any]] = ROWS, **changes: Any) -> dict[str, Any]:
        result = self.create(self.upload(rows), **changes)
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

    def wait(self, job: dict[str, Any], seconds: float = 60) -> dict[str, Any]:
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            current = self.client.get(f"/api/jobs/{job['job_id']}").json()
            if current["status"] in TERMINAL:
                return current
            sleep(0.05)
        pytest.fail("The worker did not finish in time")

    def report(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(job["artifacts"]["report"]).json()

    def result_cells(self, job: dict[str, Any]) -> dict[str, Any]:
        workbook = load_workbook(BytesIO(self.client.get(job["artifacts"]["result"]).content))
        try:
            return {
                cell.coordinate: cell.value
                for row in workbook[SHEET].iter_rows()
                for cell in row
                if cell.value is not None
            }
        finally:
            workbook.close()


def request_body(dataset_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "sheet_name": SHEET,
        "range_ref": "A1:D7",
        "question": "Find facts about this mountain.",
        "context_columns": ["Mountain", "Country", "Rank"],
        "targets": [
            {"column": "Height", "kind": "number", "unit": "m"},
            {"column": "Notes", "kind": "text"},
            {"column": "Climbed in winter", "kind": "yes_no"},
        ],
        "output_columns": OUTPUT_COLUMNS,
        **changes,
    }


@contextmanager
def world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    key: str | None = KEY,
    delay: float = 0.0,
    factory: Any = "logged",
    plan_client: Any = None,
    network: bool = False,
    offline: bool = True,
) -> Iterator[World]:
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    log = tmp_path / "requests.jsonl"
    if factory == "logged":
        factory = LoggedApiFactory(KEY, log, delay=delay)
    app = create_app(
        settings=PlanRunSettings(allow_web_lookup=enabled, allow_network=network),
        offline_plan_client=plan_client,
        offline_executor_factory=offline_executor if offline else None,
        offline_lookup_api=factory,
        admin_code=ADMIN,
    )
    with TestClient(app) as client:
        bodies: list[bytes] = []
        original = client.request

        def spy(*args: Any, **kwargs: Any):
            result = original(*args, **kwargs)
            bodies.append(result.content)
            return result

        monkeypatch.setattr(client, "request", spy)
        yield World(client, app.state.plan_run, log, bodies)


def everything_written(directory: Path) -> list[tuple[str, bytes]]:
    """Every file under a directory; a workbook also as each of its unzipped parts."""
    found: list[tuple[str, bytes]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        found.append((str(path), data))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                found += [(f"{path}:{name}", archive.read(name)) for name in archive.namelist()]
    return found


# --- The setting, the key and the limits ----------------------------------------------


def test_the_setting_is_read_like_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENRICHMENT_ALLOW_WEB_LOOKUP", "ENRICHMENT_WEB_LOOKUP_MODEL"):
        monkeypatch.delenv(name, raising=False)
    off = PlanRunSettings.from_environment()
    assert (off.allow_web_lookup, off.web_lookup_model) == (False, "gpt-6-luna")
    monkeypatch.setenv("ENRICHMENT_ALLOW_WEB_LOOKUP", "true")
    assert PlanRunSettings.from_environment().allow_web_lookup is False
    monkeypatch.setenv("ENRICHMENT_ALLOW_WEB_LOOKUP", "1")
    monkeypatch.setenv("ENRICHMENT_WEB_LOOKUP_MODEL", " other-model ")
    on = PlanRunSettings.from_environment()
    assert (on.allow_web_lookup, on.web_lookup_model) == (True, "other-model")
    monkeypatch.setenv("ENRICHMENT_WEB_LOOKUP_MODEL", "two words")
    with pytest.raises(RuntimeError, match="ENRICHMENT_WEB_LOOKUP_MODEL"):
        PlanRunSettings.from_environment()


def test_off_by_default_nothing_is_read_or_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A key is there and everything else is as for a run: only the setting is missing.
    with world(tmp_path, monkeypatch, enabled=False) as w:
        assert w.client.get("/api/limits").json()["web_lookup"] is False
        dataset_id = w.upload()

        def profiled(*args: object, **kwargs: object) -> None:
            pytest.fail("The table was read although the universal search is off")

        monkeypatch.setattr(plan_run, "profile_worksheet", profiled)
        refused = w.create(dataset_id)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "web_lookup_disabled"
        assert w.service.plans == {}
        assert not w.log.exists()


def test_switched_on_without_a_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch, key=None) as w:
        assert w.client.get("/api/limits").json()["web_lookup"] is False
        refused = w.create(w.upload())
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "web_lookup_no_key"
        assert w.service.plans == {}


def test_limits_say_yes_only_with_the_setting_a_key_and_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        limits = w.client.get("/api/limits").json()
        assert limits["web_lookup"] is True
        # Nothing else about the limits is changed, and the planner is not told of it.
        assert "web_lookup" not in limits["capabilities"]
        listed = w.client.get("/api/capabilities").json()["capabilities"]
        assert "web_lookup" not in json.dumps(listed)
    # A live server: the network gate of the planner applies to the lookup too.
    with world(tmp_path, monkeypatch, offline=False, factory=None) as w:
        assert w.client.get("/api/limits").json()["web_lookup"] is False
    with world(tmp_path, monkeypatch, offline=False, factory=None, network=True) as w:
        assert w.client.get("/api/limits").json()["web_lookup"] is True


# --- The whole cycle ------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["notes", "sheet"])
def test_create_approve_run_download_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.upload()
        before = w.service.datasets[dataset_id].read_bytes()
        created = w.create(dataset_id, sources_in_workbook=mode)
        assert created.status_code == 201, created.text
        draft = created.json()

        # The draft has the shape of a planner draft, marked as the server's own.
        assert draft["kind"] == "fallback"
        assert draft["status"] == "draft" and draft["planning_status"] == "ready"
        assert draft["instruction"] == "Find facts about this mountain."
        assert draft["sources_in_workbook"] == mode and draft["existing_outputs"] == "keep"
        assert draft["output_columns"] == OUTPUT_COLUMNS
        assert draft["validation"]["valid"], draft["validation"]
        assert len(draft["fingerprint"]) == 64
        [step] = draft["plan"]["steps"]
        assert step["capability"] == "web_lookup"
        assert sorted(o["target_column"] for o in draft["plan"]["outputs"]) == sorted(
            OUTPUT_COLUMNS
        )
        # What will happen is said in the plan itself.
        [assumption] = draft["plan"]["assumptions"]
        assert "one request per row" in assumption and "not verified" in assumption
        assert w.client.get(f"/api/plans/{draft['plan_id']}").json() == draft
        assert w.client.post(f"/api/plans/{draft['plan_id']}/validate").json()["valid"]
        assert w.start(draft).status_code == 409  # not approved yet

        job = w.run(draft)
        # The failed rows leave something to look at; the status rules are the old ones.
        assert job["status"] == "needs_review", job
        assert job["kind"] == "fallback"
        assert job["timeout_seconds"] == MAX_TIMEOUT_SECONDS
        assert job["metrics"]["total_rows"] == 6 and job["metrics"]["successful_rows"] == 4

        cells = w.result_cells(job)
        for row in (2, 3, 6):  # Everest, Lhotse, Everest again
            assert cells[f"E{row}"] == 8849 and not isinstance(cells[f"E{row}"], str)
            assert cells[f"D{row}"] == "1953" and isinstance(cells[f"D{row}"], str)
            assert cells[f"F{row}"] == "No"
        # K2: no height given (an empty slot), the rest written, the filled note kept.
        assert cells["D4"] == "keep me" and cells["F4"] == "Yes" and "E4" not in cells
        # Nowhere (NOTHING_FOUND) and Broken: nothing written.
        assert not any(f"{c}{r}" in cells for r in (5, 7) for c in "DEF")
        assert (cells["E1"], cells["F1"]) == ("Height", "Climbed in winter")
        # The source is never touched.
        assert w.service.datasets[dataset_id].read_bytes() == before

        report = w.report(job)
        assert report["kind"] == "fallback" and report["approved_plan"]["kind"] == "fallback"
        assert report["web_lookup"] == {
            "configuration_id": CONFIGURATION,
            "requests": 5,  # six rows, one of them a repeat
            "input_tokens": 5000,
            "output_tokens": 1000,
            "web_searches": 10,
        }
        codes = {
            row["record_id"]: row["row_result"]["steps"][0]["result"]["error_code"]
            for row in report["execution"]["records"]
        }
        assert codes[f"{SHEET}!5"] == "NOTHING_FOUND" and codes[f"{SHEET}!7"] == "INVALID_RESPONSE"
        by_row = {row["row"]: row["ai_found"] for row in report["rows"]}
        everest = [
            {"column": "Height", "basis": "stated", "links": 1},
            {"column": "Notes", "basis": "concluded", "links": 2},
            {"column": "Climbed in winter", "basis": "stated", "links": 1},
        ]
        assert by_row[2] == by_row[3] == by_row[6] == everest
        # K2: the note was kept, the height empty: only what was written is listed.
        assert by_row[4] == [{"column": "Climbed in winter", "basis": "stated", "links": 1}]
        assert by_row[5] == by_row[7] == []
        # Links are not repeated there; they are in the evidence.
        assert PAGE not in json.dumps(by_row) and PAGE_2 not in json.dumps(by_row)
        assert report["step_provenance"] == [
            {
                "step_id": "lookup",
                "capability": "web_lookup",
                "source": "openai_web_search",
                "trust": "ai_found",
            }
        ]
        assert report["sources_in_workbook"] == {"mode": mode, "applied": True}
        workbook = load_workbook(BytesIO(w.client.get(job["artifacts"]["result"]).content))
        try:
            if mode == "notes":
                note = workbook[SHEET]["E2"].comment.text
                assert "Source: OpenAI web search" in note and f"Reference: {PAGE}" in note
                assert workbook.sheetnames == [SHEET]
            else:
                assert workbook.sheetnames == [SHEET, "Sources"]
        finally:
            workbook.close()

        # What the worker's client was asked: the configured model, low effort, 30 seconds.
        asked = calls(w.log)
        assert len(asked) == 5 and all(call["key_ok"] for call in asked)
        assert {call["model"] for call in asked} == {"gpt-6-luna"}
        assert {json.dumps(call["reasoning"]) for call in asked} == {'{"effort": "low"}'}
        assert {call["timeout"] for call in asked} == {30.0}
        assert all(call["tools"][0]["type"] == "web_search" for call in asked)

        # The spend is a model event with its own purpose; failed requests count.
        [event] = [
            e
            for e in w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
            if e["kind"] == "model"
        ]
        assert (event["purpose"], event["model"]) == ("web_lookup", "gpt-6-luna")
        assert (event["requests"], event["input_tokens"], event["output_tokens"]) == (5, 5000, 1000)
        assert event["web_searches"] == 10
        summary = w.client.get("/api/usage", headers={"x-admin-code": ADMIN}).json()
        [visitor] = summary["visitors"]
        assert (visitor["model_requests"], visitor["web_searches"]) == (5, 10)
        assert visitor["input_tokens"] == 5000 and visitor["output_tokens"] == 1000

        # Review has nothing to decide for a lookup, and a resume has nothing to continue.
        review = w.client.get(f"/api/jobs/{job['job_id']}/review").json()
        assert review["items"] == [] and review["informational"] == []
        resumed = w.client.post(
            f"/api/jobs/{job['job_id']}/resume",
            json={"decisions": [{"operation_id": "op", "name": "x"}]},
        )
        assert resumed.status_code == 422


def test_a_run_in_which_every_row_is_found_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        job = w.run(draft)
        # Not "needs_review": there is nothing to review, so no empty Review step.
        assert job["status"] == "completed", job
        assert job["metrics"]["successful_rows"] == 3
        assert w.report(job)["web_lookup"]["requests"] == 3


def test_an_explicit_timeout_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        w.approve(draft)
        started = w.start(draft, timeout_seconds=120)
        assert started.status_code == 202 and started.json()["timeout_seconds"] == 120
        w.wait(started.json())


# --- What is refused ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"context_columns": ["Mountian"]}, "does not exist"),
        ({"context_columns": ["Mountain", "mountain"]}, "chosen twice"),
        ({"question": "   "}, "must not be blank"),
        (
            {"targets": [{"column": "Notes", "kind": "number"}]},
            "is to be filled with number",
        ),
        ({"targets": [{"column": "Height", "kind": "text", "unit": "m"}]}, "only for a number"),
        ({"targets": [{"column": "Mountain", "kind": "text"}]}, "cannot both identify"),
    ],
)
def test_a_request_the_plan_builder_rejects_is_a_422_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any], fragment: str
) -> None:
    with world(tmp_path, monkeypatch) as w:
        # "Notes" holds text: filling it with numbers is a mismatch; "Mountain" is context.
        refused = w.create(w.upload(), output_columns={"Notes": "D"}, **changes)
        assert refused.status_code == 422, refused.text
        error = refused.json()["error"]
        assert error["code"] == "fallback_plan_invalid"
        assert fragment in error["message"]
        assert w.service.plans == {}


def test_the_body_cannot_bring_a_plan_a_kind_or_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        dataset_id = w.upload()
        for extra in ({"kind": "planner"}, {"plan": {}}, {"model": "x"}, {"allow_network": True}):
            refused = w.create(dataset_id, **extra)
            assert refused.status_code == 422
            assert refused.json()["error"]["code"] == "invalid_request"
        too_large = w.client.post("/api/fallback-plans", content=b"{" + b" " * (70 * 1024) + b"}")
        assert too_large.status_code == 413
        assert w.service.plans == {}


def lookup_plan(tmp_path: Path) -> dict[str, Any]:
    """A well-formed fallback plan, made as the server makes it."""
    path = tmp_path / "plain.xlsx"
    path.write_bytes(workbook_bytes())
    profile = profile_worksheet(path, SHEET, "d", selection=TableSelection(SHEET, "A1:D7"))
    request = FallbackRequest(
        question="Find facts.",
        context_columns=("Mountain",),
        targets=(FallbackTarget(column="Height", kind="number", unit="m"),),
    )
    return build_fallback_plan(request, profile.columns).model_dump(mode="json")


@pytest.mark.parametrize("enabled", [False, True])
def test_a_supplied_plan_with_the_lookup_is_refused_on_the_normal_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    with world(tmp_path, monkeypatch, enabled=enabled) as w:
        dataset_id = w.upload()
        refused = w.client.post(
            "/api/plans",
            json={
                "dataset_id": dataset_id,
                "sheet_name": SHEET,
                "range_ref": "A1:D7",
                "instruction": "Fill Height.",
                "output_columns": {"Height": "E"},
                "plan": lookup_plan(tmp_path),
            },
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "web_lookup_plan_refused"
        assert w.service.plans == {}
        # The body cannot say what kind it is either.
        kind = w.client.post(
            "/api/plans",
            json={
                "dataset_id": dataset_id,
                "sheet_name": SHEET,
                "range_ref": "A1:D7",
                "instruction": "Fill Height.",
                "output_columns": {"Height": "E"},
                "kind": "fallback",
            },
        )
        assert kind.status_code == 422 and kind.json()["error"]["code"] == "invalid_request"


def test_a_planner_reply_with_the_lookup_never_becomes_a_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = {"status": "ready", "plan": lookup_plan(tmp_path), "reason": None}
    planner = ScriptedPlanClient(*([reply] * 8))
    with world(tmp_path, monkeypatch, plan_client=planner) as w:
        refused = w.client.post(
            "/api/plans",
            json={
                "dataset_id": w.upload(),
                "sheet_name": SHEET,
                "range_ref": "A1:D7",
                "instruction": "Fill Height.",
                "output_columns": {"Height": "E"},
            },
        )
        assert planner.calls  # the planner was asked
        assert refused.status_code >= 400, refused.text
        assert refused.json()["error"]["code"] in {"planning_failed", "web_lookup_plan_refused"}
        assert w.service.plans == {}
        assert not w.log.exists()


def test_a_service_that_gets_a_lookup_plan_from_the_planner_refuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit check after the planner, with a planner stub that skips its own."""
    from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
    from ai_data_enrichment.workspace_errors import WorkspaceError

    plan = EnrichmentPlan.model_validate(lookup_plan(tmp_path))

    class Generated:
        class response:  # noqa: N801
            status, reason = "ready", None

        attempts: list[Any] = []
        unfilled_targets: tuple[str, ...] = ()

    Generated.response.plan = plan  # type: ignore[attr-defined]

    class Planner:
        def __init__(self, **_: Any) -> None: ...

        def generate(self, **_: Any) -> Any:
            return Generated

    monkeypatch.setattr(plan_run, "LLMPlanner", Planner)
    planner = ScriptedPlanClient()
    with world(tmp_path, monkeypatch, plan_client=planner) as w:
        body = plan_run.PlanRequest(
            dataset_id=w.upload(),
            sheet_name=SHEET,
            range_ref="A1:D7",
            instruction="Fill Height.",
            output_columns={"Height": "E"},
        )
        with pytest.raises(WorkspaceError) as refused:
            w.service.create_plan(body)
        assert refused.value.code == "web_lookup_plan_refused"
        assert w.service.plans == {}


def test_tampering_with_a_stored_draft_between_approval_and_run_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        w.approve(draft)
        stored = w.service.plans[draft["plan_id"]]
        assert stored["fingerprint"] == draft_fingerprint(stored)

        def refused(code: str) -> None:
            result = w.start(draft)
            assert result.status_code in (409, 422), result.text
            assert result.json()["error"]["code"] == code
            assert w.service.jobs == {}

        # The kind is covered by the fingerprint: take it away or change it.
        original = dict(stored)
        del stored["kind"]
        refused("approval_mismatch")
        stored["kind"] = "planner"
        refused("approval_mismatch")
        stored.clear()
        stored.update(original)
        # So is the plan.
        stored["plan"] = {**stored["plan"], "assumptions": ["changed"]}
        refused("approval_mismatch")
        stored.clear()
        stored.update(original)
        # Even with a fingerprint made to fit, the kind must agree with the plan.
        stored["kind"] = "planner"
        stored["fingerprint"] = draft_fingerprint(stored)
        refused("web_lookup_plan_refused")
        stored["kind"] = "something-else"
        stored["fingerprint"] = draft_fingerprint(stored)
        refused("web_lookup_plan_refused")
        # The same holds for validation and approval of such a draft.
        assert not w.client.post(f"/api/plans/{draft['plan_id']}/validate").json()["valid"]
        stored.clear()
        stored.update(original)
        assert w.start(draft).status_code == 202


def test_a_normal_draft_marked_as_a_fallback_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        path = tmp_path / "normal.xlsx"
        path.write_bytes(workbook_bytes())
        # A normal draft of the capitals sample, made through the normal route.
        capitals = Path("samples/capitals.xlsx").read_bytes()
        dataset_id = w.client.post(
            "/api/datasets", params={"filename": "capitals.xlsx"}, content=capitals
        ).json()["dataset_id"]
        created = w.client.post(
            "/api/plans",
            json={
                "dataset_id": dataset_id,
                "sheet_name": "Sheet1",
                "range_ref": "A1:E11",
                "instruction": "Calculate literal city distances in km.",
                "output_columns": {"distance": "E"},
                "plan": json.loads(Path("demo/capitals_api_plan.json").read_text("utf-8")),
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        assert "kind" not in draft  # a planner draft has none, as before
        w.approve(draft)
        stored = w.service.plans[draft["plan_id"]]
        stored["kind"] = "fallback"
        stored["fingerprint"] = draft_fingerprint(stored)
        refused = w.start(draft)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "web_lookup_plan_refused"


def test_switched_off_or_keyless_after_the_draft_the_run_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        w.approve(draft)
        w.service.settings = replace(w.service.settings, allow_web_lookup=False)
        refused = w.start(draft)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "web_lookup_disabled"
        w.service.settings = replace(w.service.settings, allow_web_lookup=True)
        w.service.provider.remove_key()
        keyless = w.start(draft)
        assert keyless.status_code == 422
        assert keyless.json()["error"]["code"] == "web_lookup_no_key"
        assert w.service.jobs == {} and not w.log.exists()


def test_a_live_run_needs_the_network_gate_like_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch, offline=False, factory=None, network=True) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        w.approve(draft)
        refused = w.start(draft)  # no consent in this request
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "network_disabled"
        assert w.service.jobs == {}


def test_an_offline_application_without_a_stand_in_client_refuses_the_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An application assembled offline skips the network gate for its runs. Without a
    # stand-in client a lookup would reach the real service, so it is not offered at all.
    with world(tmp_path, monkeypatch, factory=None) as w:
        assert w.client.get("/api/limits").json()["web_lookup"] is False
        refused = w.create(w.upload())
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "web_lookup_disabled"
        assert w.service.plans == {}


def test_a_live_application_hands_the_production_client_factory_to_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `openai_api` is replaced by a stand-in that fails (see the autouse fixture): a run
    # that fails with it shows that the service hands the production factory over.
    with world(tmp_path, monkeypatch, offline=False, factory=None, network=True) as w:
        job = w.run(w.draft(FOUND_ROWS, range_ref="A1:D4"), allow_network=True)
        assert job["status"] == "failed" and job["error"]["code"] == "execution_failed"
        assert not w.log.exists()


def test_the_production_client_is_made_with_one_attempt_and_a_short_timeout() -> None:
    client = plan_run_worker.openai_api(KEY)
    try:
        assert client.max_retries == 0  # type: ignore[attr-defined]
        assert client.timeout == 30.0  # type: ignore[attr-defined]
        assert str(client.base_url) == "https://api.openai.com/v1/"  # type: ignore[attr-defined]
    finally:
        client.close()  # type: ignore[attr-defined]


# --- Time -----------------------------------------------------------------------------


def test_rows_after_the_deadline_fail_and_earlier_rows_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The run's limit is 52 s, so no request is begun 32 s after the worker's start;
    # a request takes 3 s here and the handler keeps 30 s for one.
    monkeypatch.setattr(plan_run, "MAX_TIMEOUT_SECONDS", 52)
    with world(tmp_path, monkeypatch, delay=3.0) as w:
        draft = w.draft(FOUND_ROWS, range_ref="A1:D4")
        job = w.run(draft)
        assert job["timeout_seconds"] == 52
        assert job["status"] == "needs_review", job  # not "timed_out": the run finished
        codes = {
            row["record_id"]: row["row_result"]["steps"][0]["result"]["error_code"]
            for row in w.report(job)["execution"]["records"]
        }
        assert codes == {
            f"{SHEET}!2": None,
            f"{SHEET}!3": "DEADLINE_REACHED",
            f"{SHEET}!4": "DEADLINE_REACHED",
        }
        cells = w.result_cells(job)
        assert cells["E2"] == 8849 and cells["F2"] == "No"
        assert not any(f"{c}{r}" in cells for r in (3, 4) for c in "DEF")
        assert len(calls(w.log)) == 1
        assert w.report(job)["web_lookup"]["requests"] == 1


def test_a_run_that_is_stopped_still_records_what_it_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No reserve and a long deadline, so the supervisor's limit is what stops the run.
    monkeypatch.setattr(plan_run_worker, "FINISH_RESERVE_SECONDS", -1000.0)
    with world(tmp_path, monkeypatch, delay=5.0) as w:
        draft = w.draft(FOUND_ROWS + [["Annapurna", "Nepal", 6, None]], range_ref="A1:D5")
        job = w.run(draft, timeout_seconds=12)
        assert job["status"] == "timed_out", job
        assert "result" not in job["artifacts"]
        spent = w.report(job)["web_lookup"]
        assert spent["configuration_id"] == CONFIGURATION
        assert 1 <= spent["requests"] <= 3
        assert spent["input_tokens"] == 1000 * spent["requests"]
        assert spent["output_tokens"] == 200 * spent["requests"]
        events = [
            e
            for e in w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
            if e["kind"] == "model"
        ]
        assert [e["requests"] for e in events] == [spent["requests"]]
        assert events[0]["purpose"] == "web_lookup"


# --- The key --------------------------------------------------------------------------


def test_the_key_is_in_no_file_no_response_and_no_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    with world(tmp_path, monkeypatch) as w:
        # The check can fail: the key is found where it is put.
        planted = w.directory / "planted.txt"
        planted.write_text(f"x {KEY} y", encoding="utf-8")
        assert any(KEY.encode() in data for _, data in everything_written(w.directory))
        planted.unlink()

        job = w.run(w.draft(rows=ROWS, sources_in_workbook="sheet"))
        assert job["status"] == "needs_review"
        assert calls(w.log) and all(call["key_ok"] for call in calls(w.log))  # it did arrive
        w.client.get(f"/api/jobs/{job['job_id']}/artifacts/report")
        w.client.get("/api/usage", headers={"x-admin-code": ADMIN})
        w.client.get("/api/provider-settings")

        files = everything_written(w.directory)
        assert len(files) > 3 and any(name.endswith("report.json") for name, _ in files)
        for name, data in files:
            assert KEY.encode() not in data, name
        for body in w.bodies:
            assert KEY.encode() not in body
        out, err = capfd.readouterr()
        assert KEY not in out + err + caplog.text
        assert KEY not in json.dumps(w.client.app.state.usage.summary())  # type: ignore[attr-defined]
        assert KEY not in repr(w.service.jobs) + repr(w.service.plans)
        lookup = plan_run_worker.LookupSettings(
            model="m", reasoning_effort="low", api_key=plan_run.SecretStr(KEY), deadline_seconds=1
        )
        assert KEY not in repr(lookup) + str(lookup)


def test_a_failure_that_quotes_the_key_does_not_reach_a_file_or_a_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    with world(tmp_path, monkeypatch, factory=LeakyFactory()) as w:
        job = w.run(w.draft(FOUND_ROWS, range_ref="A1:D4"))
        assert job["status"] == "failed" and job["error"]["code"] == "execution_failed"
        w.client.get(job["artifacts"]["report"])
        for name, data in everything_written(w.directory):
            assert KEY.encode() not in data, name
        for body in w.bodies:
            assert KEY.encode() not in body
        out, err = capfd.readouterr()
        assert KEY not in out + err + caplog.text
        # The worker did log the failure, with the key taken out.
        assert "Illegal header value" in err and "[key]" in err
