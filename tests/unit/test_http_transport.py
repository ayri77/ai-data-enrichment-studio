"""Offline tests for the HTTP JSON transport."""

from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL


def test_get_json_encodes_parameters_and_parses_response() -> None:
    """Encode query parameters and decode JSON without network access."""
    response = BytesIO(b'{"results": [{"id": 2950159}]}')

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=response,
    ) as mock_urlopen:
        result = UrllibJsonTransport().get_json(
            url=GEOCODING_URL,
            params={
                "name": "München",
                "countryCode": "DE",
            },
            timeout_seconds=7.0,
        )

    assert result == {"results": [{"id": 2950159}]}

    mock_urlopen.assert_called_once()

    request_url = mock_urlopen.call_args.args[0]
    timeout = mock_urlopen.call_args.kwargs["timeout"]

    parsed_url = urlsplit(request_url)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "geocoding-api.open-meteo.com"
    assert parsed_url.path == "/v1/search"

    assert parse_qs(parsed_url.query) == {
        "name": ["München"],
        "countryCode": ["DE"],
    }

    assert timeout == 7.0
