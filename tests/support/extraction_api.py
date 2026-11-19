"""A fake model client for the text extraction, made INSIDE the run worker.

As ``tests.support.lookup_api`` does for the universal search: a top-level, picklable factory
that ``create_app(offline_extraction_api=...)`` stands for. It is called in the worker with the
key the worker was given, checks it, and returns an object with ``responses.create(**kwargs)``
that appends one JSON line per request to a log file. The log holds the request (which is the
text of a row: a test reads it to see what was sent), never the key.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_data_enrichment.providers.text_extraction import ExtractionClient
from tests.support.text_extraction import entry, response
from tests.support.web_lookup import StatusError

# A sentence that is in every mail body but in no quoted passage.
SENTINEL = "SENTINEL-MAILBODY-4711-moonlit-harbour"
RECEIPT = "Total: 9,99 EUR"
RECEIPT_2 = "Total: 20,00 EUR"


def answer(applies: bool = True, **entries: dict[str, Any]) -> dict[str, Any]:
    """A reply for the three targets of the tests; slots not given are null."""
    values = {f"value_{i}": entry() for i in range(1, 4)}
    values.update(entries)
    return {"applies": applies, "values": values}


def sentinel_answer(kwargs: dict[str, Any]) -> Any:
    """What the fake model says for the targets Amount (number), Paid on (date), Renews (yes/no).

    The reply depends on a passage only one mail has. Nothing it quotes holds the sentinel.
    """
    text: str = kwargs["input"]
    if "Receipt FIRST" in text:
        return response(
            answer(
                value_1=entry(9.99, RECEIPT),
                value_2=entry("2026-11-03", "Paid on 3 Nov 2026"),
                value_3=entry("Yes", "Renews on 01.12.2026"),
            )
        )
    if "Receipt SECOND" in text:
        return response(answer(value_1=entry(20.0, RECEIPT_2)))
    if "Advertisement" in text:
        return response(answer(applies=False))
    if "Quiet note" in text:
        return response(answer())
    if "Broken quote" in text:
        return response(answer(value_1=entry(5.0, "Total: 5,00 EUR")))
    if "Server trouble" in text:
        raise StatusError(500)
    return response(answer())


@dataclass(frozen=True)
class LoggedExtractionApiFactory:
    expected_key: str
    log: Path
    delay: float = 0.0
    answer: Callable[[dict[str, Any]], Any] = sentinel_answer

    def __call__(self, api_key: str) -> ExtractionClient:
        return _LoggedApi(self, api_key == self.expected_key)


class _LoggedApi:
    def __init__(self, factory: LoggedExtractionApiFactory, key_ok: bool) -> None:
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
                        "tools": kwargs.get("tools"),
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
class LeakyExtractionFactory:
    """A factory whose failure message quotes the sentinel and the key, as a library's might."""

    def __call__(self, api_key: str) -> ExtractionClient:
        raise RuntimeError(f"{SENTINEL} {api_key}")


def forbidden_extraction_api(api_key: str) -> ExtractionClient:
    """Stands in for the real client in tests that must never make one."""
    raise AssertionError("A test reached the real OpenAI client factory.")


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text("utf-8").splitlines() if line]
