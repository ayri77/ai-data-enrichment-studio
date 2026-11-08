"""Bounded LangGraph orchestration over existing planning and execution services."""

from collections.abc import Iterable
from typing import Literal, Protocol

from langgraph.graph import END, START, StateGraph

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.planning.llm_planner import PlanGenerationResult
from ai_data_enrichment.validation.models import ValidationResult
from ai_data_enrichment.validation.plan_validator import PlanValidator

from .state import AgentState


class PlannerService(Protocol):
    """Generate a candidate plan using an existing planner."""

    def generate(
        self,
        *,
        instruction: str,
        dataset_profile: DatasetProfile,
    ) -> PlanGenerationResult: ...


class PlanRepairService(Protocol):
    """Propose a repaired plan based on deterministic validation diagnostics."""

    def repair(
        self,
        *,
        plan: EnrichmentPlan,
        validation: ValidationResult,
        dataset_profile: DatasetProfile,
        instruction: str | None,
    ) -> EnrichmentPlan: ...


class ExecutionService(Protocol):
    """Execute an already validated plan through the existing JobExecutor."""

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
    repairer: PlanRepairService | None = None,
    max_repair_attempts: int = 2,
):
    """Compile a graph with bounded plan repair and mandatory revalidation."""

    if type(max_repair_attempts) is not int or max_repair_attempts < 0:
        raise ValueError("max_repair_attempts must be a non-negative integer.")

    def obtain_plan(state: AgentState) -> AgentState:
        """Use a supplied plan or obtain a candidate from the planner."""

        if "plan" in state:
            return {
                "status": "validating",
                "repair_attempts": 0,
            }

        profile = state.get("dataset_profile")
        instruction = state.get("instruction")

        if planner is None or profile is None or not instruction:
            return {
                "status": "rejected",
                "error": "A plan or configured planner with input context is required.",
                "repair_attempts": 0,
            }

        result = planner.generate(
            instruction=instruction,
            dataset_profile=profile,
        )

        if result.response.plan is None:
            return {
                "status": "rejected",
                "error": result.response.reason or "Planner did not produce a plan.",
                "repair_attempts": 0,
            }

        return {
            "plan": result.response.plan,
            "status": "validating",
            "repair_attempts": 0,
        }

    def route_after_planning(state: AgentState) -> Literal["validate", "__end__"]:
        """Only candidate plans proceed to deterministic validation."""
        return "validate" if state.get("status") != "rejected" else "__end__"

    def validate(state: AgentState) -> AgentState:
        """Validate each candidate independently, including repaired plans."""

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
            "error": "",
        }

    def route_after_validation(
        state: AgentState,
    ) -> Literal["execute", "repair_plan", "__end__"]:
        """Execute valid plans or repair invalid plans within the attempt budget."""

        if state.get("status") == "executing":
            return "execute"

        if (
            repairer is not None
            and state.get("plan") is not None
            and state.get("validation") is not None
            and state.get("dataset_profile") is not None
            and state.get("repair_attempts", 0) < max_repair_attempts
        ):
            return "repair_plan"

        return "__end__"

    def repair_plan(state: AgentState) -> AgentState:
        """Request one repair; never execute the proposed plan directly."""

        plan = state.get("plan")
        validation = state.get("validation")
        profile = state.get("dataset_profile")
        attempts = state.get("repair_attempts", 0)

        if (
            repairer is None
            or plan is None
            or validation is None
            or profile is None
            or attempts >= max_repair_attempts
        ):
            return {
                "status": "rejected",
                "error": "Plan repair is unavailable.",
            }

        # Count an attempted repair even when its response is malformed.
        next_attempt = attempts + 1

        try:
            candidate = repairer.repair(
                plan=plan,
                validation=validation,
                dataset_profile=profile,
                instruction=state.get("instruction"),
            )
        except ValueError:
            return {
                "repair_attempts": next_attempt,
                "status": "rejected",
                "error": "Plan repair returned an invalid proposal.",
            }

        if not isinstance(candidate, EnrichmentPlan):
            return {
                "repair_attempts": next_attempt,
                "status": "rejected",
                "error": "Plan repair returned an invalid proposal.",
            }

        return {
            "plan": candidate,
            "repair_attempts": next_attempt,
            "status": "validating",
        }

    def route_after_repair(state: AgentState) -> Literal["validate", "__end__"]:
        """Only successfully constructed proposals receive another validation."""
        return "validate" if state.get("status") == "validating" else "__end__"

    def execute(state: AgentState) -> AgentState:
        """Delegate deterministic execution to the existing JobExecutor."""

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

    builder = StateGraph(AgentState)

    builder.add_node("obtain_plan", obtain_plan)
    builder.add_node("validate", validate)
    builder.add_node("repair_plan", repair_plan)
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
        ["execute", "repair_plan", END],
    )

    builder.add_conditional_edges(
        "repair_plan",
        route_after_repair,
        ["validate", END],
    )

    builder.add_edge("execute", END)

    return builder.compile()
