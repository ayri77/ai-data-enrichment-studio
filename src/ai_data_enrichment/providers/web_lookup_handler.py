"""The ``web_lookup`` capability: one request per row, several columns answered.

Every expected failure is an ``OperationResult`` with a code; nothing here raises for
a bad reply or a failed request. Messages are fixed sentences: they never carry the
reply, the request, a cell value or the key. There are no retries.
"""

import time
from collections.abc import Callable, Mapping
from math import isfinite
from typing import Any

from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.providers.web_lookup import (
    LookupTask,
    WebLookupClient,
    WebLookupUsage,
    api_citations,
    build_request,
    configuration_id,
    tokens,
)
from ai_data_enrichment.providers.web_lookup_reply import ParsedReply, ReplyFailure, parse_reply

DEFAULT_TIMEOUT_SECONDS = 60.0
_TASKS_KEPT = 16
# Client errors that say "try again later", not "this request is wrong".
_TRANSIENT_STATUSES = {408, 409, 429}


def _failure(code: str, message: str) -> OperationResult:
    return OperationResult(status="failure", error_code=code, message=message)


def _context_text(value: object) -> str | None:
    """A context value as the text sent; None when it is absent or empty."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return str(int(value)) if value.is_integer() else str(value)
    raise TypeError


def _status_failure(status: int) -> OperationResult:
    if status in (401, 403):
        return _failure("SOURCE_ACCESS_DENIED", "The web search service refused the request.")
    if 400 <= status < 500 and status not in _TRANSIENT_STATUSES:
        return _failure(
            "REQUEST_REFUSED", f"The web search service rejected the request (HTTP {status})."
        )
    return _failure("PROVIDER_ERROR", "The web search request failed.")


class WebLookupHandler:
    """Ask a model with web search for several columns of one row."""

    def __init__(
        self,
        client: WebLookupClient,
        *,
        model: str,
        reasoning_effort: str | None,
        usage: WebLookupUsage,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        deadline: float | None = None,
        on_reply: Callable[[ParsedReply | ReplyFailure], None] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = reasoning_effort
        self._usage = usage
        self._timeout = timeout_seconds
        self._clock = clock
        self._deadline = deadline
        # Receives the per-slot diagnostics of each read reply (no row content).
        self._on_reply = on_reply
        self._tasks: dict[str, LookupTask] = {}

    @property
    def configuration_id(self) -> str:
        return configuration_id(self._model, self._effort)

    def _task(self, text: str) -> LookupTask | None:
        task = self._tasks.get(text)
        if task is None:
            try:
                task = LookupTask.from_json(text)
            except ValueError:
                return None
            if len(self._tasks) >= _TASKS_KEPT:
                self._tasks.clear()
            self._tasks[text] = task
        return task

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        text = inputs.get("task")
        task = self._task(text) if isinstance(text, str) else None
        if task is None:
            return _failure("INVALID_INPUT", "The lookup task is missing or not valid.")

        row_context: dict[str, str] = {}
        for slot, header in task.context:
            try:
                shown = _context_text(inputs.get(slot))
            except TypeError:
                return _failure("INVALID_INPUT", "A context value must be text or a number.")
            if shown is not None:
                row_context[header] = shown
        if not row_context:
            return _failure("MISSING_VALUE", "The row has no value to search for.")

        if self._deadline is not None and self._clock() + self._timeout > self._deadline:
            return _failure("DEADLINE_REACHED", "The time for the lookup run is used up.")

        request: dict[str, Any] = build_request(
            task, row_context, model=self._model, reasoning_effort=self._effort
        )
        try:
            response = self._client.responses.create(**request, timeout=self._timeout)
        except Exception as exc:
            self._usage.add()
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and not isinstance(status, bool):
                return _status_failure(status)
            # The class name tells a timeout from a programming error; it holds no content.
            return _failure(
                "PROVIDER_ERROR",
                f"The web search request failed or timed out ({type(exc).__name__}).",
            )

        input_tokens, output_tokens = tokens(response)
        self._usage.add(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            web_searches=api_citations(response)["search_calls"],
        )
        if getattr(response, "status", None) != "completed":
            return _failure("RESPONSE_INCOMPLETE", "The web search response was not completed.")

        parsed = parse_reply(response, task)
        if self._on_reply is not None:
            self._on_reply(parsed)
        if isinstance(parsed, ReplyFailure):
            return _failure(parsed.code, parsed.message)
        return OperationResult(status="success", value=parsed.record, evidence=parsed.evidence)
