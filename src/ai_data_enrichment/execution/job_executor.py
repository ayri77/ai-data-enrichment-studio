"""Execute a validated DAG across bounded source-independent input records."""

from collections.abc import Iterable, Mapping

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.executor import DAGExecutor
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
    ) -> None:
        self._handlers = dict(handlers)
        self._selected_providers = dict(selected_providers)
        self._configuration_ids = dict(configuration_ids)
        self._policy = policy

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

        memo = JobMemoization(self._policy)
        wrapped_handlers = {
            cap: memo.wrap_handler(
                capability_id=cap,
                provider_id=self._selected_providers[cap],
                configuration_id=self._configuration_ids[cap],
                handler=self._handlers[cap],
            )
            for cap in capabilities
        }
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
            executed_handler_calls=counts.executed_handler_calls,
            reused_operations=counts.reused_operations,
            memoized_results=counts.memoized_results,
            memo_capacity_skips=counts.memo_capacity_skips,
        )
        return JobExecutionResult(records=tuple(results), metrics=metrics)
