# M4-3 — Controlled execution recovery (MVP)

## Scope

M4-3 adds **opt-in, whole-job execution protection** to the bounded LangGraph
workflow. It does not change the existing deterministic `JobExecutor`,
provider request budgets, caching rules, resolution audits or M3 human approvals.

The workflow distinguishes `completed`, `partial`, `needs_review`,
`failed`, `rejected` and `execution_uncertain`. The outcome classifier
uses `JobExecutionMetrics` and, when present, `ResolutionJobReport`.
`needs_review` means a human decision is pending in the existing M3
resolution workflow; `execution_uncertain` is different: a prior execution
may have already called providers but no durable final result is available.

## Optional execution journal

`build_agent_graph(..., journal=ExecutionJournal(path))` enables the
SQLite execution boundary. Without a journal, the graph retains its previous
non-durable execution behavior. With a journal, callers must supply a stable
`execution_id`, `dataset_profile`, `plan` and `records`.

The journal uses an atomic SQLite `BEGIN IMMEDIATE` claim, keyed by
`execution_id`, bound to a SHA-256 fingerprint of canonical JSON containing
the plan, dataset profile and ordered input records (fingerprint version 1).

| Journal claim | Graph behavior |
| --- | --- |
| `claimed` (new) | Call `JobExecutor` once and persist the validated `JobExecutionResult`. |
| `finished` | Load the stored result and classify it; do **not** call `JobExecutor` again. |
| `started` (no stored result) | Stop with `execution_uncertain`; never automatically retry. |
| Same ID, different fingerprint | Reject execution with a diagnostic; do not call providers. |

An exception after a successful claim, including a failure to save the final
result, leaves an uncertain execution and prevents implicit replay. The
journal stores whole-job outcomes, **not** per-operation checkpoints.

## Deliberate limitations and safety boundaries

- **No exactly-once guarantee for external calls.** A process can stop after
  a provider accepts a request but before the final result is committed.
  The journal prevents automatic whole-job replay under the same execution ID;
  it cannot undo calls or reconstruct their intermediate responses.
- **No automatic resume inside a partially completed DAG.** The system does
  not re-execute uncertain jobs, even if some operations may have completed.
  Resolving an uncertain execution requires a separate explicit operational
  decision, not an automatic retry.
- **No LangGraph durable checkpointer** in this MVP. The SQLite execution
  journal is a separate safety guard, not a replacement for general
  planning-node checkpoint/resume.
- **No distributed queue, distributed lease or arbitrary provider retry.**
  This phase intentionally limits failure handling to the whole-job boundary.
- **Fingerprint scope is limited.** It covers plan, dataset profile and
  normalized records, but not provider versions, network configuration,
  request policies, cache rules, credentials or the entire runtime environment.
  Reusing an execution ID presupposes compatible execution configuration.
- **Only the journal-protected path is guarded.** Calling the `JobExecutor`
  directly, disabling the journal or selecting a different execution ID
  bypasses this whole-job claim check. The check is not a global deduplication
  guarantee.
- **Result persistence requires a writable SQLite location.** This
  implementation is intended for local, single-host MVP use. Backups,
  corruption recovery, retention and production storage policies are not
  implemented here.

## Verification

Offline tests cover result classification, atomic first/duplicate claim,
conflicting fingerprints, result serialization and reopening SQLite, stable
input fingerprints, and LangGraph integration for first execution, restored
results, conflicting input, blocked uncertain execution and missing identity.

These tests simulate interruptions with exceptions; they do not establish
process-kill crash safety or provider-level exactly-once semantics.

## Deferred

If product requirements later demand durable mid-DAG resume, evaluate
operation-level idempotency and an explicit persisted execution protocol first.
A LangGraph checkpointer alone cannot guarantee safe replays of side-effectful
provider operations. For this academic MVP, prioritize M4-4 offline end-to-end
acceptance and observability over additional infrastructure.
