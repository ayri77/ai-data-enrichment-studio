"""Tests for the builtin capabilities."""

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import DataType, InputSpec, OutputSpec, ParameterSpec


def test_builtin_capabilities_have_unique_ids() -> None:
    """Built-in capabilities should have unique IDs."""
    capabilities = get_builtin_capabilities()
    ids = [c.id for c in capabilities]
    assert len(ids) == len(set(ids))


def test_builtin_capabilities_can_be_registered() -> None:
    """All built-in capabilities should be registered successfully."""
    capabilities = get_builtin_capabilities()
    registry = CapabilityRegistry()
    for capability in capabilities:
        registry.register(capability)
    expected_ids = {
        "geocode",
        "resolve_entity",
        "lookup_property",
        "geodesic_distance",
    }

    all_ids = {c.id for c in registry.list_all()}

    assert all_ids == expected_ids


def test_builtin_capabilities_include_geocode() -> None:
    """Built-in capabilities should include the geocode contract."""

    capabilities = get_builtin_capabilities()
    geocode_capability = next((c for c in capabilities if c.id == "geocode"), None)
    assert geocode_capability is not None
    assert geocode_capability.version == "1.0"
    assert geocode_capability.inputs == {
        "city": InputSpec(
            type=DataType.TEXT,
            description="City name",
        ),
        "country": InputSpec(
            type=DataType.TEXT,
            description="Country name",
        ),
    }
    assert geocode_capability.parameters == {}
    assert geocode_capability.output == OutputSpec(
        type=DataType.GEO_POINT,
        description="Resolved coordinates",
    )


def test_builtin_capabilities_include_resolve_entity() -> None:
    """Built-in capabilities should include the resolve_entity contract."""

    capabilities = get_builtin_capabilities()
    resolve_entity_capability = next((c for c in capabilities if c.id == "resolve_entity"), None)
    assert resolve_entity_capability is not None
    assert resolve_entity_capability.version == "1.0"
    assert resolve_entity_capability.inputs == {
        "name": InputSpec(
            type=DataType.TEXT,
            description="Entity name",
        ),
        "country": InputSpec(
            type=DataType.TEXT,
            description="Country name",
        ),
    }
    assert resolve_entity_capability.parameters == {
        "entity_type": ParameterSpec(
            type=DataType.TEXT,
            allowed_values=["mountain"],
            description="Entity type",
        ),
    }
    assert resolve_entity_capability.output == OutputSpec(
        type=DataType.ENTITY_REF,
        description="Resolved entity reference",
    )


def test_builtin_capabilities_include_lookup_property() -> None:
    """Built-in capabilities should include the lookup_property contract."""

    capabilities = get_builtin_capabilities()
    lookup_property_capability = next((c for c in capabilities if c.id == "lookup_property"), None)
    assert lookup_property_capability is not None
    assert lookup_property_capability.version == "1.0"
    assert lookup_property_capability.inputs == {
        "entity": InputSpec(
            type=DataType.ENTITY_REF,
            description="Entity reference",
        ),
    }
    assert lookup_property_capability.parameters == {
        "property": ParameterSpec(
            type=DataType.TEXT,
            allowed_values=["elevation"],
            description="Property",
        ),
    }
    assert lookup_property_capability.output == OutputSpec(
        type=DataType.QUANTITY,
        description="Resolved property value",
    )


def test_builtin_capabilities_include_geodesic_distance() -> None:
    """Built-in capabilities should include the geodesic_distance contract."""
    capabilities = get_builtin_capabilities()
    geodesic_distance_capability = next(
        (c for c in capabilities if c.id == "geodesic_distance"), None
    )
    assert geodesic_distance_capability is not None
    assert geodesic_distance_capability.version == "1.0"
    assert geodesic_distance_capability.inputs == {
        "point_a": InputSpec(
            type=DataType.GEO_POINT,
            description="First geographic point",
        ),
        "point_b": InputSpec(
            type=DataType.GEO_POINT,
            description="Second geographic point",
        ),
    }
    assert geodesic_distance_capability.parameters == {
        "unit": ParameterSpec(
            type=DataType.TEXT,
            allowed_values=["km"],
            description="Distance unit",
        ),
    }
    assert geodesic_distance_capability.output == OutputSpec(
        type=DataType.QUANTITY,
        description="Geodesic distance",
    )
