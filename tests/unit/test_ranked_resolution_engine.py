"""Offline scenarios for provider-neutral decisions and Open-Meteo ranked resolution."""

import pytest
from pydantic import ValidationError
from tests.support.http import StubJsonTransport

from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.open_meteo_candidates import normalize_open_meteo_candidates
from ai_data_enrichment.providers.wikidata_candidates import normalize_wikidata_search_candidates
from ai_data_enrichment.resolution.engine import ResolutionEngine
from ai_data_enrichment.resolution.geographic import GeographicRankingStrategy
from ai_data_enrichment.resolution.models import ResolutionPolicy, ResolutionReport


def _berlin() -> list[dict[str, object]]:
    return [
        {
            "id": 2950159,
            "name": "Berlin",
            "country_code": "DE",
            "admin1": "State of Berlin",
            "feature_code": "PPLC",
            "population": 3426354,
            "latitude": 52.52437,
            "longitude": 13.41053,
        },
        {
            "id": 2950158,
            "name": "Berlin",
            "country_code": "DE",
            "admin1": "Schleswig-Holstein",
            "feature_code": "PPL",
            "latitude": 54.033,
            "longitude": 10.45,
        },
    ]


def _policy(
    *,
    ranked: bool = False,
    auto: bool = False,
    prominence: bool = False,
) -> ResolutionPolicy:
    return ResolutionPolicy(
        policy_id="geo_prominence" if ranked else "strict_geo",
        version="1.0",
        strategy="ranked" if ranked else "strict",
        auto_accept_ranked=auto,
        prefer_prominent=prominence,
    )


def _resolve(
    results: list[object],
    *,
    policy: ResolutionPolicy,
    region: str | None = None,
    city: str = "Berlin",
) -> tuple[StubJsonTransport, object]:
    transport = StubJsonTransport({"results": results})
    inputs: dict[str, str] = {"city": city, "country": "Germany"}
    if region is not None:
        inputs["administrative_area"] = region
    outcome = OpenMeteoResolutionService(transport).resolve(inputs=inputs, policy=policy)
    return transport, outcome


def test_strict_berlin_still_asks_for_review_and_keeps_both_candidates() -> None:
    transport, outcome = _resolve(_berlin(), policy=_policy())
    assert len(transport.requests) == 1
    assert outcome.operation.status == "failure"
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert outcome.report.decision.status == "needs_review"
    assert outcome.report.policy.strategy == "strict"
    assert {a.rank for a in outcome.report.assessments} == {1}
    assert len(outcome.report.candidate_set.candidates) == 2


def test_berlin_prominence_auto_selects_capital_and_preserves_alternative() -> None:
    transport, outcome = _resolve(
        _berlin(),
        policy=_policy(ranked=True, auto=True, prominence=True),
    )
    assert len(transport.requests) == 1
    assert outcome.operation.status == "success"
    assert outcome.operation.value == GeoPoint(latitude=52.52437, longitude=13.41053)
    assert outcome.operation.evidence[0].reference == "geonames:2950159"
    report = outcome.report
    assert report.decision.status == "auto_resolved"
    assert report.decision.selected is not None
    assert report.decision.selected.entity_id == "2950159"
    assert {c.reference.entity_id for c in report.candidate_set.candidates} == {
        "2950159",
        "2950158",
    }
    assessment_by_id = {a.reference.entity_id: a for a in report.assessments}
    assert assessment_by_id["2950159"].rank == 1
    assert assessment_by_id["2950158"].rank == 2
    assert "country_capital_role" in assessment_by_id["2950159"].reason_codes
    assert "confidence" not in report.model_dump_json()
    assert ResolutionReport.model_validate_json(report.model_dump_json()) == report


def test_reordered_source_candidates_do_not_change_selection_or_ranks() -> None:
    policy = _policy(ranked=True, auto=True, prominence=True)
    _, first = _resolve(_berlin(), policy=policy)
    _, second = _resolve(list(reversed(_berlin())), policy=policy)
    assert first.report.decision == second.report.decision
    assert first.report.assessments == second.report.assessments


