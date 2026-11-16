"""Shared fakes for the web lookup tests: a task, replies and a fake API object."""

import json
from types import SimpleNamespace as NS
from typing import Any

from ai_data_enrichment.providers.web_lookup import LookupColumn, LookupTask

PAGE = "https://example.org/everest"
PAGE_2 = "https://example.org/everest/climbs"


def mixed_task() -> LookupTask:
    return LookupTask(
        question="Find facts about this mountain.",
        context=(("ctx_1", "Mountain"), ("ctx_2", "Country")),
        columns=(
            LookupColumn(slot="value_1", header="Height", kind="number", unit="m"),
            LookupColumn(slot="value_2", header="First ascent", kind="text"),
            LookupColumn(slot="value_3", header="Climbed in winter", kind="yes_no"),
        ),
    )


def slot(**changes: Any) -> dict[str, Any]:
    base = {
        "value": None,
        "basis": None,
        "sources": [],
        "quote": None,
    }
    return {**base, **changes}


def good_reply() -> dict[str, Any]:
    return {
        "value_1": slot(value=8849, unit="m", basis="stated", sources=[PAGE], quote="8,849 m"),
        "value_2": slot(
            value="1953", basis="concluded", sources=[PAGE, PAGE_2], quote="Hillary and Tenzing"
        ),
        "value_3": slot(value=False, basis="stated", sources=[PAGE_2]),
    }


def response(
    reply: Any,
    *,
    reported: tuple[str, ...] = (PAGE, PAGE_2),
    cited: tuple[str, ...] = (),
    status: str = "completed",
    searches: int = 2,
) -> Any:
    text = reply if isinstance(reply, str) else json.dumps(reply)
    output: list[Any] = [
        NS(
            type="web_search_call",
            action=NS(type="search", sources=[NS(type="url", url=u) for u in reported]),
        )
    ]
    output += [NS(type="web_search_call", action=NS(type="search", sources=[]))] * (searches - 1)
    output.append(
        NS(
            type="message",
            content=[NS(annotations=[NS(type="url_citation", url=u) for u in cited])],
        )
    )
    return NS(
        status=status,
        output=output,
        output_text=text,
        usage=NS(input_tokens=1000, output_tokens=200),
    )


class StatusError(Exception):
    """Looks like the OpenAI client's HTTP error; the body is never read."""

    def __init__(self, status_code: int, body: str = "secret body sk-test") -> None:
        super().__init__(body)
        self.status_code = status_code


class FakeApi:
    """An object with ``responses.create(**kwargs)``; it records every call."""

    def __init__(self, *replies: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._replies = list(replies)

    @property
    def responses(self) -> "FakeApi":
        return self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._replies[min(len(self.calls), len(self._replies)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def empty_reply() -> dict[str, Any]:
    """The reply for ``mixed_task`` in which nothing was found for any slot."""
    return {"value_1": slot(unit=None), "value_2": slot(), "value_3": slot()}


class RowApi(FakeApi):
    """A fake API object whose answer depends on the request: ``choose(kwargs) -> response``.

    Every call is recorded. ``choose`` sees the keyword arguments of ``responses.create``;
    the row's values are in ``kwargs["input"]``.
    """

    def __init__(self, choose: Any, *, api_key: str = "sk-test-secret") -> None:
        super().__init__()
        self._choose = choose
        # Like the OpenAI client, the object carries the key; nothing may copy it out.
        self.api_key = api_key

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._choose(kwargs)
        if isinstance(item, Exception):
            raise item
        return item
