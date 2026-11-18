"""Fakes for the Gmail source: an HTTP session with canned answers and a fake sign-in.

Nothing here imports ``google.*`` and nothing touches the network. The session has a ``get``
method only, so any other request method fails at once.
"""

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def raw_of(path: Path) -> str:
    """A file as Gmail gives it in ``raw``: base64url, without padding."""
    return base64.urlsafe_b64encode(path.read_bytes()).decode().rstrip("=")


class Reply:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


Handler = Callable[[str, dict[str, Any]], Reply | Exception]


class FakeSession:
    """``get(url, params, timeout)``; the handler decides the answer from path and params."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = 0) -> Reply:
        assert url.startswith(BASE + "/"), url
        assert timeout > 0
        path = url.removeprefix(BASE + "/")
        self.calls.append((path, dict(params or {})))
        answer = self._handler(path, dict(params or {}))
        if isinstance(answer, Exception):
            raise answer
        return answer


class MailboxSession(FakeSession):
    """A mailbox: ``messages`` maps id to raw text; ``page`` ids per listing page."""

    def __init__(
        self, messages: dict[str, str], *, page: int = 2, address: str = "owner@example.org"
    ) -> None:
        self.messages = messages
        self.page = page
        self.address = address
        super().__init__(self._answer)

    def _answer(self, path: str, params: dict[str, Any]) -> Reply:
        if path == "profile":
            return Reply(200, {"emailAddress": self.address})
        if path == "messages":
            ids = list(self.messages)
            start = int(params.get("pageToken") or 0)
            chunk = ids[start : start + self.page]
            body: dict[str, Any] = {"messages": [{"id": i, "threadId": "t"} for i in chunk]}
            if start + self.page < len(ids):
                body["nextPageToken"] = str(start + self.page)
            return Reply(200, body)
        identifier = path.removeprefix("messages/")
        if identifier in self.messages:
            return Reply(200, {"id": identifier, "raw": self.messages[identifier]})
        return Reply(404, {"error": {"code": 404}})


class FakeCredentials:
    def __init__(self, *, valid: bool = True, refresh_token: str | None = "r", stamp: str = "t0"):
        self.valid = valid
        self.refresh_token = refresh_token
        self.stamp = stamp

    def to_json(self) -> str:
        return f'{{"stamp": "{self.stamp}"}}'


class FakeBackend:
    """Stands for ``GoogleBackend``: records what happens, answers as told."""

    def __init__(self, *, stored: FakeCredentials | None = None, refresh_fails: bool = False):
        self.auth_errors: tuple[type[BaseException], ...] = ()
        self.stored = stored
        self.refresh_fails = refresh_fails
        self.events: list[str] = []

    def credentials_from_json(self, text: str) -> Any:
        self.events.append("load")
        if self.stored is None:
            raise ValueError("not usable")
        return self.stored

    def refresh(self, credentials: Any) -> None:
        from ai_data_enrichment.mail.gmail import RefreshFailed

        self.events.append("refresh")
        if self.refresh_fails:
            raise RefreshFailed
        credentials.valid = True
        credentials.stamp = "refreshed"

    def sign_in(self, client_file: Path) -> Any:
        self.events.append(f"sign_in:{client_file.name}")
        return FakeCredentials(stamp="signed-in")

    def session(self, credentials: Any) -> Any:
        raise AssertionError("not used here")
