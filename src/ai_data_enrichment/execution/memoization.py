"""Bounded, job-scoped memoization at the capability-handler boundary.

A fresh JobMemoization is required for every job. This module deliberately
does not own DAG traversal, persistent caches, retries or external HTTP calls.
"""

from collections.abc import Mapping

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.job_models import JobExecutionMetrics, JobExecutionPolicy
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.execution.operation_keys import _OperationKey, _operation_key


class _MemoizingHandler:
    """Maintain the existing CapabilityHandler interface for DAGExecutor."""

    def __init__(
        self,
        session: "JobMemoization",
        handler: CapabilityHandler,
        capability_id: str,
        provider_id: str,
        configuration_id: str,
    ) -> None:
        self._session = session
        self._handler = handler
        self._capability_id = capability_id
        self._provider_id = provider_id
        self._configuration_id = configuration_id

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Reuse approved, successful operations within this job only."""
        session = self._session
        session._logical_operations += 1

        eligible = (
            self._capability_id,
            self._provider_id,
        ) in session._policy.memoizable_providers

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

        # Exceptions remain programming/provider failures to be handled by the
        # caller, not successes and not fabricated OperationResult values.
        session._executed_handler_calls += 1
        result = self._handler.execute(inputs=inputs, parameters=parameters)

        if key is not None and result.status == "success":
            if len(session._cache) < session._policy.max_memoized_results:
                session._cache[key] = result
            else:
                session._memo_capacity_skips += 1

        return result


class JobMemoization:
    """One-job store and counters shared by explicitly bound handler wrappers."""

    def __init__(self, policy: JobExecutionPolicy) -> None:
        self._policy = policy
        self._cache: dict[_OperationKey, OperationResult] = {}
        self._logical_operations = 0
        self._executed_handler_calls = 0
        self._reused_operations = 0
        self._memo_capacity_skips = 0

    def wrap_handler(
        self,
        *,
        capability_id: str,
        provider_id: str,
        configuration_id: str,
        handler: CapabilityHandler,
    ) -> CapabilityHandler:
        """Bind provider identity and a non-secret, stable configuration ID.

        The caller must distinguish materially different provider configurations;
        never pass credentials as configuration identifiers.
        """
        if not capability_id.strip() or not provider_id.strip() or not configuration_id.strip():
            raise ValueError("Capability, provider and configuration IDs must not be blank.")
        if not callable(getattr(handler, "execute", None)):
            raise TypeError("Wrapped handler must implement execute.")
        return _MemoizingHandler(
            session=self,
            handler=handler,
            capability_id=capability_id,
            provider_id=provider_id,
            configuration_id=configuration_id,
        )

    def snapshot_metrics(self) -> JobExecutionMetrics:
        """Return an immutable snapshot of handler-boundary counters.

        Row counts are deliberately zero; the future JobExecutor will combine
        these counters with its separately calculated row outcomes.
        """
        return JobExecutionMetrics(
            logical_operations=self._logical_operations,
            executed_handler_calls=self._executed_handler_calls,
            reused_operations=self._reused_operations,
            memoized_results=len(self._cache),
            memo_capacity_skips=self._memo_capacity_skips,
        )
