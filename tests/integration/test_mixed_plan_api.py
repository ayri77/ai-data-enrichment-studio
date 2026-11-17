"""A mixed plan through the HTTP API and the run worker, offline.

The planner draft leaves one chosen column unfilled (the geological age of a mountain has no
source here); the universal search is asked for exactly that column and the server joins the
two: the planner's steps for the height, one web lookup step for the age. The routes, the
gates, the worker process, the real executor, handlers, reader and workbook writer are real;
the source steps are answered by the offline fixtures and the lookup by a fake model client
made inside the worker (``tests.support.lookup_api``). Nothing reaches a network.
"""

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from tests.integration.test_plan_run_api import annapurna_executor
from tests.integration.test_web_lookup_api import ADMIN, KEY, everything_written
from tests.support.lookup_api import LoggedApiFactory, calls, forbidden_api
from tests.support.planning import MOUNTAINS_AGE_INSTRUCTION, ScriptedPlanClient, ready_response
from tests.support.web_lookup import PAGE, response, slot

from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.capabilities.fallback import plan_shape
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.plan_run import (
    TERMINAL,
    PlanRunService,
    PlanRunSettings,
    draft_fingerprint,
)
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.run_limits import MAX_TIMEOUT_SECONDS

SHEET = "Sheet1"
COLUMNS = {"height": "C", "age": "D"}
CONTEXT = ["Mountain", "Country"]
TARGET = {"column": "age", "kind": "number", "unit": "million years"}


def age_answer(kwargs: dict[str, Any]) -> Any:
    """What the fake model says for one mountain: an age, or nothing for K2."""
    if "- Mountain: K2" in kwargs["input"]:
        return response({"value_1": slot(unit=None)})
    return response(
        {
            "value_1": slot(
                value=50, unit="million years", basis="concluded", sources=[PAGE], quote="uplift"
            )
        }
    )


