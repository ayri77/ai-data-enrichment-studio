"""A user-approved Open-Meteo location is re-verified by repeating the search."""

from typing import Any

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.enrichment_plan import LiteralValue, PlanStep
from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.open_meteo_approved import ApprovedOpenMeteoResolutionService
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.resolution.approvals import ResolutionApproval
from ai_data_enrichment.resolution.models import ResolutionPolicy, ResolutionReport

STRICT = ResolutionPolicy(policy_id="capital_strict", version="1.0", strategy="strict")
INPUTS = {"city": "Berlin", "country": "Germany"}
CAPITAL, NAMESAKE, DISTRICT = "2950159", "2950158", "2950157"


def location(location_id: str, feature_code: str, admin1: str, latitude: float) -> dict[str, Any]:
    return {
        "id": int(location_id),
        "name": "Berlin",
        "country_code": "DE",
        "feature_code": feature_code,
        "admin1": admin1,
        "latitude": latitude,
        "longitude": 13.4,
        "population": 1000,
    }


BERLINS = [
    location(CAPITAL, "PPLC", "State of Berlin", 52.52),
    location(NAMESAKE, "PPL", "Schleswig-Holstein", 54.03),
    location(DISTRICT, "PPLX", "Hamburg", 53.55),  # A district: excluded by type.
]


def source_report(results: list[dict[str, Any]] | None = None) -> ResolutionReport:
    transport = StubJsonTransport({"results": BERLINS if results is None else results})
    outcome = OpenMeteoResolutionService(transport).resolve(inputs=INPUTS, policy=STRICT)
    assert outcome.report.decision.status == "needs_review"
    return outcome.report


def approval(entity_id: str = NAMESAKE) -> ResolutionApproval:
    return ResolutionApproval(
        record_id="Sheet1!2",
        step_id="geocode_to",
        operation_id="operation-1",
        source="open-meteo",
        entity_id=entity_id,
    )


def service(
    results: list[dict[str, Any]] | None = None, *, entity_id: str = NAMESAKE, error=None
) -> tuple[ApprovedOpenMeteoResolutionService, StubJsonTransport]:
    transport = StubJsonTransport({"results": BERLINS if results is None else results}, error=error)
    approved = ApprovedOpenMeteoResolutionService(
        transport, approval=approval(entity_id), source_report=source_report()
    )
    return approved, transport


def test_approved_location_is_selected_after_a_fresh_search() -> None:
    approved, transport = service()
    outcome = approved.resolve(inputs=INPUTS, policy=STRICT, parameters={})
    assert outcome.operation.status == "success"
    assert outcome.operation.value == GeoPoint(latitude=54.03, longitude=13.4)
    assert len(transport.requests) == 1  # The search really was repeated.
    decision = outcome.report.decision
    assert decision.status == "resolved" and decision.basis == "user_override"
    assert decision.selected is not None and decision.selected.entity_id == NAMESAKE
    assert decision.reason_codes == ("user_approved", "fresh_source_verified")
    # The audit keeps every fresh candidate, not only the approved one.
    assert len(outcome.report.candidate_set.candidates) == 3
    selected = next(
        c for c in outcome.report.candidate_set.candidates if c.reference == decision.selected
    )
    assert outcome.operation.evidence == selected.evidence


@pytest.mark.parametrize(
    "fresh,status",
    [
        ([BERLINS[0], BERLINS[2]], "needs_review"),  # The approved location disappeared.
        ([], "unresolved"),  # Nothing is found any more.
    ],
)
def test_approval_does_not_replace_missing_fresh_evidence(fresh, status) -> None:
    approved, _ = service(fresh)
    outcome = approved.resolve(inputs=INPUTS, policy=STRICT, parameters={})
    assert outcome.operation.status == "failure"
    assert outcome.operation.error_code == "APPROVAL_NOT_REVERIFIED"
    assert outcome.operation.value is None
    assert outcome.report.decision.status == status
    assert outcome.report.decision.selected is None
    assert outcome.report.decision.reason_codes == ("approved_source_reverification_failed",)


def test_a_candidate_excluded_by_hard_constraints_cannot_be_approved_in() -> None:
    approved, _ = service(entity_id=DISTRICT)
    outcome = approved.resolve(inputs=INPUTS, policy=STRICT, parameters={})
    assert outcome.operation.error_code == "APPROVAL_NOT_REVERIFIED"
    assert outcome.report.decision.status == "needs_review"


def test_provider_failure_is_reported_as_provider_failure() -> None:
    approved, _ = service(error=JsonTransportError("network"))
    outcome = approved.resolve(inputs=INPUTS, policy=STRICT, parameters={})
    assert outcome.operation.status == "failure"
    assert outcome.report.decision.status == "provider_failure"


@pytest.mark.parametrize(
    "inputs",
    [
        {"city": "Bern", "country": "Germany"},
        {"city": "Berlin", "country": "France"},
        {"city": "Berlin", "country": "Germany", "administrative_area": "Hamburg"},
        {"city": "Berlin"},
    ],
)
def test_changed_row_inputs_are_rejected_before_any_request(inputs) -> None:
    approved, transport = service()
    with pytest.raises(ValueError):
        approved.resolve(inputs=inputs, policy=STRICT, parameters={})
    assert transport.requests == []


def test_equivalent_spelling_of_the_same_query_is_accepted() -> None:
    approved, _ = service()
    approved.validate_inputs({"city": "  berlin ", "country": "DE"})


def step(capability: str = "geocode", parameters: dict | None = None) -> PlanStep:
    return PlanStep(
        id="geocode_to",
        capability=capability,
        inputs={"city": LiteralValue(kind="literal", value="Berlin")},
        parameters=parameters or {},
    )


def test_step_contract_must_match_the_source_query() -> None:
    approved, transport = service()
    approved.validate_step(step(), provider_id="open-meteo")
    for changed, provider in [
        (step(parameters={"required_role": "country_capital"}), "open-meteo"),
        (step("resolve_entity"), "open-meteo"),
        (step(), "wikidata"),
    ]:
        with pytest.raises(ValueError):
            approved.validate_step(changed, provider_id=provider)
    with pytest.raises(ValueError):
        approved.resolve(
            inputs=INPUTS, policy=STRICT, parameters={"required_role": "country_capital"}
        )
    assert transport.requests == []


def test_construction_requires_a_reviewable_verified_open_meteo_candidate() -> None:
    report = source_report()
    transport = StubJsonTransport({"results": BERLINS})
    with pytest.raises(ValueError):  # Unknown location ID.
        ApprovedOpenMeteoResolutionService(
            transport, approval=approval("999"), source_report=report
        )
    wikidata = approval().model_copy(update={"source": "wikidata", "entity_id": "Q64"})
    with pytest.raises(ValueError):  # Wrong source.
        ApprovedOpenMeteoResolutionService(transport, approval=wikidata, source_report=report)
    resolved = OpenMeteoResolutionService(StubJsonTransport({"results": BERLINS[:1]})).resolve(
        inputs=INPUTS, policy=STRICT
    )
    assert resolved.report.decision.status == "resolved"
    with pytest.raises(ValueError):  # Nothing to review.
        ApprovedOpenMeteoResolutionService(
            transport, approval=approval(CAPITAL), source_report=resolved.report
        )
