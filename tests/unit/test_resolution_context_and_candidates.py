"""Offline tests for typed context binding and source-specific normalization."""

import pytest
from pydantic import ValidationError
from tests.support.http import StubJsonTransport

from ai_data_enrichment.capabilities.definitions import (
    get_geocode_spec,
    get_geodesic_distance_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_candidates import (
    administrative_area_matches,
    normalize_open_meteo_candidates,
)
from ai_data_enrichment.providers.wikidata_candidates import (
    normalize_wikidata_search_candidates,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator


def _berlin_locations() -> list[dict[str, object]]:
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


def _geocode_plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve_city",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City"),
                    "country": ColumnReference(kind="column", name="Country"),
                    "administrative_area": ColumnReference(kind="column", name="Bundesland"),
                },
                parameters={},
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output", step_id="resolve_city"
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output", step_id="resolve_city"
                    ),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="DistanceKm",
            )
        ],
        assumptions=[],
    )


def test_optional_region_is_validated_against_arbitrarily_named_excel_column() -> None:
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())
    registry.register(get_geodesic_distance_spec())
    profile = DatasetProfile(
        dataset_id="berlin",
        name="Berlin locations",
        columns=[
            ColumnProfile(name=name, inferred_type=PrimitiveType.TEXT, null_count=0)
            for name in ("City", "Country", "Bundesland")
        ],
    )
    result = PlanValidator(registry, profile).validate(_geocode_plan())
    assert result.valid
    assert get_geocode_spec().inputs["administrative_area"].required is False


def test_optional_region_cannot_use_wrong_type_or_unknown_semantic_name() -> None:
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())
    registry.register(get_geodesic_distance_spec())
    profile = DatasetProfile(
        dataset_id="berlin",
        name="Berlin locations",
        columns=[
            ColumnProfile(name="City", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Country", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Bundesland", inferred_type=PrimitiveType.NUMBER, null_count=0),
        ],
    )
    result = PlanValidator(registry, profile).validate(_geocode_plan())
    assert any(d.code == "INPUT_TYPE_MISMATCH" for d in result.errors)

    plan = _geocode_plan()
    plan.steps[0].inputs["unknown_region_argument"] = plan.steps[0].inputs.pop(
        "administrative_area"
    )
    result = PlanValidator(registry, profile).validate(plan)
    assert any(d.code == "UNKNOWN_INPUT" for d in result.errors)


def test_geocode_context_selects_berlin_schleswig_holstein_over_capital() -> None:
    transport = StubJsonTransport({"results": _berlin_locations()})
    handler = OpenMeteoGeocodingHandler(transport)
    result = handler.execute(
        inputs={
            "city": "Berlin",
            "country": "Germany",
            "administrative_area": "Schleswig-Holstein",
        },
        parameters={},
    )
    assert result.status == "success"
    assert result.evidence[0].reference == "geonames:2950158"
    assert result.value == GeoPoint(latitude=54.033, longitude=10.45)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("region", "geonames_id"),
    [
        ("Schleswig-Holstein", "2950158"),
        ("DE-SH", "2950158"),
        ("State of Berlin", "2950159"),
        ("Berlin", "2950159"),
        ("DE-BE", "2950159"),
    ],
)
def test_geocode_accepts_region_names_and_iso_codes(
    region: str, geonames_id: str
) -> None:
    handler = OpenMeteoGeocodingHandler(
        StubJsonTransport({"results": _berlin_locations()})
    )
    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany", "administrative_area": region},
        parameters={},
    )
    assert result.status == "success"
    assert result.evidence[0].reference == f"geonames:{geonames_id}"


@pytest.mark.parametrize(
    ("area", "actual", "expected"),
    [
        ("DE-SH", "Schleswig-Holstein", True),
        ("DE-BE", "State of Berlin", True),
        ("DE-HH", "Free and Hanseatic City of Hamburg", True),
        ("DE-BE", "Schleswig-Holstein", False),
        ("US-CA", "State of Berlin", False),
        ("Unknown Area", "State of Berlin", False),
    ],
)
def test_administrative_area_matching_is_country_aware(
    area: str, actual: str, expected: bool
) -> None:
    assert (
        administrative_area_matches(area, country_code="DE", actual_admin1=actual)
        is expected
    )