def heights_workbook() -> bytes:
    """The mountains table with a number in every cell of the height column."""
    book = load_workbook("samples/mountains.xlsx")
    for row in range(2, 12):
        book[SHEET][f"C{row}"] = 1000 * row
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("An offline test attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)
    monkeypatch.setattr(UrllibJsonTransport, "post_json", denied)
    monkeypatch.setattr(plan_run, "openai_api", forbidden_api)
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def partial_answer(unfilled: list[str]) -> dict[str, Any]:
    return {**ready_response("mountains"), "unfilled_targets": unfilled}


@dataclass
class World:
    client: TestClient
    service: PlanRunService
    log: Path

    @property
    def directory(self) -> Path:
        return self.client.app.state.directory  # type: ignore[attr-defined]

    def upload(self, content: bytes | None = None) -> str:
        result = self.client.post(
            "/api/datasets",
            params={"filename": "mountains.xlsx"},
            content=content or Path("samples/mountains.xlsx").read_bytes(),
        )
        assert result.status_code == 201, result.text
        return result.json()["dataset_id"]

    def planner(self, dataset_id: str | None = None, **changes: Any) -> dict[str, Any]:
        body = {
            "dataset_id": dataset_id or self.upload(),
            "sheet_name": SHEET,
            "range_ref": "A1:C11",
            "instruction": MOUNTAINS_AGE_INSTRUCTION,
            "output_columns": COLUMNS,
            **changes,
        }
        result = self.client.post("/api/plans", json=body)
        assert result.status_code == 201, result.text
        return result.json()

    def ask(self, plan_id: str | None, **changes: Any):
        body: dict[str, Any] = {
            "context_columns": CONTEXT,
            "targets": [TARGET],
            **({"plan_id": plan_id} if plan_id else {}),
            **changes,
        }
        return self.client.post("/api/fallback-plans", json=body)

    def mixed(self, base: dict[str, Any] | None = None, **changes: Any) -> dict[str, Any]:
        base = base or self.planner()
        result = self.ask(base["plan_id"], **changes)
        assert result.status_code == 201, result.text
        return result.json()

    def approve(self, draft: dict[str, Any]) -> None:
        result = self.client.post(
            f"/api/plans/{draft['plan_id']}/approve",
            json={"approved": True, "fingerprint": draft["fingerprint"]},
        )
        assert result.status_code == 200, result.text

    def start(self, draft: dict[str, Any], **body: Any):
        return self.client.post(f"/api/plans/{draft['plan_id']}/runs", json=body)

    def wait(self, job: dict[str, Any]) -> dict[str, Any]:
        deadline = monotonic() + 60
        while monotonic() < deadline:
            current = self.client.get(f"/api/jobs/{job['job_id']}").json()
            if current["status"] in TERMINAL:
                return current
            sleep(0.05)
        pytest.fail("The worker did not finish in time")

    def run(self, draft: dict[str, Any], **body: Any) -> dict[str, Any]:
        self.approve(draft)
        started = self.start(draft, **body)
        assert started.status_code == 202, started.text
        return self.wait(started.json())

    def report(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(job["artifacts"]["report"]).json()

    def book(self, job: dict[str, Any]):
        return load_workbook(BytesIO(self.client.get(job["artifacts"]["result"]).content))

    def cells(self, job: dict[str, Any]) -> dict[str, Any]:
        book = self.book(job)
        try:
            return {
                cell.coordinate: cell.value
                for row in book[SHEET].iter_rows()
                for cell in row
                if cell.value is not None
            }
        finally:
            book.close()


@contextmanager
def world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    key: str | None = KEY,
    plans: int = 6,
    stand_in: bool = True,
    network: bool = False,
    factory: Any = annapurna_executor,
) -> Iterator[World]:
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    log = tmp_path / "requests.jsonl"
    app = create_app(
        settings=PlanRunSettings(allow_web_lookup=enabled, allow_network=network),
        offline_plan_client=ScriptedPlanClient(*[partial_answer(["age"])] * plans),
        offline_executor_factory=factory,
        offline_lookup_api=LoggedApiFactory(KEY, log, answer=age_answer) if stand_in else None,
        admin_code=ADMIN,
    )
    with TestClient(app) as client:
        yield World(client, app.state.plan_run, log)


# --- Making the draft -------------------------------------------------------------------


def test_the_mixed_draft_completes_a_planner_draft_and_leaves_it_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        assert base["unfilled_targets"] == ["age"] and "kind" not in base
        stored = copy.deepcopy(w.service.plans[base["plan_id"]])

        created = w.ask(base["plan_id"])
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["plan_id"] != base["plan_id"] and draft["kind"] == "mixed"
        assert draft["status"] == "draft" and draft["planning_status"] == "ready"
        # What the planner draft decided is kept; what the lookup fills is named.
        assert draft["dataset_id"] == base["dataset_id"]
        assert (draft["sheet_name"], draft["range_ref"]) == (SHEET, "A1:C11")
        assert draft["output_columns"] == COLUMNS
        assert draft["unfilled_targets"] == [] and draft["searched_targets"] == ["age"]
        assert draft["planner_plan_id"] == base["plan_id"]
        assert draft["existing_outputs"] == "keep" and draft["sources_in_workbook"] == "none"
        assert draft["mountain_resolution_policy"] == "strict"
        assert draft["instruction"] == MOUNTAINS_AGE_INSTRUCTION
        assert draft["validation"]["valid"], draft["validation"]
        assert draft["fingerprint"] == draft_fingerprint(draft) and len(draft["fingerprint"]) == 64
        # The planner's steps and outputs are kept as they were; the lookup is last.
        plan, own = draft["plan"], base["plan"]
        assert plan["steps"][:-1] == own["steps"]
        assert plan["outputs"][:-1] == own["outputs"]
        assert [s["capability"] for s in plan["steps"]] == [
            "resolve_entity",
            "lookup_property",
            "web_lookup",
        ]
        assert plan["steps"][-1]["id"] == "lookup"
        assert plan["outputs"][-1] == {
            "step_id": "lookup",
            "value_field": "value_1",
            "target_column": "age",
        }
        assert plan["assumptions"][-1].startswith("Each value is found by an AI model")
        # The question defaults to the instruction of the planner draft.
        task = json.loads(plan["steps"][-1]["inputs"]["task"]["value"])
        assert task["question"] == MOUNTAINS_AGE_INSTRUCTION.strip()
        assert plan_shape(EnrichmentPlan.model_validate(plan)) == "mixed"
        assert w.client.get(f"/api/plans/{draft['plan_id']}").json() == draft
        assert w.client.post(f"/api/plans/{draft['plan_id']}/validate").json()["valid"]
        # The planner draft is stored as it was.
        assert w.service.plans[base["plan_id"]] == stored
        assert w.client.get(f"/api/plans/{base['plan_id']}").json() == base
        assert w.start(draft).status_code == 409  # not approved yet
        assert not w.log.exists()


def test_the_question_and_the_workbook_note_can_be_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner(sources_in_workbook="sheet", existing_outputs="replace")
        kept = w.mixed(base, question="  How old is it?  ")
        task = json.loads(kept["plan"]["steps"][-1]["inputs"]["task"]["value"])
        assert task["question"] == "How old is it?"
        # Not sent: the planner draft's own choices are kept.
        assert (kept["sources_in_workbook"], kept["existing_outputs"]) == ("sheet", "replace")
        chosen = w.mixed(base, sources_in_workbook="notes")
        assert chosen["sources_in_workbook"] == "notes" and chosen["existing_outputs"] == "replace"


def test_the_draft_is_the_same_bytes_for_the_same_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        first, second = w.mixed(base), w.mixed(base)
        assert first["plan"] == second["plan"]
        assert first["plan_id"] != second["plan_id"]


# --- What is refused --------------------------------------------------------------------


def refusal(result: Any, status: int, code: str, fragment: str = "") -> None:
    assert result.status_code == status, result.text
    error = result.json()["error"]
    assert error["code"] == code and fragment in error["message"], error


def test_every_refusal_of_the_plan_id_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        before = dict(w.service.plans)

        refusal(w.ask("0" * 32), 404, "plan_not_found")
        # A draft the server built is not a planner draft.
        mixed = w.mixed(base)
        refusal(w.ask(mixed["plan_id"]), 422, "mixed_base_not_planner")
        fallback = w.client.post(
            "/api/fallback-plans",
            json={
                "dataset_id": base["dataset_id"],
                "sheet_name": SHEET,
                "range_ref": "A1:C11",
                "question": "Find it.",
                "context_columns": CONTEXT,
                "targets": [TARGET],
                "output_columns": {"age": "D"},
            },
        ).json()
        assert fallback["kind"] == "fallback"
        refusal(w.ask(fallback["plan_id"]), 422, "mixed_base_not_planner")
        # A planner draft that fills every column has nothing to search for.
        full = w.client.post(
            "/api/plans",
            json={
                "dataset_id": base["dataset_id"],
                "sheet_name": SHEET,
                "range_ref": "A1:C11",
                "instruction": "Find the elevation in metres.",
                "output_columns": {"height": "C"},
                "plan": base["plan"],
            },
        ).json()
        assert full["unfilled_targets"] == []
        refusal(w.ask(full["plan_id"]), 422, "mixed_nothing_unfilled")
        # A draft that is not valid is not completed.
        w.service.plans[base["plan_id"]]["instruction"] = "x" * 3
        w.service.plans[base["plan_id"]]["plan"]["steps"][0]["capability"] = "nonexistent"
        refusal(w.ask(base["plan_id"]), 422, "mixed_base_invalid")
        w.service.plans[base["plan_id"]]["plan"]["steps"][0]["capability"] = "resolve_entity"
        w.service.plans[base["plan_id"]]["instruction"] = MOUNTAINS_AGE_INSTRUCTION
        assert w.service.plans[base["plan_id"]]["validation"] == base["validation"]
        assert set(w.service.plans) - set(before) == {
            mixed["plan_id"],
            fallback["plan_id"],
            full["plan_id"],
        }

        # The request names exactly the columns the planner left unfilled.
        for targets, fragment in (
            ([{"column": "height", "kind": "number"}], "exactly the ones the plan left unfilled"),
            ([TARGET, {"column": "extra", "kind": "text"}], "Not left unfilled: extra"),
            ([{"column": "Age", "kind": "number"}], "Missing: age"),
            ([{**TARGET, "kind": "text", "unit": "x"}], "only for a number"),
            ([TARGET, TARGET], "filled twice"),
        ):
            refusal(w.ask(base["plan_id"], targets=targets), 422, "fallback_plan_invalid", fragment)
        # A context column that the plan writes would change during the run.
        # The planner draft of a table whose height column holds numbers: it is a column
        # that could identify a row, but the plan writes to it.
        filled = w.planner(w.upload(content=heights_workbook()))
        n = len(w.service.plans)
        refusal(
            w.ask(filled["plan_id"], context_columns=["Mountain", "height"]),
            422,
            "fallback_plan_invalid",
            "'height' identifies the row for the web lookup, but the plan writes to it",
        )
        refusal(
            w.ask(base["plan_id"], context_columns=["Mountian"]),
            422,
            "fallback_plan_invalid",
            "does not exist",
        )
        refusal(
            w.ask(base["plan_id"], question="   "),
            422,
            "fallback_plan_invalid",
            "must not be blank",
        )
        # The table and the columns come from the planner draft: they are not sent again.
        for field, value in (
            ("dataset_id", base["dataset_id"]),
            ("sheet_name", SHEET),
            ("range_ref", "A1:C11"),
            ("output_columns", COLUMNS),
            ("existing_outputs", "keep"),
        ):
            refusal(
                w.ask(base["plan_id"], **{field: value}),
                422,
                "fallback_plan_invalid",
                f"do not send {field}",
            )
        # Without plan_id the old form needs its fields.
        refusal(w.ask(None), 422, "invalid_request")
        assert len(w.service.plans) == n
        assert w.service.plans[base["plan_id"]]["fingerprint"] == base["fingerprint"]


def test_the_planner_and_a_pasted_plan_still_cannot_bring_a_web_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        mixed = w.mixed()
        body = {
            "dataset_id": mixed["dataset_id"],
            "sheet_name": SHEET,
            "range_ref": "A1:C11",
            "instruction": "x",
            "output_columns": COLUMNS,
            "plan": mixed["plan"],
        }
        refusal(w.client.post("/api/plans", json=body), 422, "web_lookup_plan_refused")
        body["kind"] = "mixed"
        refusal(w.client.post("/api/plans", json=body), 422, "invalid_request")


def test_the_setting_the_key_and_the_stand_in_apply_as_for_a_fallback_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch, enabled=False) as w:
        base = w.planner()
        refusal(w.ask(base["plan_id"]), 403, "web_lookup_disabled")
        assert list(w.service.plans) == [base["plan_id"]]
    with world(tmp_path, monkeypatch, key=None) as w:
        base = w.planner()
        refusal(w.ask(base["plan_id"]), 422, "web_lookup_no_key")
    with world(tmp_path, monkeypatch, stand_in=False) as w:
        base = w.planner()
        refusal(w.ask(base["plan_id"]), 403, "web_lookup_disabled")
        assert list(w.service.plans) == [base["plan_id"]]


