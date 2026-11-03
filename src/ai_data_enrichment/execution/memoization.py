"""Bounded job-local deduplication with optional cross-job SQLite reuse.

No changes to DAG ordering, provider adapters, retry budgets or HTTP transport.
Only explicitly approved successful operations are stored at either cache level.
"""

from collections.abc import Mapping

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.job_models import JobExecutionMetrics, JobExecutionPolicy
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.execution.operation_keys import (
    _operation_key,
    _OperationKey,
    persistent_key_digest,
)
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache


class MemoCapacity:
    """One job-wide cap shared across legacy and audited success-only memoizers."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("Memo capacity must be nonnegative.")
        self._limit = limit
        self._used = 0

    def reserve(self) -> bool:
        if self._used >= self._limit:
            return False
        self._used += 1
        return True


class _MemoizingHandler:
    """Preserve CapabilityHandler.execute while applying two cache levels."""

    def __init__(
        self,
        session: "JobMemoization",
        handler: CapabilityHandler,
        capability_id: str,
        provider_id: str,
        configuration_id: str,
        capability_version: str | None,
        provider_version: str | None,
    ) -> None:
        self._session = session
        self._handler = handler
        self._capability_id = capability_id
        self._provider_id = provider_id
        self._configuration_id = configuration_id
        self._capability_version = capability_version
        self._provider_version = provider_version

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Reuse memory first, SQLite second; execute the provider on misses."""
        session = self._session
        session._logical_operations += 1
        binding = (self._capability_id, self._provider_id)
        eligible = binding in session._policy.memoizable_providers

        key = (
            _operation_key(
                capability_id=self._capability_id,
                provider_id=self._provider_id,
                configuration_id=self._configuration_id,
                inputs=inputs,
                parameters=parameters,
            )
            if eligible
            else None
        )

        if key is not None and key in session._cache:
            session._reused_operations += 1
            return session._cache[key]

        ttl = session._policy.persistent_cache_ttls.get(binding)
        persistent_key: str | None = None
        if key is not None and ttl is not None and session._persistent_cache is not None:
            # Validated by wrap_handler before this wrapper becomes executable.
            assert self._capability_version is not None
            assert self._provider_version is not None
            persistent_key = persistent_key_digest(
                capability_id=self._capability_id,
                capability_version=self._capability_version,
                provider_id=self._provider_id,
                provider_version=self._provider_version,
                configuration_id=self._configuration_id,
                inputs=inputs,
                parameters=parameters,
            )
            if persistent_key is not None:
                lookup = session._persistent_cache.get(persistent_key)
                if lookup.status == "hit":
                    assert lookup.result is not None
                    session._persistent_cache_hits += 1
                    session._retain_success(key, lookup.result)
                    return lookup.result
                session._persistent_cache_misses += 1
                if lookup.status == "invalid":
                    session._persistent_cache_invalid_entries += 1

        # Exceptions are not converted to success. Storage errors also propagate.
        session._executed_handler_calls += 1
        result = self._handler.execute(inputs=inputs, parameters=parameters)
        if result.status == "success":
            if persistent_key is not None and ttl is not None:
                assert session._persistent_cache is not None
                session._persistent_cache.put(
                    persistent_key,
                    result,
                    ttl_seconds=ttl,
                    capability_id=self._capability_id,
                    provider_id=self._provider_id,
                )
                session._persistent_cache_writes += 1
            if key is not None:
                session._retain_success(key, result)
        return result


class JobMemoization:
    """Fresh per-job memoization store with optional shared persistent backend."""

    def __init__(
        self,
        policy: JobExecutionPolicy,
        persistent_cache: SQLiteOperationCache | None = None,
        shared_capacity: MemoCapacity | None = None,
    ) -> None:
        if policy.persistent_cache_ttls and persistent_cache is None:
            raise ValueError("Persistent cache TTLs require an explicit SQLite cache.")
        self._policy = policy
        self._persistent_cache = persistent_cache
        self._capacity = shared_capacity or MemoCapacity(policy.max_memoized_results)
        self._cache: dict[_OperationKey, OperationResult] = {}
        self._logical_operations = 0
        self._executed_handler_calls = 0
        self._reused_operations = 0
        self._persistent_cache_hits = 0
        self._persistent_cache_misses = 0
        self._persistent_cache_writes = 0
        self._persistent_cache_invalid_entries = 0
        self._memo_capacity_skips = 0

    def _retain_success(self, key: _OperationKey, result: OperationResult) -> None:
        """Keep approved successes up to the configured in-memory capacity."""
        if self._capacity.reserve():
            self._cache[key] = result
        else:
            self._memo_capacity_skips += 1

    def wrap_handler(
        self,
        *,
        capability_id: str,
        provider_id: str,
        configuration_id: str,
        handler: CapabilityHandler,
        capability_version: str | None = None,
        provider_version: str | None = None,
    ) -> CapabilityHandler:
        """Bind explicit versions for durable caching; legacy memory-only API works.

        Configuration and version IDs must reflect semantic behavior and contain
        no secrets; they are not read from unvalidated LLM-generated plan text.
        """
        if not capability_id.strip() or not provider_id.strip() or not configuration_id.strip():
            raise ValueError("Capability, provider and configuration IDs must not be blank.")
        if not callable(getattr(handler, "execute", None)):
            raise TypeError("Wrapped handler must implement execute.")
        if (capability_id, provider_id) in self._policy.persistent_cache_ttls and (
            not capability_version
            or not capability_version.strip()
            or not provider_version
            or not provider_version.strip()
        ):
            raise ValueError("Persistent cache requires explicit capability and provider versions.")
        return _MemoizingHandler(
            session=self,
            handler=handler,
            capability_id=capability_id,
            provider_id=provider_id,
            configuration_id=configuration_id,
            capability_version=capability_version,
            provider_version=provider_version,
        )

    def snapshot_metrics(self) -> JobExecutionMetrics:
        """Capture separate logical, in-memory, persistent and provider counts."""
        return JobExecutionMetrics(
            logical_operations=self._logical_operations,
            fallback_attempts=0,
            fallback_successes=0,
            executed_handler_calls=self._executed_handler_calls,
            reused_operations=self._reused_operations,
            persistent_cache_hits=self._persistent_cache_hits,
            persistent_cache_misses=self._persistent_cache_misses,
            persistent_cache_writes=self._persistent_cache_writes,
            persistent_cache_invalid_entries=self._persistent_cache_invalid_entries,
            memoized_results=len(self._cache),
            memo_capacity_skips=self._memo_capacity_skips,
        )
