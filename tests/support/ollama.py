"""A fake HTTP opener for the Ollama client tests: no network, every request recorded."""

import io
import json
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from typing import Any


class FakeReply:
    """What ``opener.open`` returns: a context manager with ``read``."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "FakeReply":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


@dataclass(frozen=True)
class Call:
    method: str
    url: str
    body: dict[str, Any] | None
    timeout: float | None
    headers: dict[str, str]


def http_error(
    status: int, body: str = "secret body text from the server"
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:11434/x", status, "msg", Message(), io.BytesIO(body.encode())
    )


class FakeOpener:
    """Answers each request with the next item: a dict (JSON), bytes, or an exception to raise.

    ``choose``, when given, is called with the ``Call`` instead and returns such an item.
    """

    def __init__(self, *items: Any, choose: Callable[[Call], Any] | None = None) -> None:
        self.calls: list[Call] = []
        self._items = list(items)
        self._choose = choose

    def open(self, fullurl: Any, data: Any = None, timeout: Any = None) -> FakeReply:
        request = fullurl
        raw = request.data
        call = Call(
            request.get_method(),
            request.full_url,
            json.loads(raw) if raw else None,
            timeout,
            {key.lower(): value for key, value in request.header_items()},
        )
        self.calls.append(call)
        item = (
            self._choose(call)
            if self._choose
            else self._items[min(len(self.calls), len(self._items)) - 1]
        )
        if isinstance(item, Exception):
            raise item
        return FakeReply(item if isinstance(item, bytes) else json.dumps(item).encode())


def chat_payload(
    content: Any,
    *,
    prompt: int = 700,
    evals: int = 90,
    done_reason: str = "stop",
    thinking: str | None = None,
    cached: int | None = None,
) -> dict[str, Any]:
    """The body of a non-streaming ``/api/chat`` answer."""
    text = content if isinstance(content, str) else json.dumps(content)
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if thinking is not None:
        message["thinking"] = thinking
    payload: dict[str, Any] = {
        "model": "m",
        "created_at": "2026-11-19T10:00:00Z",
        "message": message,
        "done": True,
        "done_reason": done_reason,
        "total_duration": 1,
        "prompt_eval_count": prompt,
        "eval_count": evals,
    }
    if cached is not None:
        payload["prompt_eval_cached_count"] = cached
    return payload