def test_a_run_is_refused_when_the_setting_or_the_key_is_gone_after_the_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.mixed()
        w.approve(draft)
        w.service.settings = PlanRunSettings(allow_web_lookup=False)
        refusal(w.start(draft), 403, "web_lookup_disabled")
        w.service.settings = PlanRunSettings(allow_web_lookup=True)
        assert w.start(draft).status_code == 202
        w.wait(next(iter(w.service.jobs.values())))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        w.service.provider = type(w.service.provider).from_environment(None)
        refusal(w.start(draft), 422, "web_lookup_no_key")


def test_the_plan_ids_make_room_never_drops_the_draft_it_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_run, "MAX_PLANS", 3)
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()  # the oldest plan: the first one the normal rule would drop
        other = w.planner()
        third = w.planner()
        assert list(w.service.plans) == [base["plan_id"], other["plan_id"], third["plan_id"]]
        first = w.mixed(base)
        assert base["plan_id"] in w.service.plans and other["plan_id"] not in w.service.plans
        assert list(w.service.plans) == [base["plan_id"], third["plan_id"], first["plan_id"]]
        # And again: the oldest after the base goes, never the base.
        second = w.mixed(base)
        assert list(w.service.plans) == [base["plan_id"], first["plan_id"], second["plan_id"]]
    # Nothing but the base is left to drop: refused, and nothing was changed.
    monkeypatch.setattr(plan_run, "MAX_PLANS", 1)
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        refusal(w.ask(base["plan_id"]), 409, "workspace_full")
        assert list(w.service.plans) == [base["plan_id"]]
        assert w.service.plans[base["plan_id"]]["fingerprint"] == base["fingerprint"]


