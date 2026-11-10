"""Offline integration test for the minimal autonomous runner."""

from collections.abc import Mapping

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.execution.models import (
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.orchestration.autonomous.contracts import (
    AgentDecision,
    Finish,
    ObservationRef,
    SourceFieldRef,
    ToolCall,
    ToolObservation,
)
from ai_data_enrichment.orchestration.autonomous.runtime import AutonomousRunner
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler


class FixtureGeocoder:
    """Return deterministic coordinates without network access."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        locations = {
            ("London", "United Kingdom"): GeoPoint(
                latitude=51.5074,
                longitude=-0.1278,
            ),
            ("Rome", "Italy"): GeoPoint(
                latitude=41.9028,
                longitude=12.4964,
            ),
        }

        city = inputs.get("city")
        country = inputs.get("country")

        if not isinstance(city, str) or not isinstance(country, str):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Expected city and country strings.",
            )

        point = locations.get((city, country))

        if point is None:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Location not found in offline fixture.",
            )

        return OperationResult(
            status="success",
            value=point,
            evidence=(
                Evidence(
                    source="test_fixture",
                    reference=f"{city}:{country}",
                ),
            ),
        )


class ScriptedDecisionMaker:
    """Return scripted actions for a reproducible test."""

    def __init__(self, decisions: tuple[AgentDecision, ...]) -> None:
        self.decisions = decisions

    def decide(
        self,
        *,
        instruction: str,
        record: InputRecord,
        observations: tuple[ToolObservation, ...],
    ) -> AgentDecision:
        return self.decisions[len(observations)]


def test_autonomous_distance_happy_path() -> None:
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    decisions: tuple[AgentDecision, ...] = (
        ToolCall(
            call_id="london",
            capability_id="geocode",
            inputs={
                "city": SourceFieldRef(column="Capital_From"),
                "country": SourceFieldRef(column="Country_From"),
            },
        ),
        ToolCall(
            call_id="rome",
            capability_id="geocode",
            inputs={
                "city": SourceFieldRef(column="Capital_To"),
                "country": SourceFieldRef(column="Country_To"),
            },
        ),
        ToolCall(
            call_id="distance",
            capability_id="geodesic_distance",
            inputs={
                "point_a": ObservationRef(call_id="london"),
                "point_b": ObservationRef(call_id="rome"),
            },
            parameters={"unit": "km"},
        ),
        Finish(result_call_id="distance"),
    )

    runner = AutonomousRunner(
        registry=registry,
        handlers={
            "geocode": FixtureGeocoder(),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        allowed_capabilities=frozenset({"geocode", "geodesic_distance"}),
        decision_maker=ScriptedDecisionMaker(decisions),
        max_decisions=8,
    )

    result = runner.run(
        instruction="Calculate the distance from London to Rome.",
        record=InputRecord(
            record_id="Capitals!2",
            values={
                "Capital_From": "London",
                "Country_From": "United Kingdom",
                "Capital_To": "Rome",
                "Country_To": "Italy",
            },
        ),
    )

    assert result.status == "completed"
    assert result.result_call_id == "distance"
    assert len(result.observations) == 3

    assert isinstance(result.value, Quantity)
    assert result.value.unit == "km"
    assert 1400 < result.value.value < 1500

    assert [observation.capability_id for observation in result.observations] == [
        "geocode",
        "geocode",
        "geodesic_distance",
    ]
