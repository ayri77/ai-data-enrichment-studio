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
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.retry_after = retry_after
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
            ) from exc
        except TimeoutError as exc:
            raise JsonTransportError("timeout") from exc
        except OSError as exc:
            raise JsonTransportError("network") from exc