# --- The gate ---------------------------------------------------------------------------


def test_the_gate_refuses_every_mismatch_of_kind_and_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        mixed = w.mixed(base)
        lookup_only = {
            **mixed["plan"],
            "steps": [mixed["plan"]["steps"][-1]],
            "outputs": mixed["plan"]["outputs"][-1:],
        }
        planner_only = base["plan"]
        cases = [
            ("planner", mixed["plan"]),
            ("planner", lookup_only),
            ("fallback", mixed["plan"]),
            ("fallback", planner_only),
            ("mixed", planner_only),
            ("mixed", lookup_only),
            ("other", mixed["plan"]),
        ]
        for kind, plan in cases:
            draft = copy.deepcopy(mixed)
            draft["kind"] = kind
            draft["plan"] = plan
            with pytest.raises(plan_run.WorkspaceError) as caught:
                PlanRunService.plan_gate(draft, EnrichmentPlan.model_validate(plan))
            assert caught.value.code == "web_lookup_plan_refused", (
                kind,
                plan_shape(EnrichmentPlan.model_validate(plan)),
            )
            assert w.service.validate(draft)["errors"][0]["code"] == "web_lookup_plan_refused"
        # The three that agree pass.
        for kind, plan in (
            ("planner", planner_only),
            ("fallback", lookup_only),
            ("mixed", mixed["plan"]),
        ):
            PlanRunService.plan_gate({"kind": kind}, EnrichmentPlan.model_validate(plan))
        PlanRunService.plan_gate({}, EnrichmentPlan.model_validate(planner_only))


