"""Opt-in integration tests for live LLM plan generation."""

import os

import pytest
from openai import OpenAI

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    StepOutputReference,
)
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


def _build_live_planner() -> LLMPlanner:
    """Create a live planner with built-in capabilities and configured LLM."""

    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    client = OpenAIPlanClient(
        api=OpenAI(timeout=30.0, max_retries=0),
        model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
        max_output_tokens=4096,
    )

    return LLMPlanner(
        client=client,
        registry=registry,
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_mountain_plan() -> None:
    """Generate and validate a mountain elevation plan using a real LLM."""

    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain dataset",
        columns=[
            ColumnProfile(
                name="Mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
            ColumnProfile(
                name="Country",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
            ColumnProfile(
                name="height",
                inferred_type=PrimitiveType.NUMBER,
                null_count=0,
            ),
        ],
    )

    planner = _build_live_planner()

    result = planner.generate(
        instruction="Find the elevation of each mountain.",
        dataset_profile=profile,
    )

    print("\nPlanning response:")
    print(result.response.model_dump_json(indent=2))

    assert result.response.status == "ready", result.response.reason
    assert result.response.plan is not None

    print("\nValidation result:")
    assert result.validation is not None
    print(result.validation.model_dump_json(indent=2))

    assert result.validation.valid, result.validation.errors

    plan = result.response.plan

    capabilities = [step.capability for step in plan.steps]

    assert capabilities.count("resolve_entity") == 1
    assert capabilities.count("lookup_property") == 1

    resolve_step = next(step for step in plan.steps if step.capability == "resolve_entity")

    lookup_step = next(step for step in plan.steps if step.capability == "lookup_property")

    assert resolve_step.inputs["name"] == ColumnReference(
        kind="column",
        name="Mountain",
    )

    assert resolve_step.inputs["country"] == ColumnReference(
        kind="column",
        name="Country",
    )

    assert resolve_step.parameters["entity_type"] == "mountain"

    assert lookup_step.inputs["entity"] == StepOutputReference(
        kind="step_output",
        step_id=resolve_step.id,
    )

    assert lookup_step.parameters["property"] == "elevation"

    assert any(
        output.step_id == lookup_step.id
        and output.value_field == "value"
        and output.target_column == "height"
        for output in plan.outputs
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_capitals_plan() -> None:
    """Generate and validate a geodesic distance plan using a real LLM."""

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

    planner = _build_live_planner()

    result = planner.generate(
        instruction="Calculate straight-line distances between capitals in kilometers.",
        dataset_profile=profile,
    )

    print("\nPlanning response:")
    print(result.response.model_dump_json(indent=2))

    assert result.response.status == "ready", result.response.reason
    assert result.response.plan is not None

    plan = result.response.plan

    print("\nValidation result:")
    assert result.validation is not None
    print(result.validation.model_dump_json(indent=2))

    assert result.validation.valid, result.validation.errors

    capabilities = [step.capability for step in plan.steps]
    assert capabilities.count("geocode") == 2
    assert capabilities.count("geodesic_distance") == 1

    distance_step = next(step for step in plan.steps if step.capability == "geodesic_distance")

    assert distance_step.parameters["unit"] == "km"

    assert any(
        output.step_id == distance_step.id
        and output.value_field == "value"
        and output.target_column == "distance"
        for output in plan.outputs
    )

    geocode_steps = [step for step in plan.steps if step.capability == "geocode"]

    from_step = next(
        step
        for step in geocode_steps
        if step.inputs.get("city")
        == ColumnReference(
            kind="column",
            name="Capital_From",
        )
    )

    to_step = next(
        step
        for step in geocode_steps
        if step.inputs.get("city")
        == ColumnReference(
            kind="column",
            name="Capital_To",
        )
    )

    assert from_step.id != to_step.id

    assert from_step.inputs["country"] == ColumnReference(
        kind="column",
        name="Country_From",
    )

    assert to_step.inputs["country"] == ColumnReference(
        kind="column",
        name="Country_To",
    )

    point_a = distance_step.inputs["point_a"]
    point_b = distance_step.inputs["point_b"]

    assert isinstance(point_a, StepOutputReference)
    assert isinstance(point_b, StepOutputReference)

    assert {point_a.step_id, point_b.step_id} == {
        from_step.id,
        to_step.id,
    }
