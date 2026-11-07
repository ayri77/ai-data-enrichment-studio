"""Tests for projecting typed operation results to Excel values."""

import pytest

from ai_data_enrichment.execution.models import (
    EntityRef,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.output.value_projection import (
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