def test_a_draft_changed_after_approval_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.mixed()
        w.approve(draft)
        stored = w.service.plans[draft["plan_id"]]
        original = copy.deepcopy(stored)

        def refused(code: str) -> None:
            result = w.start(draft)
            assert result.status_code in (409, 422), result.text
            assert result.json()["error"]["code"] == code
            assert w.service.jobs == {}

        stored["kind"] = "fallback"
        refused("approval_mismatch")
        del stored["kind"]
        refused("approval_mismatch")
        stored["kind"] = "mixed"
        stored["searched_targets"] = []
        refused("approval_mismatch")
        stored["searched_targets"] = ["age"]
        stored["unfilled_targets"] = ["age"]
        refused("approval_mismatch")
        stored["unfilled_targets"] = []
        stored["plan"]["steps"][-1]["inputs"]["task"]["value"] += " "
        refused("approval_mismatch")
        stored["plan"] = copy.deepcopy(original["plan"])
        # A fingerprint made to fit does not help when kind and plan disagree.
        stored["kind"] = "fallback"
        stored["fingerprint"] = draft_fingerprint(stored)
        refused("web_lookup_plan_refused")
        stored.clear()
        stored.update(original)
        assert w.start(draft).status_code == 202


# --- Running it -------------------------------------------------------------------------


