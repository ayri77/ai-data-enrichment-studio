"""Bounded LangGraph orchestration over existing planning and execution services."""

from collections.abc import Iterable
from typing import Literal, Protocol

from langgraph.graph import END, START, StateGraph

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.planning.llm_planner import PlanGenerationResult
from ai_data_enrichment.validation.plan_validator import PlanValidator

from .state import AgentState


class PlannerService(Protocol):
    """Existing planner interface required by the graph."""

    def generate(
        self,
        *,
        instruction: str,
        dataset_profile: DatasetProfile,
    ) -> PlanGenerationResult: ...


class ExecutionService(Protocol):
    """Existing JobExecutor interface required by the graph."""

    def execute(
        self,
        plan: EnrichmentPlan,
        records: Iterable[InputRecord],
        *,
        job_id: str | None = None,
        previous_job_id: str | None = None,
    ) -> JobExecutionResult: ...


def build_agent_graph(
    *,
    registry: CapabilityRegistry,
    executor: ExecutionService,
    planner: PlannerService | None = None,
):
    """Compile the first bounded plan/validate/execute workflow."""

    def obtain_plan(state: AgentState) -> AgentState:
        """Use an existing plan or request one from the planner."""

        if "plan" in state:
            return {"status": "validating"}

        profile = state.get("dataset_profile")
        instruction = state.get("instruction")

        if planner is None or profile is None or not instruction:
            return {
                "status": "rejected",
                "error": "A plan or configured planner with input context is required.",
            }

        result = planner.generate(
            instruction=instruction,
            dataset_profile=profile,
        )

        if result.response.plan is None:
            return {
                "status": "rejected",
                "error": result.response.reason or "Planner did not produce a plan.",
            }

        return {
            "plan": result.response.plan,
            "status": "validating",
        }

    def route_after_planning(state: AgentState) -> Literal["validate", "__end__"]:
        """Skip validation when planning did not produce a plan."""
        return "validate" if state.get("status") != "rejected" else "__end__"

    def validate(state: AgentState) -> AgentState:
        """Validate the plan using the existing deterministic validator."""

        plan = state.get("plan")
        profile = state.get("dataset_profile")

        if plan is None or profile is None:
            return {
                "status": "rejected",
                "error": "Plan validation requires a plan and dataset profile.",
            }

        result = PlanValidator(
            registry=registry,
            dataset_profile=profile,
        ).validate(plan)

        if not result.valid:
            return {
                "validation": result,
                "status": "rejected",
                "error": "Enrichment plan validation failed.",
            }

        return {
            "validation": result,
            "status": "executing",
        }

    def route_after_validation(state: AgentState) -> Literal["execute", "__end__"]:
        """Only validated plans can reach the executor."""
        return "execute" if state.get("status") == "executing" else "__end__"

    def execute(state: AgentState) -> AgentState:
        """Delegate all DAG execution to the existing JobExecutor."""

        plan = state.get("plan")
        records = state.get("records")

        if plan is None or records is None:
            return {
                "status": "failed",
                "error": "Execution requires a plan and input records.",
            }

        result = executor.execute(plan, records)

        has_failures = result.metrics.failed_rows > 0
        has_partial = result.metrics.partial_rows > 0

        return {
            "job_result": result,
            "status": "failed" if has_failures or has_partial else "completed",
        }

    # The graph controls workflow transitions, not individual DAG operations.
    builder = StateGraph(AgentState)

    builder.add_node("obtain_plan", obtain_plan)
    builder.add_node("validate", validate)
    builder.add_node("execute", execute)

    builder.add_edge(START, "obtain_plan")
    builder.add_conditional_edges(
        "obtain_plan",
        route_after_planning,
        ["validate", END],
    )
    builder.add_conditional_edges(
        "validate",
        route_after_validation,
        ["execute", END],
    )
    builder.add_edge("execute", END)

    return builder.compile()
