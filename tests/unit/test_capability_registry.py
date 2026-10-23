"""Tests for the CapabilityRegistry class."""

import pytest

from ai_data_enrichment.capabilities.definitions import get_resolve_entity_spec
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import (
    CapabilitySpec,
    DataType,
    InputSpec,
    OutputSpec,
)


def test_registry_get_unknown_capability() -> None:
    """Registry should return None for an unknown capability."""
    registry = CapabilityRegistry()

    assert registry.get("unknown") is None


def test_registry_register_and_get_capability() -> None:
    """Registry should store and retrieve a registered capability."""
    registry = CapabilityRegistry()

    spec = CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve geographical coordinates",
        inputs={},
        output=OutputSpec(
            type=DataType.GEO_POINT,
            description="Resolved coordinates",
        ),
    )

    registry.register(spec)

    assert registry.get("geocode") == spec


def test_registry_rejects_duplicate_id() -> None:
    """Registry should reject a capability with a duplicate ID."""
    registry = CapabilityRegistry()

    spec = CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve geographical coordinates",
        inputs={},
        output=OutputSpec(
            type=DataType.GEO_POINT,
            description="Resolved coordinates",
        ),
    )

    registry.register(spec)

    duplicate_spec = spec.model_copy(update={"version": "2.0"})

    with pytest.raises(ValueError) as exc_info:
        registry.register(duplicate_spec)

    assert str(exc_info.value) == "Capability with ID 'geocode' already registered"
    assert registry.get("geocode") is spec


def test_registry_list_all_capabilities() -> None:
    """Registry should return all registered capabilities."""
    registry = CapabilityRegistry()

    spec1 = CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve geographical coordinates",
        inputs={},
        output=OutputSpec(
            type=DataType.GEO_POINT,
            description="Resolved coordinates",
        ),
    )

    spec2 = CapabilitySpec(
        id="geodesic_distance",
        version="1.0",
        description="Calculate the geodesic distance between two points",
        inputs={
            "point_a": InputSpec(
                type=DataType.GEO_POINT,
                description="First geographic point",
            ),
            "point_b": InputSpec(
                type=DataType.GEO_POINT,
                description="Second geographic point",
            ),
        },
        output=OutputSpec(
            type=DataType.QUANTITY,
            description="Calculated distance",
        ),
    )

    registry.register(spec1)
    registry.register(spec2)

    result = registry.list_all()

    assert len(result) == 2
    assert {spec.id for spec in result} == {
        "geocode",
        "geodesic_distance",
    }


def test_registry_list_all_returns_independent_list() -> None:
    """Registry should return an independent list of capabilities."""
    registry = CapabilityRegistry()

    spec = CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve geographical coordinates",
        inputs={},
        output=OutputSpec(
            type=DataType.GEO_POINT,
            description="Resolved coordinates",
        ),
    )

    registry.register(spec)

    result = registry.list_all()

    assert len(result) == 1
    assert result[0].id == "geocode"

    result.clear()

    assert registry.get("geocode") is spec
    assert len(registry.list_all()) == 1


def test_registry_describe_all() -> None:
    """Registry should return JSON-compatible capability descriptions."""
    registry = CapabilityRegistry()
    spec = get_resolve_entity_spec()
    registry.register(spec)

    descriptions = registry.describe_all()

    assert descriptions == [spec.model_dump(mode="json")]
    assert descriptions[0]["parameters"]["entity_type"]["allowed_values"] == ["mountain"]
