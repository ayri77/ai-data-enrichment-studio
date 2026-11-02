"""Map transport failures into provider-independent operation results."""

from json import JSONDecodeError

from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.retry_transport import RetryExhausted


def transport_failure_result(
    error: JsonTransportError | JSONDecodeError | OSError,
    *,
    provider_label: str,
) -> OperationResult:
    """Preserve existing public failure codes while isolating HTTP details."""
    malformed_json = isinstance(error, JSONDecodeError) or (
        isinstance(error, JsonTransportError) and error.kind == "invalid_json"
    )

    if isinstance(error, JsonTransportError) and error.kind == "budget_exceeded":
        reason = getattr(error, "reason", "request_budget")
        return OperationResult(
            status="failure",
            error_code=(
                "RATE_BUDGET_EXCEEDED" if reason == "rate_budget" else "REQUEST_BUDGET_EXCEEDED"
            ),
            message=f"{provider_label} request budget exhausted.",
        )

    if isinstance(error, RetryExhausted):
        return OperationResult(
            status="failure",
            error_code="RETRY_EXHAUSTED",
            message=f"{provider_label} transient request retries exhausted.",
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
