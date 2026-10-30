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
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_mountain_plan() -> None:
    """Generate and validate a mountain elevation plan using a real LLM."""

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

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

    api = OpenAI(timeout=30.0, max_retries=0)

    client = OpenAIPlanClient(
        api=api,
        model="gpt-6-luna",
        max_output_tokens=4096,
    )

    planner = LLMPlanner(client=client, registry=registry)

    result = planner.generate(
        instruction="Find the elevation of each mountain in meters.",
        dataset_profile=profile,
    )

    print("\nGenerated plan:")
    print(result.plan.model_dump_json(indent=2))

    print("\nValidation result:")
    print(result.validation.model_dump_json(indent=2))

    assert result.validation.valid, result.validation.errors


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_capitals_plan() -> None:
    """Generate and validate a geodesic distance plan using a real LLM."""

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

    client = OpenAIPlanClient(
        api=OpenAI(timeout=30.0, max_retries=0),
        model="gpt-6-luna",
        max_output_tokens=4096,
    )

    planner = LLMPlanner(client=client, registry=registry)

    result = planner.generate(
        instruction="Calculate straight-line distances between capitals in kilometers.",
        dataset_profile=profile,
    )

    print("\nGenerated plan:")
    print(result.plan.model_dump_json(indent=2))

    print("\nValidation result:")
    print(result.validation.model_dump_json(indent=2))

    assert result.validation.valid, result.validation.errors

    capabilities = [step.capability for step in result.plan.steps]
    assert capabilities.count("geocode") == 2
    assert capabilities.count("geodesic_distance") == 1

    distance_step = next(
        step for step in result.plan.steps if step.capability == "geodesic_distance"
    )
    assert distance_step.parameters["unit"] == "km"
    assert any(
        output.step_id == distance_step.id
        and output.value_field == "value"
        and output.target_column == "distance"
        for output in result.plan.outputs
    )
