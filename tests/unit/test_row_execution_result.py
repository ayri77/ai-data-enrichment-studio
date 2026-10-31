"""Tests for per-row execution outcomes."""

import pytest
from pydantic import ValidationError

from ai_data_enrichment.execution.models import (
    OperationResult,
    Quantity,
    RowExecutionResult,
    StepExecutionResult,
)


def test_row_execution_result_preserves_step_order() -> None:
    """Keep step outcomes in their actual execution order."""

    first = StepExecutionResult(
        step_id="resolve_entity",
        result=OperationResult(
            status="success",
            value="Q513",
        ),
    )
    second = StepExecutionResult(
        step_id="lookup_property",
        result=OperationResult(
            status="success",
            value=Quantity(value=8848.86, unit="m"),
        ),
    )

    row = RowExecutionResult(steps=(first, second))

    assert [step.step_id for step in row.steps] == [
        "resolve_entity",
        "lookup_property",
    ]
    assert row.steps[1].result.value == Quantity(
        value=8848.86,
        unit="m",
    )


def test_row_execution_result_preserves_failures() -> None:
    """Retain failures without replacing them with missing values."""

    row = RowExecutionResult(
        steps=(
            StepExecutionResult(
                step_id="resolve_entity",
                result=OperationResult(
                    status="failure",
                    error_code="NOT_FOUND",
                    message="Entity could not be resolved",
                ),
            ),
        ),
    )

    assert row.steps[0].result.status == "failure"
    assert row.steps[0].result.error_code == "NOT_FOUND"


def test_row_execution_result_rejects_duplicate_steps() -> None:
    """A DAG step must have at most one outcome for each row."""

    outcome = StepExecutionResult(
        step_id="geocode",
        result=OperationResult(status="success", value="found"),
    )

    with pytest.raises(ValidationError):
        RowExecutionResult(steps=(outcome, outcome))


def test_row_execution_result_json_roundtrip() -> None:
    """Preserve typed values when serializing a completed row."""

    original = RowExecutionResult(
        steps=(
            StepExecutionResult(
                step_id="lookup_property",
                result=OperationResult(
                    status="success",
                    value=Quantity(value=8848.86, unit="m"),
                ),
            ),
        ),
    )

    restored = RowExecutionResult.model_validate_json(original.model_dump_json())

    assert restored == original
    assert isinstance(restored.steps[0].result.value, Quantity)


def test_row_execution_result_defaults_to_empty_steps() -> None:
    """Represent a row before any operations have been executed."""

    row = RowExecutionResult()

    assert row.steps == ()
