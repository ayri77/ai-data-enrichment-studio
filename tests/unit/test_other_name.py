"""A row looked up under another name: same source, same checks, both names audited."""

from collections.abc import Mapping
from typing import Any

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, PlanStep
from ai_data_enrichment.execution.models import RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.open_meteo_approved import ApprovedOpenMeteoResolutionService
from ai_data_enrichment.providers.open_meteo_resolution import (
    GeocodingResolutionOutcome,
    OpenMeteoResolutionService,
)
from ai_data_enrichment.resolution.approvals import ResolutionApproval
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionRecordLink,
    ResolutionReport,
)
from ai_data_enrichment.resolution.other_name import (
    OtherNameResolutionService,
    same_name,
    searched_under,
)
from ai_data_enrichment.resolution.resume import OtherName, accepts_other_name, build_other_names
from ai_data_enrichment.review import ReviewDecision, review_payload, user_chosen_steps

STRICT = ResolutionPolicy(policy_id="capital_strict", version="1.0", strategy="strict")
ROW = {"city": "München", "country": "Germany"}


def munich(
    location_id: int = 2867714, feature_code: str = "PPLA", name: str = "Munich"
) -> dict[str, Any]:
    return {
        "id": location_id,
        "name": name,
        "country_code": "DE",
        "feature_code": feature_code,
        "admin1": f"Region {location_id}",
        "latitude": 48.0 + location_id / 10_000_000,
        "longitude": 11.5,
    }


def renamed(*results: dict[str, Any], error: Exception | None = None):
    transport = StubJsonTransport({"results": list(results)}, error=error)
    service = OtherNameResolutionService(
        OpenMeteoResolutionService(transport),
        capability="geocode",
        row_name="München",
        searched_as="  Munich ",
    )
    return service, transport


def test_the_source_is_asked_for_the_given_name_and_the_audit_keeps_both() -> None:
    service, transport = renamed(munich())
    outcome = service.resolve(inputs=ROW, policy=STRICT, parameters={})
    assert transport.requests[0][1]["name"] == "Munich"
    assert transport.requests[0][1]["countryCode"] == "DE"
    query = outcome.report.candidate_set.query
    assert (query.name, query.searched_as) == ("München", "Munich")
    # The decision is the policy's own: one verified match, nothing weakened.
    assert outcome.operation.status == "success"
    assert outcome.report.decision.basis == "unique_verified"


def test_several_matches_under_the_given_name_still_need_a_choice() -> None:
    service, _ = renamed(munich(1), munich(2))
    outcome = service.resolve(inputs=ROW, policy=STRICT, parameters={})
    assert outcome.operation.status == "failure"
    assert outcome.report.decision.status == "needs_review"
    assert outcome.report.candidate_set.query.searched_as == "Munich"


def test_rows_that_share_the_name_share_one_search() -> None:
    service, transport = renamed(munich())
    first = service.resolve(inputs=ROW, policy=STRICT, parameters={})
    assert service.resolve(inputs=dict(ROW), policy=STRICT, parameters={}) is first
    assert len(transport.requests) == 1


def test_a_source_failure_is_not_kept_for_the_next_row() -> None:
    service, transport = renamed(error=JsonTransportError("network"))
    for _ in range(2):
        outcome = service.resolve(inputs=ROW, policy=STRICT, parameters={})
        assert outcome.report.decision.status == "provider_failure"
        assert outcome.report.candidate_set.query.searched_as == "Munich"
    assert len(transport.requests) == 2


@pytest.mark.parametrize("city", ["Nürnberg", 12, None])
def test_a_row_that_no_longer_holds_the_reviewed_value_is_rejected(city) -> None:
    service, transport = renamed(munich())
    with pytest.raises(ValueError):
        service.resolve(inputs={**ROW, "city": city}, policy=STRICT, parameters={})
    assert transport.requests == []


