"""Tests for dependency ordering in execution plans."""

from graphlib import CycleError

import pytest

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.dag import order_plan_steps


def make_plan(steps: list[PlanStep]) -> EnrichmentPlan:
    """Build a minimal plan for testing dependency order."""
    return EnrichmentPlan(
        version="1.0",
        steps=steps,
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation",
            ),
        ],
        assumptions=[],
    )


def make_resolve_step() -> PlanStep:
    """Create an entity resolution step without dependencies."""
    return PlanStep(
        id="resolve",
        capability="resolve_entity",
        inputs={
            "name": LiteralValue(kind="literal", value="Everest"),
            "country": LiteralValue(kind="literal", value="Nepal"),
        },
        parameters={"entity_type": "mountain"},
    )


def make_lookup_step(dependency: str = "resolve") -> PlanStep:
    """Create a property lookup depending on another step."""
    return PlanStep(
        id="lookup",
        capability="lookup_property",
        inputs={
            "entity": StepOutputReference(
                kind="step_output",
                step_id=dependency,
            ),
        },
        parameters={"property": "elevation"},
    )


def test_dag_orders_reversed_steps() -> None:
    """Execute dependencies before consumers regardless of JSON order."""
    plan = make_plan(
        [
            make_lookup_step(),
            make_resolve_step(),
        ]
    )

    ordered = order_plan_steps(plan)

    assert [step.id for step in ordered] == ["resolve", "lookup"]


def test_dag_supports_independent_branches() -> None:
    """Independent operations do not need to wait for unrelated inputs."""
    geocode = PlanStep(
        id="geocode",
        capability="geocode",
        inputs={
            "city": LiteralValue(kind="literal", value="Hamburg"),
            "country": LiteralValue(kind="literal", value="Germany"),
        },
        parameters={},
    )

    plan = make_plan(
        [
            make_lookup_step(),
            geocode,
            make_resolve_step(),
        ]
    )

    ordered_ids = [step.id for step in order_plan_steps(plan)]

    assert set(ordered_ids) == {"resolve", "lookup", "geocode"}
    assert ordered_ids.index("resolve") < ordered_ids.index("lookup")


def test_dag_rejects_missing_dependency() -> None:
    """Reject references to steps absent from the plan."""
    plan = make_plan(
        [
            make_lookup_step(dependency="missing"),
        ]
    )

    with pytest.raises(ValueError, match="unknown steps"):
        order_plan_steps(plan)


def test_dag_rejects_cycles() -> None:
    """A cyclic graph has no valid execution order."""
    resolve = make_resolve_step()
    resolve.inputs["name"] = StepOutputReference(
        kind="step_output",
        step_id="lookup",
    )

    plan = make_plan(
        [
            resolve,
            make_lookup_step(),
        ]
    )

    with pytest.raises(CycleError):
        order_plan_steps(plan)


def test_dag_rejects_duplicate_step_ids() -> None:
    """Two step definitions must never share a dependency graph key."""
    plan = make_plan([make_resolve_step(), make_resolve_step()])

    with pytest.raises(ValueError, match="duplicate step IDs"):
        order_plan_steps(plan)
