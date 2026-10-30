"""Test provider-independent LLM plan generation."""

import json
from typing import Any

import pytest

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationError


class FakePlanClient:
    """Return a predefined JSON response without calling an external model."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        self.calls.append((messages, response_schema))
        return json.dumps(self.response)


def test_llm_planner_generates_and_validates_mountain_plan() -> None:
    """Compose model context from the request, profile and capability registry."""
    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain dataset",
        columns=[
            ColumnProfile(name="Mountain", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Country", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="height", inferred_type=PrimitiveType.NUMBER, null_count=0),
        ],
    )
    candidate = {
        "version": "1.0",
        "steps": [
            {
                "id": "mountain",
                "capability": "resolve_entity",
                "inputs": {
                    "name": {"kind": "column", "name": "Mountain"},
                    "country": {"kind": "column", "name": "Country"},
                },
                "parameters": {"entity_type": "mountain"},
            },
            {
                "id": "elevation",
                "capability": "lookup_property",
                "inputs": {
                    "entity": {"kind": "step_output", "step_id": "mountain"},
                },
                "parameters": {"property": "elevation"},
            },
        ],
        "outputs": [
            {"step_id": "elevation", "value_field": "value", "target_column": "height"},
        ],
        "assumptions": [],
    }
    client = FakePlanClient(response=candidate)
    planner = LLMPlanner(client=client, registry=registry)

    result = planner.generate(
        instruction="Find the elevation of each mountain in meters.",
        dataset_profile=profile,
    )

    assert result.plan == EnrichmentPlan.model_validate(candidate)
    assert result.validation.valid is True
    assert len(client.calls) == 1

    messages, response_schema = client.calls[0]
    prompt = " ".join(message["content"] for message in messages)
    for expected in ("elevation", "Mountain", "Country", "resolve_entity", "lookup_property"):
        assert expected in prompt
    assert "steps" in response_schema["properties"]


def test_llm_planner_reports_invalid_json() -> None:
    """Report malformed model output as a planning error."""

    class InvalidJsonClient:
        def complete(
            self,
            *,
            messages: list[dict[str, str]],
            response_schema: dict[str, Any],
        ) -> str:
            return '{"steps":'

    planner = LLMPlanner(
        client=InvalidJsonClient(),
        registry=CapabilityRegistry(),
    )

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
    )

    with pytest.raises(PlanGenerationError, match="Invalid JSON response"):
        planner.generate(
            instruction="Enrich the dataset.",
            dataset_profile=profile,
        )


def test_llm_planner_reports_invalid_plan_schema() -> None:
    """Reject valid JSON that does not match the EnrichmentPlan schema."""

    client = FakePlanClient(
        response={
            "version": "1.0",
            "steps": "not a list",
            "outputs": [],
            "assumptions": [],
        }
    )

    planner = LLMPlanner(
        client=client,
        registry=CapabilityRegistry(),
    )

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
    )

    with pytest.raises(PlanGenerationError, match="Invalid plan schema"):
        planner.generate(
            instruction="Enrich the dataset.",
            dataset_profile=profile,
        )


def test_llm_planner_returns_invalid_plan_with_diagnostics() -> None:
    """Preserve structurally valid plans that fail semantic validation."""

    candidate = {
        "version": "1.0",
        "steps": [
            {
                "id": "lookup",
                "capability": "unknown_capability",
                "inputs": {},
                "parameters": {},
            }
        ],
        "outputs": [],
        "assumptions": [],
    }

    planner = LLMPlanner(
        client=FakePlanClient(response=candidate),
        registry=CapabilityRegistry(),
    )

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
    )

    result = planner.generate(
        instruction="Enrich the dataset.",
        dataset_profile=profile,
    )

    assert result.plan == EnrichmentPlan.model_validate(candidate)
    assert result.validation.valid is False

    assert any(error.code == "UNSUPPORTED_CAPABILITY" for error in result.validation.errors)


def test_llm_planner_generates_and_validates_capitals_plan() -> None:
    """Validate a distance plan with two independent geocoding steps."""

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    profile = DatasetProfile(
        dataset_id="capitals",
        name="Capital distances",
        columns=[
            ColumnProfile(name="Capital_From", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Country_From", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Capital_To", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Country_To", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="distance", inferred_type=PrimitiveType.NUMBER, null_count=0),
        ],
    )

    candidate = {
        "version": "1.0",
        "steps": [
            {
                "id": "origin",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "Capital_From"},
                    "country": {"kind": "column", "name": "Country_From"},
                },
                "parameters": {},
            },
            {
                "id": "destination",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "Capital_To"},
                    "country": {"kind": "column", "name": "Country_To"},
                },
                "parameters": {},
            },
            {
                "id": "distance",
                "capability": "geodesic_distance",
                "inputs": {
                    "point_a": {"kind": "step_output", "step_id": "origin"},
                    "point_b": {"kind": "step_output", "step_id": "destination"},
                },
                "parameters": {"unit": "km"},
            },
        ],
        "outputs": [
            {
                "step_id": "distance",
                "value_field": "value",
                "target_column": "distance",
            }
        ],
        "assumptions": ["Straight-line distance means geodesic distance."],
    }

    client = FakePlanClient(response=candidate)
    planner = LLMPlanner(client=client, registry=registry)

    result = planner.generate(
        instruction="Calculate straight-line distances between capitals in kilometers.",
        dataset_profile=profile,
    )

    assert result.validation.valid, result.validation.errors
    assert result.plan == EnrichmentPlan.model_validate(candidate)
    assert len(result.plan.steps) == 3

    messages, _ = client.calls[0]
    prompt = " ".join(message["content"] for message in messages)

    assert "Capital_From" in prompt
    assert "Capital_To" in prompt
    assert "geodesic_distance" in prompt
