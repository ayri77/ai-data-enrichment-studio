"""Shared offline JSON transport double for provider tests."""

from collections.abc import Mapping


class StubJsonTransport:
    """Return a fixed payload or exception while recording requests."""

    def __init__(self, payload: object, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.requests.append((url, dict(params), timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.payload
