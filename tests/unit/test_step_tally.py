from ai_data_enrichment.execution.models import (
    OperationResult,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.execution.step_tally import StepTally, classify_step

FILLED = OperationResult(status="success", value="x")


def failure(code: str) -> OperationResult:
    return OperationResult(status="failure", error_code=code, message="text")


def row(**results: OperationResult) -> RowExecutionResult:
    return RowExecutionResult(
        steps=tuple(StepExecutionResult(step_id=key, result=r) for key, r in results.items())
    )


def test_a_step_is_filled_failed_or_skipped_by_its_typed_result() -> None:
    assert classify_step(FILLED) == "filled"
    assert classify_step(failure("NOT_FOUND")) == "failed"
    assert classify_step(failure("DEPENDENCY_FAILED")) == "skipped"


def test_nothing_is_counted_before_the_first_row_finishes() -> None:
    assert StepTally(["a", "b"]).snapshot() == {}


def test_the_tally_counts_each_step_over_the_rows_added() -> None:
    tally = StepTally(["a", "b"])
    tally.add(row(a=FILLED, b=FILLED))
    tally.add(row(a=failure("NOT_FOUND"), b=failure("DEPENDENCY_FAILED")))
    tally.add(row(a=failure("MISSING_VALUE"), b=failure("DEPENDENCY_FAILED")))
    assert tally.snapshot() == {
        "a": {"filled": 1, "failed": 2, "skipped": 0},
        "b": {"filled": 1, "failed": 0, "skipped": 2},
    }


def test_a_snapshot_is_a_copy_and_unknown_steps_are_ignored() -> None:
    tally = StepTally(["a"])
    tally.add(row(a=FILLED, other=FILLED))
    seen = tally.snapshot()
    tally.add(row(a=FILLED))
    assert seen == {"a": {"filled": 1, "failed": 0, "skipped": 0}}
    assert tally.snapshot()["a"]["filled"] == 2


def test_shared_progress_is_read_whole_or_not_at_all() -> None:
    from ai_data_enrichment.plan_run_worker import progress_slots, read_progress

    shared = [0] * progress_slots(2)
    assert read_progress(shared, ["a", "b"]) == (0, 0, {})
    shared[:] = [2, 5, 4, 1, 1, 0, 0, 0, 2]
    assert read_progress(shared, ["a", "b"]) == (
        2,
        5,
        {
            "a": {"filled": 1, "failed": 1, "skipped": 0},
            "b": {"filled": 0, "failed": 0, "skipped": 2},
        },
    )
    shared[2] = 5  # The worker is writing.
    assert read_progress(shared, ["a", "b"]) is None
