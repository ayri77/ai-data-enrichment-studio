"""Integration tests for audited capital resolution through JobExecutor."""

from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.capability import Identifier
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    InputReference,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import (
    InputRecord,
    JobExecutionPolicy,
)
from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import (
    OpenMeteoResolutionService,
)
from ai_data_enrichment.resolution.models import ResolutionPolicy


def test_required_capital_flows_through_audited_job_executor() -> None:
    """A strict capital constraint must reach the resolution service."""

    transport = StubJsonTransport(
        {
            "results": [
                {
                    "id": 2950159,
                    "name": "Berlin",
                    "country_code": "DE",
                    "feature_code": "PPLC",
                    "admin1": "State of Berlin",
                    "latitude": 52.52437,
                    "longitude": 13.41053,
                },
                {
                    "id": 2950158,
                    "name": "Berlin",
                    "country_code": "DE",
                    "feature_code": "PPL",
                    "admin1": "Schleswig-Holstein",
                    "latitude": 54.033,
                    "longitude": 10.45,
                },
            ]
        }
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="capital",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city"),
                    "country": ColumnReference(kind="column", name="country"),
                },
                parameters={"required_role": "country_capital"},
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="capital"),
                    "point_b": StepOutputReference(kind="step_output", step_id="capital"),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    executor = JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={
            "geocode": "open-meteo",
            "geodesic_distance": "local",
        },
        configuration_ids={
            "geocode": "capital-test-v1",
            "geodesic_distance": "haversine-v1",
        },
        capability_versions={"geocode": "1.1"},
        provider_versions={"geocode": "open-meteo-v1"},
        policy=JobExecutionPolicy(
            max_rows=10,
            batch_size=10,
            max_memoized_results=10,
        ),
        resolution_services={
            "geocode": OpenMeteoResolutionService(transport),
        },
        resolution_policies={
            "geocode": ResolutionPolicy(
                policy_id="strict_capital",
                version="1.0",
                strategy="strict",
            ),
        },
    )

    result = executor.execute(
        plan,
        [
            InputRecord(
                record_id="Berlin!2",
                values={"city": "Berlin", "country": "Germany"},
            )
        ],
        job_id="capital-test-job",
    )

    # The role constraint resolves Berlin's capital, not its namesake.
    assert result.metrics.successful_rows == 1
    assert len(transport.requests) == 1

    steps = result.records[0].row_result.steps

    assert steps[0].result.status == "success"
    assert steps[0].result.value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )
    assert steps[1].result.status == "success"

    # Both candidates remain in the audited decision.
    report = result.resolution_report
    assert report is not None
    assert report.status == "completed"
    assert len(report.operations) == 1

    audit = report.operations[0]
    assert audit.report.decision.status == "resolved"
    assert audit.report.decision.selected is not None
    assert audit.report.decision.selected.entity_id == "2950159"

    assert {
        candidate.reference.entity_id for candidate in audit.report.candidate_set.candidates
    } == {"2950159", "2950158"}

    assert {(link.record_id, link.step_id) for link in audit.record_links} == {
        ("Berlin!2", "capital")
    }


def test_mixed_geocode_steps_keep_distinct_resolution_semantics() -> None:
    """Ordinary and capital geocoding must not share resolution decisions."""
    transport = StubJsonTransport(
        {
            "results": [
                {
                    "id": 2950159,
                    "name": "Berlin",
                    "country_code": "DE",
                    "feature_code": "PPLC",
                    "latitude": 52.52437,
                    "longitude": 13.41053,
                },
                {
                    "id": 2950158,
                    "name": "Berlin",
                    "country_code": "DE",
                    "feature_code": "PPL",
                    "latitude": 54.033,
                    "longitude": 10.45,
                },
            ]
        }
    )

    inputs: dict[Identifier, InputReference] = {
        "city": ColumnReference(kind="column", name="city"),
        "country": ColumnReference(kind="column", name="country"),
    }

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="ordinary_city",
                capability="geocode",
                inputs=inputs,
                parameters={},
            ),
            PlanStep(
                id="capital_city",
                capability="geocode",
                inputs=inputs,
                parameters={"required_role": "country_capital"},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    executor = JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
        },
        selected_providers={"geocode": "open-meteo"},
        configuration_ids={"geocode": "mixed-geocode-v1"},
        capability_versions={"geocode": "1.1"},
        provider_versions={"geocode": "open-meteo-v1"},
        policy=JobExecutionPolicy(
            max_rows=10,
            batch_size=10,
            max_memoized_results=10,
            memoizable_providers=frozenset(
                {
                    ("geocode", "open-meteo"),
                }
            ),
        ),
        resolution_services={
            "geocode": OpenMeteoResolutionService(transport),
        },
        resolution_policies={
            "geocode": ResolutionPolicy(
                policy_id="strict_geocode",
                version="1.0",
                strategy="strict",
            ),
        },
    )

    result = executor.execute(
        plan,
        [
            InputRecord(
                record_id="Berlin!2",
                values={"city": "Berlin", "country": "Germany"},
            )
        ],
    )

    steps = {step.step_id: step.result for step in result.records[0].row_result.steps}

    assert steps["ordinary_city"].status == "failure"
    assert steps["ordinary_city"].error_code == "AMBIGUOUS"

    assert steps["capital_city"].status == "success"
    assert steps["capital_city"].value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )

    # The two parameter sets must produce separate operations.
    assert len(transport.requests) == 2

    report = result.resolution_report
    assert report is not None
    assert len(report.operations) == 2

    decisions = {
        link.step_id: operation.report.decision
        for operation in report.operations
        for link in operation.record_links
    }

    assert decisions["ordinary_city"].status == "needs_review"
    assert decisions["ordinary_city"].selected is None

    assert decisions["capital_city"].status == "resolved"
    assert decisions["capital_city"].selected is not None
    assert decisions["capital_city"].selected.entity_id == "2950159"
