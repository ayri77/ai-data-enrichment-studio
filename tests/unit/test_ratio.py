"""Tests for the ratio of two quantities."""

import pytest

from ai_data_enrichment.capabilities.definitions import get_ratio_spec
from ai_data_enrichment.domain.capability import DataType
from ai_data_enrichment.execution.models import OperationResult, Quantity
from ai_data_enrichment.output.value_projection import project_operation_field
from ai_data_enrichment.providers.ratio import RatioHandler


def ratio(numerator: object, denominator: object) -> OperationResult:
    return RatioHandler().execute(
        inputs={"numerator": numerator, "denominator": denominator},  # type: ignore[dict-item]
        parameters={},
    )


def test_two_quantities_of_one_unit_give_a_plain_number() -> None:
    result = ratio(Quantity(value=460.0, unit="km"), Quantity(value=344.0, unit="km"))

    assert result.status == "success"
    assert result.value == pytest.approx(1.3372, abs=1e-4)
    assert isinstance(result.value, float)
    assert [(e.source, e.reference) for e in result.evidence] == [("calculation", "ratio:km")]
    # A plain number is written to a cell through its "value" field.
    assert project_operation_field(result, "value") == result.value


def test_different_units_are_refused_rather_than_converted() -> None:
    result = ratio(Quantity(value=460.0, unit="km"), Quantity(value=344_000.0, unit="m"))

    assert (result.status, result.error_code) == ("failure", "UNIT_MISMATCH")


def test_division_by_zero_is_a_failure_not_a_value() -> None:
    result = ratio(Quantity(value=460.0, unit="km"), Quantity(value=0.0, unit="km"))

    assert result.error_code == "DIVISION_BY_ZERO"


def test_a_result_too_large_to_be_a_number_is_a_failure() -> None:
    result = ratio(Quantity(value=1e308, unit="km"), Quantity(value=1e-308, unit="km"))

    assert result.error_code == "INVALID_INPUT"


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(1.0, Quantity(value=1.0, unit="km")), (Quantity(value=1.0, unit="km"), None)],
)
def test_only_quantities_are_divided(numerator, denominator) -> None:
    assert ratio(numerator, denominator).error_code == "INVALID_INPUT"


def test_the_contract_takes_two_quantities_and_returns_a_number() -> None:
    spec = get_ratio_spec()

    assert {name: port.type for name, port in spec.inputs.items()} == {
        "numerator": DataType.QUANTITY,
        "denominator": DataType.QUANTITY,
    }
    assert spec.parameters == {}
    assert spec.output.type == DataType.NUMBER
    assert spec.output.fields == {"value": DataType.NUMBER}
