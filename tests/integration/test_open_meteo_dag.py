"""Offline integration test for geocoding and distance calculation."""

from collections.abc import Mapping

from ai_data_enrichment.capabilities.definitions import (
    get_geocode_spec,
    get_geodesic_distance_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import GeoPoint, Quantity
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.registry import ProviderRegistry


class FixtureTransport:
    """Return known locations without making external HTTP requests."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.locations = {
            "Berlin": {
                "id": 2950159,
                "name": "Berlin",
                "country_code": "DE",
                "latitude": 52.52437,
                "longitude": 13.41053,
            },
            "Hamburg": {
                "id": 2911298,
                "name": "Hamburg",
                "country_code": "DE",
                "latitude": 53.55073,
                "longitude": 9.99302,
            },
        }

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        city = params["name"]
        assert params["countryCode"] == "DE"
        self.requests.append(city)
        return {"results": [self.locations[city]]}


def geocode_step(step_id: str, city: str) -> PlanStep:
    """Create a geocoding step for a named German city."""
    return PlanStep(
        id=step_id,
        capability="geocode",
        inputs={
            "city": LiteralValue(kind="literal", value=city),
            "country": LiteralValue(kind="literal", value="Germany"),
        },
        parameters={},
    )


def test_geocode_two_cities_and_calculate_distance() -> None:
    """Execute a complete geocode -> distance DAG offline."""
    transport = FixtureTransport()

    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())
    capabilities.register(get_geodesic_distance_spec())

    providers = ProviderRegistry(capabilities)
    providers.register(
        capability_id="geocode",
        provider_id="open_meteo",
        handler=OpenMeteoGeocodingHandler(transport=transport),
    )
    providers.register(
        capability_id="geodesic_distance",
        provider_id="haversine",
        handler=GeodesicDistanceHandler(),
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            # Deliberately place the dependent step first.
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output",
                        step_id="berlin",
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output",
                        step_id="hamburg",
                    ),
                },
                parameters={"unit": "km"},
            ),
            geocode_step("berlin", "Berlin"),
            geocode_step("hamburg", "Hamburg"),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
            ),
        ],
        assumptions=[],
    )

    handlers = providers.resolve_handlers(
        capability_ids=[step.capability for step in plan.steps],
        selected_providers={
            "geocode": "open_meteo",
            "geodesic_distance": "haversine",
        },
    )

    execution = DAGExecutor(handlers).execute_row(plan, row={})
    outcomes = {step.step_id: step.result for step in execution.steps}

    execution_order = [step.step_id for step in execution.steps]

    assert len(execution_order) == 3
    assert set(execution_order[:2]) == {"berlin", "hamburg"}
    assert execution_order[-1] == "distance"

    assert all(result.status == "success" for result in outcomes.values())

    assert outcomes["berlin"].value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )
    assert outcomes["hamburg"].value == GeoPoint(
        latitude=53.55073,
        longitude=9.99302,
    )

    distance = outcomes["distance"].value
    assert isinstance(distance, Quantity)
    assert distance.unit == "km"
    assert 250.0 < distance.value < 260.0

    assert outcomes["berlin"].evidence[0].source == "open-meteo"
    assert outcomes["hamburg"].evidence[0].source == "open-meteo"
    assert outcomes["distance"].evidence[0].source == "calculation"

    assert set(transport.requests) == {"Berlin", "Hamburg"}
    assert len(transport.requests) == 2


def test_failed_geocoding_blocks_dependent_distance() -> None:
    """Preserve successful branches and block dependent calculations."""

    class MissingHamburgTransport(FixtureTransport):
        def get_json(
            self,
            *,
            url: str,
            params: Mapping[str, str],
            timeout_seconds: float,
        ) -> object:
            if params["name"] == "Hamburg":
                self.requests.append("Hamburg")
                return {"results": []}

            return super().get_json(
                url=url,
                params=params,
                timeout_seconds=timeout_seconds,
            )

    transport = MissingHamburgTransport()

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output",
                        step_id="berlin",
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output",
                        step_id="hamburg",
                    ),
                },
                parameters={"unit": "km"},
            ),
            geocode_step("berlin", "Berlin"),
            geocode_step("hamburg", "Hamburg"),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
            ),
        ],
        assumptions=[],
    )

    executor = DAGExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport=transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        }
    )

    execution = executor.execute_row(plan, row={})
    outcomes = {step.step_id: step.result for step in execution.steps}

    assert outcomes["berlin"].status == "success"
    assert outcomes["berlin"].value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )

    assert outcomes["hamburg"].status == "failure"
    assert outcomes["hamburg"].error_code == "NOT_FOUND"

    assert outcomes["distance"].status == "failure"
    assert outcomes["distance"].error_code == "DEPENDENCY_FAILED"
    assert outcomes["distance"].value is None

    assert set(transport.requests) == {"Berlin", "Hamburg"}
    assert len(transport.requests) == 2