def test_geocode_without_region_preserves_legacy_ambiguous_result() -> None:
    handler = OpenMeteoGeocodingHandler(
        StubJsonTransport({"results": _berlin_locations()})
    )
    result = handler.execute(inputs={"city": "Berlin", "country": "Germany"}, parameters={})
    assert result.status == "failure"
    assert result.error_code == "AMBIGUOUS"


@pytest.mark.parametrize("region", [" ", "", "\t"])
def test_blank_region_is_invalid_without_http_request(region: str) -> None:
    transport = StubJsonTransport({"results": _berlin_locations()})
    handler = OpenMeteoGeocodingHandler(transport)
    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany", "administrative_area": region},
        parameters={},
    )
    assert result.error_code == "INVALID_INPUT"
    assert not transport.requests


def test_missing_region_metadata_does_not_prove_uniqueness() -> None:
    locations = _berlin_locations()
    del locations[0]["admin1"]
    handler = OpenMeteoGeocodingHandler(StubJsonTransport({"results": locations}))
    result = handler.execute(
        inputs={
            "city": "Berlin",
            "country": "Germany",
            "administrative_area": "Schleswig-Holstein",
        },
        parameters={},
    )
    assert result.status == "failure"
    assert result.error_code == "CONTEXT_UNVERIFIED"


def test_unmatched_known_region_returns_not_found() -> None:
    handler = OpenMeteoGeocodingHandler(
        StubJsonTransport({"results": _berlin_locations()})
    )
    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany", "administrative_area": "Bayern"},
        parameters={},
    )
    assert result.error_code == "NOT_FOUND"


def test_candidate_normalization_keeps_alternatives_and_source_evidence() -> None:
    result = normalize_open_meteo_candidates(
        _berlin_locations(),
        city="Berlin",
        country_code="DE",
        administrative_area="Schleswig-Holstein",
    )
    assert result.search_complete is True
    assert result.verification_complete is True
    assert result.query.context[1].name == "administrative_area"
    assert len(result.candidates) == 2
    capital = result.candidates[0]
    assert capital.reference.entity_id == "2950159"
    assert capital.verification == "verified"
    assert capital.evidence[0].reference == "geonames:2950159"
    assert {a.name: a.value for a in capital.attributes}["administrative_role"] == "country_capital"
    assert {a.name: a.value for a in capital.attributes}["population"] == 3426354
    assert result.candidates[1].reference.entity_id == "2950158"
    assert result.candidates[1].entity_type == "settlement"
    assert result.model_validate_json(result.model_dump_json()) == result


def test_saturated_open_meteo_page_is_explicitly_incomplete() -> None:
    locations = _berlin_locations() * 5
    with pytest.raises(ValidationError, match="Duplicate candidate"):
        normalize_open_meteo_candidates(locations, city="Berlin", country_code="DE")

    # Distinct unrelated results still saturate the bounded provider page.
    extra = [
        {
            "id": 100 + i,
            "name": f"Other Town {i}",
            "country_code": "DE",
            "latitude": 52.0,
            "longitude": 10.0,
        }
        for i in range(8)
    ]
    result = normalize_open_meteo_candidates(
        _berlin_locations() + extra,
        city="Berlin",
        country_code="DE",
    )
    assert result.search_complete is False
    assert result.verification_complete is True
    assert result.incomplete_reason == "result_limit_reached"


@pytest.mark.parametrize(
    ("update", "expected_error"),
    [
        ({"id": None}, "invalid ID"),
        ({"latitude": 101.0}, "coordinates"),
        ({"admin1": 123}, "administrative area"),
        ({"population": True}, "population"),
    ],
)
def test_invalid_matching_open_meteo_candidate_fails_closed(
    update: dict[str, object], expected_error: str
) -> None:
    location = _berlin_locations()[0]
    location.update(update)
    with pytest.raises(ValueError, match=expected_error):
        normalize_open_meteo_candidates([location], city="Berlin", country_code="DE")


