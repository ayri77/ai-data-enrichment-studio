"""The call: one request per text, every expected failure a result with a code.

Nothing here raises for a bad reply or a failed request. Messages are fixed sentences:
they never carry the reply, the request, the text or the key. There are no retries.
"""

from typing import Any

from ai_data_enrichment.providers.text_extraction import (
    ExtractionClient,
    ExtractionTask,
    ExtractionUsage,
    build_request,
    configuration_id,
    tokens,
)
from ai_data_enrichment.providers.text_extraction_reply import (
    ExtractionResult,
    parse_reply,
)

DEFAULT_TIMEOUT_SECONDS = 60.0
# Client errors that say "try again later", not "this request is wrong".
_TRANSIENT_STATUSES = {408, 409, 429}


def _status_failure(status: int) -> ExtractionResult:
    if status in (401, 403):
        return ExtractionResult("SOURCE_ACCESS_DENIED", "The service refused the request.")
    if 400 <= status < 500 and status not in _TRANSIENT_STATUSES:
        return ExtractionResult(
            "REQUEST_REFUSED", f"The service rejected the request (HTTP {status})."
        )
    return ExtractionResult("PROVIDER_ERROR", "The request failed.")


class TextExtractionHandler:
    """Ask a model for several columns of one row, from one text."""

    def __init__(
        self,
        client: ExtractionClient,
        *,
        model: str,
        reasoning_effort: str | None,
        usage: ExtractionUsage,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = reasoning_effort
        self._usage = usage
        self._timeout = timeout_seconds

    @property
    def configuration_id(self) -> str:
        return configuration_id(self._model, self._effort)

    def extract(self, task: ExtractionTask, text: str) -> ExtractionResult:
        request: dict[str, Any] = build_request(
            task, text, model=self._model, reasoning_effort=self._effort
        )
        try:
            response = self._client.responses.create(**request, timeout=self._timeout)
        except Exception as exc:
            self._usage.add()
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and not isinstance(status, bool):
                return _status_failure(status)
            # The class name tells a timeout from a programming error; it holds no content.
            return ExtractionResult(
                "PROVIDER_ERROR", f"The request failed or timed out ({type(exc).__name__})."
            )

        input_tokens, output_tokens = tokens(response)
        self._usage.add(input_tokens=input_tokens, output_tokens=output_tokens)
        if getattr(response, "status", None) != "completed":
            return ExtractionResult(
                "RESPONSE_INCOMPLETE",
                "The response was not completed.",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return parse_reply(response, task, text)
