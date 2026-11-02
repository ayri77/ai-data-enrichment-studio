"""Job-scoped HTTP admission, provider pacing, and operation budgets.

The application creates one session per job and injects the same transport
wrappers into all handlers belonging to a provider. No implicit retry or
outbound traffic is enabled by importing this module.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from time import monotonic, sleep

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.retry_transport import RetryingJsonTransport, TransportRetryPolicy


class ProviderRequestPolicy(BaseModel):
    """Finite application-owned limits for one provider during one job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_requests_per_job: StrictInt = Field(ge=0, le=1_000_000)
    max_operations_per_job: StrictInt | None = Field(default=None, ge=0, le=1_000_000)
    min_interval_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    max_total_wait_seconds: float = Field(default=60.0, ge=0, allow_inf_nan=False)

    @field_validator("min_interval_seconds", "max_total_wait_seconds", mode="before")
    @classmethod
    def reject_boolean_durations(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Provider wait durations must not be booleans.")
        return value


class RequestAdmissionDenied(JsonTransportError):
    """An HTTP attempt was rejected before calling the underlying transport."""

    def __init__(self, reason: str) -> None:
        if reason not in ("request_budget", "rate_budget"):
            raise ValueError("Unknown request admission refusal.")
        self.reason = reason
        super().__init__("budget_exceeded")


@dataclass(frozen=True)
class ProviderRequestCounters:
    """Counters for one provider, separate from logical DAG handler metrics."""

    transport_attempts: int = 0
    retry_attempts: int = 0
    requests_blocked_by_budget: int = 0
    rate_limit_waits: int = 0
    rate_limit_wait_seconds: float = 0.0
    retry_wait_seconds: float = 0.0
    retry_after_waits: int = 0
    terminal_transport_failures: int = 0
    operations_started: int = 0
    operations_blocked_by_budget: int = 0


@dataclass
class _ProviderState:
    attempts: int = 0
    retries: int = 0
    blocked: int = 0
    pacing_waits: int = 0
    pacing_seconds: float = 0.0
    retry_seconds: float = 0.0
    next_allowed_at: float | None = None
    operations: int = 0
    operations_blocked: int = 0


class _OperationBudgetHandler:
    """Check provider operation limits only on actual uncached handler calls."""

    def __init__(
        self,
        session: "ProviderRequestSession",
        provider_id: str,
        handler: CapabilityHandler,
    ) -> None:
        self._session = session
        self._provider_id = provider_id
        self._handler = handler

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        if not self._session._admit_operation(self._provider_id):
            return OperationResult(
                status="failure",
                error_code="PROVIDER_OPERATION_BUDGET_EXCEEDED",
                message="Approved provider operation budget exhausted.",
            )
        return self._handler.execute(inputs=inputs, parameters=parameters)


class ProviderRequestSession:
    """One sequential job, shared by every approved handler of each provider."""

    def __init__(
        self,
        policies: Mapping[str, ProviderRequestPolicy],
        *,
        monotonic_clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not policies or any(not provider.strip() for provider in policies):
            raise ValueError("Request policies need nonblank approved provider IDs.")
        self._policies = dict(policies)
        self._states = {provider: _ProviderState() for provider in policies}
        self._retry_wrappers: dict[str, list[RetryingJsonTransport]] = {
            provider: [] for provider in policies
        }
        self._clock = monotonic_clock
        self._sleep = sleeper
        self._wall_clock = wall_clock
        self._job_started = False

    def begin_job(self) -> None:
        """Avoid carrying a per-job rate or budget counter into a second job."""
        if self._job_started:
            raise ValueError("A provider request session cannot be reused across jobs.")
        self._job_started = True

    def _now(self) -> float:
        current = float(self._clock())
        if not isfinite(current):
            raise ValueError("Provider monotonic clock must be finite.")
        return current

    def _wait(self, provider_id: str, duration: float, *, rate: bool) -> None:
        state = self._states[provider_id]
        policy = self._policies[provider_id]
        if duration < 0 or not isfinite(duration):
            raise ValueError("Requested wait must be finite and nonnegative.")
        if state.pacing_seconds + state.retry_seconds + duration > policy.max_total_wait_seconds:
            state.blocked += 1
            raise RequestAdmissionDenied("rate_budget")
        if duration > 0:
            self._sleep(duration)
            if rate:
                state.pacing_waits += 1
                state.pacing_seconds += duration
            else:
                state.retry_seconds += duration

    def _admit_attempt(self, provider_id: str, retry: bool) -> None:
        state = self._states[provider_id]
        policy = self._policies[provider_id]
        if state.attempts >= policy.max_requests_per_job:
            state.blocked += 1
            raise RequestAdmissionDenied("request_budget")
        now = self._now()
        wait = max(0.0, (state.next_allowed_at or now) - now)
        self._wait(provider_id, wait, rate=True)
        # Reserve an attempt before entering the network transport. A failure,
        # HTTP 429 or timeout consumes that attempt.
        state.attempts += 1
        if retry:
            state.retries += 1
        state.next_allowed_at = max(now + wait, self._now()) + policy.min_interval_seconds

    def _admit_operation(self, provider_id: str) -> bool:
        state = self._states[provider_id]
        limit = self._policies[provider_id].max_operations_per_job
        if limit is not None and state.operations >= limit:
            state.operations_blocked += 1
            return False
        state.operations += 1
        return True

    def wrap_handler(self, provider_id: str, handler: CapabilityHandler) -> CapabilityHandler:
        """Provide an optional operation cap outside job-scoped cache wrappers."""
        if provider_id not in self._policies:
            return handler
        return _OperationBudgetHandler(self, provider_id, handler)

    def wrap_transport(
        self,
        provider_id: str,
        transport: JsonTransport,
        *,
        retry_policy: TransportRetryPolicy,
    ) -> RetryingJsonTransport:
        """Create a transport sharing admission state with all sibling handlers."""
        if provider_id not in self._policies:
            raise ValueError(f"No approved request policy for provider {provider_id!r}.")
        wrapper = RetryingJsonTransport(
            transport,
            policy=retry_policy,
            sleeper=lambda delay: self._wait(provider_id, delay, rate=False),
            wall_clock=self._wall_clock,
            before_attempt=lambda retry: self._admit_attempt(provider_id, retry),
        )
        self._retry_wrappers[provider_id].append(wrapper)
        return wrapper

    def snapshot(self) -> dict[str, ProviderRequestCounters]:
        """Capture actual admitted transport attempts, not handler counts."""
        results: dict[str, ProviderRequestCounters] = {}
        for provider_id, state in self._states.items():
            wrappers = self._retry_wrappers[provider_id]
            stats = [wrapper.snapshot_stats() for wrapper in wrappers]
            results[provider_id] = ProviderRequestCounters(
                transport_attempts=state.attempts,
                retry_attempts=state.retries,
                requests_blocked_by_budget=state.blocked,
                rate_limit_waits=state.pacing_waits,
                rate_limit_wait_seconds=state.pacing_seconds,
                retry_wait_seconds=state.retry_seconds,
                retry_after_waits=sum(item.retry_after_waits for item in stats),
                terminal_transport_failures=sum(item.terminal_failures for item in stats),
                operations_started=state.operations,
                operations_blocked_by_budget=state.operations_blocked,
            )
        return results
