"""Review items: which candidates are selectable and which one is suggested."""

from typing import Any

from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionRecordLink,
)
from ai_data_enrichment.review import (
    ReviewDecision,
    approvals_from_decisions,
    other_name_audit,
    review_payload,
    searched_steps,
)

STRICT = ResolutionPolicy(policy_id="capital_strict", version="1.0", strategy="strict")
PLAN = EnrichmentPlan.model_validate(
    {
        "version": "1.0",
        "steps": [
            {
                "id": "place",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "city"},
                    "country": {"kind": "column", "name": "country"},
                },
                "parameters": {},
            }
        ],
        "outputs": [],
        "assumptions": [],
    }
)


def location(location_id: int, feature_code: str, population: int | None = None) -> dict[str, Any]:
    return {
        "id": location_id,
        "name": "Berlin",
        "country_code": "DE",
        "feature_code": feature_code,
        "admin1": f"Region {location_id}",
        "latitude": 50.0 + location_id,
        "longitude": 13.4,
        **({"population": population} if population is not None else {}),
    }


def job_report(*results: dict[str, Any]) -> ResolutionJobReport:
    outcome = OpenMeteoResolutionService(StubJsonTransport({"results": list(results)})).resolve(
        inputs={"city": "Berlin", "country": "Germany"}, policy=STRICT
    )
    return ResolutionJobReport(
        job_id="job-1",
        status="awaiting_review",
        total_rows=2,
        processed_rows=2,
        operations=(
            ResolutionOperationAudit(
                operation_id="operation-1",
                report=outcome.report,
                record_links=(
                    ResolutionRecordLink(record_id="Sheet1!2", step_id="place"),
                    ResolutionRecordLink(record_id="Sheet1!7", step_id="place"),
                ),
            ),
        ),
    )


def candidates(*results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = review_payload("job-1", job_report(*results), PLAN)
    assert len(payload["items"]) == 1
    return {c["entity_id"]: c for c in payload["items"][0]["candidates"]}


def test_capital_is_suggested_over_a_namesake_and_listed_first() -> None:
    payload = review_payload("job-1", job_report(location(2, "PPL"), location(1, "PPLC")), PLAN)
    listed = payload["items"][0]["candidates"]
    assert [c["entity_id"] for c in listed] == ["1", "2"]
    assert listed[0]["recommended"] and not listed[1]["recommended"]
    assert listed[0]["recommendation_reasons"] == ["country_capital_role"]
    assert listed[0]["attributes"]["administrative_role"] == "country_capital"


def test_larger_population_is_suggested_among_peers_only_when_all_are_known() -> None:
    known = candidates(location(1, "PPL", 500), location(2, "PPL", 9000))
    assert known["2"]["recommended"] and not known["1"]["recommended"]
    assert known["2"]["recommendation_reasons"] == ["population_recorded"]
    partly_known = candidates(location(1, "PPL", 500), location(2, "PPL"))
    assert not any(c["recommended"] for c in partly_known.values())


def test_excluded_candidates_are_shown_but_never_selectable_or_suggested() -> None:
    listed = candidates(location(1, "PPL"), location(2, "PPL"), location(3, "PPLX"))
    assert listed["3"]["selectable"] is False and listed["3"]["blocker"] == "excluded"
    assert listed["3"]["reason_codes"] == ["entity_type_mismatch"]
    assert not listed["3"]["recommended"]
    assert listed["1"]["selectable"] and listed["2"]["selectable"]


def test_resolved_operations_and_missing_reports_need_no_review() -> None:
    assert review_payload("job-1", job_report(location(1, "PPLC")), PLAN)["items"] == []
    empty = review_payload("job-1", None, PLAN)
    assert empty["items"] == [] and empty["informational"] == [] and empty["affected_rows"] == 0


def test_one_decision_expands_to_every_record_and_step_sharing_the_operation() -> None:
    report = job_report(location(1, "PPL"), location(2, "PPL"))
    manifest = approvals_from_decisions(
        [ReviewDecision(operation_id="operation-1", source="open-meteo", entity_id="2")],
        report,
        workbook_sha256="a" * 64,
    )
    assert manifest is not None and manifest.source_job_id == "job-1"
    assert [(a.record_id, a.step_id, a.entity_id) for a in manifest.approvals] == [
        ("Sheet1!2", "place", "2"),
        ("Sheet1!7", "place", "2"),
    ]
    skipped = approvals_from_decisions(
        [ReviewDecision(operation_id="operation-1")], report, workbook_sha256="a" * 64
    )
    assert skipped is None


def test_the_origin_of_another_name_comes_from_what_the_model_proposed_not_the_request() -> None:
    report = job_report(location(1, "PPL"), location(2, "PPL"))
    [operation] = report.operations
    operation_id, name = operation.operation_id, operation.report.candidate_set.query.name
    proposals = {operation_id: ("Munich", "Monaco di Baviera")}

    def origin(decision: dict[str, Any], proposed=proposals) -> list[str]:
        entries = other_name_audit([ReviewDecision(**decision)], report, proposed, "job")
        return [entry["origin"] for entry in entries]

    typed = {"operation_id": operation_id, "name": "Muenchen"}
    assert origin(typed) == ["user"]
    # Spacing and letter case do not make a proposed name the user's own.
    [entry] = other_name_audit(
        [ReviewDecision(operation_id=operation_id, name="  monaco  di baviera ")],
        report,
        proposals,
        "job",
    )
    assert entry == {
        "job_id": "job",
        "operation_id": operation_id,
        "name": name,
        "searched_as": "monaco di baviera",
        "origin": "model",
    }
    # A proposal for another item, or none at all, is not a proposal for this one.
    assert origin({"operation_id": operation_id, "name": "Munich"}, {"other": ("Munich",)}) == [
        "user"
    ]
    assert origin({"operation_id": operation_id, "name": "Munich"}, {}) == ["user"]
    # A chosen candidate without a name, and an unknown operation, give no entry.
    assert origin({"operation_id": operation_id, "source": "open-meteo", "entity_id": "1"}) == []
    assert origin({"operation_id": "unknown", "name": "Munich"}) == []


def test_rows_say_under_which_name_a_value_was_found_only_when_one_was_selected() -> None:
    def operation(name: str, searched_as: str | None, selected: bool) -> dict[str, Any]:
        return {
            "report": {
                "candidate_set": {"query": {"name": name, "searched_as": searched_as}},
                "decision": {"selected": {"entity_id": "1"} if selected else None},
            },
            "record_links": [{"record_id": f"row-{name}", "step_id": "geocode"}],
        }

    resolution = {
        "operations": [
            operation("München", "Munich", True),
            operation("Kiev", "Kyiv", False),
            operation("Paris", None, True),
            operation("Wien", "Vienna", True),
        ]
    }
    entries = [{"name": "München", "searched_as": "Munich", "origin": "model"}]
    assert searched_steps(resolution, entries) == {
        "row-München": {"geocode": {"searched_as": "Munich", "origin": "model"}},
        # Searched under another name by an earlier run whose record is gone.
        "row-Wien": {"geocode": {"searched_as": "Vienna"}},
    }
    assert searched_steps(None, entries) == {}
