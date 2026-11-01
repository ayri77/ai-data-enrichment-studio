"""Deterministic, offline retry classification and transport wrapper tests."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from pydantic import ValidationError

from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.retry_transport import (
    RetryDelayLimitExceeded,
    RetryingJsonTransport,
    TransportRetryPolicy,
    retry_after_seconds,
    retryable_transport_error,
)


class ScriptedTransport:
    """Count each actual get_json call and replay controlled outcomes."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls.append((url, dict(params), timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeTime:
    def __init__(self) -> None:
        self.wall = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.wall += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.wall


def wrap(
    transport: ScriptedTransport,
    *,
    policy: TransportRetryPolicy | None = None,
    clock: FakeTime | None = None,
) -> RetryingJsonTransport:
    time = clock or FakeTime()
    return RetryingJsonTransport(
        transport,
        policy=policy or TransportRetryPolicy(),
        sleeper=time.sleep,
        wall_clock=time.now,
    )


def invoke(wrapper: RetryingJsonTransport) -> object:
    return wrapper.get_json(
        url="https://approved.test/search",
        params={"country": "DE"},
        timeout_seconds=1.5,
    )


@pytest.mark.parametrize(
    "kind",
    ["timeout", "network"],
)
def test_transient_transport_error_recovers_and_preserves_parameters(kind: str) -> None:
    transport = ScriptedTransport(
        [JsonTransportError(kind), {"ok": True}]
    )
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    assert invoke(retry) == {"ok": True}
    assert len(transport.calls) == 2
    assert transport.calls[0] == transport.calls[1]
    assert clock.waits == [0.25]
    stats = retry.snapshot_stats()
    assert stats.transport_attempts == 2
    assert stats.retry_attempts == 1
    assert stats.retry_wait_seconds == 0.25
    assert stats.terminal_failures == 0


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("http_status", 400),
        ("http_status", 401),
        ("http_status", 403),
        ("http_status", 404),
        ("http_status", 418),
        ("invalid_json", None),
        ("http_status", None),
    ],
)
def test_permanent_transport_error_never_retries(kind: str, status: int | None) -> None:
    transport = ScriptedTransport([JsonTransportError(kind, status_code=status)])
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    with pytest.raises(JsonTransportError):
        invoke(retry)
    assert len(transport.calls) == 1
    assert clock.waits == []
    assert retry.snapshot_stats().terminal_failures == 1


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_http_status_recovers(status: int) -> None:
    transport = ScriptedTransport(
        [JsonTransportError("http_status", status_code=status), {"ok": 1}]
    )
    retry = wrap(transport)
    assert invoke(retry) == {"ok": 1}
    assert retry.snapshot_stats().retry_attempts == 1


def test_policy_can_disable_http_retry_code_without_disabling_network_retry() -> None:
    policy = TransportRetryPolicy(retryable_status_codes=frozenset({503}))
    assert (
        retryable_transport_error(JsonTransportError("http_status", status_code=429), policy)
        is False
    )
    assert retryable_transport_error(JsonTransportError("network"), policy) is True


def test_total_attempt_bound_includes_initial_request_and_exponential_backoff() -> None:
    transport = ScriptedTransport([JsonTransportError("timeout")] * 3)
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    with pytest.raises(JsonTransportError) as failure:
        invoke(retry)
    assert failure.value.kind == "timeout"
    assert len(transport.calls) == 3
    assert clock.waits == [0.25, 0.5]
    assert retry.snapshot_stats().retry_attempts == 2
    assert retry.snapshot_stats().terminal_failures == 1


def test_one_attempt_policy_has_no_sleep() -> None:
    transport = ScriptedTransport([JsonTransportError("network")])
    clock = FakeTime()
    retry = wrap(transport, clock=clock, policy=TransportRetryPolicy(max_attempts=1))
    with pytest.raises(JsonTransportError):
        invoke(retry)
    assert len(transport.calls) == 1
    assert clock.waits == []


def test_backoff_is_bounded_by_maximum() -> None:
    transport = ScriptedTransport([JsonTransportError("timeout")] * 4)
    clock = FakeTime()
    retry = wrap(
        transport,
        clock=clock,
        policy=TransportRetryPolicy(
            max_attempts=4,
            base_backoff_seconds=1.0,
            max_backoff_seconds=1.5,
        ),
    )
    with pytest.raises(JsonTransportError):
        invoke(retry)
    assert clock.waits == [1.0, 1.5, 1.5]


