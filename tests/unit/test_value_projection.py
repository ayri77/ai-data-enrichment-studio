"""Tests for projecting typed operation results to Excel values."""

import pytest

from ai_data_enrichment.execution.models import (
    EntityRef,
    FoundRecord,
    FoundValue,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.output.value_projection import (
    field_is_empty,
    project_operation_field,
)


@pytest.mark.parametrize(
    ("value", "field", "expected"),
    [
        (42.5, "value", 42.5),
        ("Berlin", "value", "Berlin"),
        (Quantity(value=8848.86, unit="m"), "value", 8848.86),
        (Quantity(value=8848.86, unit="m"), "unit", "m"),
        (
            GeoPoint(latitude=52.52, longitude=13.405),
            "latitude",
            52.52,
        ),
        (
            EntityRef(source="wikidata", entity_id="Q64"),
            "entity_id",
            "Q64",
        ),
    ],
)
def test_projects_supported_fields(
    value: RuntimeValue,
    field: str,
    expected: str | float,
) -> None:
    result = OperationResult(
        status="success",
        value=value,
    )

    assert project_operation_field(result, field) == expected


def test_rejects_unsuccessful_result() -> None:
    result = OperationResult(
        status="failure",
        error_code="NOT_FOUND",
        message="Entity not found",
    )

    with pytest.raises(
        ValueError,
        match="Cannot project an unsuccessful",
    ):
        project_operation_field(result, "value")


def test_rejects_unknown_field() -> None:
    result = OperationResult(
        status="success",
        value=Quantity(value=8848.86, unit="m"),
    )

    with pytest.raises(
        ValueError,
        match="Unsupported output field: latitude",
    ):
        project_operation_field(result, "latitude")


def found_result() -> OperationResult:
    return OperationResult(
        status="success",
        value=FoundRecord(
            slots={
                "value_1": FoundValue(value="Hamburg", basis="stated"),
                "value_2": FoundValue(value=1.9, basis="concluded"),
                "value_3": FoundValue(value=None, basis=None),
            }
        ),
    )


def test_projects_a_slot_of_a_found_record() -> None:
    result = found_result()
    assert project_operation_field(result, "value_1") == "Hamburg"
    assert project_operation_field(result, "value_2") == 1.9
    assert field_is_empty(result, "value_3") is True
    assert field_is_empty(result, "value_1") is False
    with pytest.raises(ValueError, match="No value found"):
        project_operation_field(result, "value_3")


def test_an_unknown_slot_is_an_unsupported_field_and_not_an_empty_one() -> None:
    result = found_result()
    assert field_is_empty(result, "value_9") is False
    assert field_is_empty(result, "slots") is False
    with pytest.raises(ValueError, match="Unsupported output field"):
        project_operation_field(result, "value_9")
    with pytest.raises(ValueError, match="Unsupported output field"):
        project_operation_field(result, "slots")
