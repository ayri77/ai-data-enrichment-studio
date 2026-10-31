"""Tests for the capability execution interface."""

from collections.abc import Mapping

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.models import (
    GeoPoint,
    OperationResult,
    RuntimeValue,
)


class StubGeocodeHandler:
    """Simulate geocoding without making external requests."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        # A controlled failure lets us test the same interface for both outcomes.
        if inputs["city"] == "Unknown":
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="City could not be resolved",
            )

        return OperationResult(
            status="success",
            value=GeoPoint(latitude=53.55, longitude=10.0),
        )


def test_capability_handler_returns_success() -> None:
    """A compatible implementation can be used through the protocol."""

    handler: CapabilityHandler = StubGeocodeHandler()

    result = handler.execute(
        inputs={"city": "Hamburg", "country": "Germany"},
        parameters={},
    )

    assert result.status == "success"
    assert result.value == GeoPoint(latitude=53.55, longitude=10.0)


def test_capability_handler_returns_failure() -> None:
    """The same interface also represents an explicit operation failure."""

    handler: CapabilityHandler = StubGeocodeHandler()

    result = handler.execute(
        inputs={"city": "Unknown", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.value is None
    assert result.error_code == "NOT_FOUND"
