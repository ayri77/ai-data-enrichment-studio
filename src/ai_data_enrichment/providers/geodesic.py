"""Deterministic great-circle distance calculation."""

from collections.abc import Mapping
from math import asin, cos, radians, sin, sqrt

from ai_data_enrichment.execution.models import (
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)

EARTH_RADIUS_KM = 6371.0088


class GeodesicDistanceHandler:
    """Calculate spherical great-circle distances in kilometers."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Return the distance between two geographic points."""
        point_a = inputs.get("point_a")
        point_b = inputs.get("point_b")

        if not isinstance(point_a, GeoPoint) or not isinstance(point_b, GeoPoint):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Both point_a and point_b must be GeoPoint values.",
            )

        if parameters.get("unit") != "km":
            return OperationResult(
                status="failure",
                error_code="INVALID_PARAMETER",
                message="Distance unit must be 'km'.",
            )

        lat_a = radians(point_a.latitude)
        lat_b = radians(point_b.latitude)
        delta_lat = lat_b - lat_a
        delta_lon = radians(point_b.longitude - point_a.longitude)

        haversine = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2

        # Clamp rounding errors near identical or antipodal points.
        haversine = min(1.0, max(0.0, haversine))

        distance_km = 2 * EARTH_RADIUS_KM * asin(sqrt(haversine))

        return OperationResult(
            status="success",
            value=Quantity(value=distance_km, unit="km"),
            evidence=(
                Evidence(
                    source="calculation",
                    reference="haversine:earth-radius-km:6371.0088",
                ),
            ),
        )
