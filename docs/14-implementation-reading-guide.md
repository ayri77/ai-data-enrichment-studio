# Reading guide: implementation map and invariants

A map of the code as it stands at the MVP: the deterministic pipeline, the web
API around it and the browser interface. Read the contracts and the boundaries
between layers before the details of a single source adapter.

Paths in the first two lists are relative to `src/ai_data_enrichment/`.

## Recommended reading order

### The pipeline

1. `domain/dataset_profile.py`, `domain/enrichment_plan.py`,
   `domain/capability.py` — source-independent metadata, plan and
   capability contracts.
2. `capabilities/definitions.py`, `capabilities/registry.py`,
   `validation/plan_validator.py`, `validation/intent_policy.py` — the
   registered operations, static validation, and the bounded checks of a plan
   against what the instruction asked for.
3. `execution/dag.py`, `execution/executor.py`,
   `execution/job_models.py`, `execution/job_executor.py` —
   dependency order, row execution and bounded multi-row scheduling.
4. `execution/memoization.py`, `execution/operation_keys.py`,
   `execution/sqlite_cache.py` — success-only reuse and cache identity.
5. `providers/registry.py`, `providers/request_policy.py`,
   `providers/retry_transport.py` — approved source adapters and
   explicit request budgets and retries.
6. `resolution/models.py`, `resolution/engine.py`, `resolution/geography.py`,
   `resolution/job.py`, `resolution/approvals.py` — how an entity is found,
   verified and audited; country names in any language; scoped approvals.
7. `ingestion/table_detection.py`, `ingestion/table_selection.py`,
   `ingestion/excel_records.py`, `ingestion/table_rows.py`,
   `output/excel_writer.py`, `orchestration/excel_runner.py` — finding the
   table, source row identity, preflight of target cells and safe workbook
   publishing, including the explicit replacement of existing values.
8. `planning/llm_planner.py`, `planning/llm_plan_repairer.py`,
   `planning/instructions.py`, `planning/model_catalog.py`,
   `planning/openai_client.py` — plan generation, repair from validation
   feedback, and the one place that describes each supported model. The model
   never executes anything.

### The web application

9. `api.py`, `workspace_errors.py`, `run_limits.py`, `sample_catalog.py` —
   the application factory, workbook upload and preview, sanitized errors, the
   bounds of one run, the bundled examples.
10. `plan_run.py` — plans, approval, runs, artifacts, review and resume; all
    state is in the memory of one process. `plan_run_worker.py` — the separate
    process one run executes in.
11. `review.py`, `resolution/resume.py`, `resolution/other_name.py`,
    `providers/open_meteo_approved.py`, `providers/wikidata_approved.py` —
    turning an audited report into review items, a user's decisions into a
    verified second pass, and a search under another name.
12. `spellings.py`, `task_ideas.py` — the two places besides planning where a
    language model is asked: other names for a place, and tasks for a table.
13. `provider_settings.py`, `local_server.py`, `hosted.py` — model and key for
    a process, the local launcher, and the entry point of a hosted instance
    behind an access code.
14. `cli.py` — the command-line entry point over the same pipeline
    ([command-line and developer tools](28-cli-and-developer-tools.md)).

### The interface (`frontend/src/`)

15. `api.ts` — every request and the shapes of the answers.
16. `useWorkflow.ts`, `useDataset.ts`, `useLimits.ts`, `mode.tsx` — the state of
    one session: the workbook, the task, the plan, the run, the review; which
    edit invalidates what; Basic and Advanced.
17. `planModel.ts`, `runModel.ts`, `reviewModel.ts`, `resultsModel.ts`,
    `tableModel.ts` — pure functions that turn server answers into what a
    screen says. They are where the wording rules live and are tested without a
    browser.
18. `components/` — the workspaces (`DatasetWorkspace`; `WorkspaceViews` for
    the task panel, Plan and Execution; `ReviewWorkspace`; `ExportWorkspace`
    for Results) and the shell around them. `locales/*.json` — the texts
    in English, Ukrainian and German.

## How one run goes in the browser

```text
POST /api/datasets            upload; the workbook is kept in memory
POST /api/datasets/{id}/preview   find and profile the table, return its rows
POST /api/plans               the model plans, or a supplied plan is taken;
                              the plan is validated and gets a fingerprint
POST /api/plans/{id}/approve  the user approves exactly that fingerprint
POST /api/plans/{id}/runs     a separate process runs the pipeline below
GET  /api/jobs/{id}           progress in rows, then the final status
GET  /api/jobs/{id}/review    names to choose between, names without a match
POST /api/jobs/{id}/resume    decisions start a new run of the same plan;
                              each chosen entity is verified again
GET  /api/jobs/{id}/artifacts/{result|report}
```

