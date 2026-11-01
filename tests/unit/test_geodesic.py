"""Tests for deterministic great-circle distance calculation."""

from math import pi

import pytest

from ai_data_enrichment.capabilities.definitions import get_geodesic_distance_spec
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import GeoPoint, Quantity, RuntimeValue
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.registry import ProviderRegistry


def test_distance_between_identical_points_is_zero() -> None:
    """Identical coordinates must produce exactly zero distance."""
    hamburg = GeoPoint(latitude=53.55, longitude=10.0)

    handler = GeodesicDistanceHandler()

    result = handler.execute(
        inputs={
            "point_a": hamburg,
            "point_b": hamburg,
        },
        parameters={"unit": "km"},
    )

    assert result.status == "success"
    assert result.value == Quantity(value=0.0, unit="km")
    assert len(result.evidence) == 1
    assert result.evidence[0].source == "calculation"
    assert "haversine" in result.evidence[0].reference


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"unit": "mi"},
        {"unit": ""},
    ],
    ids=["missing-unit", "unsupported-unit", "empty-unit"],
)
def test_rejects_invalid_distance_unit(
    parameters: dict[str, str],
) -> None:
    """Reject missing or unsupported distance units."""
    handler = GeodesicDistanceHandler()

    result = handler.execute(
        inputs={
            "point_a": GeoPoint(latitude=53.55, longitude=10.0),
            "point_b": GeoPoint(latitude=52.52, longitude=13.405),
        },
        parameters=parameters,
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_PARAMETER"
    assert result.value is None


@pytest.mark.parametrize(
    ("longitude_a", "longitude_b", "central_angle"),
    [
        (0.0, 90.0, 90.0),
        (179.0, -179.0, 2.0),
        (0.0, 180.0, 180.0),
    ],
    ids=[
        "quarter-equator",
        "antimeridian-crossing",
        "antipodal-points",
    ],
)
def test_distance_matches_equatorial_arc(
    longitude_a: float,
    longitude_b: float,
    central_angle: float,
) -> None:
    """Match independently calculated spherical equatorial arcs."""
    earth_radius_km = 6371.0088
    expected_distance = earth_radius_km * pi * central_angle / 180.0

    handler = GeodesicDistanceHandler()

    result = handler.execute(
        inputs={
            "point_a": GeoPoint(latitude=0.0, longitude=longitude_a),
            "point_b": GeoPoint(latitude=0.0, longitude=longitude_b),
        },
        parameters={"unit": "km"},
    )

    assert result.status == "success"
    assert isinstance(result.value, Quantity)
    assert result.value.unit == "km"
    assert result.value.value == pytest.approx(
        expected_distance,
        abs=1e-8,
    )


def test_distance_between_cities_is_accurate_and_symmetric() -> None:
    """Match a reference distance regardless of point order."""
    hamburg = GeoPoint(latitude=53.55, longitude=10.0)
    berlin = GeoPoint(latitude=52.52, longitude=13.405)

    handler = GeodesicDistanceHandler()

    forward = handler.execute(
        inputs={"point_a": hamburg, "point_b": berlin},
        parameters={"unit": "km"},
    )

    backward = handler.execute(
        inputs={"point_a": berlin, "point_b": hamburg},
        parameters={"unit": "km"},
    )

    assert forward.status == "success"
    assert backward.status == "success"

    assert isinstance(forward.value, Quantity)
    assert isinstance(backward.value, Quantity)

    # Reference calculated independently using the spherical cosine law.
    assert forward.value.value == pytest.approx(254.822, abs=0.01)

    # Reversing the endpoints must not change the distance.
    assert backward.value.value == pytest.approx(
        forward.value.value,
        abs=1e-9,
    )


@pytest.mark.parametrize(
    "inputs",
    [
        {
            "point_b": GeoPoint(latitude=52.52, longitude=13.405),
        },
        {
            "point_a": GeoPoint(latitude=53.55, longitude=10.0),
        },
        {
            "point_a": "Hamburg",
            "point_b": GeoPoint(latitude=52.52, longitude=13.405),
        },
        {
            "point_a": GeoPoint(latitude=53.55, longitude=10.0),
            "point_b": "Berlin",
        },
    ],
    ids=[
        "missing-point-a",
        "missing-point-b",
        "invalid-point-a",
        "invalid-point-b",
    ],
)
def test_rejects_invalid_geo_inputs(
    inputs: dict[str, RuntimeValue],
) -> None:
    """Reject missing coordinates and non-GeoPoint values."""
    handler = GeodesicDistanceHandler()

    result = handler.execute(
        inputs=inputs,
        parameters={"unit": "km"},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_INPUT"
    assert result.message
    assert result.value is None


def test_geodesic_handler_integrates_with_execution_engine() -> None:
    """Execute real distance calculation through approved provider binding."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geodesic_distance_spec())

    providers = ProviderRegistry(capabilities)
    providers.register(
        capability_id="geodesic_distance",
        provider_id="haversine",
        handler=GeodesicDistanceHandler(),
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": ColumnReference(
                        kind="column",
                        name="origin",
                    ),
                    "point_b": ColumnReference(
                        kind="column",
                        name="destination",
                    ),
                },
                parameters={"unit": "km"},
            ),
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
        selected_providers={"geodesic_distance": "haversine"},
    )

    source_row = {
        "origin": GeoPoint(latitude=53.55, longitude=10.0),
        "destination": GeoPoint(latitude=52.52, longitude=13.405),
    }

    execution = DAGExecutor(handlers).execute_row(plan, source_row)

    assert len(execution.steps) == 1
    assert execution.steps[0].step_id == "distance"

    result = execution.steps[0].result

    assert result.status == "success"
    assert isinstance(result.value, Quantity)
    assert result.value.unit == "km"
    assert result.value.value == pytest.approx(254.822, abs=0.01)
    assert result.evidence[0].source == "calculation"
