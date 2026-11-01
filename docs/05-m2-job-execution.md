# M2 — Multi-row Job Execution Contract

**Status:** Proposed design for [Issue #34](https://github.com/ayri77/ai-data-enrichment-studio/issues/34); not implemented yet.
**Boundary:** Source-independent, in-memory execution of a **validated** `EnrichmentPlan` over multiple records. This document refines the existing [architecture](02-architecture.md), [domain model](03-domain-model.md), and [planning contracts](04-planning-and-capabilities.md); it does not replace their conceptual `RowResult`/`JobResult`.

## 1. Goal and architectural placement

Reuse the existing single-row `DAGExecutor.execute_row(plan, row)` unchanged if possible. Introduce a job-level coordinator (`JobExecutor`, provisional name) that processes a bounded collection of normalized input records. Each row keeps its own DAG outcomes, while **one shared, job-scoped memoization layer** wraps approved capability handlers for eligible repeat operations.

```text
Validated EnrichmentPlan + selected providers + input records
    -> JobExecutor (limits, row identity/order, chunks, counters)
        -> DAGExecutor (single-row dependency semantics unchanged)
            -> job-scoped memoizing CapabilityHandler wrappers
                -> existing approved provider handlers
    -> ordered per-record execution results + aggregate job metrics
```

The engine does not import Excel, openpyxl, CLI, LangGraph, or provider-specific Wikidata/Open-Meteo structures. Provider choices and memoization eligibility are application-controlled, not LLM plan inputs.

## 2. Proposed minimal contracts

These are **design shapes, not existing Python APIs**. Names and field types should be finalized in the first implementation PR.

| Contract | Responsibility | Required invariant |
| --- | --- | --- |
| `InputRecord(record_id, values)` | Source-independent input identity plus `Mapping[str, Optional[RuntimeValue]]` | Stable, non-empty, unique `record_id` within a job |
| `JobExecutionPolicy` | Positive `max_rows`, positive `batch_size`, bounded `max_memoized_results` and explicitly eligible capabilities/providers | Enforced by deterministic Python, never by generated plan text |
| `RecordExecution(record_id, row_result)` | Associate a `RowExecutionResult` with its input record | Same input record identity, in input order; no mutation of source values |
| `JobExecutionResult(records, metrics)` | Ordered complete in-memory results and counters | Exactly one record outcome for each accepted input record |
| `JobExecutionMetrics` | Counts described in section 5 | Logical requests, actual handler executions and reuse are not conflated |

`RowExecutionResult`, `StepExecutionResult`, `OperationResult` and `Evidence` retain their current contracts. The conceptual `FieldResult`, `RowResult` and `JobResult` in the domain model remain higher-level reporting/output concepts; no Excel projection or output artifact is introduced in #34.

**Input preflight:** The caller supplies an already validated plan and resolved, approved provider bindings (using `PlanValidator`/`ProviderRegistry` before execution). The job boundary rejects missing/duplicate record IDs and enforces the row limit *before* invoking providers. It may materialize at most `max_rows + 1` items for preflight; job result storage is necessarily proportional to accepted rows. An unbounded source without a limit is not accepted.

**Order and chunks:** Process rows in supplied order, in chunks of at most `batch_size`. `batch_size` is a scheduling/memory boundary, **not** an assumption that a provider supports a native bulk API. The memoization scope spans all chunks of the same job; a new job starts empty. First implementation is deterministic and sequential. Concurrency, distributed execution and resumability are deferred.

## 3. Deduplication and reuse semantics

Memoization operates **at the handler invocation boundary**, after a DAG step's typed inputs and validated parameters have been resolved. Do **not** memoize the entire `RowExecutionResult`: a row might contain independent successes and failures or depend on other steps.

An eligible operation key includes:

- Registered capability ID and **selected provider identity**; provider selection is known outside the plan. Do not assume that a plain `capability -> handler` mapping by itself carries the provider ID.
- Canonical, typed input values: sorted mapping keys; explicit distinction between `str`, finite `float`, `GeoPoint`, `EntityRef`, and `Quantity`; complete entity source/ID and numeric unit.
- All parameters that influence the operation, in deterministic key order.
- Any provider configuration/semantic version marker required to distinguish materially different behavior *inside the job*. Secrets are never key material or emitted in diagnostic output.

Canonicalization is **structural, not speculative semantic normalization**. For example, `"Berlin"`, `" berlin "`, `"DE"`, and `"Germany"` must not silently collapse to the same key. Reject or bypass caching for unsupported/non-finite values; do not turn an invalid value into a valid cache key.

**Conservative initial eligibility:** Reuse only explicitly approved, successful, immutable `OperationResult` values; failures, ambiguous results and transient transport errors are **not** memoized in #34. Reused results retain their original typed value and `Evidence`; every consuming row still receives its own `StepExecutionResult` and `record_id`. A repeated failure may therefore invoke its handler again. Failure caching/TTL belongs to #35 and retry/budget behavior to #36.

`max_memoized_results` bounds retained successes. Once full, new unique operations still execute but are not memoized; count these capacity skips. Entries already present can still be reused. No silent eviction or cross-job persistence is required by #34.

## 4. Failure and correctness behavior

- An expected provider failure is an `OperationResult(status="failure", ...)`; preserve its diagnostic in the current row. An unrelated row must continue.
- Existing `DAGExecutor` behavior stays authoritative: a failed dependency blocks dependent steps with `DEPENDENCY_FAILED`, while independent steps continue.
- Missing source columns / missing values remain explicit per-row outcomes; they must not reuse another row's operation.
- A programming exception or invalid job configuration is **not** silently converted to a successful row; fail fast with an actionable error. Recovery from interrupted jobs is outside #34.
- Derive row summary counts from step outcomes: all successful -> successful; mixed success/failure -> partial; no successful steps -> failed. Individual step outcomes remain visible.
- Do not invent values or new source `Evidence`. Reuse never changes the meaning of existing provider results.

## 5. Metrics and observability

Job metrics must distinguish:

| Metric | Definition |
| --- | --- |
| `total_rows`, `successful_rows`, `partial_rows`, `failed_rows` | Counts over accepted records |
| `logical_operations` | DAG steps that reach the handler boundary (excluding blocked steps) |
| `executed_handler_calls` | Actual calls into underlying handlers, including repeats that are ineligible for memoization |
| `reused_operations` | Eligible logical operations satisfied by an earlier successful in-job result |
| `memoized_results`, `memo_capacity_skips` | Retained successful keys and uncached successes due to capacity limit |

For a fixed workload, `logical_operations = executed_handler_calls + reused_operations` when every reached operation is either invoked or reused. Report blocked operations separately if the implementation exposes them.

**Do not claim that handler calls equal HTTP requests.** A single Wikidata resolution may issue several HTTP requests; provider-level network request/attempt metrics are added when instrumentation and budgets are introduced in #36. Similarly, persistent-cache hits are #35 metrics, separate from in-job reuse.

## 6. Verification plan for #34

Implement in coherent, reviewable slices:

1. **Contracts and policy:** record identity/order, policy validation, bounded row preflight, structured job outcomes/metrics; unit tests. No provider adapter changes.
2. **Job-scoped memoizing handler:** deterministic typed key, explicit eligibility/provider identity, success-only reuse, evidence fidelity and capacity ceiling; offline unit tests, including different input types, parameters and providers.
3. **Integration and scale:** run the two already approved DAGs over multi-row fixtures, row failure isolation, stable order across chunks, repeated operations across chunk boundaries, no source mutation, and **1,000 input rows** with observed handler-call savings.

Suggested deterministic benchmark: 1,000 rows repeating 10 verified input identities through a two-step `resolve_entity -> lookup_property` DAG; with memoization enabled and sufficient capacity, expect 2,000 logical handler-boundary operations but only 20 actual handler invocations. Verify counts with instrumented offline handlers; this is **not** a claim about Wikidata HTTP request counts or real-world timing. Repeat with deduplication disabled to measure the baseline.

Finish with Ruff lint/format, Pyright, full offline pytest and a reviewed PR before closing #34. Broader integration, timing benchmark and documentation verification remain part of #37.

## 7. Deferred ownership

| Issue | Not part of #34 |
| --- | --- |
| #35 | SQLite cross-job cache, TTL, cache invalidation, compatibility/versioning |
| #36 | Retries/backoff, rate limits, real provider request budgets, controlled fallback |
| #37 | Final M2 acceptance, measured benchmark, end-to-end provider quality audit |
| #44 | Robustness against external API/semantic drift |
| M3 | XLSX writes, workbook preservation and evaluation of enriched numeric outputs |
| M4/M5 | LangGraph orchestration, async workers, durable progress and recovery |

**Acceptance decision:** Do not merge a design that mixes job orchestration, provider transport, persistent caching, or Excel writes into a single executor.
