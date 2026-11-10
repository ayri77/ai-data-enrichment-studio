"""Tests for autonomous agent decision validation."""

import pytest

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.orchestration.autonomous.contracts import ToolCall
from ai_data_enrichment.orchestration.autonomous.validation import validate_tool_call


def test_unregistered_capability_is_rejected() -> None:
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    call = ToolCall(
        call_id="delete_data",
        capability_id="delete_database",
        inputs={},
    )

    with pytest.raises(ValueError, match="not registered"):
        validate_tool_call(
            call=call,
            registry=registry,
            allowed_capabilities=frozenset({"delete_database"}),
            source_columns=frozenset(),
            observations={},
        )


def test_registered_but_disallowed_capability_is_rejected() -> None:
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    call = ToolCall(
        call_id="geocode_london",
        capability_id="geocode",
        inputs={},
    )

    with pytest.raises(ValueError, match="not allowed"):
        validate_tool_call(
            call=call,
            registry=registry,
            allowed_capabilities=frozenset({"geodesic_distance"}),
            source_columns=frozenset({"Capital_From"}),
            observations={},
        )