@pytest.mark.parametrize(
    ("region", "expected_id", "latitude"),
    [
        ("Schleswig-Holstein", "2950158", 54.033),
        ("DE-SH", "2950158", 54.033),
        ("Berlin", "2950159", 52.52437),
        ("DE-BE", "2950159", 52.52437),
    ],
)
def test_explicit_region_outweighs_capital_role(
    region: str, expected_id: str, latitude: float
) -> None:
    _, outcome = _resolve(
        _berlin(),
        policy=_policy(ranked=True, auto=True, prominence=True),
        region=region,
    )
    assert outcome.operation.status == "success"
    assert isinstance(outcome.operation.value, GeoPoint)
    assert outcome.operation.value.latitude == latitude
    assert outcome.report.decision.status == "resolved"
    assert outcome.report.decision.selected is not None
    assert outcome.report.decision.selected.entity_id == expected_id
    rejected = [
        assessment
        for assessment in outcome.report.assessments
        if assessment.reference.entity_id != expected_id
    ]
    assert len(rejected) == 1
    assert rejected[0].eligibility == "excluded"
    assert "administrative_area_mismatch" in rejected[0].reason_codes


def test_ranked_without_auto_accept_proposes_capital_but_abstains() -> None:
    _, outcome = _resolve(
        _berlin(),
        policy=_policy(ranked=True, auto=False, prominence=True),
    )
    assert outcome.report.decision.status == "needs_review"
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert len([a for a in outcome.report.assessments if a.rank == 1]) == 1


def test_ranked_without_prominence_does_not_choose_a_winner() -> None:
    _, outcome = _resolve(
        _berlin(),
        policy=_policy(ranked=True, auto=True, prominence=False),
    )
    assert outcome.report.decision.status == "needs_review"
    assert {a.rank for a in outcome.report.assessments} == {1}


def test_two_neustadts_with_no_role_or_population_remain_tied() -> None:
    raw = [
        {
            "id": 1000 + i,
            "name": "Neustadt",
            "country_code": "DE",
            "feature_code": "PPL",
            "latitude": 49.0 + float(i),
            "longitude": 10.0,
        }
        for i in range(2)
    ]
    _, outcome = _resolve(
        raw,
        policy=_policy(ranked=True, auto=True, prominence=True),
        city="Neustadt",
    )
    assert outcome.report.decision.status == "needs_review"
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert {a.rank for a in outcome.report.assessments} == {1}


def test_population_only_creates_review_order_not_automatic_identity() -> None:
    raw = [
        {
            "id": 1001,
            "name": "Neustadt",
            "country_code": "DE",
            "feature_code": "PPL",
            "population": 180000,
            "latitude": 49.0,
            "longitude": 10.0,
        },
        {
            "id": 1002,
            "name": "Neustadt",
            "country_code": "DE",
            "feature_code": "PPL",
            "population": 1000,
            "latitude": 50.0,
            "longitude": 11.0,
        },
    ]
    _, outcome = _resolve(
        raw,
        policy=_policy(ranked=True, auto=True, prominence=True),
        city="Neustadt",
    )
    assert outcome.report.decision.status == "needs_review"
    assert {a.reference.entity_id: a.rank for a in outcome.report.assessments} == {
        "1001": 1,
        "1002": 2,
    }


def test_unknown_population_is_not_treated_as_zero() -> None:
    raw = _berlin()
    for row in raw:
        row["feature_code"] = "PPL"
        row.pop("population", None)
    raw[0]["population"] = 350000
    _, outcome = _resolve(raw, policy=_policy(ranked=True, auto=True, prominence=True))
    assert outcome.report.decision.status == "needs_review"
    assert {a.rank for a in outcome.report.assessments} == {1}


def test_unknown_location_type_prevents_false_uniqueness() -> None:
    raw = _berlin()
    raw[1].pop("feature_code")
    _, outcome = _resolve(raw, policy=_policy(ranked=True, auto=True, prominence=True))
    assert outcome.report.decision.status == "needs_review"
    assert outcome.operation.status == "failure"
    assert any(a.eligibility == "unchecked" for a in outcome.report.assessments)


def test_district_is_excluded_as_not_a_city() -> None:
    raw = _berlin()
    raw[1]["feature_code"] = "PPLX"
    _, outcome = _resolve(raw, policy=_policy(ranked=True, auto=True, prominence=True))
    assert outcome.report.decision.status == "resolved"
    assert any(
        a.eligibility == "excluded" and "entity_type_mismatch" in a.reason_codes
        for a in outcome.report.assessments
    )


