"""A fake model client for the universal search, made INSIDE the run worker.

The worker is a spawned process: what is handed to it must be picklable, and what it
does can only be seen through files. ``LoggedApiFactory`` is a top-level, picklable
callable, the seam ``create_app(offline_lookup_api=...)`` stands for. It is called in
the worker with the key the worker was given, checks it, and returns an object with
``responses.create(**kwargs)`` that appends one JSON line per request to a log file. The
log holds the request, never the key: only whether the key was the expected one.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_data_enrichment.providers.web_lookup import WebLookupClient
from tests.support.web_lookup import (
    PAGE_2,
    StatusError,
    empty_reply,
    good_reply,
    response,
    slot,
)

SENTINEL = "SENTINEL-RAW-REPLY"
K2 = {
    "value_1": slot(unit=None),  # no height given
    "value_2": slot(value="Italian expedition", basis="stated", sources=[PAGE_2], quote="1954"),
    "value_3": slot(value=True, basis="stated", sources=[PAGE_2]),
}


def mountain_answer(kwargs: dict[str, Any]) -> Any:
    """What the fake model says: depends on the mountain in the request."""
    text: str = kwargs["input"]
    if "- Mountain: K2" in text:
        return response(K2)
    if "- Mountain: Nowhere" in text:
        return response(empty_reply())
    if "- Mountain: Broken" in text:
        return response(f"{SENTINEL} {{not json")
    return response(good_reply())


@dataclass(frozen=True)
class LoggedApiFactory:
    expected_key: str
    log: Path
    # Seconds each request takes, so that a test can run into a limit.
    delay: float = 0.0
    answer: Callable[[dict[str, Any]], Any] = mountain_answer

    def __call__(self, api_key: str) -> WebLookupClient:
        return _LoggedApi(self, api_key == self.expected_key)


class _LoggedApi:
    def __init__(self, factory: LoggedApiFactory, key_ok: bool) -> None:
        self._factory, self._key_ok = factory, key_ok

    @property
    def responses(self) -> "_LoggedApi":
        return self

    def create(self, **kwargs: Any) -> Any:
        with self._factory.log.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "key_ok": self._key_ok,
                        "input": kwargs["input"],
                        "model": kwargs["model"],
                        "reasoning": kwargs.get("reasoning"),
                        "timeout": kwargs.get("timeout"),
                        "tools": kwargs["tools"],
                    }
                )
                + "\n"
            )
        if not self._key_ok:
            raise StatusError(401)
        if self._factory.delay:
            time.sleep(self._factory.delay)
        return self._factory.answer(kwargs)


@dataclass(frozen=True)
class LeakyFactory:
    """A factory whose failure message quotes the key, as a library's might."""

    def __call__(self, api_key: str) -> WebLookupClient:
        raise RuntimeError(f"Illegal header value b'Bearer {api_key}'")


def forbidden_api(api_key: str) -> WebLookupClient:
    """Stands in for the real client in tests that must never make one."""
    raise AssertionError("A test reached the real OpenAI client factory.")


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text("utf-8").splitlines() if line]