def step(capability: str = "geocode", parameters: dict | None = None, **inputs: Any) -> PlanStep:
    return PlanStep.model_validate(
        {
            "id": "place",
            "capability": capability,
            "inputs": inputs
            or {
                "city": {"kind": "column", "name": "city"},
                "country": {"kind": "column", "name": "country"},
            },
            "parameters": parameters or {},
        }
    )


def test_the_step_must_be_the_audited_lookup_of_the_same_source() -> None:
    service, _ = renamed(munich())
    service.validate_step(step(), provider_id="open-meteo")
    service.validate_step(
        step(parameters={"required_role": "country_capital"}), provider_id="open-meteo"
    )
    for other, provider in (
        (step("resolve_entity", {"entity_type": "mountain"}), "wikidata"),
        (step(), "wikidata"),
        (step(parameters={"required_role": "largest_city"}), "open-meteo"),
    ):
        with pytest.raises(ValueError):
            service.validate_step(other, provider_id=provider)


class MountainStub:
    """Accepts no step parameters, as the mountain service does."""

    def __init__(self, found: GeocodingResolutionOutcome) -> None:
        self.found = found
        self.names: list[object] = []

    def resolve(
        self, *, inputs: Mapping[str, RuntimeValue], policy: ResolutionPolicy
    ) -> GeocodingResolutionOutcome:
        self.names.append(inputs["name"])
        return self.found


def test_a_mountain_is_searched_under_the_given_name_without_step_parameters() -> None:
    found = OpenMeteoResolutionService(StubJsonTransport({"results": []})).resolve(
        inputs={"city": "Elbrus", "country": "Russia"}, policy=STRICT
    )
    stub = MountainStub(found)
    service = OtherNameResolutionService(
        stub, capability="resolve_entity", row_name="Эльбрус", searched_as="Elbrus"
    )
    outcome = service.resolve(inputs={"name": "Эльбрус", "country": "Russia"}, policy=STRICT)
    assert stub.names == ["Elbrus"]
    assert outcome.report.candidate_set.query.name == "Эльбрус"


def test_an_overlong_recorded_name_matches_by_its_beginning() -> None:
    assert same_name("  mont   BLANC ", "Mont Blanc")
    assert same_name("L" * 600, "L" * 511 + "…")
    assert not same_name("Mont Blanc du Tacul", "Mont Blanc")


def test_the_audit_model_validates_the_recorded_name() -> None:
    service, _ = renamed()
    report = service.resolve(inputs=ROW, policy=STRICT, parameters={}).report
    assert report.decision.status == "unresolved"
    with pytest.raises(ValueError):
        searched_under(report, row_name="München", searched_as="   ")
    with pytest.raises(ValueError):
        ResolutionQuery(name="München", entity_type="city", searched_as="")


PLAN = EnrichmentPlan.model_validate(
    {"version": "1.0", "steps": [step().model_dump()], "outputs": [], "assumptions": []}
)


def job_report(report: ResolutionReport) -> ResolutionJobReport:
    return ResolutionJobReport(
        job_id="job-1",
        status="awaiting_review",
        total_rows=2,
        processed_rows=2,
        operations=(
            ResolutionOperationAudit(
                operation_id="operation-1",
                report=report,
                record_links=(
                    ResolutionRecordLink(record_id="Sheet1!2", step_id="place"),
                    ResolutionRecordLink(record_id="Sheet1!7", step_id="place"),
                ),
            ),
        ),
    )


def village(location_id: int) -> dict[str, Any]:
    """A place the source knows under the row's own spelling."""
    return munich(location_id, "PPL", "München")


def searched(*results: dict[str, Any], error: Exception | None = None) -> ResolutionReport:
    transport = StubJsonTransport({"results": list(results)}, error=error)
    return OpenMeteoResolutionService(transport).resolve(inputs=ROW, policy=STRICT).report


def test_one_name_covers_every_row_of_the_operation() -> None:
    names = build_other_names(
        names={"operation-1": " Munich "}, report=job_report(searched()), plan=PLAN
    )
    expected = OtherName(
        operation_id="operation-1", capability="geocode", row_name="München", searched_as="Munich"
    )
    assert names == {("Sheet1!2", "place"): expected, ("Sheet1!7", "place"): expected}


