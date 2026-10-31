"""Built-in capability definitions."""

from ai_data_enrichment.domain.capability import (
    CapabilitySpec,
    DataType,
    InputSpec,
    OutputSpec,
    ParameterSpec,
)


def get_geocode_spec() -> CapabilitySpec:
    """Return the geocode capability definition."""

    return CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve geographical coordinates",
        inputs={
            "city": InputSpec(
                type=DataType.TEXT,
                description="City name",
            ),
            "country": InputSpec(
                type=DataType.TEXT,
                description="Country name",
            ),
        },
        parameters={},
        output=OutputSpec(
            type=DataType.GEO_POINT,
            description="Resolved coordinates",
        ),
    )


def get_resolve_entity_spec() -> CapabilitySpec:
    """Return the resolve_entity capability definition."""
    return CapabilitySpec(
        id="resolve_entity",
        version="1.0",
        description="Resolve entity reference",
        inputs={
            "name": InputSpec(
                type=DataType.TEXT,
                description="Entity name",
            ),
            "country": InputSpec(
                type=DataType.TEXT,
                description="Country name",
            ),
        },
        parameters={
            "entity_type": ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["mountain"],
                description="Entity type",
            ),
        },
        output=OutputSpec(
            type=DataType.ENTITY_REF,
            description="Resolved entity reference",
        ),
    )


def get_lookup_property_spec() -> CapabilitySpec:
    """Return the lookup_property capability definition."""
    return CapabilitySpec(
        id="lookup_property",
        version="1.0",
        description="Resolve property value",
        inputs={
            "entity": InputSpec(
                type=DataType.ENTITY_REF,
                description="Entity reference",
            ),
        },
        parameters={
            "property": ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["elevation"],
                description="Property",
            ),
        },
        output=OutputSpec(
            type=DataType.QUANTITY,
            description="Resolved property value",
            fields={
                "value": DataType.NUMBER,
                "unit": DataType.TEXT,
            },
        ),
    )


def get_geodesic_distance_spec() -> CapabilitySpec:
    """Return the geodesic_distance capability definition."""
    return CapabilitySpec(
        id="geodesic_distance",
        version="1.0",
        description=(
            "Calculate the shortest geodesic distance between two geographic "
            "points along the Earth's surface (often called straight-line or "
            "as-the-crow-flies distance), not road or driving distance."
        ),
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
        parameters={
            "unit": ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["km"],
                description="Distance unit",
            ),
        },
        output=OutputSpec(
            type=DataType.QUANTITY,
            description="Geodesic distance",
            fields={
                "value": DataType.NUMBER,
                "unit": DataType.TEXT,
            },
        ),
    )


def get_builtin_capabilities() -> list[CapabilitySpec]:
    """Return the built-in capability definitions."""
    return [
        get_geocode_spec(),
        get_resolve_entity_spec(),
        get_lookup_property_spec(),
        get_geodesic_distance_spec(),
    ]
