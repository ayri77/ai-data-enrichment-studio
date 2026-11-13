"""Driving distance by road from openrouteservice (HeiGIT), computed on OpenStreetMap data.

A value from here is the result of a routing service for two coordinates. It is
not read from a record that could be opened and checked, so its evidence names
the service, the profile and the date of the road data it used, nothing more.

The service needs a personal key. Without one the capability is not offered at
all: it is not registered, the planner is not told about it and a plan that
asks for it does not validate.
"""

import os
from collections.abc import Mapping
from json import JSONDecodeError

from ai_data_enrichment.execution.models import (
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonPostTransport, JsonTransportError

ROUTING_PROVIDER = "openrouteservice"
# The variable that holds the key. Its value is read where a request is sent
# and nowhere else; it is never written to a report, a log or a plan.
ROUTING_KEY_VARIABLE = "OPENROUTESERVICE_API_KEY"
ROUTING_PROFILE = "driving-car"
# api.openrouteservice.org was shut down in November 2026 in favour of this host.
DIRECTIONS_URL = f"https://api.heigit.org/openrouteservice/v2/directions/{ROUTING_PROFILE}"
# A route across a continent takes the service a few seconds.
ROUTING_TIMEOUT_SECONDS = 20.0

# The service's own error codes, stated in the body of an error answer.
_SERVICE_FAILURES = {
    2004: ("ROUTE_TOO_LONG", "The places are too far apart for the routing service."),
    2009: ("NO_ROUTE", "The routing service found no road route between the places."),
    2010: ("POINT_NOT_ON_ROAD", "The routing service found no road near one of the places."),
}


def routing_key() -> str | None:
    """The configured key, or None when road distances are not offered."""
    return os.getenv(ROUTING_KEY_VARIABLE, "").strip() or None


def _service_code(payload: object) -> int | None:
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if type(code) is int else None


def _graph_date(payload: Mapping[str, object]) -> str | None:
    """The date of the road data the route was computed on, when the answer states it."""
    metadata = payload.get("metadata")
    engine = metadata.get("engine") if isinstance(metadata, dict) else None
    date = engine.get("graph_date") if isinstance(engine, dict) else None
    # Only a short plain value becomes part of a reference.
    if isinstance(date, str) and 0 < len(date) <= 40 and date.isprintable():
        return date.strip() or None
    return None


def _route_metres(payload: object) -> float | None:
    """The length of the first route in an answer, or None when it states none."""
    routes = payload.get("routes") if isinstance(payload, dict) else None
    first = routes[0] if isinstance(routes, list) and routes else None
    summary = first.get("summary") if isinstance(first, dict) else None
    metres = summary.get("distance") if isinstance(summary, dict) else None
    # A boolean is not a length, although Python counts it as a number.
    if isinstance(metres, bool) or not isinstance(metres, (int, float)):
        return None
    return float(metres) if 0 <= metres < 1e9 else None


class OpenRouteServiceDistanceHandler:
    """Ask for the length of the driving route between two points."""

    def __init__(self, transport: JsonPostTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
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

        # The same place twice has nothing to route; the service is not asked.
        if point_a == point_b:
            return OperationResult(
                status="success",
                value=Quantity(value=0.0, unit="km"),
                evidence=(Evidence(source="calculation", reference="same-point"),),
            )

        try:
            payload = self._transport.post_json(
                url=DIRECTIONS_URL,
                body={
                    # The service takes longitude first.
                    "coordinates": [
                        [point_a.longitude, point_a.latitude],
                        [point_b.longitude, point_b.latitude],
                    ],
                    # Only the length is used; the path itself is not requested.
                    "instructions": False,
                    "geometry": False,
                    "units": "m",
                },
                timeout_seconds=ROUTING_TIMEOUT_SECONDS,
            )
        except JsonTransportError as exc:
            failure = _SERVICE_FAILURES.get(_service_code(exc.payload) or 0)
            if failure is not None:
                return OperationResult(status="failure", error_code=failure[0], message=failure[1])
            if exc.kind == "http_status" and exc.status_code in (401, 403):
                # The service answers the same way for a wrong key and for a
                # used-up daily quota.
                return OperationResult(
                    status="failure",
                    error_code="SOURCE_ACCESS_DENIED",
                    message="The routing service refused the request.",
                )
            return transport_failure_result(exc, provider_label="Routing service")
        except (JSONDecodeError, OSError) as exc:
            return transport_failure_result(exc, provider_label="Routing service")

        metres = _route_metres(payload)
        if metres is None:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Routing service returned no route length.",
            )

        assert isinstance(payload, dict)
        graph_date = _graph_date(payload)
        return OperationResult(
            status="success",
            value=Quantity(value=metres / 1000.0, unit="km"),
            evidence=(
                Evidence(
                    source=ROUTING_PROVIDER,
                    reference=(
                        f"{ROUTING_PROFILE}:osm-{graph_date}" if graph_date else ROUTING_PROFILE
                    ),
                ),
            ),
        )