@pytest.mark.parametrize(
    ("report", "accepted"),
    [
        (lambda: searched(), True),  # Not found.
        (lambda: searched(village(1), village(2)), True),  # Several matches.
        (lambda: searched(village(1)), False),  # Already settled.
        (lambda: searched(error=JsonTransportError("network")), False),
    ],
)
def test_only_an_outcome_a_name_can_change_accepts_one(report, accepted) -> None:
    built = report()
    assert accepts_other_name(built) is accepted
    payload = review_payload("job-1", job_report(built), PLAN)
    assert [item["other_name"] for item in payload["items"] + payload["informational"]] == (
        [] if built.decision.status == "resolved" else [accepted]
    )
    if not accepted:
        with pytest.raises(ValueError):
            build_other_names(names={"operation-1": "Munich"}, report=job_report(built), plan=PLAN)


@pytest.mark.parametrize("name", ["münchen", "  ", "M" * 513])
def test_a_name_that_cannot_help_is_rejected(name) -> None:
    with pytest.raises(ValueError):
        build_other_names(names={"operation-1": name}, report=job_report(searched()), plan=PLAN)


def test_a_step_fed_by_another_step_cannot_be_given_a_name() -> None:
    fed = step(
        city={"kind": "step_output", "step_id": "earlier"}, country={"kind": "column", "name": "c"}
    )
    plan = PLAN.model_copy(update={"steps": (fed,)})
    with pytest.raises(ValueError):
        build_other_names(names={"operation-1": "Munich"}, report=job_report(searched()), plan=plan)
    with pytest.raises(ValueError):
        build_other_names(names={"missing": "Munich"}, report=job_report(searched()), plan=PLAN)


def test_a_decision_is_a_candidate_or_a_name_never_both() -> None:
    assert ReviewDecision(operation_id="operation-1", name="Munich").name == "Munich"
    with pytest.raises(ValueError):
        ReviewDecision(
            operation_id="operation-1", source="open-meteo", entity_id="1", name="Munich"
        )


def test_a_value_found_under_the_users_name_is_marked_as_the_users_decision() -> None:
    service, _ = renamed(munich())
    found = service.resolve(inputs=ROW, policy=STRICT, parameters={}).report
    plain = searched(village(1))
    assert plain.decision.status == "resolved"
    for report, expected in ((found, {"Sheet1!2": {"place"}, "Sheet1!7": {"place"}}), (plain, {})):
        assert user_chosen_steps(job_report(report).model_dump(mode="json")) == expected
    # A name that found nothing decided nothing.
    nothing, _ = renamed()
    missing = nothing.resolve(inputs=ROW, policy=STRICT, parameters={}).report
    assert user_chosen_steps(job_report(missing).model_dump(mode="json")) == {}


def test_a_candidate_found_under_another_name_is_reverified_under_that_name() -> None:
    service, _ = renamed(munich(1), munich(2))
    reviewed = service.resolve(inputs=ROW, policy=STRICT, parameters={}).report
    transport = StubJsonTransport({"results": [munich(1), munich(2)]})
    approved = ApprovedOpenMeteoResolutionService(
        transport,
        approval=ResolutionApproval(
            record_id="Sheet1!2",
            step_id="place",
            operation_id="operation-1",
            source="open-meteo",
            entity_id="2",
        ),
        source_report=reviewed,
    )
    # The row is recognized by its own value; the repeat search uses the given name.
    outcome = approved.resolve(inputs=ROW, policy=STRICT, parameters={})
    assert transport.requests[0][1]["name"] == "Munich"
    assert outcome.report.decision.basis == "user_override"
    query = outcome.report.candidate_set.query
    assert (query.name, query.searched_as) == ("München", "Munich")
    with pytest.raises(ValueError):
        approved.resolve(inputs={**ROW, "city": "Munich"}, policy=STRICT, parameters={})
