# Reading guide: implementation map and invariants

This is a guide to the **current deterministic pipeline**, not a promise
that the planned M4 LangGraph agent or M5 web UI already exists. Read the
public contracts and workflow boundaries before individual provider details.

## Recommended reading order

1. `domain/dataset_profile.py`, `domain/enrichment_plan.py`,
   `domain/capability.py` — source-independent metadata, plan and
   capability contracts.
2. `capabilities/definitions.py`, `capabilities/registry.py`,
   `validation/plan_validator.py` — approved operations, static
   validation and rejection before runtime execution.
3. `execution/dag.py`, `execution/executor.py`,
   `execution/job_models.py`, `execution/job_executor.py` —
   dependency order, row execution and bounded multi-row scheduling.
4. `execution/memoization.py`, `execution/operation_keys.py`,
   `execution/sqlite_cache.py` — success-only reuse and cache identity.
5. `providers/registry.py`, `providers/request_policy.py`,
   `providers/retry_transport.py` — approved concrete providers and
   explicit request budgets/retries.
6. `resolution/models.py`, `resolution/engine.py`,
   `resolution/job.py`, `resolution/approvals.py` —
   verified candidate decisions, audit links and scoped approvals.
7. `ingestion/table_selection.py`, `ingestion/excel_records.py`,
   `output/excel_writer.py`, `orchestration/excel_runner.py` —
   source row identity, preflight target validation and safe workbook
   publishing.
8. `planning/llm_planner.py`, `planning/openai_client.py`,
   `cli.py` — LLM-assisted *plan generation* and the command-line
   entry point. The LLM does not execute arbitrary Python.

Paths above are relative to `src/ai_data_enrichment/`.

## How one Excel job runs

```text
explicit worksheet/range selection
   -> profile selected table
   -> validate registered plan and output bindings
   -> read bounded typed rows with original Excel coordinates
   -> preflight ALL output target cells (no provider request yet)
   -> execute topologically ordered DAG for each row
      -> approved provider/optional audited resolver
      -> optional bounded request policy, memo and separate SQLite caches
      -> retain per-row result and audited decision links
   -> map output values back to original row coordinates
   -> write a NEW workbook; do not modify the source
```

The orchestration entry point is `ExcelEnrichmentRunner.run`. A
reviewed mountain workflow is demonstrated in `demo/README.md`.

## Non-obvious invariants

- **Planner versus executor:** the planner emits a typed plan.
  `PlanValidator` decides whether its capability IDs, parameter shapes,
  data references and DAG dependencies are allowed. Never treat LLM
  instructions as directly executable handlers.
- **Preflight before side effects:** the Excel runner verifies destination
  coordinates before executing providers. JobExecutor checks provider,
  policy, approval and fallback bindings before starting row processing.
- **DAG topology versus JSON order:** `DAGExecutor` uses plan
  dependencies, not incidental step order, to determine execution.
- **Ordinary versus audited cache:** ordinary operations may use an
  `OperationResult` cache; audited resolution must keep a paired
  operation result **and** the corresponding `ResolutionReport`.
  Scoped human approvals bypass shared memo/persistent caches.
- **Memoization versus audit deduplication:** a memo hit avoids another
  provider execution. `ResolutionAuditCollector` independently merges
  identical operation-key/report pairs, preserving **every** source
  record/step link. Thus fewer unique audit operations do not imply
  fewer audited source rows.
- **Abstention is meaningful:** unknown, ambiguous, incompletely
  verified and provider-failed resolutions must not be silently
  converted into a successful factual value.
- **Workbook preservation:** a successful run creates a separate
  output file and maps successful values to physical worksheet rows.
  Output target checks happen before execution; unsupported targets
  must fail explicitly.

## Where to place explanatory comments

Prefer docstrings for a module/class contract and short comments at
**branching decisions, cache identity boundaries, side effects,
authorization checks and algorithms with non-obvious invariants**.
Avoid comments that merely repeat the next statement. Complex methods
may benefit from later structural refactoring, but such refactors
should be separate from comment-only changes.

## Reproducing current evidence

- `uv run pytest -q`: offline tests; live LLM tests need explicit opt-in.
- `uv run python -m scripts.benchmark_m3_excel --rows 1000`:
  offline XLSX pipeline performance/consistency, **not** live API
  performance or independent factual accuracy.
- `docs/12-m2-acceptance.md` and `docs/13-m3-excel-table-selection.md`:
  scope and boundaries.

## Status and remaining work

M1 and M2 are implemented. M3 includes Excel execution, audited
resume, Capitals E2E coverage and an offline 1,000-row benchmark.
M3 Epic #4 remains open for independent evaluation and other
acceptance requirements; #20, #21, #22 and #66 track related concerns.
M4 #5 (stateful bounded agent orchestration) and M5 #6 (portfolio
application) are not implemented by this guide.
