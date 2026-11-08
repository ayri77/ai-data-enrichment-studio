"""Classify overall agent outcomes from existing job execution contracts."""

from ai_data_enrichment.execution.job_models import JobExecutionResult

from .state import AgentStatus


def classify_job_outcome(result: JobExecutionResult) -> AgentStatus:
    """Return the terminal workflow status without losing review requirements."""

    metrics = result.metrics
    review = result.resolution_report

    # Explicit human review takes priority over row-level failures.
    if review is not None:
        if review.status == "awaiting_review" or any(
            operation.report.decision.status == "needs_review" for operation in review.operations
        ):
            return "needs_review"

        if review.status == "failed":
            return "failed"

        if review.status == "cancelled":
            return "partial" if metrics.successful_rows > 0 else "failed"

    # Mixed or partially successful execution.
    if metrics.partial_rows > 0:
        return "partial"

    if metrics.failed_rows > 0:
        return "partial" if metrics.successful_rows > 0 else "failed"

    return "completed"
