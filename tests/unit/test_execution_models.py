"""Tests for typed runtime values used by the execution engine."""

import pytest
from pydantic import BaseModel, ValidationError

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
)


def test_geo_point_accepts_valid_coordinates() -> None:
    """Accept geographic coordinates within their valid ranges."""
    point = GeoPoint(latitude=53.55, longitude=10.0)

    assert point.latitude == 53.55
    assert point.longitude == 10.0


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (91.0, 0.0),
        (-91.0, 0.0),
        (0.0, 181.0),
        (0.0, -181.0),
    ],
)
def test_geo_point_rejects_out_of_range_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """Reject coordinates outside geographic bounds."""
    with pytest.raises(ValidationError):
        GeoPoint(latitude=latitude, longitude=longitude)


def test_entity_ref_accepts_valid_identity() -> None:
    """Represent an entity by its source and source-specific identifier."""

    entity = EntityRef(
        source="wikidata",
        entity_id="Q513",
    )

    assert entity.source == "wikidata"
    assert entity.entity_id == "Q513"


@pytest.mark.parametrize(
    ("source", "entity_id"),
    [
        ("", "Q513"),
        ("   ", "Q513"),
        ("wikidata", ""),
        ("wikidata", "   "),
    ],
)
def test_entity_ref_rejects_empty_identity(
    source: str,
    entity_id: str,
) -> None:
    """Reject missing or whitespace-only entity identifiers."""

    with pytest.raises(ValidationError):
        EntityRef(
            source=source,
            entity_id=entity_id,
        )


def test_entity_ref_json_roundtrip() -> None:
    """Preserve entity identity through JSON serialization."""

    original = EntityRef(
        source="wikidata",
        entity_id="Q513",
    )

    restored = EntityRef.model_validate_json(original.model_dump_json())

    assert restored == original


def test_quantity_accepts_numeric_value_with_unit() -> None:
    """Store a finite numeric value together with its unit."""

    quantity = Quantity(value=8848.86, unit="m")

    assert quantity.value == 8848.86
    assert quantity.unit == "m"


def test_quantity_accepts_negative_values() -> None:
    """Allow negative values for quantities such as elevation."""

    quantity = Quantity(value=-430.0, unit="m")

    assert quantity.value == -430.0


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_quantity_rejects_non_finite_values(value: float) -> None:
    """Reject values that cannot represent finite measurements."""

    with pytest.raises(ValidationError):
        Quantity(value=value, unit="km")


@pytest.mark.parametrize("unit", ["", "   "])
def test_quantity_rejects_empty_unit(unit: str) -> None:
    """Require an explicit, nonempty measurement unit."""

    with pytest.raises(ValidationError):
        Quantity(value=100.0, unit=unit)


def test_quantity_json_roundtrip() -> None:
    """Preserve the value and unit through JSON serialization."""

    original = Quantity(value=742.5, unit="km")

    restored = Quantity.model_validate_json(original.model_dump_json())

    assert restored == original


def test_operation_result_success() -> None:
    """A successful operation contains a typed value."""

    point = GeoPoint(latitude=53.55, longitude=10.0)

    result = OperationResult(
        status="success",
        value=point,
    )

    assert result.status == "success"
    assert result.value == point
    assert result.error_code is None
    assert result.message is None


def test_operation_result_failure() -> None:
    """A failed operation contains diagnostics instead of a value."""

    result = OperationResult(
        status="failure",
        error_code="NOT_FOUND",
        message="Mountain entity could not be resolved",
    )

    assert result.status == "failure"
    assert result.value is None
    assert result.error_code == "NOT_FOUND"
    assert result.message == "Mountain entity could not be resolved"


