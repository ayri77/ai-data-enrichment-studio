"""Contract tests for source-independent multi-row job execution models."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from ai_data_enrichment.execution.job_models import (
    InputRecord,
    JobExecutionMetrics,
    JobExecutionPolicy,
    JobExecutionResult,
    RecordExecution,
    prepare_input_records,
)
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RowExecutionResult,
    RuntimeValue,
    StepExecutionResult,
)


def row_result(*statuses: str) -> RowExecutionResult:
    """Build separate step outcomes, preserving per-step evidence."""
    return RowExecutionResult(
        steps=tuple(
            StepExecutionResult(
                step_id=f"step_{index}",
                result=(
                    OperationResult(
                        status="success",
                        value=Quantity(value=100.0, unit="m"),
                        evidence=(Evidence(source="fixture", reference=f"step/{index}"),),
                    )
                    if status == "success"
                    else OperationResult(
                        status="failure",
                        error_code="NOT_FOUND",
                        message="No matching result.",
                    )
                ),
            )
            for index, status in enumerate(statuses)
        )
    )


def test_input_record_preserves_typed_values_and_original_mapping() -> None:
    source: dict[str, RuntimeValue | None] = {
        "mountain": "Everest",
        "country": "Nepal",
        "height": 8848.86,
        "missing": None,
        "point": GeoPoint(latitude=27.9, longitude=86.9),
        "entity": EntityRef(source="wikidata", entity_id="Q513"),
        "quantity": Quantity(value=8848.86, unit="m"),
    }

    record = InputRecord(record_id="row-001", values=source)

    assert record.record_id == "row-001"
    assert record.values == source
    assert record.values is not source
    assert isinstance(record.values["point"], GeoPoint)
    assert isinstance(record.values["entity"], EntityRef)
    assert isinstance(record.values["quantity"], Quantity)
    assert source["mountain"] == "Everest"


@pytest.mark.parametrize("record_id", ["", " ", "\t"])
def test_input_record_rejects_empty_identity(record_id: str) -> None:
    with pytest.raises(ValidationError):
        InputRecord(record_id=record_id, values={})


@pytest.mark.parametrize("column", ["", " ", "\n"])
def test_input_record_rejects_blank_column_names(column: str) -> None:
    with pytest.raises(ValidationError, match="column names"):
        InputRecord(record_id="row-1", values={column: "abc"})


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True])
def test_input_record_rejects_unsafe_runtime_values(invalid: object) -> None:
    with pytest.raises(ValidationError):
        InputRecord.model_validate({"record_id": "row-1", "values": {"value": invalid}})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_rows", 0),
        ("max_rows", -1),
        ("max_rows", True),
        ("batch_size", 0),
        ("batch_size", 1.5),
        ("max_memoized_results", -1),
        ("max_memoized_results", False),
    ],
)
def test_job_policy_rejects_invalid_limits(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        JobExecutionPolicy.model_validate({field: value})


def test_job_policy_requires_explicit_nonblank_approved_bindings() -> None:
    policy = JobExecutionPolicy(
        max_rows=5,
        batch_size=2,
        max_memoized_results=0,
        memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
    )
    assert policy.max_memoized_results == 0
    assert ("resolve_entity", "wikidata") in policy.memoizable_providers
    assert JobExecutionPolicy().memoizable_providers == frozenset()

    for invalid in [("", "wikidata"), ("resolve_entity", " ")]:
        with pytest.raises(ValidationError, match="provider IDs"):
            JobExecutionPolicy(memoizable_providers=frozenset({invalid}))


def test_prepare_input_records_preserves_order_and_does_not_change_inputs() -> None:
    first = InputRecord(record_id="b", values={"city": "Hamburg"})
    second = InputRecord(record_id="a", values={"city": "Berlin"})
    source = [first, second]

    prepared = prepare_input_records(
        iter(source), policy=JobExecutionPolicy(max_rows=2, batch_size=1)
    )

    assert prepared == (first, second)
    assert source == [first, second]
    assert prepared[0].values == {"city": "Hamburg"}


def test_prepare_input_records_rejects_duplicate_ids() -> None:
    records = [
        InputRecord(record_id="same", values={"city": "Berlin"}),
        InputRecord(record_id="same", values={"city": "Hamburg"}),
    ]

    with pytest.raises(ValueError, match="Duplicate record ID"):
        prepare_input_records(records, JobExecutionPolicy(max_rows=10))


def test_prepare_input_records_stops_at_max_rows_plus_one() -> None:
    consumed: list[int] = []

    def generate_records() -> Iterator[InputRecord]:
        index = 0
        while True:
            consumed.append(index)
            yield InputRecord(record_id=f"row-{index}", values={"value": float(index)})
            index += 1

    with pytest.raises(ValueError, match="max_rows=3"):
        prepare_input_records(generate_records(), JobExecutionPolicy(max_rows=3))

    assert consumed == [0, 1, 2, 3]


def test_prepare_input_records_accepts_empty_job() -> None:
    assert prepare_input_records([], JobExecutionPolicy()) == ()


def test_prepare_input_records_rejects_wrong_record_type() -> None:
    with pytest.raises(TypeError, match="InputRecord"):
        prepare_input_records([{"record_id": "row-1"}], JobExecutionPolicy())  # type: ignore[list-item]


def test_job_metrics_distinguish_requests_execution_and_reuse() -> None:
    metrics = JobExecutionMetrics(
        total_rows=2,
        successful_rows=1,
        failed_rows=1,
        logical_operations=5,
        executed_handler_calls=3,
        reused_operations=2,
        memoized_results=2,
        memo_capacity_skips=1,
    )
    assert metrics.logical_operations == 5
    assert metrics.reused_operations == 2


@pytest.mark.parametrize(
    "values",
    [
        {"total_rows": 2, "successful_rows": 1},
        {"logical_operations": 2, "executed_handler_calls": 1},
        {"logical_operations": 1, "executed_handler_calls": 1, "memoized_results": 2},
        {"logical_operations": 1, "executed_handler_calls": 1, "memo_capacity_skips": 2},
        {"total_rows": -1},
        {"logical_operations": True},
    ],
)
def test_job_metrics_reject_inconsistent_or_invalid_counts(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobExecutionMetrics.model_validate(values)


def test_job_result_preserves_order_evidence_and_partial_failures() -> None:
    rows = (
        RecordExecution(record_id="b", row_result=row_result("success", "failure")),
        RecordExecution(record_id="a", row_result=row_result("success")),
        RecordExecution(record_id="c", row_result=row_result("failure")),
    )
    result = JobExecutionResult(
        records=rows,
        metrics=JobExecutionMetrics(
            total_rows=3,
            successful_rows=1,
            partial_rows=1,
            failed_rows=1,
            logical_operations=4,
            executed_handler_calls=4,
        ),
    )

    assert [r.record_id for r in result.records] == ["b", "a", "c"]
    assert result.records[0].row_result.steps[0].result.evidence == (
        Evidence(source="fixture", reference="step/0"),
    )
    assert result.records[0].row_result.steps[1].result.error_code == "NOT_FOUND"


def test_job_result_rejects_duplicate_record_identity() -> None:
    rows = (
        RecordExecution(record_id="same", row_result=row_result("success")),
        RecordExecution(record_id="same", row_result=row_result("success")),
    )

    with pytest.raises(ValidationError, match="unique"):
        JobExecutionResult(
            records=rows,
            metrics=JobExecutionMetrics(total_rows=2, successful_rows=2),
        )


def test_job_result_rejects_mismatching_row_counts() -> None:
    row = RecordExecution(record_id="r1", row_result=row_result("success"))
    with pytest.raises(ValidationError, match="match record count"):
        JobExecutionResult(records=(row,), metrics=JobExecutionMetrics())

    with pytest.raises(ValidationError, match="outcome metrics"):
        JobExecutionResult(
            records=(row,),
            metrics=JobExecutionMetrics(total_rows=1, failed_rows=1),
        )


def test_empty_job_result_has_zero_metrics() -> None:
    result = JobExecutionResult(records=(), metrics=JobExecutionMetrics())
    assert result.records == ()
    assert result.metrics.total_rows == 0