def test_the_run_fills_the_source_column_and_the_searched_column_each_with_its_own_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner(sources_in_workbook="notes")
        draft = w.mixed(base)
        job = w.run(draft)
        assert job["kind"] == "mixed" and job["timeout_seconds"] == MAX_TIMEOUT_SECONDS
        assert job["status"] == "needs_review", job
        cells = w.cells(job)
        # The age comes from the lookup in every row but K2 (the fake found none there).
        ages = {r for r in range(2, 12) if cells.get(f"D{r}") == 50}
        assert ages == set(range(2, 12)) - {3}
        assert cells["D1"] == "age" and cells["C1"] == "height"
        # The height comes from the source steps where the fixtures verify it.
        heights = {r: cells[f"C{r}"] for r in range(2, 12) if f"C{r}" in cells}
        assert heights and all(isinstance(v, int | float) for v in heights.values())

        report = w.report(job)
        assert report["kind"] == "mixed" and report["approved_plan"]["kind"] == "mixed"
        assert report["searched_targets"] == ["age"] and report["unfilled_targets"] == []
        assert report["web_lookup"] == {
            "configuration_id": "openai-web-lookup-p1-gpt-6-luna-low",
            "requests": 10,
            "input_tokens": 10000,
            "output_tokens": 2000,
            "web_searches": 20,
            "answers_kept": 0,
        }
        assert report["step_provenance"] == [
            {
                "step_id": "resolve",
                "capability": "resolve_entity",
                "source": "wikidata",
                "trust": "resolved_name",
            },
            {
                "step_id": "elevation",
                "capability": "lookup_property",
                "source": "wikidata",
                "trust": "record",
            },
            {
                "step_id": "lookup",
                "capability": "web_lookup",
                "source": "openai_web_search",
                "trust": "ai_found",
            },
        ]
        # ``ai_found`` lists the searched column only, never the height.
        found = {row["row"]: row["ai_found"] for row in report["rows"]}
        assert found[2] == [{"column": "age", "basis": "concluded", "links": 1}]
        assert found[3] == [] and PAGE not in json.dumps(found)
        assert all(c["column"] == "age" for entries in found.values() for c in entries)
        assert report["sources_in_workbook"] == {"mode": "notes", "applied": True}

        # Each column carries its own kind of trust in its notes.
        book = w.book(job)
        try:
            height_note = book[SHEET]["C2"].comment.text
            age_note = book[SHEET]["D2"].comment.text
        finally:
            book.close()
        assert "Source: Wikidata" in height_note and "found by an AI web search" not in height_note
        assert "Source: OpenAI web search" in age_note
        assert (
            "found by an AI web search, not verified" in age_note
            and f"Reference: {PAGE}" in age_note
        )

        # The lookup asked once per row; the planner's model events are the planner's own.
        asked = calls(w.log)
        assert len(asked) == 10 and all(call["key_ok"] for call in asked)
        events = [
            e
            for e in w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
            if e["kind"] == "model"
        ]
        assert sorted((e["purpose"], e["model"], e["requests"]) for e in events) == [
            ("plan", "offline", 1),
            ("web_lookup", "gpt-6-luna", 10),
        ]
        [spent] = [e for e in events if e["purpose"] == "web_lookup"]
        assert (spent["input_tokens"], spent["output_tokens"], spent["web_searches"]) == (
            10000,
            2000,
            20,
        )
        # The planner draft was not run and is still there, unchanged.
        assert w.client.get(f"/api/plans/{base['plan_id']}").json() == base


def row_of(report: dict[str, Any], row: int) -> dict[str, Any]:
    return next(item for item in report["rows"] if item["row"] == row)