def test_missing_region_metadata_requires_review_not_false_selected_entity() -> None:
    raw = _berlin()
    raw[0].pop("admin1")
    _, outcome = _resolve(
        raw,
        policy=_policy(ranked=True, auto=True, prominence=True),
        region="Schleswig-Holstein",
    )
    assert outcome.report.decision.status == "needs_review"
    assert outcome.operation.status == "failure"
    assert any(
        "administrative_area_unverified" in a.reason_codes
        for a in outcome.report.assessments
    )


def test_complete_no_match_is_unresolved_without_spelling_correction() -> None:
    _, outcome = _resolve([], policy=_policy(), city="Hambourg")
    assert outcome.report.decision.status == "unresolved"
    assert outcome.report.candidate_set.query.name == "Hambourg"
    assert outcome.operation.error_code == "NOT_FOUND"
    assert outcome.report.candidate_set.candidates == ()


def test_saturated_search_page_prevents_auto_acceptance() -> None:
    extras = [
        {
            "id": 900 + i,
            "name": f"Other city {i}",
            "country_code": "DE",
            "latitude": 50.0,
            "longitude": 10.0,
        }
        for i in range(8)
    ]
    _, outcome = _resolve(
        _berlin() + extras,
        policy=_policy(ranked=True, auto=True, prominence=True),
    )
    assert outcome.report.decision.status == "incomplete_verification"
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.candidate_set.incomplete_reason == "result_limit_reached"


def test_invalid_provider_payload_cannot_be_silently_ranked() -> None:
    raw = _berlin()
    raw[0]["latitude"] = "invalid"
    _, outcome = _resolve(raw, policy=_policy(ranked=True, auto=True, prominence=True))
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.operation.error_code == "INVALID_RESPONSE"
    assert outcome.report.candidate_set.provider_error_code == "INVALID_RESPONSE"


def test_provider_transport_failure_has_a_failure_report_and_one_request() -> None:
    transport = StubJsonTransport(None, error=TimeoutError("Request timed out"))
    outcome = OpenMeteoResolutionService(transport).resolve(
        inputs={"city": "Berlin", "country": "Germany"},
        policy=_policy(ranked=True, auto=True, prominence=True),
    )
    assert len(transport.requests) == 1
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.operation.status == "failure"
    assert outcome.report.candidate_set.provider_error_code == outcome.operation.error_code


def test_invalid_resolution_input_does_not_contact_provider() -> None:
    transport = StubJsonTransport({"results": _berlin()})
    resolver = OpenMeteoResolutionService(transport)
    with pytest.raises(ValueError, match="nonblank city"):
        resolver.resolve(inputs={"city": " ", "country": "Germany"}, policy=_policy())
    with pytest.raises(ValueError, match="nonblank text"):
        resolver.resolve(
            inputs={
                "city": "Berlin",
                "country": "Germany",
                "administrative_area": " ",
            },
            policy=_policy(),
        )
    assert not transport.requests


def test_generic_engine_abstains_on_unchecked_wikidata_candidate_set() -> None:
    candidates = normalize_wikidata_search_candidates(
        [
            {"id": "Q513", "label": "Mount Everest"},
            {"id": "Q61882061", "label": "The Climbers", "match": {"text": "Mount Everest"}},
        ],
        name="Mount Everest",
        country_code="NP",
        search_complete=True,
    )
    # The generic engine does not know how to verify Wikidata P31 / P17.
    # This geographic strategy therefore abstains instead of selecting Q513.
    report = ResolutionEngine().resolve(
        candidate_set=candidates,
        policy=_policy(ranked=True, auto=True, prominence=True),
        strategy=GeographicRankingStrategy(),
    )
    assert report.decision.status == "incomplete_verification"
    assert report.decision.selected is None
    assert len(report.assessments) == 2
    assert all(a.eligibility == "unchecked" for a in report.assessments)


def test_report_validation_rejects_false_auto_selection() -> None:
    candidates = normalize_open_meteo_candidates(
        _berlin(),
        city="Berlin",
        country_code="DE",
    )
    report = ResolutionEngine().resolve(
        candidate_set=candidates,
        policy=_policy(ranked=True, auto=True, prominence=True),
        strategy=GeographicRankingStrategy(),
    )
    with pytest.raises(ValidationError):
        report.model_copy(
            update={"decision": {"status": "auto_resolved", "selected": {"source": "other"}}}
        ).model_validate(report.model_dump(mode="json"))
