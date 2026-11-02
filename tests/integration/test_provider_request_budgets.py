"""Verify shared request admission, rate pacing, retries and cache bypass offline."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.request_policy import (
    ProviderRequestPolicy,
    ProviderRequestSession,
    RequestAdmissionDenied,
)
from ai_data_enrichment.providers.retry_transport import TransportRetryPolicy


class FakeTime:
    """Monotonic and UTC clocks that advance only through injected sleep."""

    def __init__(self) -> None:
        self.seconds = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.seconds

    def utc(self) -> datetime:
        return datetime.fromtimestamp(1730000000 + self.seconds, tz=UTC)

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.seconds += duration


class CountingTransport:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.outcomes = list(outcomes) if outcomes is not None else None

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls.append((url, dict(params)))
        if self.outcomes is None:
            return {"ok": True}
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def session(
    clock: FakeTime,
    *,
    max_requests: int = 10,
    min_interval: float = 0.0,
    max_wait: float = 60.0,
    max_operations: int | None = None,
) -> ProviderRequestSession:
    return ProviderRequestSession(
        {
            "wikidata": ProviderRequestPolicy(
                max_requests_per_job=max_requests,
                min_interval_seconds=min_interval,
                max_total_wait_seconds=max_wait,
                max_operations_per_job=max_operations,
            )
        },
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
        wall_clock=clock.utc,
    )


def request(transport: JsonTransport) -> object:
    return transport.get_json(
        url="https://approved.example/test",
        params={"name": "mountain"},
        timeout_seconds=1.0,
    )


def no_retry() -> TransportRetryPolicy:
    return TransportRetryPolicy(max_attempts=1)


def test_provider_rate_limit_shared_across_transport_wrappers() -> None:
    clock = FakeTime()
    state = session(clock, min_interval=2.5)
    first = CountingTransport()
    second = CountingTransport()
    a = state.wrap_transport("wikidata", first, retry_policy=no_retry())
    b = state.wrap_transport("wikidata", second, retry_policy=no_retry())

    request(a)
    request(b)
    request(a)

    assert clock.sleeps == [2.5, 2.5]
    assert len(first.calls) == 2
    assert len(second.calls) == 1
    stats = state.snapshot()["wikidata"]
    assert stats.transport_attempts == 3
    assert stats.retry_attempts == 0
    assert stats.rate_limit_waits == 2
    assert stats.rate_limit_wait_seconds == 5.0


def test_request_budget_counts_failed_attempts_and_blocks_retries() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=2)
    backend = CountingTransport(
        [JsonTransportError("timeout"), JsonTransportError("timeout"), {"ok": True}]
    )
    transport = state.wrap_transport(
        "wikidata",
        backend,
        retry_policy=TransportRetryPolicy(max_attempts=3, base_backoff_seconds=0.1),
    )
    with pytest.raises(RequestAdmissionDenied) as blocked:
        request(transport)

    assert blocked.value.reason == "request_budget"
    assert len(backend.calls) == 2
    stats = state.snapshot()["wikidata"]
    assert stats.transport_attempts == 2
    assert stats.retry_attempts == 1
    assert stats.requests_blocked_by_budget == 1
    assert stats.retry_wait_seconds == pytest.approx(0.3)


def test_rate_wait_budget_refuses_before_network() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=10, min_interval=5, max_wait=2)
    backend = CountingTransport()
    transport = state.wrap_transport("wikidata", backend, retry_policy=no_retry())
    request(transport)
    with pytest.raises(RequestAdmissionDenied) as failure:
        request(transport)
    assert failure.value.reason == "rate_budget"
    assert len(backend.calls) == 1
    stats = state.snapshot()["wikidata"]
    assert stats.requests_blocked_by_budget == 1
    assert stats.rate_limit_waits == 0


def test_retry_waits_and_rate_waits_share_total_job_budget() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=5, min_interval=2.0, max_wait=1.5)
    backend = CountingTransport([JsonTransportError("timeout"), {"ok": True}])
    transport = state.wrap_transport(
        "wikidata",
        backend,
        retry_policy=TransportRetryPolicy(max_attempts=3, base_backoff_seconds=1),
    )
    with pytest.raises(RequestAdmissionDenied) as denied:
        request(transport)
    assert denied.value.reason == "rate_budget"
    assert len(backend.calls) == 1
    stats = state.snapshot()["wikidata"]
    assert stats.retry_wait_seconds == 1.0
    assert stats.transport_attempts == 1
    assert stats.requests_blocked_by_budget == 1


def test_counters_of_different_providers_are_isolated() -> None:
    clock = FakeTime()
    state = ProviderRequestSession(
        {
            "wikidata": ProviderRequestPolicy(max_requests_per_job=1),
            "open-meteo": ProviderRequestPolicy(max_requests_per_job=2),
        },
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
        wall_clock=clock.utc,
    )
    wikidata = CountingTransport()
    meteo = CountingTransport()
    w = state.wrap_transport("wikidata", wikidata, retry_policy=no_retry())
    m = state.wrap_transport("open-meteo", meteo, retry_policy=no_retry())
    request(w)
    request(m)
    request(m)
    with pytest.raises(RequestAdmissionDenied):
        request(w)
    counts = state.snapshot()
    assert (counts["wikidata"].transport_attempts, counts["open-meteo"].transport_attempts) == (
        1,
        2,
    )
    assert counts["open-meteo"].requests_blocked_by_budget == 0


def test_denied_request_maps_to_explicit_failure_without_exposing_params() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=0)
    transport = state.wrap_transport("wikidata", CountingTransport(), retry_policy=no_retry())
    try:
        request(transport)
    except JsonTransportError as exc:
        result = transport_failure_result(exc, provider_label="Wikidata")
    else:
        raise AssertionError("Request should be blocked.")
    assert result.status == "failure"
    assert result.error_code == "REQUEST_BUDGET_EXCEEDED"
    assert "mountain" not in result.message


class FakeResolver:
    def __init__(self, transport: JsonTransport) -> None:
        self.transport = transport
        self.calls = 0

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls += 1
        try:
            # A single logical handler invocation can require two HTTP attempts.
            for _ in range(2):
                self.transport.get_json(
                    url="https://approved.example/entity",
                    params={"name": str(inputs["name"])},
                    timeout_seconds=1.0,
                )
        except JsonTransportError as error:
            return transport_failure_result(error, provider_label="Wikidata")
        return OperationResult(
            status="success",
            value=EntityRef(source="wikidata", entity_id="Q513"),
            evidence=(Evidence(source="wikidata", reference="Q513:P31"),),
        )


class FakeLookup:
    def __init__(self, transport: JsonTransport) -> None:
        self.transport = transport
        self.calls = 0

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls += 1
        try:
            self.transport.get_json(
                url="https://approved.example/elevation",
                params={"id": "Q513"},
                timeout_seconds=1.0,
            )
        except JsonTransportError as error:
            return transport_failure_result(error, provider_label="Wikidata")
        return OperationResult(
            status="success",
            value=Quantity(value=8848.86, unit="m"),
            evidence=(Evidence(source="wikidata", reference="Q513:P2044"),),
        )


def job_plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(kind="column", name="mountain"),
                    "country": ColumnReference(kind="column", name="country"),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="lookup",
                capability="lookup_property",
                inputs={"entity": StepOutputReference(kind="step_output", step_id="resolve")},
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[OutputMapping(step_id="lookup", value_field="value", target_column="elevation")],
        assumptions=[],
    )


def job_input(index: int, mountain: str) -> InputRecord:
    return InputRecord(record_id=f"row-{index}", values={"mountain": mountain, "country": "Nepal"})


def test_job_counts_physical_requests_and_cache_hits_across_rows() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=3)
    backend = CountingTransport()
    resolver = FakeResolver(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    lookup = FakeLookup(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    job = JobExecutor(
        handlers={"resolve_entity": resolver, "lookup_property": lookup},
        selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
        configuration_ids={"resolve_entity": "fixture-v1", "lookup_property": "fixture-v1"},
        policy=JobExecutionPolicy(
            max_rows=10,
            batch_size=1,
            memoizable_providers=frozenset(
                {("resolve_entity", "wikidata"), ("lookup_property", "wikidata")}
            ),
        ),
        request_session=state,
    ).execute(
        job_plan(),
        [job_input(0, "Everest"), job_input(1, "K2"), job_input(2, "Everest")],
    )
    assert [x.row_result.steps[-1].result.status for x in job.records] == [
        "success",
        "failure",
        "success",
    ]
    assert job.records[1].row_result.steps[0].result.error_code == "REQUEST_BUDGET_EXCEEDED"
    assert job.records[1].row_result.steps[1].result.error_code == "DEPENDENCY_FAILED"
    assert len(backend.calls) == 3
    assert resolver.calls == 2
    assert lookup.calls == 1
    assert job.metrics.executed_handler_calls == 3
    assert job.metrics.reused_operations == 2
    assert job.provider_requests["wikidata"].transport_attempts == 3
    assert job.provider_requests["wikidata"].requests_blocked_by_budget == 1
    assert job.provider_requests["wikidata"].operations_started == 3


def test_operation_limit_denies_uncached_calls_but_not_memory_hits() -> None:
    clock = FakeTime()
    state = session(clock, max_requests=100, max_operations=1)
    backend = CountingTransport()
    resolver = FakeResolver(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    lookup = FakeLookup(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    job = JobExecutor(
        handlers={"resolve_entity": resolver, "lookup_property": lookup},
        selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
        configuration_ids={"resolve_entity": "fixture-v1", "lookup_property": "fixture-v1"},
        policy=JobExecutionPolicy(
            memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
        ),
        request_session=state,
    ).execute(job_plan(), [job_input(0, "Everest"), job_input(1, "Everest")])
    assert resolver.calls == 1
    assert lookup.calls == 0
    assert job.metrics.executed_handler_calls == 3
    assert job.metrics.reused_operations == 1
    assert job.provider_requests["wikidata"].operations_started == 1
    assert job.provider_requests["wikidata"].operations_blocked_by_budget == 2
    assert len(backend.calls) == 2
    assert job.records[1].row_result.steps[-1].result.error_code == (
        "PROVIDER_OPERATION_BUDGET_EXCEEDED"
    )


def test_persistent_hit_uses_zero_network_attempts(tmp_path: Path) -> None:
    path = tmp_path / "provider-reuse.sqlite3"
    inputs = [job_input(0, "Everest")]
    for iteration in range(2):
        clock = FakeTime()
        state = session(clock, max_requests=3)
        backend = CountingTransport()
        resolver = FakeResolver(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
        lookup = FakeLookup(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
        with SQLiteOperationCache(path, clock=lambda: 1000.0) as cache:
            result = JobExecutor(
                handlers={"resolve_entity": resolver, "lookup_property": lookup},
                selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
                configuration_ids={"resolve_entity": "fixture-v1", "lookup_property": "fixture-v1"},
                capability_versions={"resolve_entity": "1.0", "lookup_property": "1.0"},
                provider_versions={"resolve_entity": "fixture-v1", "lookup_property": "fixture-v1"},
                persistent_cache=cache,
                policy=JobExecutionPolicy(
                    memoizable_providers=frozenset(
                        {("resolve_entity", "wikidata"), ("lookup_property", "wikidata")}
                    ),
                    persistent_cache_ttls={
                        ("resolve_entity", "wikidata"): 60.0,
                        ("lookup_property", "wikidata"): 60.0,
                    },
                ),
                request_session=state,
            ).execute(job_plan(), inputs)
        assert result.records[0].row_result.steps[-1].result.status == "success"
        assert result.provider_requests["wikidata"].transport_attempts == (
            3 if iteration == 0 else 0
        )
        assert result.metrics.persistent_cache_hits == (0 if iteration == 0 else 2)


def test_request_session_is_single_use_across_jobs() -> None:
    clock = FakeTime()
    state = session(clock)
    backend = CountingTransport()
    resolver = FakeResolver(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    lookup = FakeLookup(state.wrap_transport("wikidata", backend, retry_policy=no_retry()))
    executor = JobExecutor(
        handlers={"resolve_entity": resolver, "lookup_property": lookup},
        selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
        configuration_ids={"resolve_entity": "fixture-v1", "lookup_property": "fixture-v1"},
        policy=JobExecutionPolicy(),
        request_session=state,
    )
    executor.execute(job_plan(), [job_input(0, "Everest")])
    with pytest.raises(ValueError, match="cannot be reused"):
        executor.execute(job_plan(), [job_input(1, "Everest")])


@pytest.mark.parametrize(
    "bad",
    [
        {"max_requests_per_job": -1},
        {"max_requests_per_job": True},
        {"max_operations_per_job": -1, "max_requests_per_job": 1},
        {"min_interval_seconds": -1, "max_requests_per_job": 1},
        {"min_interval_seconds": True, "max_requests_per_job": 1},
        {"max_total_wait_seconds": float("inf"), "max_requests_per_job": 1},
        {"max_total_wait_seconds": False, "max_requests_per_job": 1},
        {"unknown": "field", "max_requests_per_job": 1},
    ],
)
def test_invalid_provider_request_policy_fails_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProviderRequestPolicy.model_validate(bad)
