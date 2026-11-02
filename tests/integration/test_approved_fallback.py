"""Offline acceptance checks for registered, semantically approved provider fallback."""

import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_data_enrichment.capabilities.definitions import get_geocode_spec
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.fallback import ApprovedFallback
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import Evidence, GeoPoint, OperationResult, RuntimeValue
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.providers.request_policy import (
    ProviderRequestPolicy,
    ProviderRequestSession,
)
from ai_data_enrichment.providers.retry_transport import (
    RetryingJsonTransport,
    TransportRetryPolicy,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator


class FakeTransport:
    """Count only admitted physical get_json calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        self.calls += 1
        if self.fail:
            raise JsonTransportError("http_status", status_code=503)
        return {"latitude": 53.55, "longitude": 10.0}


class Geocoder:
    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        source: str,
        mode: str = "network",
    ) -> None:
        self.transport = transport
        self.source = source
        self.mode = mode
        self.calls = 0

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls += 1
        if self.mode in ("NOT_FOUND", "AMBIGUOUS", "INVALID_INPUT", "PROVIDER_ERROR"):
            return OperationResult(
                status="failure",
                error_code=self.mode,
                message="Explicit source failure.",
            )
        if self.transport is not None:
            try:
                self.transport.get_json(
                    url="https://approved.test/location",
                    params={"city": str(inputs["city"])},
                    timeout_seconds=1.0,
                )
            except JsonTransportError as error:
                return transport_failure_result(error, provider_label=self.source)

        return OperationResult(
            status="success",
            value=GeoPoint(latitude=53.55, longitude=10.0),
            evidence=(
                (Evidence(source=self.source, reference="location/101"),)
                if self.mode != "no_evidence"
                else ()
            ),
        )


def make_plan() -> EnrichmentPlan:
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="geo",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city"),
                    "country": ColumnReference(kind="column", name="country"),
                },
                parameters={},
            )
        ],
        outputs=[OutputMapping(step_id="geo", value_field="value", target_column="point")],
        assumptions=[],
    )
    capability = CapabilityRegistry()
    capability.register(get_geocode_spec())
    profile = DatasetProfile(
        dataset_id="offline-geocodes",
        name="Registered fallback test",
        columns=[
            ColumnProfile(name="city", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="country", inferred_type=PrimitiveType.TEXT, null_count=0),
        ],
    )
    validation = PlanValidator(registry=capability, dataset_profile=profile).validate(plan)
    assert validation.valid, validation.errors
    return plan


def rows(amount: int = 1) -> list[InputRecord]:
    return [
        InputRecord(record_id=f"row-{index}", values={"city": "Hamburg", "country": "Germany"})
        for index in range(amount)
    ]


def setup(
    *,
    primary_mode: str = "network",
    fallback_mode: str = "network",
    primary_quota: int = 0,
    fallback_quota: int = 10,
    cache: SQLiteOperationCache | None = None,
    approved: ApprovedFallback | None = None,
    registry_backup: bool = True,
    contract: str = "strict-city-country-point-v1",
    mismatch: bool = False,
    fallback_enabled: bool = True,
) -> tuple[JobExecutor, Geocoder, Geocoder, FakeTransport, FakeTransport]:
    capability_registry = CapabilityRegistry()
    capability_registry.register(get_geocode_spec())
    provider_registry = ProviderRegistry(capability_registry)

    primary_http = FakeTransport()
    backup_http = FakeTransport()
    session = ProviderRequestSession(
        {
            "primary": ProviderRequestPolicy(max_requests_per_job=primary_quota),
            "backup": ProviderRequestPolicy(max_requests_per_job=fallback_quota),
        }
    )
    no_retry = TransportRetryPolicy(max_attempts=1)
    primary = Geocoder(
        source="primary",
        mode=primary_mode,
        transport=session.wrap_transport("primary", primary_http, retry_policy=no_retry),
    )
    backup = Geocoder(
        source="backup",
        mode=fallback_mode,
        transport=session.wrap_transport("backup", backup_http, retry_policy=no_retry),
    )
    provider_registry.register(capability_id="geocode", provider_id="primary", handler=primary)
    if registry_backup:
        provider_registry.register(capability_id="geocode", provider_id="backup", handler=backup)

    policy = JobExecutionPolicy(
        max_rows=1000,
        batch_size=37,
        max_memoized_results=10,
        memoizable_providers=frozenset(
            {("geocode", "primary"), ("geocode", "backup")}
        ),
        persistent_cache_ttls=(
            {("geocode", "primary"): 60.0, ("geocode", "backup"): 60.0}
            if cache is not None
            else {}
        ),
    )
    selected = {"geocode": "primary"}
    rule = approved or ApprovedFallback(
        primary_provider_id="primary",
        fallback_provider_id="backup",
        compatibility_contract_id=contract,
        fallback_configuration_id="backup-config-v1",
        fallback_provider_version="backup-v1",
        fallback_evidence_source="backup",
        trigger_error_codes=frozenset(
            {
                "RETRY_EXHAUSTED",
                "REQUEST_BUDGET_EXCEEDED",
                "RATE_BUDGET_EXCEEDED",
                "PROVIDER_OPERATION_BUDGET_EXCEEDED",
            }
        ),
    )
    executor = JobExecutor(
        handlers=provider_registry.resolve_handlers(
            capability_ids=["geocode"], selected_providers=selected
        ),
        selected_providers=selected,
        configuration_ids={"geocode": "primary-config-v1"},
        capability_versions={"geocode": "1.0"},
        provider_versions={"geocode": "primary-v1"},
        persistent_cache=cache,
        policy=policy,
        request_session=session,
        provider_registry=provider_registry,
        approved_fallbacks={"geocode": rule} if fallback_enabled else {},
        semantic_contracts={
            ("geocode", "primary"): contract,
            ("geocode", "backup"): "incompatible-v2" if mismatch else contract,
        },
    )
    return executor, primary, backup, primary_http, backup_http


def test_primary_budget_exhaustion_uses_only_registered_compatible_alternate() -> None:
    executor, primary, backup, primary_http, backup_http = setup()
    outcome = executor.execute(make_plan(), rows(3))

    assert [record.row_result.steps[0].result.status for record in outcome.records] == [
        "success",
        "success",
        "success",
    ]
    assert primary.calls == 3
    assert backup.calls == 1
    assert primary_http.calls == 0
    assert backup_http.calls == 1
    assert outcome.metrics.fallback_attempts == 3
    assert outcome.metrics.fallback_successes == 3
    assert outcome.metrics.logical_operations == 6
    assert outcome.metrics.reused_operations == 2
    assert outcome.provider_requests["primary"].requests_blocked_by_budget == 3
    assert outcome.provider_requests["backup"].transport_attempts == 1
    result = outcome.records[0].row_result.steps[0].result
    assert result.evidence == (Evidence(source="backup", reference="location/101"),)


def test_persistent_fallback_is_keyed_only_to_effective_provider(tmp_path: Path) -> None:
    path = tmp_path / "fallback.sqlite3"
    with SQLiteOperationCache(path, clock=lambda: 1000.0) as cache:
        first, _, _, primary_http, backup_http = setup(cache=cache)
        first_job = first.execute(make_plan(), rows())
        assert primary_http.calls == 0
        assert backup_http.calls == 1
        assert first_job.metrics.persistent_cache_writes == 1

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT provider_id FROM operation_cache"
        ).fetchall() == [("backup",)]

    # No network budget for either provider, but cached alternate can be
    # restored; no record is ever misrepresented as a primary provider hit.
    with SQLiteOperationCache(path, clock=lambda: 1000.0) as cache:
        second, _, _, primary_http, backup_http = setup(
            cache=cache, fallback_quota=0
        )
        result = second.execute(make_plan(), rows())
        assert result.records == first_job.records
        assert result.metrics.fallback_attempts == 1
        assert result.metrics.fallback_successes == 1
        assert result.metrics.persistent_cache_hits == 1
        assert result.metrics.persistent_cache_misses == 1
        assert result.provider_requests["primary"].transport_attempts == 0
        assert result.provider_requests["backup"].transport_attempts == 0
        assert primary_http.calls == backup_http.calls == 0


@pytest.mark.parametrize("mode", ["NOT_FOUND", "AMBIGUOUS", "INVALID_INPUT", "PROVIDER_ERROR"])
def test_permanent_or_unclassified_failures_cannot_trigger_fallback(mode: str) -> None:
    executor, primary, backup, _, backup_http = setup(primary_mode=mode)
    job = executor.execute(make_plan(), rows())

    assert job.records[0].row_result.steps[0].result.error_code == mode
    assert primary.calls == 1
    assert backup.calls == backup_http.calls == 0
    assert job.metrics.fallback_attempts == 0
    assert job.metrics.fallback_successes == 0


def test_no_fallback_configured_is_explicit_primary_failure() -> None:
    executor, _, backup, _, backup_http = setup(fallback_enabled=False)
    job = executor.execute(make_plan(), rows())
    assert job.records[0].row_result.steps[0].result.error_code == (
        "REQUEST_BUDGET_EXCEEDED"
    )
    assert job.metrics.fallback_attempts == 0
    assert backup.calls == backup_http.calls == 0


def test_mismatched_output_semantics_fail_before_any_provider_calls() -> None:
    executor, primary, backup, primary_http, backup_http = setup(mismatch=True)
    with pytest.raises(ValueError, match="equivalent output"):
        executor.execute(make_plan(), rows())
    assert primary.calls == backup.calls == primary_http.calls == backup_http.calls == 0


def test_unregistered_backup_fails_preflight() -> None:
    executor, primary, backup, primary_http, backup_http = setup(registry_backup=False)
    with pytest.raises(ValueError, match="must be registered"):
        executor.execute(make_plan(), rows())
    assert primary.calls == backup.calls == primary_http.calls == backup_http.calls == 0


def test_fallback_without_evidence_is_rejected_before_persistent_store(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "empty-evidence.db") as cache:
        executor, primary, backup, _, _ = setup(cache=cache, fallback_mode="no_evidence")
        result = executor.execute(make_plan(), rows(2))
        assert primary.calls == backup.calls == 2
        assert result.metrics.fallback_attempts == 2
        assert result.metrics.fallback_successes == 0
        assert result.metrics.persistent_cache_writes == 0
        assert result.records[0].row_result.steps[0].result.error_code == (
            "REQUEST_BUDGET_EXCEEDED"
        )


def test_1000_rows_use_fallback_without_exceeding_alternate_budget() -> None:
    executor, primary, backup, primary_http, backup_http = setup(fallback_quota=1)
    job = executor.execute(make_plan(), rows(1000))
    assert len(job.records) == 1000
    assert job.metrics.successful_rows == 1000
    assert job.metrics.fallback_attempts == 1000
    assert job.metrics.fallback_successes == 1000
    assert primary.calls == 1000
    assert backup.calls == 1
    assert primary_http.calls == 0
    assert backup_http.calls == 1
    assert job.provider_requests["backup"].transport_attempts == 1


def test_second_provider_failure_preserves_original_terminal_error() -> None:
    executor, _, backup, _, backup_http = setup(fallback_mode="NOT_FOUND")
    result = executor.execute(make_plan(), rows())
    assert result.records[0].row_result.steps[0].result.error_code == (
        "REQUEST_BUDGET_EXCEEDED"
    )
    assert backup.calls == 1
    assert backup_http.calls == 0
    assert result.metrics.fallback_attempts == 1
    assert result.metrics.fallback_successes == 0


def test_retry_exhaustion_is_structured_and_eligible_for_approved_fallback() -> None:
    backend = FakeTransport(fail=True)
    retry = RetryingJsonTransport(
        backend,
        policy=TransportRetryPolicy(
            max_attempts=2,
            base_backoff_seconds=0,
            max_backoff_seconds=0,
        ),
        sleeper=lambda _: None,
    )
    with pytest.raises(JsonTransportError) as exc:
        retry.get_json(url="https://approved.test", params={}, timeout_seconds=1.0)
    assert backend.calls == 2
    outcome = transport_failure_result(exc.value, provider_label="Primary")
    assert outcome.error_code == "RETRY_EXHAUSTED"


@pytest.mark.parametrize(
    "changes",
    [
        {"fallback_provider_id": "primary"},
        {"fallback_provider_id": " "},
        {"compatibility_contract_id": " "},
        {"trigger_error_codes": frozenset()},
        {"trigger_error_codes": frozenset({"NOT_FOUND"})},
    ],
)
def test_fallback_approval_rejects_invalid_or_permanent_triggers(
    changes: dict[str, object],
) -> None:
    valid = {
        "primary_provider_id": "primary",
        "fallback_provider_id": "backup",
        "compatibility_contract_id": "contract-v1",
        "fallback_configuration_id": "backup-config-v1",
        "fallback_provider_version": "backup-v1",
        "fallback_evidence_source": "backup",
    }
    valid.update(changes)
    with pytest.raises(ValidationError):
        ApprovedFallback.model_validate(valid)
