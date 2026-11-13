"""Reusable HTTP JSON transport and typed transport failures."""

import json
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TransportFailureKind = Literal[
    "timeout", "network", "http_status", "invalid_json", "budget_exceeded"
]


class JsonTransportError(Exception):
    """A transport failure with metadata for future retry policies.

    The exception message deliberately contains no request URL or credentials.
    """

    def __init__(
        self,
        kind: TransportFailureKind,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
        payload: object = None,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.retry_after = retry_after
        # What a source said in the body of an error answer, when it was JSON.
        # Sources state why they refused there; it is never part of the message.
        self.payload = payload
        super().__init__(f"JSON transport failure: {kind}.")


class JsonTransport(Protocol):
    """Fetch decoded JSON independently of the HTTP implementation."""

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


class JsonPostTransport(Protocol):
    """Send a JSON body and fetch decoded JSON, for sources that compute an answer."""

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> object: ...


# An error answer is read only this far; a source's reason for refusing is short.
MAX_ERROR_BODY_BYTES = 4096


def _error_payload(error: HTTPError) -> object:
    """The JSON body of an error answer, or None when there is none to read."""
    try:
        return json.loads(error.read(MAX_ERROR_BODY_BYTES))
    except (ValueError, OSError):
        return None


class UrllibJsonTransport:
    """Fetch JSON using urllib, with optional application-controlled headers."""

    def __init__(self, *, headers: Mapping[str, str] | None = None) -> None:
        self._headers = dict(headers or {})

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        query_string = urlencode(params)
        request_url = f"{url}?{query_string}" if query_string else url
        request = Request(request_url, headers=self._headers) if self._headers else request_url
        return self._fetch(request, timeout_seconds, read_error_body=False)

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> object:
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                **self._headers,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )
        return self._fetch(request, timeout_seconds, read_error_body=True)

    def _fetch(self, request: Request | str, timeout_seconds: float, *, read_error_body: bool):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                try:
                    return json.loads(response.read())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise JsonTransportError("invalid_json") from exc
        except HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise JsonTransportError(
                "http_status",
                status_code=exc.code,
                retry_after=retry_after,
                payload=_error_payload(exc) if read_error_body else None,
            ) from exc
        except TimeoutError as exc:
            raise JsonTransportError("timeout") from exc
        except OSError as exc:
            raise JsonTransportError("network") from exc