def test_retry_after_delta_seconds_and_http_date() -> None:
    now = FakeTime().now()
    assert retry_after_seconds(" 7 ", now=now) == 7.0
    assert retry_after_seconds(format_datetime(now + timedelta(seconds=9)), now=now) == 9.0
    assert retry_after_seconds(format_datetime(now - timedelta(seconds=2)), now=now) == 0.0


@pytest.mark.parametrize("raw", ["", "abc", "-1", "1.5", "2026-01-01", "Sunday"])
def test_invalid_retry_after_is_ignored_in_favor_of_bounded_backoff(raw: str) -> None:
    now = FakeTime().now()
    assert retry_after_seconds(raw, now=now) is None
    transport = ScriptedTransport(
        [JsonTransportError("http_status", status_code=503, retry_after=raw), {"ok": 1}]
    )
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    assert invoke(retry) == {"ok": 1}
    assert clock.waits == [0.25]


def test_retry_after_delay_is_honored_within_bound() -> None:
    transport = ScriptedTransport(
        [JsonTransportError("http_status", status_code=429, retry_after="4"), {"ok": 1}]
    )
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    assert invoke(retry) == {"ok": 1}
    assert clock.waits == [4.0]
    assert retry.snapshot_stats().retry_after_waits == 1


def test_server_hint_over_ceiling_causes_explicit_failure_without_further_attempts() -> None:
    transport = ScriptedTransport(
        [JsonTransportError("http_status", status_code=429, retry_after="200")]
    )
    clock = FakeTime()
    retry = wrap(
        transport,
        clock=clock,
        policy=TransportRetryPolicy(max_retry_after_seconds=10.0),
    )
    with pytest.raises(RetryDelayLimitExceeded) as failure:
        invoke(retry)
    assert failure.value.reason == "retry_after_limit"
    assert failure.value.status_code == 429
    assert retry.snapshot_stats().rejected_delays == 1
    assert len(transport.calls) == 1
    assert clock.waits == []


def test_untrusted_huge_hint_cannot_bypass_delay_ceiling() -> None:
    assert retry_after_seconds("9" * 1000, now=FakeTime().now()) == float("inf")
    retry = wrap(
        ScriptedTransport(
            [JsonTransportError("http_status", status_code=503, retry_after="9" * 1000)]
        )
    )
    with pytest.raises(RetryDelayLimitExceeded):
        invoke(retry)


def test_total_wait_budget_denies_another_retry() -> None:
    transport = ScriptedTransport([JsonTransportError("timeout")] * 3)
    clock = FakeTime()
    retry = wrap(
        transport,
        clock=clock,
        policy=TransportRetryPolicy(max_total_retry_delay_seconds=0.6),
    )
    with pytest.raises(RetryDelayLimitExceeded) as failure:
        invoke(retry)
    assert failure.value.reason == "total_delay_limit"
    assert len(transport.calls) == 2
    assert clock.waits == [0.25]
    assert retry.snapshot_stats().retry_attempts == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"max_attempts": 0},
        {"max_attempts": 9},
        {"max_attempts": True},
        {"base_backoff_seconds": -1},
        {"max_backoff_seconds": -1},
        {"max_retry_after_seconds": float("inf")},
        {"max_total_retry_delay_seconds": float("nan")},
        {"base_backoff_seconds": 2.0, "max_backoff_seconds": 1.0},
        {"retryable_status_codes": frozenset({99})},
        {"retryable_status_codes": frozenset({True})},
        {"unexpected_field": 1},
    ],
)
def test_invalid_policy_is_rejected_before_any_transport_call(
    invalid: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TransportRetryPolicy.model_validate(invalid)


def test_success_does_not_retry_and_statistics_accumulate_across_calls() -> None:
    transport = ScriptedTransport(
        [{"ok": 1}, JsonTransportError("timeout"), {"ok": 2}]
    )
    clock = FakeTime()
    retry = wrap(transport, clock=clock)
    assert invoke(retry) == {"ok": 1}
    assert invoke(retry) == {"ok": 2}
    assert len(transport.calls) == 3
    stats = retry.snapshot_stats()
    assert stats.transport_attempts == 3
    assert stats.retry_attempts == 1
    assert stats.terminal_failures == 0


def test_semantic_result_is_returned_untouched_without_retries() -> None:
    # Successfully decoded JSON may represent NOT_FOUND; the provider validates it.
    payload = {"results": [], "status": "not_found"}
    transport = ScriptedTransport([payload])
    retry = wrap(transport)
    assert invoke(retry) is payload
    assert len(transport.calls) == 1
