"""Offline tests for Open-Meteo geocoding."""

import pytest
from tests.support.http import StubJsonTransport as StubTransport

from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.open_meteo import (
    GEOCODING_URL,
    OpenMeteoGeocodingHandler,
)


def test_resolves_berlin_with_country_context() -> None:
    """Resolve Berlin, Germany from a controlled API response."""
    transport = StubTransport(
        {
            "results": [
                {
                    "id": 2950159,
                    "name": "Berlin",
                    "country": "Germany",
                    "country_code": "DE",
                    "latitude": 52.52437,
                    "longitude": 13.41053,
                }
            ]
        }
    )

    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "success"
    assert result.value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )
    assert result.evidence[0].source == "open-meteo"
    assert result.evidence[0].reference == "geonames:2950159"

    assert transport.requests == [
        (
            GEOCODING_URL,
            {
                "name": "Berlin",
                "countryCode": "DE",
                "count": "10",
                "language": "en",
                "format": "json",
            },
            10.0,
        )
    ]


def test_rejects_result_from_wrong_country() -> None:
    """Never accept coordinates belonging to a different country."""
    transport = StubTransport(
        {
            "results": [
                {
                    "id": 5000001,
                    "name": "Berlin",
                    "country": "United States",
                    "country_code": "US",
                    "latitude": 44.4687,
                    "longitude": -71.1851,
                }
            ]
        }
    )

    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "NOT_FOUND"
    assert result.value is None
    assert result.message


def test_selects_exact_city_match_not_first_result() -> None:
    """Select the exact city match instead of trusting result order."""
    transport = StubTransport(
        {
            "results": [
                {
                    "id": 2950158,
                    "name": "Bernau bei Berlin",
                    "country": "Germany",
                    "country_code": "DE",
                    "latitude": 52.6798,
                    "longitude": 13.5871,
                },
                {
                    "id": 2950159,
                    "name": "Berlin",
                    "country": "Germany",
                    "country_code": "DE",
                    "latitude": 52.52437,
                    "longitude": 13.41053,
                },
            ]
        }
    )

    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "success"
    assert result.value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )
    assert result.evidence[0].reference == "geonames:2950159"


@pytest.mark.parametrize(
    ("results", "expected_error"),
    [
        (
            [],
            "NOT_FOUND",
        ),
        (
            [
                {
                    "id": 1001,
                    "name": "Neustadt",
                    "country_code": "DE",
                    "latitude": 49.35,
                    "longitude": 8.14,
                },
                {
                    "id": 1002,
                    "name": "Neustadt",
                    "country_code": "DE",
                    "latitude": 50.85,
                    "longitude": 9.12,
                },
            ],
            "AMBIGUOUS",
        ),
    ],
    ids=["no-results", "multiple-exact-matches"],
)
def test_handles_missing_and_ambiguous_locations(
    results: list[dict[str, object]],
    expected_error: str,
) -> None:
    """Never fabricate coordinates or select ambiguous candidates."""
    transport = StubTransport({"results": results})
    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Neustadt", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == expected_error
    assert result.value is None
    assert result.message
    assert not result.evidence


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "unexpected text",
        {"results": "not a list"},
    ],
    ids=[
        "null-response",
        "string-response",
        "invalid-results-type",
    ],
)
def test_rejects_malformed_api_response(payload: object) -> None:
    """Return a structured failure for malformed provider responses."""
    transport = StubTransport(payload)
    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_RESPONSE"
    assert result.value is None
    assert result.message
    assert not result.evidence


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value"),
    [
        ("latitude", None),
        ("longitude", "invalid"),
        ("latitude", 91.0),
        ("id", None),
    ],
    ids=[
        "null-latitude",
        "invalid-longitude-type",
        "latitude-out-of-range",
        "null-geonames-id",
    ],
)
def test_rejects_malformed_location_fields(
    invalid_field: str,
    invalid_value: object,
) -> None:
    """Reject malformed location data without producing coordinates."""
    location: dict[str, object] = {
        "id": 2950159,
        "name": "Berlin",
        "country_code": "DE",
        "latitude": 52.52437,
        "longitude": 13.41053,
    }

    location[invalid_field] = invalid_value

    transport = StubTransport({"results": [location]})
    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_RESPONSE"
    assert result.value is None
    assert result.message
    assert not result.evidence


def test_handles_provider_error_response() -> None:
    """Distinguish API errors from locations not found."""
    transport = StubTransport(
        {
            "error": True,
            "reason": "Parameter count must be between 1 and 100.",
        }
    )

    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "PROVIDER_ERROR"
    assert result.value is None
    assert result.message
    assert not result.evidence


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("Request timed out"),
        ConnectionError("Network unavailable"),
    ],
    ids=["timeout", "connection-error"],
)
def test_handles_transport_failure(error: OSError) -> None:
    """Convert transport failures into structured operation results."""
    handler = OpenMeteoGeocodingHandler(transport=StubTransport(None, error=error))

    result = handler.execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "PROVIDER_ERROR"
    assert result.value is None
    assert result.message
    assert not result.evidence


@pytest.mark.parametrize(
    ("city", "country"),
    [
        (42.0, "Germany"),
        ("Berlin", 42.0),
        ("   ", "Germany"),
    ],
    ids=[
        "numeric-city",
        "numeric-country",
        "blank-city",
    ],
)
def test_rejects_invalid_inputs(
    city: str | float,
    country: str | float,
) -> None:
    """Reject invalid inputs before contacting the provider."""
    transport = StubTransport({"results": []})
    handler = OpenMeteoGeocodingHandler(transport=transport)

    result = handler.execute(
        inputs={"city": city, "country": country},
        parameters={},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_INPUT"
    assert result.value is None
    assert not transport.requests
