"""Bounded per-request retry policy for the existing JsonTransport protocol.

This is the isolated first #36 slice: provider/job-wide budgets and rate
pacing are integrated separately. No network requests occur unless the caller
invokes an injected transport.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from time import sleep
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ai_data_enrichment.providers.http_transport import (
    JsonTransport,
    JsonTransportError,
    TransportFailureKind,
)

_RETRYABLE_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504})


class TransportRetryPolicy(BaseModel):
    """Application-owned hard limits, never populated by an LLM plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: StrictInt = Field(default=3, ge=1, le=8)
    base_backoff_seconds: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    max_backoff_seconds: float = Field(default=4.0, ge=0, allow_inf_nan=False)
    max_retry_after_seconds: float = Field(default=30.0, ge=0, allow_inf_nan=False)
    max_total_retry_delay_seconds: float = Field(default=60.0, ge=0, allow_inf_nan=False)
    retryable_status_codes: frozenset[StrictInt] = Field(
        default_factory=lambda: _RETRYABLE_HTTP_CODES
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("Base backoff cannot exceed maximum backoff.")
        if any(code < 100 or code > 599 for code in self.retryable_status_codes):
            raise ValueError("Retryable HTTP status codes must be between 100 and 599.")
        return self


class RetryExhausted(JsonTransportError):
    """A recoverable transport failure persisted through all permitted attempts."""

    def __init__(self, original: JsonTransportError, *, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(
            cast(TransportFailureKind, original.kind),
            status_code=original.status_code,
            retry_after=original.retry_after,
        )


class RetryDelayLimitExceeded(JsonTransportError):
    """A retry was denied because an explicit safe waiting limit was exceeded."""

    def __init__(
        self,
        original: JsonTransportError,
        *,
        reason: Literal["retry_after_limit", "total_delay_limit"],
    ) -> None:
        self.reason = reason
        super().__init__(
            cast(TransportFailureKind, original.kind),
            status_code=original.status_code,
            retry_after=original.retry_after,
        )


@dataclass(frozen=True)
class RetryTransportStats:
    """Per-wrapper physical transport attempts, independent of DAG operations."""

    transport_attempts: int
    retry_attempts: int
    retry_wait_seconds: float
    retry_after_waits: int
    terminal_failures: int
    rejected_delays: int


def retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    """Parse supported HTTP Retry-After syntax without trusting arbitrary text."""
    if value is None or not value.strip():
        return None
    hint = value.strip()
    if hint.isascii() and hint.isdecimal():
        # Avoid constructing huge integers from untrusted headers.
        if len(hint) > 12:
            return float("inf")
        return float(int(hint))

    try:
        date = parsedate_to_datetime(hint)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if date.tzinfo is None or now.tzinfo is None:
        return None
    seconds = (date.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    if not isfinite(seconds):
        return None
    return max(0.0, seconds)


def retryable_transport_error(error: JsonTransportError, policy: TransportRetryPolicy) -> bool:
    """Retry transport failures only, never domain-level semantic failures."""
    if error.kind in ("timeout", "network"):
        return True
    if error.kind == "http_status":
        return error.status_code in policy.retryable_status_codes
    return False


class RetryingJsonTransport:
    """Composable JsonTransport wrapper; retries one requested HTTP operation."""

    def __init__(
        self,
        transport: JsonTransport,
        *,
        policy: TransportRetryPolicy,
        sleeper: Callable[[float], None] = sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        before_attempt: Callable[[bool], None] | None = None,
    ) -> None:
        self._transport = transport
        self._policy = policy
        self._sleeper = sleeper
        self._wall_clock = wall_clock
        self._before_attempt = before_attempt
        self._attempts = 0
        self._retries = 0
        self._wait_seconds = 0.0
        self._hint_waits = 0
        self._failures = 0
        self._rejected = 0

    def snapshot_stats(self) -> RetryTransportStats:
        """Capture the physical attempts through this wrapper, not HTTP bytes."""
        return RetryTransportStats(
            transport_attempts=self._attempts,
            retry_attempts=self._retries,
            retry_wait_seconds=self._wait_seconds,
            retry_after_waits=self._hint_waits,
            terminal_failures=self._failures,
            rejected_delays=self._rejected,
        )

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        """Make at most max_attempts calls; never bypass a denied wait hint."""
        return self._send(
            lambda: self._transport.get_json(
                url=url, params=params, timeout_seconds=timeout_seconds
            )
        )

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> object:
        """The same bounded attempts for a request that sends a JSON body.

        Only for requests that compute an answer and change nothing at the
        source, so that repeating one is safe.
        """
        post = getattr(self._transport, "post_json", None)
        if not callable(post):
            raise TypeError("The wrapped transport cannot send a JSON body.")
        return self._send(lambda: post(url=url, body=body, timeout_seconds=timeout_seconds))

    def _send(self, call: Callable[[], object]) -> object:
        waited = 0.0
        for index in range(self._policy.max_attempts):
            if self._before_attempt is not None:
                self._before_attempt(index > 0)
            self._attempts += 1
            if index:
                self._retries += 1
            try:
                return call()
            except JsonTransportError as error:
                if not retryable_transport_error(error, self._policy):
                    self._failures += 1
                    raise
                if index + 1 >= self._policy.max_attempts:
                    self._failures += 1
                    if self._policy.max_attempts > 1:
                        raise RetryExhausted(error, attempts=index + 1) from error
                    raise

                delay = min(
                    self._policy.max_backoff_seconds,
                    self._policy.base_backoff_seconds * 2**index,
                )
                hint = (
                    retry_after_seconds(error.retry_after, now=self._wall_clock())
                    if error.kind == "http_status"
                    else None
                )
                if hint is not None:
                    if hint > self._policy.max_retry_after_seconds:
                        self._failures += 1
                        self._rejected += 1
                        raise RetryDelayLimitExceeded(error, reason="retry_after_limit") from error
                    delay = max(delay, hint)

                if waited + delay > self._policy.max_total_retry_delay_seconds:
                    self._failures += 1
                    self._rejected += 1
                    raise RetryDelayLimitExceeded(error, reason="total_delay_limit") from error

                self._sleeper(delay)
                waited += delay
                self._wait_seconds += delay
                if hint is not None and hint >= delay:
                    self._hint_waits += 1

        # The loop cannot fall through when max_attempts >= 1.
        raise AssertionError("Unreachable retry transport state.")
