"""The planner measurement script: what it records besides the model's answers."""

from typing import Any, cast

import pytest
from scripts.experiment_planner import (
    SERVER_REQUEST_TIMEOUT_SECONDS,
    RecordingClient,
    request_timing,
)

from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


def test_request_timing_counts_every_request_and_those_the_server_would_drop() -> None:
    slow = SERVER_REQUEST_TIMEOUT_SECONDS + 1.5
    records: list[dict[str, object]] = [
        {"request_seconds": [1.0, slow]},  # a run with one repair
        {"request_seconds": [2.0]},
        {},  # a run that never reached the model
    ]
    assert request_timing(records) == {
        "requests": 3,
        "median_seconds": 2.0,
        "max_seconds": slow,
        "over_limit": 1,
    }
    assert request_timing([]) == {
        "requests": 0,
        "median_seconds": None,
        "max_seconds": None,
        "over_limit": 0,
    }


class _Client:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        if self.answer is None:
            raise TimeoutError("model did not answer")
        return self.answer


def test_a_failed_request_is_timed_too() -> None:
    answered = RecordingClient(cast(OpenAIPlanClient, _Client("{}")))
    assert answered.complete(messages=[], response_schema={}) == "{}"
    assert cast(float, answered.completions[0]["seconds"]) >= 0

    silent = RecordingClient(cast(OpenAIPlanClient, _Client(None)))
    with pytest.raises(TimeoutError):
        silent.complete(messages=[], response_schema={})
    # A timeout is exactly the request whose duration matters.
    assert silent.completions[0]["error_type"] == "TimeoutError"
    assert cast(float, silent.completions[0]["seconds"]) >= 0
