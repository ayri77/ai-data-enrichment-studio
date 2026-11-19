from ai_data_enrichment.execution.models import (
    OperationResult,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.execution.step_tally import (
    StepTally,
    classify_step,
    is_settled_not_applicable,
    outcome_kinds,
)

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


def test_a_plan_that_reads_texts_counts_a_text_that_is_not_about_the_task_apart() -> None:
    tally = StepTally(["extract"], extraction_steps=["extract"])
    assert tally.kinds == ("filled", "failed", "skipped", "not_applicable")
    tally.add(row(extract=FILLED))
    tally.add(row(extract=failure("NOT_APPLICABLE")))
    tally.add(row(extract=failure("NOT_APPLICABLE")))
    # Every other failure of the step counts as it always did.
    tally.add(row(extract=failure("NOTHING_FOUND")))
    tally.add(row(extract=failure("PROVIDER_ERROR")))
    assert tally.snapshot() == {
        "extract": {"filled": 1, "failed": 2, "skipped": 0, "not_applicable": 2}
    }


def test_no_other_plan_has_a_fourth_count_or_settles_that_code() -> None:
    # A step that is not an extraction step: the same code is an ordinary failure.
    tally = StepTally(["lookup"])
    assert tally.kinds == ("filled", "failed", "skipped")
    tally.add(row(lookup=failure("NOT_APPLICABLE")))
    assert tally.snapshot() == {"lookup": {"filled": 0, "failed": 1, "skipped": 0}}
    assert outcome_kinds(extraction=False) == ("filled", "failed", "skipped")
    assert not is_settled_not_applicable("web_lookup", failure("NOT_APPLICABLE"))
    assert not is_settled_not_applicable("text_extraction", failure("NOTHING_FOUND"))
    assert not is_settled_not_applicable("text_extraction", FILLED)
    assert is_settled_not_applicable("text_extraction", failure("NOT_APPLICABLE"))


def test_the_shared_progress_of_an_extraction_run_has_a_fourth_count_and_its_request_numbers() -> (
    None
):
    from ai_data_enrichment.plan_run_worker import (
        MirroredExtractionUsage,
        progress_slots,
        read_extraction_usage,
        read_progress,
    )

    # Other plans: three counts per step, as before; an extraction plan: four, and three numbers.
    assert progress_slots(2) == 3 + 3 * 2
    assert progress_slots(2, lookup=True) == 3 + 3 * 2 + 4
    assert progress_slots(1, extraction=True) == 3 + 4 + 3
    shared = [0] * progress_slots(1, extraction=True)
    kinds = outcome_kinds(extraction=True)
    shared[:7] = [3, 5, 4, 1, 0, 0, 2]
    assert read_progress(shared, ["extract"], kinds) == (
        3,
        5,
        {"extract": {"filled": 1, "failed": 0, "skipped": 0, "not_applicable": 2}},
    )
    usage = MirroredExtractionUsage(shared, 7)
    usage.add(input_tokens=700, output_tokens=90)
    usage.add()
    assert read_extraction_usage(shared, 1) == {
        "requests": 2,
        "input_tokens": 700,
        "output_tokens": 90,
    }
