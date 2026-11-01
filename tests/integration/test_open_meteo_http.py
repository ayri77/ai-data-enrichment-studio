"""Offline integration tests for Open-Meteo HTTP geocoding."""

from io import BytesIO
from unittest.mock import patch

from ai_data_enrichment.execution.models import Evidence, GeoPoint
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.providers.open_meteo import (
    OpenMeteoGeocodingHandler,
)


def test_geocoding_through_http_transport() -> None:
    """Resolve a location through the real transport without network access."""
    response = BytesIO(
        b'{"results": [{"id": 2950159, "name": "Berlin", '
        b'"country": "Germany", "country_code": "DE", '
        b'"latitude": 52.52437, "longitude": 13.41053}]}'
    )

    handler = OpenMeteoGeocodingHandler(transport=UrllibJsonTransport())

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=response,
    ) as mock_urlopen:
        result = handler.execute(
            inputs={"city": "Berlin", "country": "Germany"},
            parameters={},
        )

    assert result.status == "success"
    assert result.value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
    )
    assert result.evidence == (
        Evidence(
            source="open-meteo",
            reference="geonames:2950159",
        ),
    )

    mock_urlopen.assert_called_once()


def test_geocoding_rejects_malformed_json() -> None:
    """Return a structured failure when HTTP response is invalid JSON."""
    response = BytesIO(b'{"results": [}')

    handler = OpenMeteoGeocodingHandler(transport=UrllibJsonTransport())

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=response,
    ):
        result = handler.execute(
            inputs={"city": "Berlin", "country": "Germany"},
            parameters={},
        )

    assert result.status == "failure"
    assert result.error_code == "INVALID_RESPONSE"
    assert result.value is None
    assert result.message
    assert not result.evidence
