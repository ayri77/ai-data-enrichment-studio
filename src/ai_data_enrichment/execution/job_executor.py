"""Execute a validated DAG across bounded source-independent input records."""

from collections.abc import Iterable, Mapping

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.fallback import (
    ApprovedFallback,
    FallbackStats,
    _EvidenceCheckedFallback,
    _FallbackCoordinator,
    validate_compatibility,
)
from ai_data_enrichment.execution.job_models import (
    InputRecord,
    JobExecutionMetrics,
    JobExecutionPolicy,
    JobExecutionResult,
    RecordExecution,
    classify_row_outcome,
    prepare_input_records,
)
from ai_data_enrichment.execution.memoization import JobMemoization
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.providers.request_policy import ProviderRequestSession


class JobExecutor:
    """Coordinate records and per-job reuse without changing DAG semantics.

    The caller is responsible for providing a previously validated plan and
    approved provider bindings, for example via ProviderRegistry.
    """

    def __init__(
        self,
        *,
        handlers: Mapping[str, CapabilityHandler],
        selected_providers: Mapping[str, str],
        configuration_ids: Mapping[str, str],
        policy: JobExecutionPolicy,
        persistent_cache: SQLiteOperationCache | None = None,
        capability_versions: Mapping[str, str] | None = None,
        provider_versions: Mapping[str, str] | None = None,
        request_session: ProviderRequestSession | None = None,
        provider_registry: ProviderRegistry | None = None,
        approved_fallbacks: Mapping[str, ApprovedFallback] | None = None,
        semantic_contracts: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._selected_providers = dict(selected_providers)
        self._configuration_ids = dict(configuration_ids)
        self._policy = policy
        self._persistent_cache = persistent_cache
        self._capability_versions = dict(capability_versions or {})
        self._provider_versions = dict(provider_versions or {})
        self._request_session = request_session
        self._provider_registry = provider_registry
        self._approved_fallbacks = dict(approved_fallbacks or {})
        self._semantic_contracts = dict(semantic_contracts or {})

    def execute(
        self,
        plan: EnrichmentPlan,
        records: Iterable[InputRecord],
    ) -> JobExecutionResult:
        """Process a bounded job sequentially, preserving row IDs and order.

        Reject the entire input (including duplicate IDs or excess rows)
        before invoking any handler. A new memoization session is created
        for every call, even when reusing the same JobExecutor instance.
        """
        prepared = prepare_input_records(records, self._policy)
        capabilities = sorted({step.capability for step in plan.steps})

        # Check all required bindings before starting any record, rather than
        # discovering an unbound capability halfway through a job.
        missing_handlers = [cap for cap in capabilities if cap not in self._handlers]
        missing_providers = [cap for cap in capabilities if cap not in self._selected_providers]
        missing_configs = [cap for cap in capabilities if cap not in self._configuration_ids]
        if missing_handlers or missing_providers or missing_configs:
            raise ValueError(
                "Incomplete job provider bindings: "
                f"handlers={missing_handlers}, "
                f"providers={missing_providers}, "
                f"configurations={missing_configs}."
            )

        persistent_capabilities = [
            cap
            for cap in capabilities
            if (cap, self._selected_providers[cap]) in self._policy.persistent_cache_ttls
        ]
        if persistent_capabilities:
            if self._persistent_cache is None:
                raise ValueError("Persistent cache policy requires a SQLite cache instance.")
            for cap in persistent_capabilities:
                if not self._capability_versions.get(cap) or not self._provider_versions.get(cap):
                    raise ValueError(f"Missing persistent cache versions for capability: {cap}.")

        # Validate all fallback identities and semantic approvals before any
        # handler or transport can run. No automatic second-provider discovery.
        fallback_handlers: dict[str, CapabilityHandler] = {}
        if self._approved_fallbacks:
            if self._provider_registry is None or self._request_session is None:
                raise ValueError("Fallback requires a registry and a bounded request session.")
            if set(self._approved_fallbacks) - set(capabilities):
                raise ValueError("Fallback policy names a capability absent from the plan.")

            for cap, approved in self._approved_fallbacks.items():
                primary_id = self._selected_providers[cap]
                validate_compatibility(
                    capability_id=cap,
                    approved=approved,
                    selected_provider=primary_id,
                    semantic_contracts=self._semantic_contracts,
                )
                registered_primary = self._provider_registry.get(
                    capability_id=cap, provider_id=primary_id
                )
                registered_fallback = self._provider_registry.get(
                    capability_id=cap, provider_id=approved.fallback_provider_id
                )
                if registered_primary is not self._handlers[cap] or registered_fallback is None:
                    raise ValueError(f"Fallback providers must be registered for {cap}.")
                if not (
                    self._request_session.has_provider_policy(primary_id)
                    and self._request_session.has_provider_policy(approved.fallback_provider_id)
                ):
                    raise ValueError(f"Fallback requires separate provider budgets for {cap}.")
                if (cap, approved.fallback_provider_id) in self._policy.persistent_cache_ttls:
                    if not self._capability_versions.get(cap):
                        raise ValueError(f"Missing fallback capability version for {cap}.")
                fallback_handlers[cap] = registered_fallback

        if self._request_session is not None:
            self._request_session.begin_job()
        memo = JobMemoization(self._policy, self._persistent_cache)
        fallback_stats = FallbackStats()
        wrapped_handlers: dict[str, CapabilityHandler] = {}
        for cap in capabilities:
            primary_id = self._selected_providers[cap]
            primary_raw = self._handlers[cap]
            if self._request_session is not None:
                primary_raw = self._request_session.wrap_handler(primary_id, primary_raw)
            primary = memo.wrap_handler(
                capability_id=cap,
                provider_id=primary_id,
                configuration_id=self._configuration_ids[cap],
                handler=primary_raw,
                capability_version=self._capability_versions.get(cap),
                provider_version=self._provider_versions.get(cap),
            )
            approved = self._approved_fallbacks.get(cap)
            if approved is None:
                wrapped_handlers[cap] = primary
                continue

            assert self._request_session is not None
            alternate_raw = _EvidenceCheckedFallback(fallback_handlers[cap], approved=approved)
            alternate_raw = self._request_session.wrap_handler(
                approved.fallback_provider_id, alternate_raw
            )
            alternate = memo.wrap_handler(
                capability_id=cap,
                provider_id=approved.fallback_provider_id,
                configuration_id=approved.fallback_configuration_id,
                handler=alternate_raw,
                capability_version=self._capability_versions.get(cap),
                provider_version=approved.fallback_provider_version,
            )
            wrapped_handlers[cap] = _FallbackCoordinator(
                primary=primary, alternate=alternate, approved=approved, stats=fallback_stats
            )
        executor = DAGExecutor(wrapped_handlers)

        results: list[RecordExecution] = []
        statuses = {"success": 0, "partial": 0, "failure": 0}
        for start in range(0, len(prepared), self._policy.batch_size):
            for record in prepared[start : start + self._policy.batch_size]:
                row_result = executor.execute_row(plan, record.values)
                statuses[classify_row_outcome(row_result)] += 1
                results.append(RecordExecution(record_id=record.record_id, row_result=row_result))

        counts = memo.snapshot_metrics()
        metrics = JobExecutionMetrics(
            total_rows=len(results),
            successful_rows=statuses["success"],
            partial_rows=statuses["partial"],
            failed_rows=statuses["failure"],
            logical_operations=counts.logical_operations,
            fallback_attempts=fallback_stats.attempts,
            fallback_successes=fallback_stats.successes,
            executed_handler_calls=counts.executed_handler_calls,
            reused_operations=counts.reused_operations,
            persistent_cache_hits=counts.persistent_cache_hits,
            persistent_cache_misses=counts.persistent_cache_misses,
            persistent_cache_writes=counts.persistent_cache_writes,
            persistent_cache_invalid_entries=counts.persistent_cache_invalid_entries,
            memoized_results=counts.memoized_results,
            memo_capacity_skips=counts.memo_capacity_skips,
        )
        return JobExecutionResult(
            records=tuple(results),
            metrics=metrics,
            provider_requests=(
                self._request_session.snapshot() if self._request_session is not None else {}
            ),
        )
