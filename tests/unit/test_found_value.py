"""Tests for the value a model found on the web and for its place in RuntimeValue."""

import math

import pytest
from pydantic import ValidationError

from ai_data_enrichment.execution.job_models import (
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    FoundRecord,
    FoundValue,
    GeoPoint,
    OperationResult,
    Quantity,
    RowExecutionResult,
    RuntimeValue,
    StepExecutionResult,
)


def stated(value: str | float = "Hamburg") -> FoundValue:
    return FoundValue(
        value=value,
        basis="stated",
        sources=("https://example.org/a", "https://example.org/b"),
        quote="Hamburg is the seat of the senate.",
    )


def not_found() -> FoundValue:
    return FoundValue(value=None, basis=None)


def test_a_found_value_keeps_text_numbers_links_quote_and_unit() -> None:
    text = stated()
    number = FoundValue(value=12.5, basis="concluded", unit="m")
    assert text.value == "Hamburg" and text.basis == "stated"
    assert text.sources == ("https://example.org/a", "https://example.org/b")
    assert text.quote == "Hamburg is the seat of the senate."
    assert number.value == 12.5 and number.unit == "m" and number.sources == ()


def test_a_value_that_was_not_found_has_no_basis() -> None:
    value = not_found()
    assert value.value is None and value.basis is None
    with pytest.raises(ValidationError):
        FoundValue(value=None, basis="stated")
    # Links searched may still be reported for "not found".
    assert FoundValue(value=None, basis=None, sources=("https://example.org",)).value is None


@pytest.mark.parametrize("value", ["yes", 0.0, 3.5])
def test_a_found_value_requires_a_basis(value: str | float) -> None:
    with pytest.raises(ValidationError):
        FoundValue(value=value, basis=None)


@pytest.mark.parametrize(
    "bad",
    [
        {"value": "", "basis": "stated"},
        {"value": "   ", "basis": "stated"},
        {"value": math.nan, "basis": "stated"},
        {"value": math.inf, "basis": "stated"},
        {"value": -math.inf, "basis": "concluded"},
        {"value": True, "basis": "stated"},
        {"value": "x", "basis": "guessed"},
        {"value": "x", "basis": "stated", "sources": ("",)},
        {"value": "x", "basis": "stated", "sources": (" ",)},
        {"value": "x", "basis": "stated", "quote": ""},
        {"value": "x", "basis": "stated", "quote": "  "},
        {"value": "x", "basis": "stated", "unit": " "},
        {"value": "x", "basis": "stated", "extra": 1},
    ],
)
def test_a_found_value_rejects_invalid_content(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FoundValue.model_validate(bad)


def test_a_found_record_needs_a_slot_and_named_slots() -> None:
    with pytest.raises(ValidationError):
        FoundRecord(slots={})
    with pytest.raises(ValidationError):
        FoundRecord.model_validate({"slots": {"not an identifier": stated()}})
    with pytest.raises(ValidationError):
        FoundRecord.model_validate({"slots": {"value_1": stated()}, "extra": 1})


def test_a_record_of_only_missing_values_is_a_valid_success() -> None:
    record = FoundRecord(slots={"value_1": not_found(), "value_2": not_found()})
    result = OperationResult(status="success", value=record)
    assert result.status == "success" and result.value == record


def mixed_values() -> list[RuntimeValue]:
    return [
        GeoPoint(latitude=53.55, longitude=10.0, population=1_800_000, timezone="Europe/Berlin"),
        GeoPoint(latitude=1.0, longitude=2.0),
        EntityRef(source="wikidata", entity_id="Q1055"),
        Quantity(value=8848.86, unit="m"),
        "Hamburg",
        42.5,
        0.0,
        FoundRecord(slots={"value_1": stated(), "value_2": not_found()}),
        FoundRecord(slots={"value_1": FoundValue(value=7.0, basis="concluded", unit="km")}),
    ]


@pytest.mark.parametrize("value", mixed_values())
def test_an_operation_result_round_trips_through_json(value: RuntimeValue) -> None:
    result = OperationResult(
        status="success",
        value=value,
        evidence=(Evidence(source="s", reference="r"),),
    )
    restored = OperationResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert type(restored.value) is type(value)


def test_a_job_result_round_trips_every_value_type() -> None:
    steps = tuple(
        StepExecutionResult(
            step_id=f"step_{index}",
            result=OperationResult(status="success", value=value),
        )
        for index, value in enumerate(mixed_values())
    )
    job = JobExecutionResult(
        records=(RecordExecution(record_id="r1", row_result=RowExecutionResult(steps=steps)),),
        metrics=JobExecutionMetrics(
            total_rows=1,
            successful_rows=1,
            logical_operations=1,
            executed_handler_calls=1,
        ),
    )
    restored = JobExecutionResult.model_validate_json(job.model_dump_json())
    assert restored == job
    values = [step.result.value for step in restored.records[0].row_result.steps]
    assert [type(value) for value in values] == [type(value) for value in mixed_values()]


def test_a_found_record_is_not_taken_for_another_type_nor_the_reverse() -> None:
    found = OperationResult.model_validate(
        {"status": "success", "value": {"slots": {"value_1": {"value": "a", "basis": "stated"}}}}
    )
    assert isinstance(found.value, FoundRecord)
    point = OperationResult.model_validate(
        {"status": "success", "value": {"latitude": 1.0, "longitude": 2.0}}
    )
    assert isinstance(point.value, GeoPoint)
    with pytest.raises(ValidationError):
        OperationResult.model_validate({"status": "success", "value": {"slots": {}}})
