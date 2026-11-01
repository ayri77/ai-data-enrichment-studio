"""Reusable result-contract checks across distinct capability handlers."""

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler


@pytest.mark.parametrize(
    "scenario",
    [
        "geocode-invalid",
        "geocode-not-found",
        "distance-invalid",
        "distance-invalid-unit",
    ],
)
def test_provider_failures_obey_operation_result_contract(scenario: str) -> None:
    """Providers return structured failures without partial or fabricated values."""
    if scenario == "geocode-invalid":
        handler = OpenMeteoGeocodingHandler(StubJsonTransport({}))
        result = handler.execute(
            inputs={"city": "", "country": "Germany"},
            parameters={},
        )
        expected_error = "INVALID_INPUT"
    elif scenario == "geocode-not-found":
        handler = OpenMeteoGeocodingHandler(StubJsonTransport({"results": []}))
        result = handler.execute(
            inputs={"city": "Berlin", "country": "Germany"},
            parameters={},
        )
        expected_error = "NOT_FOUND"
    elif scenario == "distance-invalid":
        result = GeodesicDistanceHandler().execute(
            inputs={"point_a": "invalid", "point_b": "invalid"},
            parameters={"unit": "km"},
        )
        expected_error = "INVALID_INPUT"
    else:
        point = GeoPoint(latitude=52.52437, longitude=13.41053)
        result = GeodesicDistanceHandler().execute(
            inputs={"point_a": point, "point_b": point},
            parameters={"unit": "m"},
        )
        expected_error = "INVALID_PARAMETER"

    assert result.status == "failure"
    assert result.error_code == expected_error
    assert result.message is not None and result.message.strip()
    assert result.value is None
    assert not result.evidence
