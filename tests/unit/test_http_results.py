"""Shared HTTP failure mapping contract tests."""

from json import JSONDecodeError

import pytest

from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransportError


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (JsonTransportError("timeout"), "PROVIDER_ERROR"),
        (JsonTransportError("network"), "PROVIDER_ERROR"),
        (JsonTransportError("http_status", status_code=429), "PROVIDER_ERROR"),
        (JsonTransportError("invalid_json"), "INVALID_RESPONSE"),
        (JSONDecodeError("Malformed", "{", 0), "INVALID_RESPONSE"),
        (ConnectionError("Disconnected"), "PROVIDER_ERROR"),
    ],
    ids=[
        "timeout",
        "network",
        "http-status",
        "invalid-json",
        "legacy-json-error",
        "legacy-network-error",
    ],
)
def test_transport_failure_contract(
    error: JsonTransportError | JSONDecodeError | OSError,
    expected_code: str,
) -> None:
    """Return structured failures without exposing transport details."""
    result = transport_failure_result(error, provider_label="Test API")

    assert result.status == "failure"
    assert result.error_code == expected_code
    assert result.message
    assert result.value is None
    assert not result.evidence
    assert "Test API" in result.message
