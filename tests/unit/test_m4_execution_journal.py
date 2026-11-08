"""Tests for durable, claim-once execution journal."""

from pathlib import Path

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
from ai_data_enrichment.orchestration.agent.execution_journal import (
    ExecutionJournal,
)


def make_result() -> JobExecutionResult:
    """Build a minimal successful job result using production models."""
    return JobExecutionResult(
        records=(
            RecordExecution(
                record_id="row-1",
                row_result=RowExecutionResult(
                    steps=(
                        StepExecutionResult(
                            step_id="lookup",
                            result=OperationResult(
                                status="success",
                                value="Berlin",
                            ),
                        ),
                    ),
                ),
            ),
        ),
        metrics=JobExecutionMetrics(
            total_rows=1,
            successful_rows=1,
            logical_operations=1,
            executed_handler_calls=1,
        ),
    )


def test_first_claim_starts_execution(tmp_path: Path) -> None:
    """A new execution identity may be claimed exactly once."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")

    result = journal.claim(
        execution_id="job-001",
        request_fingerprint="fingerprint-a",
    )

    assert result == "claimed"
    assert journal.load_result(execution_id="job-001") is None


def test_duplicate_claim_does_not_authorize_execution(tmp_path: Path) -> None:
    """An already started execution cannot be claimed again."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")

    assert (
        journal.claim(
            execution_id="job-001",
            request_fingerprint="fingerprint-a",
        )
        == "claimed"
    )

    assert (
        journal.claim(
            execution_id="job-001",
            request_fingerprint="fingerprint-a",
        )
        == "started"
    )


def test_claim_rejects_different_request(tmp_path: Path) -> None:
    """An execution ID must remain bound to its original input."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")

    journal.claim(
        execution_id="job-001",
        request_fingerprint="fingerprint-a",
    )

    with pytest.raises(ValueError, match="different request"):
        journal.claim(
            execution_id="job-001",
            request_fingerprint="fingerprint-b",
        )


def test_finished_execution_restores_result(tmp_path: Path) -> None:
    """A completed execution restores its result without running anything."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    expected = make_result()

    journal.claim(
        execution_id="job-001",
        request_fingerprint="fingerprint-a",
    )

    journal.finish(
        execution_id="job-001",
        result=expected,
    )

    assert (
        journal.claim(
            execution_id="job-001",
            request_fingerprint="fingerprint-a",
        )
        == "finished"
    )

    restored = journal.load_result(execution_id="job-001")

    assert restored == expected
    assert restored is not None
    assert restored.metrics.successful_rows == 1


def test_result_survives_reopening_database(tmp_path: Path) -> None:
    """A new journal instance can restore an earlier execution."""
    path = tmp_path / "journal.sqlite"
    original = ExecutionJournal(path)

    original.claim(
        execution_id="job-001",
        request_fingerprint="fingerprint-a",
    )
    original.finish(
        execution_id="job-001",
        result=make_result(),
    )

    reopened = ExecutionJournal(path)

    assert (
        reopened.claim(
            execution_id="job-001",
            request_fingerprint="fingerprint-a",
        )
        == "finished"
    )
    assert reopened.load_result(execution_id="job-001") == make_result()


def test_cannot_finish_unclaimed_or_finished_execution(tmp_path: Path) -> None:
    """Only a started execution can transition to finished."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")

    with pytest.raises(ValueError, match="not in a finishable state"):
        journal.finish(
            execution_id="missing",
            result=make_result(),
        )

    journal.claim(
        execution_id="job-001",
        request_fingerprint="fingerprint-a",
    )
    journal.finish(
        execution_id="job-001",
        result=make_result(),
    )

    with pytest.raises(ValueError, match="not in a finishable state"):
        journal.finish(
            execution_id="job-001",
            result=make_result(),
        )