The pipeline one run executes (`ExcelEnrichmentRunner.run`), the same for the
web application and the CLI:

```text
explicit worksheet/range selection
   -> profile selected table
   -> validate registered plan and output bindings
   -> read bounded typed rows with original Excel coordinates
   -> preflight ALL output target cells (no source request yet)
   -> execute topologically ordered DAG for each row
      -> approved source adapter / audited resolver
      -> bounded request policy, memo and separate SQLite caches
      -> retain per-row result and audited decision links
   -> map output values back to original row coordinates
   -> write a NEW workbook; do not modify the source
```

Routes and their bounds: [Plan/Run API](22-m5-plan-run-api.md). What each
screen shows: [five-workspace interface](24-five-workspace-ui.md).

## Non-obvious invariants

- **Planner versus executor:** the planner emits a typed plan.
  `PlanValidator` decides whether its capability IDs, parameter shapes,
  data references and DAG dependencies are allowed. Never treat model
  output as directly executable.
- **Approval is bound to one plan and one source.** The fingerprint covers
  the whole plan request (instruction, selection, output mapping, policy), the
  plan itself and the workbook's checksum. A
  run starts only for the fingerprint that was approved; any edit of the task
  produces a new draft.
- **Model output is untrusted everywhere.** A plan is validated; proposed
  names (`spellings.py`) and tasks (`task_ideas.py`) are accepted only as a
  JSON object with a list, filtered, and used as text: a proposed name is
  searched and verified like a typed one, a proposed task is planned and
  validated like a typed one.
- **What the audit says comes from the server's own record.** Whether a name
  was proposed by the model is decided from what the server remembers of the
  model's answers, not from the request (`review.other_name_audit`).
- **Preflight before side effects:** the Excel runner verifies destination
  coordinates before any source is asked. `JobExecutor` checks source,
  policy, approval and fallback bindings before starting row processing.
- **Existing values are kept unless the user says otherwise.** A cell that
  already has a value is not written. Replacement is an explicit option of the
  plan request, applies only where a new value was found, and lists every
  replaced cell with its earlier value in the audit.
- **DAG topology versus JSON order:** `DAGExecutor` uses plan
  dependencies, not incidental step order, to determine execution.
- **Ordinary versus audited cache:** ordinary operations may use an
  `OperationResult` cache; audited resolution must keep a paired
  operation result **and** the corresponding `ResolutionReport`.
  Scoped human approvals bypass shared memo and persistent caches.
- **Memoization versus audit deduplication:** a memo hit avoids another
  source request. `ResolutionAuditCollector` independently merges
  identical operation-key/report pairs, preserving **every** source
  record/step link. Fewer unique audit operations therefore do not imply
  fewer audited source rows.
- **Abstention is meaningful:** unknown, ambiguous, incompletely
  verified and failed resolutions must not be silently converted into a
  successful value. In the browser they become a review item or an empty
  cell with its reason.
- **A run is one killable process.** The API process never runs the pipeline
  itself, so a timeout or a cancellation ends the work for certain. The
  exceptions are the single source request of a search under another name and
  the model requests for names and tasks, which run in the API process and
  are bounded by count.
- **Workbook preservation:** a run creates a separate output file and maps
  successful values to physical worksheet rows. Unsupported targets fail
  explicitly before execution.
- **Texts are data.** Every server error code has a text in each language
  (`tests/unit/test_error_texts.py`), and the languages share keys and
  placeholders.

## Where to place explanatory comments

Prefer docstrings for a module or class contract and short comments at
**branching decisions, cache identity boundaries, side effects,
authorization checks and algorithms with non-obvious invariants**.
Avoid comments that merely repeat the next statement.

## Reproducing current evidence

- `uv run --locked pytest -q`: offline tests; live tests need an explicit
  opt-in.
- `cd frontend && npm test` and `npm run test:e2e`: unit tests of the
  interface and browser scenarios against injected source answers.
- `uv run --locked python -m scripts.benchmark_m3_excel --rows 1000`:
  offline pipeline performance and consistency, **not** live performance or
  factual accuracy.
- [Live planning reliability](26-live-planning-reliability.md): how planning
  was measured and how to repeat it with another model.

## Status and remaining work

The MVP is the state described in the [README](../README.md): three tasks,
five workspaces, review with a verified second pass, a hosted demo. Open
after it: measuring resolution quality on a labelled sample (#66), unit
declarations and conversion (#20), field projection (#22), review at scale
(#105), a visual view of execution (#102) and an agent mode (#106, #107).
