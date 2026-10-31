"""Dependency ordering for enrichment plan execution."""

from graphlib import TopologicalSorter

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    PlanStep,
    StepOutputReference,
)


def order_plan_steps(plan: EnrichmentPlan) -> tuple[PlanStep, ...]:
    """Order DAG steps so dependencies precede their consumers."""

    steps_by_id = {step.id: step for step in plan.steps}

    # Step IDs must uniquely identify nodes in the dependency graph.
    if len(steps_by_id) != len(plan.steps):
        raise ValueError("Plan contains duplicate step IDs")

    dependencies: dict[str, set[str]] = {}

    for step in plan.steps:
        # Only references to other steps create DAG dependencies.
        dependencies[step.id] = {
            reference.step_id
            for reference in step.inputs.values()
            if isinstance(reference, StepOutputReference)
        }

        missing = dependencies[step.id] - steps_by_id.keys()

        if missing:
            raise ValueError(
                f"Step '{step.id}' references unknown steps: {', '.join(sorted(missing))}"
            )

    # A cycle raises graphlib.CycleError instead of returning an invalid order.
    ordered_ids = TopologicalSorter(dependencies).static_order()

    return tuple(steps_by_id[step_id] for step_id in ordered_ids)
