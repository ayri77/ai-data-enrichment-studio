"""Opt-in integration tests for live LLM plan generation."""

import os

import pytest
from openai import OpenAI
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    MOUNTAINS_INSTRUCTION,
    workbook_profile,
)

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    StepOutputReference,
)
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationResult
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


def _report_attempts(result: PlanGenerationResult) -> None:
    """Record nonsecret configuration and stable diagnostics, without raw completions."""
    print(f"\nModel: {os.getenv('OPENAI_MODEL', 'gpt-6-luna')}")
    print(f"Planning status: {result.response.status}")
    print(f"Validation valid: {result.validation.valid if result.validation else None}")
    for attempt in result.attempts:
        print(f"Attempt {attempt.number}: {attempt.outcome}, codes={attempt.diagnostic_codes}")


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_mountain_plan() -> None:
    """Generate and validate a mountain elevation plan using a real LLM."""

    profile = workbook_profile("mountains")

    planner = _build_live_planner()

    result = planner.generate(
        instruction=MOUNTAINS_INSTRUCTION,
        dataset_profile=profile,
    )

    _report_attempts(result)

    assert result.response.status == "ready", result.response.reason
    assert result.response.plan is not None

    assert result.validation is not None

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
@pytest.mark.parametrize(
    "instruction",
    [
        CAPITALS_INSTRUCTION,
        CAPITALS_INSTRUCTION + " Compose two geocode steps, one per city/country pair, "
        "feeding their step_output GeoPoints into geodesic_distance with parameters.unit = km.",
    ],
    ids=["free-text", "explicit-composition"],
)
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Live LLM tests require explicit opt-in",
)
def test_live_planner_generates_capitals_plan(instruction: str) -> None:
    """Generate and validate a geodesic distance plan using a real LLM."""

    profile = workbook_profile("capitals")

    planner = _build_live_planner()

    result = planner.generate(
        instruction=instruction,
        dataset_profile=profile,
    )

    _report_attempts(result)

    assert result.response.status == "ready", result.response.reason
    assert result.response.plan is not None

    plan = result.response.plan

    assert result.validation is not None

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
    assert all("required_role" not in step.parameters for step in geocode_steps)

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
