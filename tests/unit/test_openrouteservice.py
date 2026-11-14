"""Offline tests for the driving distance read from the routing service."""

from collections.abc import Mapping

import pytest

from ai_data_enrichment.capabilities.definitions import (
    get_builtin_capabilities,
    get_road_distance_spec,
    offered_capabilities,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.models import GeoPoint, Quantity
from ai_data_enrichment.orchestration.live_executor import LIVE_SOURCES, build_live_executor
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.openrouteservice import (
    DIRECTIONS_URL,
    ROUTING_KEY_VARIABLE,
    ROUTING_PROVIDER,
    OpenRouteServiceDistanceHandler,
    routing_key,
)
from ai_data_enrichment.providers.retry_transport import RetryExhausted

PARIS = GeoPoint(latitude=48.85341, longitude=2.3488)
LONDON = GeoPoint(latitude=51.50853, longitude=-0.12574)


class Routes:
    """Answer one route request with a fixed payload or failure, and record it."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, object], float]] = []

    def post_json(self, *, url: str, body: Mapping[str, object], timeout_seconds: float) -> object:
        self.requests.append((url, dict(body), timeout_seconds))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def ask(answer: object, *, a: GeoPoint = PARIS, b: GeoPoint = LONDON, unit: str = "km"):
    routes = Routes(answer)
    result = OpenRouteServiceDistanceHandler(routes).execute(
        inputs={"point_a": a, "point_b": b}, parameters={"unit": unit}
    )
    return result, routes


def refused(code: int, status: int = 404) -> JsonTransportError:
    return JsonTransportError(
        "http_status", status_code=status, payload={"error": {"code": code, "message": "x"}}
    )


def test_the_route_length_is_returned_in_kilometres_with_what_computed_it() -> None:
    result, routes = ask(
        {
            "routes": [{"summary": {"distance": 456_789.0, "duration": 19_000.0}}],
            "metadata": {"engine": {"version": "9.3.0", "graph_date": "2026-11-02T00:00:00Z"}},
        }
    )

    assert result.status == "success"
    assert result.value == Quantity(value=456.789, unit="km")
    # Not a record that could be opened: the service, its profile, its road data.
    assert [(e.source, e.reference) for e in result.evidence] == [
        ("openrouteservice", "driving-car:osm-2026-11-02T00:00:00Z")
    ]
    [(url, body, timeout)] = routes.requests
    assert (
        url
        == DIRECTIONS_URL
        == ("https://api.heigit.org/openrouteservice/v2/directions/driving-car")
    )
    # Longitude first, and only the length is asked for.
    assert body == {
        "coordinates": [[2.3488, 48.85341], [-0.12574, 51.50853]],
        "instructions": False,
        "geometry": False,
        "units": "m",
    }
    assert timeout == 20.0


@pytest.mark.parametrize("metadata", [{}, {"engine": {}}, {"engine": {"graph_date": 7}}, "x"])
def test_an_answer_without_a_data_date_still_names_the_service(metadata) -> None:
    result, _ = ask({"routes": [{"summary": {"distance": 1000}}], "metadata": metadata})

    assert result.value == Quantity(value=1.0, unit="km")
    assert result.evidence[0].reference == "driving-car"


def test_the_same_place_twice_is_zero_without_asking_the_service() -> None:
    result, routes = ask({}, b=PARIS)

    assert result.value == Quantity(value=0.0, unit="km")
    assert result.evidence[0].source == "calculation"
    assert routes.requests == []


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        (2004, 400, "ROUTE_TOO_LONG"),
        (2009, 404, "NO_ROUTE"),
        (2010, 404, "POINT_NOT_ON_ROAD"),
    ],
)
def test_the_services_own_reasons_become_distinct_failures(code, status, expected) -> None:
    result, _ = ask(refused(code, status))

    assert (result.status, result.error_code) == ("failure", expected)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_or_used_up_quota_is_not_reported_as_a_missing_route(status) -> None:
    result, _ = ask(JsonTransportError("http_status", status_code=status))

    assert result.error_code == "SOURCE_ACCESS_DENIED"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        # An unknown reason is a failed request, never "no route".
        (JsonTransportError("http_status", status_code=404), "PROVIDER_ERROR"),
        (refused(2099, 500), "PROVIDER_ERROR"),
        (JsonTransportError("http_status", status_code=400, payload="nonsense"), "PROVIDER_ERROR"),
        (JsonTransportError("timeout"), "PROVIDER_ERROR"),
        (
            RetryExhausted(JsonTransportError("http_status", status_code=429), attempts=3),
            "RETRY_EXHAUSTED",
        ),
        (JsonTransportError("invalid_json"), "INVALID_RESPONSE"),
    ],
)
def test_other_failures_keep_the_common_codes(failure, expected) -> None:
    result, _ = ask(failure)

    assert result.error_code == expected


@pytest.mark.parametrize(
    "answer",
    [
        [],
        {},
        {"routes": []},
        {"routes": ["x"]},
        {"routes": [{"summary": {}}]},
        {"routes": [{"summary": {"distance": "456"}}]},
        {"routes": [{"summary": {"distance": True}}]},
        {"routes": [{"summary": {"distance": -1.0}}]},
        {"routes": [{"summary": {"distance": float("nan")}}]},
        {"routes": [{"summary": {"distance": float("inf")}}]},
    ],
)
def test_an_answer_without_a_usable_length_is_not_a_value(answer) -> None:
    result, _ = ask(answer)

    assert result.error_code == "INVALID_RESPONSE"


def test_inputs_and_unit_are_checked_before_the_service_is_asked() -> None:
    routes = Routes({})
    handler = OpenRouteServiceDistanceHandler(routes)

    wrong_input = handler.execute(
        inputs={"point_a": PARIS, "point_b": "London"}, parameters={"unit": "km"}
    )
    wrong_unit = handler.execute(
        inputs={"point_a": PARIS, "point_b": LONDON}, parameters={"unit": "mi"}
    )

    assert wrong_input.error_code == "INVALID_INPUT"
    assert wrong_unit.error_code == "INVALID_PARAMETER"
    assert routes.requests == []


def test_the_description_tells_the_planner_it_is_not_a_straight_line() -> None:
    spec = get_road_distance_spec()

    assert "road" in spec.description and "not the straight-line" in spec.description
    assert spec.parameters["unit"].required and spec.parameters["unit"].allowed_values == ["km"]
    assert spec.output.fields.keys() == {"value", "unit"}


@pytest.mark.parametrize("value", ["", "   "])
def test_without_a_key_road_distance_is_not_offered(monkeypatch, value) -> None:
    monkeypatch.setenv(ROUTING_KEY_VARIABLE, value)

    assert routing_key() is None
    registry = builtin_registry()
    assert registry.get("road_distance") is None
    # What was offered before stays, and a ratio needs no source.
    assert {spec.id for spec in get_builtin_capabilities()} | {"ratio"} == {
        spec.id for spec in registry.list_all()
    }


def test_with_a_key_road_distance_is_offered(monkeypatch) -> None:
    monkeypatch.setenv(ROUTING_KEY_VARIABLE, " key-for-tests ")

    assert routing_key() == "key-for-tests"
    assert builtin_registry().get("road_distance") is not None


def offered(*, road_distance: bool) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=road_distance):
        registry.register(spec)
    return registry


def test_an_executor_cannot_be_built_for_road_distance_without_its_key() -> None:
    with pytest.raises(ValueError, match="routing key"):
        build_live_executor(offered(road_distance=True))

    # Nothing changes for an installation that does not offer it.
    assert build_live_executor(offered(road_distance=False)) is not None
    assert build_live_executor(offered(road_distance=True), routing_key="k") is not None


def test_an_injected_transport_must_be_able_to_send_a_route_request() -> None:
    class GetOnly:
        def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float):
            return {}

    with pytest.raises(ValueError, match="JSON body"):
        build_live_executor(offered(road_distance=True), transport=GetOnly())


def test_every_offered_capability_names_where_its_values_come_from() -> None:
    # A capability without a source could be planned but not run, and a client
    # could not say where its values come from.
    offered = {spec.id for spec in offered_capabilities(road_distance=True)}
    assert offered == set(LIVE_SOURCES)
    assert LIVE_SOURCES["road_distance"] == ROUTING_PROVIDER
