"""Tests for approved capability provider registrations."""

from collections.abc import Mapping
from typing import cast

import pytest

from ai_data_enrichment.capabilities.definitions import get_geocode_spec, get_geodesic_distance_spec
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import (
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.validation.plan_validator import PlanValidator


class StubGeocodeHandler:
    """Return fixed coordinates without external requests."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        return OperationResult(
            status="success",
            value=GeoPoint(latitude=53.55, longitude=10.0),
        )


def test_register_and_get_approved_provider() -> None:
    """Resolve a registered implementation of a known capability."""

    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)
    handler = StubGeocodeHandler()

    providers.register(
        capability_id="geocode",
        provider_id="stub_geocoder",
        handler=handler,
    )

    assert (
        providers.get(
            capability_id="geocode",
            provider_id="stub_geocoder",
        )
        is handler
    )


def test_rejects_unknown_capability() -> None:
    """Do not register providers for undeclared capabilities."""
    capabilities = CapabilityRegistry()
    providers = ProviderRegistry(capabilities)

    with pytest.raises(ValueError, match="Unknown capability 'geocode'"):
        providers.register(
            capability_id="geocode",
            provider_id="stub_geocoder",
            handler=StubGeocodeHandler(),
        )


def test_rejects_duplicate_provider_registration() -> None:
    """Do not silently replace an approved provider implementation."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)
    original_handler = StubGeocodeHandler()

    providers.register(
        capability_id="geocode",
        provider_id="stub_geocoder",
        handler=original_handler,
    )

    with pytest.raises(ValueError, match="already registered"):
        providers.register(
            capability_id="geocode",
            provider_id="stub_geocoder",
            handler=StubGeocodeHandler(),
        )

    assert (
        providers.get(
            capability_id="geocode",
            provider_id="stub_geocoder",
        )
        is original_handler
    )


def test_resolve_selected_provider() -> None:
    """Select the explicitly approved provider for a capability."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)

    first_handler = StubGeocodeHandler()
    second_handler = StubGeocodeHandler()

    providers.register(
        capability_id="geocode",
        provider_id="provider_a",
        handler=first_handler,
    )
    providers.register(
        capability_id="geocode",
        provider_id="provider_b",
        handler=second_handler,
    )

    resolved = providers.resolve_handlers(
        capability_ids=["geocode", "geocode"],
        selected_providers={"geocode": "provider_b"},
    )

    assert resolved == {"geocode": second_handler}
    assert resolved["geocode"] is not first_handler


def test_resolve_rejects_missing_selection() -> None:
    """Require explicit provider selection before execution."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)

    with pytest.raises(
        ValueError,
        match="No provider selected for capability 'geocode'",
    ):
        providers.resolve_handlers(
            capability_ids=["geocode"],
            selected_providers={},
        )


def test_resolve_rejects_unregistered_provider() -> None:
    """Reject an implementation that was never approved."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)

    with pytest.raises(
        ValueError,
        match="Provider 'unknown' is not registered",
    ):
        providers.resolve_handlers(
            capability_ids=["geocode"],
            selected_providers={"geocode": "unknown"},
        )


def test_registered_providers_execute_validated_dag() -> None:
    """Execute a validated DAG using explicitly selected providers."""

    class StubDistanceHandler:
        """Calculate a fixed distance without external services."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            assert inputs["point_a"] == inputs["point_b"]
            assert parameters["unit"] == "km"

            return OperationResult(
                status="success",
                value=Quantity(value=0.0, unit="km"),
            )

    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())
    capabilities.register(get_geodesic_distance_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output",
                        step_id="origin",
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output",
                        step_id="origin",
                    ),
                },
                parameters={"unit": "km"},
            ),
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Hamburg",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
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

    validation = PlanValidator(
        registry=capabilities,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    ).validate(plan)

    assert validation.valid, validation.errors

    providers = ProviderRegistry(capabilities)
    providers.register(
        capability_id="geocode",
        provider_id="stub_geocoder",
        handler=StubGeocodeHandler(),
    )
    providers.register(
        capability_id="geodesic_distance",
        provider_id="local_distance",
        handler=StubDistanceHandler(),
    )

    handlers = providers.resolve_handlers(
        capability_ids=[step.capability for step in plan.steps],
        selected_providers={
            "geocode": "stub_geocoder",
            "geodesic_distance": "local_distance",
        },
    )

    result = DAGExecutor(handlers).execute_row(plan, {})

    assert [step.step_id for step in result.steps] == [
        "origin",
        "distance",
    ]
    assert all(step.result.status == "success" for step in result.steps)
    assert result.steps[-1].result.value == Quantity(
        value=0.0,
        unit="km",
    )


def test_rejects_invalid_provider_handler() -> None:
    """Reject an implementation without an executable handler method."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)

    # Simulate invalid runtime configuration bypassing static type checks.
    invalid_handler = cast(CapabilityHandler, object())

    with pytest.raises(
        TypeError,
        match="Provider handler must implement execute",
    ):
        providers.register(
            capability_id="geocode",
            provider_id="invalid_provider",
            handler=invalid_handler,
        )


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_rejects_empty_provider_id(provider_id: str) -> None:
    """Reject empty or whitespace-only provider identifiers."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())

    providers = ProviderRegistry(capabilities)

    with pytest.raises(ValueError, match="Provider ID must not be empty"):
        providers.register(
            capability_id="geocode",
            provider_id=provider_id,
            handler=StubGeocodeHandler(),
        )