def test_operation_result_json_roundtrip() -> None:
    """Preserve a nested runtime value during serialization."""

    original = OperationResult(
        status="success",
        value=Quantity(value=8848.86, unit="m"),
    )

    restored = OperationResult.model_validate_json(original.model_dump_json())

    assert restored == original
    assert isinstance(restored.value, Quantity)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "success"},
        {
            "status": "success",
            "value": 42.0,
            "message": "Unexpected error details",
        },
        {
            "status": "failure",
            "value": 42.0,
            "error_code": "NOT_FOUND",
            "message": "No result",
        },
        {
            "status": "failure",
            "message": "No result",
        },
        {
            "status": "failure",
            "error_code": "NOT_FOUND",
        },
        {
            "status": "failure",
            "error_code": "   ",
            "message": "No result",
        },
        {
            "status": "failure",
            "error_code": "NOT_FOUND",
            "message": "   ",
        },
    ],
)
def test_operation_result_rejects_inconsistent_states(
    payload: dict[str, object],
) -> None:
    """Reject contradictory or incomplete execution outcomes."""

    with pytest.raises(ValidationError):
        OperationResult.model_validate(payload)


def test_evidence_accepts_source_reference() -> None:
    """Identify where an enrichment result came from."""
    evidence = Evidence(
        source="wikidata",
        reference="Q513",
    )

    assert evidence.source == "wikidata"
    assert evidence.reference == "Q513"


@pytest.mark.parametrize(
    ("source", "reference"),
    [
        ("", "Q513"),
        ("   ", "Q513"),
        ("wikidata", ""),
        ("wikidata", "   "),
    ],
)
def test_evidence_rejects_empty_fields(
    source: str,
    reference: str,
) -> None:
    """Reject missing source identity information."""
    with pytest.raises(ValidationError):
        Evidence(source=source, reference=reference)


def test_operation_result_evidence_json_roundtrip() -> None:
    """Preserve source evidence with the operation result."""
    original = OperationResult(
        status="success",
        value=Quantity(value=8848.86, unit="m"),
        evidence=(Evidence(source="wikidata", reference="Q513"),),
    )

    restored = OperationResult.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.evidence[0].reference == "Q513"


def test_operation_results_have_independent_evidence() -> None:
    """Evidence provided to one result must not affect another."""

    first = OperationResult(
        status="success",
        value=1.0,
        evidence=(Evidence(source="local", reference="calculation"),),
    )
    second = OperationResult(
        status="success",
        value=2.0,
    )

    assert len(first.evidence) == 1
    assert second.evidence == ()


@pytest.mark.parametrize(
    ("model_type", "payload"),
    [
        (GeoPoint, {"latitude": True, "longitude": 10.0}),
        (GeoPoint, {"latitude": 53.55, "longitude": False}),
        (GeoPoint, {"latitude": "53.55", "longitude": 10.0}),
        (Quantity, {"value": True, "unit": "m"}),
        (Quantity, {"value": "8848.86", "unit": "m"}),
        (OperationResult, {"status": "success", "value": True}),
    ],
)
def test_runtime_models_reject_implicit_numeric_coercion(
    model_type: type[BaseModel],
    payload: dict[str, object],
) -> None:
    """Reject booleans and numeric strings where numeric data is expected."""

    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "field_name", "new_value"),
    [
        (
            GeoPoint(latitude=53.55, longitude=10.0),
            "latitude",
            91.0,
        ),
        (
            EntityRef(source="wikidata", entity_id="Q513"),
            "entity_id",
            "",
        ),
        (
            Quantity(value=8848.86, unit="m"),
            "unit",
            "",
        ),
        (
            Evidence(source="wikidata", reference="Q513"),
            "reference",
            "",
        ),
        (
            OperationResult(status="success", value=42.0),
            "status",
            "failure",
        ),
    ],
)
def test_runtime_models_reject_mutation(
    model: BaseModel,
    field_name: str,
    new_value: object,
) -> None:
    """Prevent changes after runtime values have been validated."""

    with pytest.raises(ValidationError):
        setattr(model, field_name, new_value)


def test_operation_result_evidence_is_immutable() -> None:
    """Prevent mutation of evidence attached to an operation result."""

    result = OperationResult(
        status="success",
        value=42.0,
        evidence=(Evidence(source="wikidata", reference="Q513"),),
    )

    assert isinstance(result.evidence, tuple)
    assert len(result.evidence) == 1