def test_wikidata_search_matches_everest_but_does_not_verify_film_as_mountain() -> None:
    candidates = normalize_wikidata_search_candidates(
        [
            {"id": "Q513", "label": "Mount Everest", "match": {"text": "Mount Everest"}},
            {"id": "Q61882061", "label": "The Climbers", "match": {"text": "Mount Everest"}},
            {"id": "Q1950455", "label": "Everest Base Camp Trek"},
            {"id": "Q513", "label": "Mount Everest"},
        ],
        name="Mount Everest",
        country_code="NP",
        search_complete=True,
    )
    assert candidates.search_complete is True
    assert candidates.verification_complete is False
    assert candidates.incomplete_reason == "candidate_verification_pending"
    assert [c.reference.entity_id for c in candidates.candidates] == ["Q513", "Q61882061"]
    assert all(c.verification == "unchecked" and not c.evidence for c in candidates.candidates)
    assert candidates.candidates[0].attributes[0].value == "label"
    assert candidates.candidates[1].attributes[0].value == "alias"


def test_wikidata_truncated_search_never_claims_completeness_or_absence() -> None:
    candidates = normalize_wikidata_search_candidates(
        [{"id": "Q513", "label": "Mount Everest"}],
        name="Mount Everest",
        country_code="NP",
        search_complete=False,
    )
    assert candidates.search_complete is False
    assert candidates.incomplete_reason == "search_pages_incomplete"
    assert candidates.candidates[0].verification == "unchecked"


def test_wikidata_complete_search_without_matching_candidates_is_empty() -> None:
    candidates = normalize_wikidata_search_candidates(
        [{"id": "Q1", "label": "A different entity"}],
        name="Mount Everest",
        country_code="NP",
        search_complete=True,
    )
    assert candidates.candidates == ()
    assert candidates.search_complete and candidates.verification_complete


@pytest.mark.parametrize(
    "bad_item",
    [
        {"id": "invalid", "label": "Mount Everest"},
        {"id": None, "label": "Mount Everest"},
        {"id": "Q513", "label": "Mount Everest", "match": {"text": "Mount Everest"}},
        "not-a-mapping",
    ],
)
def test_wikidata_normalizer_rejects_malformed_or_unbounded_input(
    bad_item: object,
) -> None:
    if isinstance(bad_item, dict) and bad_item.get("id") == "Q513":
        raw = [bad_item] * 151
    else:
        raw = [bad_item]
    with pytest.raises(ValueError):
        normalize_wikidata_search_candidates(
            raw,
            name="Mount Everest",
            country_code="NP",
            search_complete=True,
        )


def test_row_context_changes_operation_identity_and_supports_successful_reuse() -> None:
    transport = StubJsonTransport({"results": _berlin_locations()})
    plan = _geocode_plan()
    job = JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={"geocode": "open-meteo", "geodesic_distance": "local"},
        configuration_ids={"geocode": "region-v1", "geodesic_distance": "haversine-v1"},
        policy=JobExecutionPolicy(
            batch_size=2,
            memoizable_providers=frozenset({("geocode", "open-meteo")}),
        ),
    )
    records = [
        InputRecord(
            record_id="row-1",
            values={"City": "Berlin", "Country": "Germany", "Bundesland": "Berlin"},
        ),
        InputRecord(
            record_id="row-2",
            values={
                "City": "Berlin",
                "Country": "Germany",
                "Bundesland": "Schleswig-Holstein",
            },
        ),
        InputRecord(
            record_id="row-3",
            values={
                "City": "Berlin",
                "Country": "Germany",
                "Bundesland": "Schleswig-Holstein",
            },
        ),
    ]
    result = job.execute(plan, records)
    assert result.metrics.successful_rows == 3
    assert result.metrics.reused_operations == 1
    assert len(transport.requests) == 2
    assert result.records[0].row_result.steps[0].result.evidence[0].reference == (
        "geonames:2950159"
    )
    assert result.records[1].row_result.steps[0].result.evidence[0].reference == (
        "geonames:2950158"
    )
    assert result.records[2].row_result.steps[0].result.evidence[0].reference == (
        "geonames:2950158"
    )


def test_mapped_but_empty_optional_context_fails_explicitly() -> None:
    """A mapped blank cell is not silently interpreted as a verified match."""
    from ai_data_enrichment.execution.executor import DAGExecutor

    transport = StubJsonTransport({"results": _berlin_locations()})
    result = DAGExecutor(
        {"geocode": OpenMeteoGeocodingHandler(transport)}
    ).execute_row(
        _geocode_plan(),
        {"City": "Berlin", "Country": "Germany", "Bundesland": None},
    )
    assert result.steps[0].result.error_code == "MISSING_VALUE"
    assert result.steps[1].result.error_code == "DEPENDENCY_FAILED"
    assert not transport.requests