def test_review_and_resume_work_for_the_source_steps_and_the_lookup_is_not_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.mixed()
        first = w.run(draft)
        assert first["status"] == "needs_review" and first["kind"] == "mixed"
        assert len(calls(w.log)) == 10

        # The name choice of the source step is in Review as for a planner job.
        review = w.client.get(f"/api/jobs/{first['job_id']}/review").json()
        [item] = [i for i in review["items"] if i["query"]["name"] == "Annapurna"]
        assert item["capability"] == "resolve_entity" and item["provider_id"] == "wikidata"
        assert [(a["row"], a["step_id"]) for a in item["affected"]] == [(11, "resolve")]
        assert {c["entity_id"] for c in item["candidates"] if c["selectable"]} == {"Q9001"}

        # The row whose source step needs review still has its searched column: the writing
        # rules put each successful step's outputs in the row, whatever the other steps did.
        cells = w.cells(first)
        assert "C11" not in cells and cells["D11"] == 50
        before = row_of(w.report(first), 11)
        assert before["status"] != "success"
        assert before["ai_found"] == [{"column": "age", "basis": "concluded", "links": 1}]
        steps = {
            step["step_id"]: step["result"]["status"]
            for record in w.report(first)["execution"]["records"]
            if record["record_id"] == "Sheet1!11"
            for step in record["row_result"]["steps"]
        }
        assert steps["lookup"] == "success" and steps["resolve"] != "success"

        decision = {
            "operation_id": item["operation_id"],
            "source": "wikidata",
            "entity_id": "Q9001",
        }
        resumed = w.client.post(
            f"/api/jobs/{first['job_id']}/resume", json={"decisions": [decision]}
        )
        assert resumed.status_code == 202, resumed.text
        queued = resumed.json()
        assert queued["kind"] == "mixed" and queued["previous_job_id"] == first["job_id"]
        assert queued["plan_id"] == draft["plan_id"]
        assert queued["timeout_seconds"] == MAX_TIMEOUT_SECONDS
        second = w.wait(queued)
        # K2 stays unfinished: the fake model found no age for it.
        assert second["status"] == "needs_review", second
        assert second["metrics"]["successful_rows"] == first["metrics"]["successful_rows"] + 1

        # The chosen name fills the source column; the searched column was not asked for again.
        cells = w.cells(second)
        assert cells["C11"] == 8091 and cells["D11"] == 50
        # Nine rows were answered by the first job and are taken over, and so is the tenth
        # (K2): "nothing found" is an answer of the search and is kept like the others.
        assert len(calls(w.log)) == 10
        report = w.report(second)
        assert report["web_lookup"]["requests"] == 0 and report["web_lookup"]["answers_kept"] == 10
        lookup_codes = [
            step["result"]["error_code"]
            for record in report["execution"]["records"]
            for step in record["row_result"]["steps"]
            if step["step_id"] == "lookup"
        ]
        assert lookup_codes.count("NOTHING_FOUND") == 1 and len(lookup_codes) == 10
        assert report["previous_job_id"] == first["job_id"] and report["kind"] == "mixed"
        row = row_of(report, 11)
        assert row["status"] == "success" and row["user_chosen_steps"] == ["resolve"]
        assert row["ai_found"] == [{"column": "age", "basis": "concluded", "links": 1}]
        assert report["searched_targets"] == ["age"]
        # The second run made no request: only the first job's ten are in the spend.
        events = [
            e
            for e in w.client.app.state.usage.summary()["recent"]  # type: ignore[attr-defined]
            if e["kind"] == "model" and e["purpose"] == "web_lookup"
        ]
        assert sorted(e["requests"] for e in events) == [10]
        # The first job is untouched.
        assert w.client.get(f"/api/jobs/{first['job_id']}").json() == first
        assert "C11" not in w.cells(first)


def test_an_explicit_timeout_is_kept_by_a_mixed_run_and_by_its_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.mixed()
        w.approve(draft)
        started = w.start(draft, timeout_seconds=120)
        assert started.status_code == 202 and started.json()["timeout_seconds"] == 120
        first = w.wait(started.json())
        review = w.client.get(f"/api/jobs/{first['job_id']}/review").json()
        [item] = [i for i in review["items"] if i["query"]["name"] == "Annapurna"]
        decision = {
            "operation_id": item["operation_id"],
            "source": "wikidata",
            "entity_id": "Q9001",
        }
        # Without a limit in the request the resume has the longest one, like the start.
        for body, expected in (({}, MAX_TIMEOUT_SECONDS), ({"timeout_seconds": 90}, 90)):
            resumed = w.client.post(
                f"/api/jobs/{first['job_id']}/resume", json={"decisions": [decision], **body}
            )
            assert resumed.status_code == 202, resumed.text
            assert resumed.json()["timeout_seconds"] == expected
            w.wait(resumed.json())


