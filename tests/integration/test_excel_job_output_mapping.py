"""Integration tests for the excel_output_mapping module."""

import pytest

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.job_models import (
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    OperationResult,
    Quantity,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    build_output_value_writes,
)


def test_maps_job_results_to_original_excel_rows() -> None:
    plan = EnrichmentPlan(
        version="1",
        steps=[
            PlanStep(
                id="lookup",
                capability="elevation_lookup",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation_m",
            ),
        ],
        assumptions=[],
    )

    def record(
        record_id: str,
        result: OperationResult,
    ) -> RecordExecution:
        return RecordExecution(
            record_id=record_id,
            row_result=RowExecutionResult(
                steps=(
                    StepExecutionResult(
                        step_id="lookup",
                        result=result,
                    ),
                ),
            ),
        )

    job_result = JobExecutionResult(
        records=(
            record(
                "record-a",
                OperationResult(
                    status="success",
                    value=Quantity(value=8848.86, unit="m"),
                ),
            ),
            record(
                "record-b",
                OperationResult(
                    status="failure",
                    error_code="NOT_FOUND",
                    message="Elevation unavailable",
                ),
            ),
            record(
                "record-c",
                OperationResult(
                    status="success",
                    value=Quantity(value=8611.0, unit="m"),
                ),
            ),
        ),
        metrics=JobExecutionMetrics(
            total_rows=3,
            successful_rows=2,
            failed_rows=1,
            logical_operations=3,
            executed_handler_calls=3,
        ),
    )

    writes = build_output_value_writes(
        plan,
        job_result,
        TableSelection("Mountains", "B6:D11"),
        row_by_record_id={
            "record-a": 7,
            "record-b": 9,
            "record-c": 11,
        },
        output_columns={"elevation_m": "E"},
    )

    assert writes == {
        "E7": 8848.86,
        "E11": 8611.0,
    }


@pytest.mark.parametrize(
    ("row_mapping", "expected_error"),
    [
        (
            {"record-a": 7},
            "Row mapping must match job record IDs",
        ),
        (
            {"record-a": 7, "record-b": 7},
            "Multiple records map to the same Excel row",
        ),
        (
            {"record-a": 7, "record-b": 12},
            "Invalid Excel row for record record-b",
        ),
        (
            {"record-a": 6, "record-b": 9},
            "Invalid Excel row for record record-a",
        ),
    ],
)
def test_rejects_invalid_row_mapping(
    row_mapping: dict[str, int],
    expected_error: str,
) -> None:
    # The plan has no outputs because this test isolates
    # validation of the record-to-row mapping.
    plan = EnrichmentPlan(
        version="1",
        steps=[],
        outputs=[],
        assumptions=[],
    )

    job_result = JobExecutionResult(
        records=(
            RecordExecution(
                record_id="record-a",
                row_result=RowExecutionResult(),
            ),
            RecordExecution(
                record_id="record-b",
                row_result=RowExecutionResult(),
            ),
        ),
        metrics=JobExecutionMetrics(
            total_rows=2,
            failed_rows=2,
        ),
    )

    with pytest.raises(ValueError, match=expected_error):
        build_output_value_writes(
            plan,
            job_result,
            TableSelection("Mountains", "B6:D11"),
            row_by_record_id=row_mapping,
            output_columns={},
        )


@pytest.mark.parametrize(
    ("expected_unit", "actual_unit", "expected_message"),
    [
        ("m", "m", None),
        ("km", "m", "Output unit mismatch"),
    ],
)
def test_explicit_quantity_unit_contract(
    expected_unit: str,
    actual_unit: str,
    expected_message: str | None,
) -> None:
    """A numeric Excel output must not silently discard a declared unit."""
    plan = EnrichmentPlan(
        version="1",
        steps=[PlanStep(id="lookup", capability="elevation_lookup", inputs={}, parameters={})],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation",
                expected_unit=expected_unit,
            )
        ],
        assumptions=[],
    )
    job = JobExecutionResult(
        records=(
            RecordExecution(
                record_id="Mountains!2",
                row_result=RowExecutionResult(
                    steps=(
                        StepExecutionResult(
                            step_id="lookup",
                            result=OperationResult(
                                status="success",
                                value=Quantity(value=8848.86, unit=actual_unit),
                            ),
                        ),
                    )
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
    def map_output() -> dict[str, str | float]:
        return build_output_value_writes(
            plan,
            job,
            TableSelection("Mountains", "A1:A2"),
            row_by_record_id={"Mountains!2": 2},
            output_columns={"elevation": "B"},
        )

    if expected_message is not None:
        with pytest.raises(ValueError, match=expected_message):
            map_output()
    else:
        assert map_output() == {"B2": 8848.86}


def test_expected_unit_rejects_nonquantity_projection() -> None:
    """The unit contract must not be satisfied by arbitrary numeric values."""
    plan = EnrichmentPlan(
        version="1",
        steps=[PlanStep(id="lookup", capability="numeric", inputs={}, parameters={})],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="distance",
                expected_unit="km",
            )
        ],
        assumptions=[],
    )
    job = JobExecutionResult(
        records=(
            RecordExecution(
                record_id="Cities!2",
                row_result=RowExecutionResult(
                    steps=(
                        StepExecutionResult(
                            step_id="lookup",
                            result=OperationResult(status="success", value=10.0),
                        ),
                    )
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
    with pytest.raises(ValueError, match="Expected unit requires a Quantity"):
        build_output_value_writes(
            plan,
            job,
            TableSelection("Cities", "A1:A2"),
            row_by_record_id={"Cities!2": 2},
            output_columns={"distance": "B"},
        )
