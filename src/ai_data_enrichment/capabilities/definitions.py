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
        version="1.1",
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
            "administrative_area": InputSpec(
                type=DataType.TEXT,
                required=False,
                description="Optional state, province or administrative region",
            ),
        },
        parameters={
            "required_role": ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["country_capital"],
                description=(
                    "Optional hard constraint requiring the resolved location "
                    "to have a verified country-capital administrative role. Omit this "
                    "parameter for literal-city lookup without a role restriction; "
                    "null is not a valid value."
                ),
            ),
        },
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
                description=(
                    "Country context for mountain entity resolution. "
                    "Accepts a single country name or slash-separated country names "
                    "(e.g. 'Nepal/China'). "
                    "For multiple countries, the resolver verifies that the mountain "
                    "belongs to at least one listed country using Wikidata country claims. "
                    "Unknown country names cause an error. "
                    "The original country value is preserved."
                ),
            ),
        },
        parameters={
            "entity_type": ParameterSpec(
                type=DataType.TEXT,
                required=True,
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
                required=True,
                allowed_values=["elevation"],
                description="Property",
            ),
        },
        output=OutputSpec(
            type=DataType.QUANTITY,
            description=(
                "Mountain elevation in metres (m). "
                "The current Wikidata provider returns a successful "
                "Quantity with unit='m' only when the source elevation "
                "is explicitly expressed in metres. "
                "Unsupported units produce an UNSUPPORTED_UNIT failure; "
                "missing or ambiguous values also produce explicit failures. "
                "No unit conversion is performed."
            ),
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
                required=True,
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


def get_road_distance_spec() -> CapabilitySpec:
    """Return the road_distance capability definition.

    Offered only where a routing source is configured; see ``offered_capabilities``.
    """
    return CapabilitySpec(
        id="road_distance",
        version="1.0",
        description=(
            "Calculate the driving distance by road between two geographic points: "
            "the length of the car route a routing service finds on the road network, "
            "not the straight-line (geodesic) distance. Fails for a pair of points "
            "that no road route connects, such as places separated by an ocean."
        ),
        inputs={
            "point_a": InputSpec(
                type=DataType.GEO_POINT,
                description="Start point of the route",
            ),
            "point_b": InputSpec(
                type=DataType.GEO_POINT,
                description="End point of the route",
            ),
        },
        parameters={
            "unit": ParameterSpec(
                type=DataType.TEXT,
                required=True,
                allowed_values=["km"],
                description="Distance unit",
            ),
        },
        output=OutputSpec(
            type=DataType.QUANTITY,
            description="Length of the driving route",
            fields={
                "value": DataType.NUMBER,
                "unit": DataType.TEXT,
            },
        ),
    )


def get_ratio_spec() -> CapabilitySpec:
    """Return the ratio capability definition."""
    return CapabilitySpec(
        id="ratio",
        version="1.0",
        description=(
            "Divide one quantity by another quantity of the same unit, to say how many "
            "times the first is larger than the second (for example a road distance "
            "over a straight-line distance). The result is a plain number without a "
            "unit. Fails when the units differ or the second quantity is zero; no "
            "unit is converted."
        ),
        inputs={
            "numerator": InputSpec(
                type=DataType.QUANTITY,
                description="Quantity to divide",
            ),
            "denominator": InputSpec(
                type=DataType.QUANTITY,
                description="Quantity to divide by, in the same unit",
            ),
        },
        output=OutputSpec(
            type=DataType.NUMBER,
            description="Ratio of the two quantities, without a unit",
            fields={
                "value": DataType.NUMBER,
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


def offered_capabilities(*, road_distance: bool) -> list[CapabilitySpec]:
    """What the application offers: the built-in set and what was added to it.

    ``road_distance`` is offered only where its routing source is configured,
    so that a plan never asks for a value nothing here can produce.
    """
    return [
        *get_builtin_capabilities(),
        get_ratio_spec(),
        *([get_road_distance_spec()] if road_distance else []),
    ]