def test_running_the_plan_again_takes_over_the_answers_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        draft = w.mixed()
        w.run(draft)
        assert len(calls(w.log)) == 10
        again = w.wait(w.start(draft).json())
        # K2, for which nothing was found, is kept too: no request at all.
        assert len(calls(w.log)) == 10
        assert w.report(again)["web_lookup"]["answers_kept"] == 10


def test_a_pure_fallback_job_still_cannot_be_resumed_and_a_planner_job_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(tmp_path, monkeypatch) as w:
        base = w.planner()
        pure = w.client.post(
            "/api/fallback-plans",
            json={
                "dataset_id": base["dataset_id"],
                "sheet_name": SHEET,
                "range_ref": "A1:C11",
                "question": "How old is this mountain?",
                "context_columns": CONTEXT,
                "targets": [TARGET],
                "output_columns": {"age": "D"},
            },
        ).json()
        job = w.run(pure)
        assert pure["kind"] == "fallback" and job["kind"] == "fallback"
        assert "searched_targets" not in pure and "searched_targets" not in w.report(job)
        resumed = w.client.post(
            f"/api/jobs/{job['job_id']}/resume",
            json={"decisions": [{"operation_id": "op", "name": "x"}]},
        )
        assert resumed.status_code == 422
        asked = len(calls(w.log))

        # A planner draft with an unfilled column runs as before: no lookup, no kind.
        planner_job = w.run(base)
        assert "kind" not in planner_job and planner_job["status"] == "needs_review"
        report = w.report(planner_job)
        assert "web_lookup" not in report and "searched_targets" not in report
        assert report["unfilled_targets"] == ["age"]
        assert all("ai_found" not in row for row in report["rows"])
        assert len(calls(w.log)) == asked
        assert "D1" not in w.cells(planner_job)


def test_the_key_is_in_no_file_no_response_and_no_log_of_a_mixed_run(
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

        bodies: list[bytes] = []
        original = w.client.request

        def spy(*args: Any, **kwargs: Any):
            result = original(*args, **kwargs)
            bodies.append(result.content)
            return result

        monkeypatch.setattr(w.client, "request", spy)
        draft = w.mixed(w.planner(sources_in_workbook="sheet"))
        first = w.run(draft)
        review = w.client.get(f"/api/jobs/{first['job_id']}/review").json()
        item = next(i for i in review["items"] if i["query"]["name"] == "Annapurna")
        decision = {
            "operation_id": item["operation_id"],
            "source": "wikidata",
            "entity_id": "Q9001",
        }
        second = w.wait(
            w.client.post(
                f"/api/jobs/{first['job_id']}/resume", json={"decisions": [decision]}
            ).json()
        )
        assert calls(w.log) and all(call["key_ok"] for call in calls(w.log))  # it did arrive
        for job in (first, second):
            w.client.get(job["artifacts"]["report"])
        w.client.get("/api/usage", headers={"x-admin-code": ADMIN})
        w.client.get("/api/provider-settings")

        files = everything_written(w.directory)
        assert len(files) > 5 and any(name.endswith("report.json") for name, _ in files)
        for name, data in files:
            assert KEY.encode() not in data, name
        assert len(bodies) > 8
        for body in bodies:
            assert KEY.encode() not in body
        out, err = capfd.readouterr()
        assert KEY not in out + err + caplog.text
        assert KEY not in json.dumps(w.client.app.state.usage.summary())  # type: ignore[attr-defined]
        assert KEY not in repr(w.service.jobs) + repr(w.service.plans)


def test_a_planner_draft_run_with_the_default_offline_fixtures_also_completes_a_mixed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plain offline executor factory is enough: no special fixture is needed."""
    with world(tmp_path, monkeypatch, factory=offline_executor) as w:
        job = w.run(w.mixed())
        cells = w.cells(job)
        assert job["kind"] == "mixed" and cells["D2"] == 50
        assert len(calls(w.log)) == 10
