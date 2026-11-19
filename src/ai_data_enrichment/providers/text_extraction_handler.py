"""The call: one request per text, every expected failure a result with a code.

Nothing here raises for a bad reply or a failed request. Messages are fixed sentences:
they never carry the reply, the request, the text or the key. There are no retries.

``extract`` is the call itself; ``execute`` is the same call as a capability handler of a
plan (``text_extraction``), with the outcomes mapped onto an ``OperationResult``.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from ai_data_enrichment.execution.models import (
    FoundRecord,
    FoundValue,
    OperationResult,
    RuntimeValue,
)
from ai_data_enrichment.providers.text_extraction import (
    ExtractionClient,
    ExtractionTask,
    ExtractionUsage,
    build_request,
    configuration_id,
    parse_task,
    tokens,
)
from ai_data_enrichment.providers.text_extraction_reply import (
    OUTCOME_FOUND,
    OUTCOME_NOT_APPLICABLE,
    OUTCOME_NOTHING_FOUND,
    ExtractionResult,
    parse_reply,
)

DEFAULT_TIMEOUT_SECONDS = 60.0
_TASKS_KEPT = 16
# Client errors that say "try again later", not "this request is wrong".
_TRANSIENT_STATUSES = {408, 409, 429}


def _failure(code: str, message: str) -> OperationResult:
    return OperationResult(status="failure", error_code=code, message=message)


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
        clock: Callable[[], float] = time.monotonic,
        deadline: float | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = reasoning_effort
        self._usage = usage
        self._timeout = timeout_seconds
        self._clock = clock
        # Used by ``execute`` only: no request is started that could end after it.
        self._deadline = deadline
        self._tasks: dict[str, ExtractionTask] = {}

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

    def _task(self, text: str) -> ExtractionTask | None:
        task = self._tasks.get(text)
        if task is None:
            task = parse_task(text)
            if task is None:
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
        """Read the cell ``text`` for the task of the plan, as a step of a plan.

        ``NOTHING_FOUND`` (the text is about the task but no value could be traced) is an
        answer and is reported as the web lookup reports its own, so it is kept the same
        way. ``NOT_APPLICABLE`` (the text is about something else) is a failed step: a
        record without any value would be taken for "nothing found" and kept, and the
        text would never be asked again.
        """
        raw = inputs.get("task")
        task = self._task(raw) if isinstance(raw, str) else None
        if task is None:
            return _failure("INVALID_INPUT", "The extraction task is missing or not valid.")

        text = inputs.get("text")
        if text is not None and not isinstance(text, str):
            return _failure("INVALID_INPUT", "The text to read must be text.")
        if text is None or not text.strip():
            return _failure("MISSING_VALUE", "The row has no text to read.")

        if self._deadline is not None and self._clock() + self._timeout > self._deadline:
            return _failure("DEADLINE_REACHED", "The time for the extraction run is used up.")

        result = self.extract(task, text)
        if result.outcome == OUTCOME_FOUND:
            found = {item.slot: item for item in result.kept}
            return OperationResult(
                status="success",
                value=FoundRecord(
                    slots={
                        slot: (
                            FoundValue(
                                value=found[slot].value,
                                basis=found[slot].mark,
                                quote=found[slot].quote,
                            )
                            if slot in found
                            else FoundValue(value=None, basis=None)
                        )
                        for slot, _ in task.slots
                    }
                ),
            )
        if result.outcome == OUTCOME_NOTHING_FOUND:
            return _failure(
                "NOTHING_FOUND", "The text holds no value that can be traced for any column."
            )
        if result.outcome == OUTCOME_NOT_APPLICABLE:
            return _failure("NOT_APPLICABLE", "The text is not about the task.")
        return _failure(result.outcome, result.message or "The extraction failed.")
