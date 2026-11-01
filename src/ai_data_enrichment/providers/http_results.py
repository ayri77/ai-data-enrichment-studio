"""Map transport failures into provider-independent operation results."""

from json import JSONDecodeError

from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.providers.http_transport import JsonTransportError


def transport_failure_result(
    error: JsonTransportError | JSONDecodeError | OSError,
    *,
    provider_label: str,
) -> OperationResult:
    """Preserve existing public failure codes while isolating HTTP details."""
    malformed_json = isinstance(error, JSONDecodeError) or (
        isinstance(error, JsonTransportError) and error.kind == "invalid_json"
    )

    if malformed_json:
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message=f"{provider_label} returned malformed JSON.",
        )

    return OperationResult(
        status="failure",
        error_code="PROVIDER_ERROR",
        message=f"{provider_label} request failed.",
    )
