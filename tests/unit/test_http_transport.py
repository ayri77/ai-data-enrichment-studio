"""Offline tests for the HTTP JSON transport."""

from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from ai_data_enrichment.providers.http_transport import JsonTransportError, UrllibJsonTransport
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


def test_transport_sends_configured_http_headers() -> None:
    """Allow explicit user-agent configuration without changing provider calls."""
    transport = UrllibJsonTransport(headers={"User-Agent": "AI-Data-Enrichment-Studio/0.1"})

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=BytesIO(b"{}"),
    ) as mock_urlopen:
        assert transport.get_json(url=GEOCODING_URL, params={}, timeout_seconds=5.0) == {}

    request = mock_urlopen.call_args.args[0]
    assert isinstance(request, Request)
    assert request.get_header("User-agent") == "AI-Data-Enrichment-Studio/0.1"
    assert request.get_method() == "GET"


@pytest.mark.parametrize(
    ("upstream_error", "expected_kind"),
    [
        (TimeoutError("Timed out"), "timeout"),
        (URLError("Connection refused"), "network"),
    ],
)
def test_transport_classifies_connection_failures(
    upstream_error: OSError,
    expected_kind: str,
) -> None:
    """Never leak raw network exceptions through the transport contract."""
    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        side_effect=upstream_error,
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().get_json(url=GEOCODING_URL, params={}, timeout_seconds=5.0)

    assert captured.value.kind == expected_kind
    assert captured.value.status_code is None
    assert captured.value.retry_after is None


@pytest.mark.parametrize(
    ("status_code", "retry_after"),
    [
        (429, "30"),
        (503, "5"),
        (400, None),
    ],
)
def test_transport_preserves_http_failure_metadata(
    status_code: int, retry_after: str | None
) -> None:
    """Capture status and retry hints without implementing retries yet."""
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after

    upstream_error = HTTPError(GEOCODING_URL, status_code, "HTTP error", headers, None)

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        side_effect=upstream_error,
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().get_json(url=GEOCODING_URL, params={}, timeout_seconds=5.0)

    assert captured.value.kind == "http_status"
    assert captured.value.status_code == status_code
    assert captured.value.retry_after == retry_after


@pytest.mark.parametrize("body", [b'{"results": [}', b"\xff"], ids=["syntax", "encoding"])
def test_transport_rejects_invalid_json(body: bytes) -> None:
    """Unparseable response bytes produce a typed invalid-json failure."""
    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=BytesIO(body),
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().get_json(url=GEOCODING_URL, params={}, timeout_seconds=5.0)

    assert captured.value.kind == "invalid_json"


ROUTE_URL = "https://routing.test/v2/directions/driving-car"


def test_post_json_sends_a_json_body_with_the_configured_headers() -> None:
    """A source that computes an answer is asked with a body, not a query string."""
    transport = UrllibJsonTransport(headers={"User-Agent": "Studio/0.1", "Authorization": "k"})

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        return_value=BytesIO(b'{"routes": []}'),
    ) as mock_urlopen:
        result = transport.post_json(
            url=ROUTE_URL,
            body={"coordinates": [[2.35, 48.85], [-0.13, 51.51]], "geometry": False},
            timeout_seconds=20.0,
        )

    assert result == {"routes": []}
    request = mock_urlopen.call_args.args[0]
    assert isinstance(request, Request)
    assert request.get_method() == "POST"
    assert request.full_url == ROUTE_URL
    assert request.data == b'{"coordinates": [[2.35, 48.85], [-0.13, 51.51]], "geometry": false}'
    assert request.get_header("Content-type") == "application/json; charset=utf-8"
    assert request.get_header("Authorization") == "k"
    assert request.get_header("User-agent") == "Studio/0.1"
    assert mock_urlopen.call_args.kwargs["timeout"] == 20.0


@pytest.mark.parametrize(
    ("body", "payload"),
    [
        (b'{"error": {"code": 2009, "message": "no route"}}', {"error": {"code": 2009}}),
        (b"<html>Not found</html>", None),
        (b"", None),
    ],
)
def test_post_json_keeps_what_the_source_said_when_it_refused(body: bytes, payload) -> None:
    """The reason is in the body of the error answer, never in the message."""
    upstream_error = HTTPError(ROUTE_URL, 404, "Not Found", Message(), BytesIO(body))

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        side_effect=upstream_error,
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().post_json(url=ROUTE_URL, body={}, timeout_seconds=5.0)

    error = captured.value
    assert (error.kind, error.status_code) == ("http_status", 404)
    if payload is None:
        assert error.payload is None
    else:
        assert isinstance(error.payload, dict)
        assert error.payload["error"]["code"] == payload["error"]["code"]
    assert "2009" not in str(error) and ROUTE_URL not in str(error)


def test_an_error_answer_is_read_only_up_to_a_bound() -> None:
    """An oversized error body is cut, so it does not parse and is left out."""
    body = b'{"error": "' + b"x" * 10_000 + b'"}'
    upstream_error = HTTPError(ROUTE_URL, 500, "Error", Message(), BytesIO(body))

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        side_effect=upstream_error,
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().post_json(url=ROUTE_URL, body={}, timeout_seconds=5.0)

    assert captured.value.payload is None


def test_get_json_does_not_read_error_bodies() -> None:
    """Existing lookups keep their behaviour: status and retry hint only."""
    body = BytesIO(b'{"error": {"code": 1}}')
    upstream_error = HTTPError(GEOCODING_URL, 400, "Bad", Message(), body)

    with patch(
        "ai_data_enrichment.providers.http_transport.urlopen",
        side_effect=upstream_error,
    ):
        with pytest.raises(JsonTransportError) as captured:
            UrllibJsonTransport().get_json(url=GEOCODING_URL, params={}, timeout_seconds=5.0)

    assert captured.value.payload is None
    assert body.tell() == 0
