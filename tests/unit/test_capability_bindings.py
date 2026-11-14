"""The binding table states what the code used to know by comparing capability ids."""

import pytest

from ai_data_enrichment.capabilities.bindings import (
    BINDINGS,
    Trust,
    binding_for,
    find_binding,
    name_resolution_for,
    name_resolvers,
)
from ai_data_enrichment.capabilities.definitions import (
    get_builtin_capabilities,
    offered_capabilities,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.orchestration.live_executor import build_live_executor
from ai_data_enrichment.plan_run_worker import remote_sources
from ai_data_enrichment.providers.openrouteservice import ROUTING_PROVIDER


class NoNetwork:
    def get_json(self, *, url, params, timeout_seconds):  # type: ignore[no-untyped-def]
        raise AssertionError("no request expected")

    def post_json(self, *, url, body, timeout_seconds):  # type: ignore[no-untyped-def]
        raise AssertionError("no request expected")


def registry(*, road_distance: bool = True) -> CapabilityRegistry:
    result = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=road_distance):
        result.register(spec)
    return result


def test_every_offered_capability_has_exactly_one_binding() -> None:
    offered = [spec.id for spec in offered_capabilities(road_distance=True)]
    bound = [item.capability_id for item in BINDINGS]
    assert sorted(bound) == sorted(offered)
    assert len(set(bound)) == len(bound)


def test_the_four_of_release_0_1_0_are_the_always_bound_ones() -> None:
    assert {item.capability_id for item in BINDINGS if item.always_bound} == {
        spec.id for spec in get_builtin_capabilities()
    }


def test_the_sources_equal_what_the_executor_registers() -> None:
    executor = build_live_executor(registry(), transport=NoNetwork())
    assert executor._selected_providers == {item.capability_id: item.source_id for item in BINDINGS}
    assert binding_for("road_distance").source_id == ROUTING_PROVIDER


def test_a_registry_without_the_added_capabilities_binds_only_the_four() -> None:
    plain = CapabilityRegistry()
    for spec in get_builtin_capabilities():
        plain.register(spec)
    executor = build_live_executor(plain)
    assert set(executor._selected_providers) == {spec.id for spec in get_builtin_capabilities()}


def test_the_reuse_set_equals_what_the_executor_used_before() -> None:
    audited = build_live_executor(
        registry(),
        transport=NoNetwork(),
        audit_mountains=True,
        audit_capitals=True,
        reuse=(object(), object()),  # type: ignore[arg-type]
    )
    assert set(audited._policy.persistent_cache_ttls) == {
        ("resolve_entity", "wikidata"),
        ("geocode", "open-meteo"),
        ("lookup_property", "wikidata"),
        ("road_distance", ROUTING_PROVIDER),
    }
    unaudited = build_live_executor(
        registry(road_distance=False),
        transport=NoNetwork(),
        reuse=(object(), object()),  # type: ignore[arg-type]
    )
    assert set(unaudited._policy.persistent_cache_ttls) == {("lookup_property", "wikidata")}


def test_only_what_is_asked_of_a_source_is_reusable() -> None:
    assert {item.capability_id for item in BINDINGS if item.reusable} == {
        "geocode",
        "resolve_entity",
        "lookup_property",
        "road_distance",
    }
    assert all(not item.reusable for item in BINDINGS if item.trust is Trust.COMPUTED)


def test_kinds_of_trust() -> None:
    assert {item.capability_id: item.trust for item in BINDINGS} == {
        "geocode": Trust.RESOLVED_NAME,
        "geodesic_distance": Trust.COMPUTED,
        "resolve_entity": Trust.RESOLVED_NAME,
        "lookup_property": Trust.RECORD,
        "ratio": Trust.COMPUTED,
        "road_distance": Trust.SERVICE,
    }


def test_a_capability_that_resolves_a_name_states_its_name_and_context() -> None:
    resolvers = {item.capability_id for item in name_resolvers()}
    assert resolvers == {
        item.capability_id for item in BINDINGS if item.trust is Trust.RESOLVED_NAME
    }
    for capability in resolvers:
        resolution = name_resolution_for(capability)
        assert resolution.name_input and resolution.entity_type
        assert resolution.approved_parameters
        assert resolution.match_description
    assert name_resolution_for("geocode").name_input == "city"
    assert name_resolution_for("geocode").context_inputs == ("administrative_area",)
    assert name_resolution_for("resolve_entity").name_input == "name"
    assert dict(name_resolution_for("resolve_entity").approved_parameters[0]) == {
        "entity_type": "mountain"
    }
    with pytest.raises(ValueError):
        name_resolution_for("lookup_property")


def test_road_distance_is_the_only_optional_capability() -> None:
    assert [item.capability_id for item in BINDINGS if item.optional] == ["road_distance"]


def test_the_sources_a_run_asks_follow_the_registry() -> None:
    assert remote_sources(registry()) == ["open-meteo", "wikidata", ROUTING_PROVIDER]
    assert remote_sources(registry(road_distance=False)) == ["open-meteo", "wikidata"]


def test_an_unknown_capability_has_no_binding() -> None:
    assert find_binding("sentiment") is None
