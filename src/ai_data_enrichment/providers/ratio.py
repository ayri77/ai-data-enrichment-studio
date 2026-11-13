"""How many times one quantity is larger than another."""

from collections.abc import Mapping
from math import isfinite

from ai_data_enrichment.execution.models import (
    Evidence,
    OperationResult,
    Quantity,
    RuntimeValue,
)


class RatioHandler:
    """Divide two quantities of the same unit. Nothing is converted."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        numerator = inputs.get("numerator")
        denominator = inputs.get("denominator")

        if not isinstance(numerator, Quantity) or not isinstance(denominator, Quantity):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Both numerator and denominator must be Quantity values.",
            )

        # Kilometres over metres would be a number that means nothing.
        if numerator.unit != denominator.unit:
            return OperationResult(
                status="failure",
                error_code="UNIT_MISMATCH",
                message="The two quantities have different units.",
            )

        if denominator.value == 0.0:
            return OperationResult(
                status="failure",
                error_code="DIVISION_BY_ZERO",
                message="The quantity to divide by is zero.",
            )

        ratio = numerator.value / denominator.value
        if not isfinite(ratio):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="The ratio of the two quantities is not a finite number.",
            )

        return OperationResult(
            status="success",
            value=ratio,
            evidence=(Evidence(source="calculation", reference=f"ratio:{numerator.unit}"),),
        )
