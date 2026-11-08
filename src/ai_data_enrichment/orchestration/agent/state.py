"""Typed state shared by M4 agent orchestration nodes."""

from typing import Literal, TypedDict

from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.validation.models import ValidationResult

AgentStatus = Literal[
    "pending",
    "planning",
    "validating",
    "executing",
    "completed",
    "partial",
    "needs_review",
    "rejected",
    "failed",
    "execution_uncertain",
]


class AgentState(TypedDict, total=False):
    """Data exchanged between nodes of the bounded agent workflow."""

    # Input and planning context.
    instruction: str
    dataset_profile: DatasetProfile
    plan: EnrichmentPlan
    # Source-independent input records for the existing JobExecutor.
    records: tuple[InputRecord, ...]

    # Outputs from existing deterministic components.
    validation: ValidationResult
    job_result: JobExecutionResult

    # Workflow control and diagnostics.
    status: AgentStatus
    error: str
    # Number of repair attempts already performed.
    repair_attempts: int

    # Stable identity for journal-protected execution.
    execution_id: str
