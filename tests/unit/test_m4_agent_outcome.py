"""Tests for terminal agent outcome classification."""

from typing import Literal

import pytest

from ai_data_enrichment.execution.job_models import (
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    OperationResult,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.orchestration.agent.outcome import classify_job_outcome
from ai_data_enrichment.orchestration.agent.state import AgentStatus
from ai_data_enrichment.resolution.models import JobReviewStatus, ResolutionJobReport

StepStatus = Literal["success", "failure"]


def make_result(
    rows: tuple[tuple[StepStatus, ...], ...],
    review_status: JobReviewStatus | None = None,
) -> JobExecutionResult:
    """Build a consistent execution result using production contracts."""

    records = tuple(
        RecordExecution(
            record_id=f"row-{index}",
            row_result=RowExecutionResult(
                steps=tuple(
                    StepExecutionResult(
                        step_id=f"step_{step_index}",
                        result=(
                            OperationResult(status="success", value="ok")
                            if status == "success"
                            else OperationResult(
                                status="failure",
                                error_code="NOT_FOUND",
                                message="No result",
                            )
                        ),
                    )
                    for step_index, status in enumerate(steps)
                )
            ),
        )
        for index, steps in enumerate(rows)
    )

    successful = sum(bool(steps) and all(s == "success" for s in steps) for steps in rows)
    partial = sum("success" in steps and "failure" in steps for steps in rows)
    failed = len(rows) - successful - partial
    operations = sum(len(steps) for steps in rows)

    review = (
        ResolutionJobReport(
            job_id="test-job",
            status=review_status,
            total_rows=len(rows),
            processed_rows=len(rows),
        )
        if review_status is not None
        else None
    )

    return JobExecutionResult(
        records=records,
        metrics=JobExecutionMetrics(
            total_rows=len(rows),
            successful_rows=successful,
            partial_rows=partial,
            failed_rows=failed,
            logical_operations=operations,
            executed_handler_calls=operations,
        ),
        resolution_report=review,
    )


@pytest.mark.parametrize(
    ("rows", "review_status", "expected"),
    [
        ((("success",),), None, "completed"),
        ((), None, "completed"),
        ((("failure",),), None, "failed"),
        ((("success",), ("failure",)), None, "partial"),
        ((("success", "failure"),), None, "partial"),
        ((("failure",),), "awaiting_review", "needs_review"),
        ((("success",),), "failed", "failed"),
        ((("success",),), "cancelled", "partial"),
        ((("failure",),), "cancelled", "failed"),
    ],
)
def test_classify_job_outcome(
    rows: tuple[tuple[StepStatus, ...], ...],
    review_status: JobReviewStatus | None,
    expected: AgentStatus,
) -> None:
    """Map consistent execution and review results to terminal agent states."""

    result = make_result(rows, review_status)

    assert classify_job_outcome(result) == expected
