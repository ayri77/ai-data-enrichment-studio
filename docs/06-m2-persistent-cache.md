# M2 — Persistent SQLite Operation Cache Contract

**Status:** Design proposal for [Issue #35](https://github.com/ayri77/ai-data-enrichment-studio/issues/35); not implemented yet.
**Prerequisite:** [#34 multi-row execution and per-job memoization](05-m2-job-execution.md) is implemented.

## 1. Purpose and boundaries

Reuse explicitly eligible successful provider operations **between independent jobs**, without allowing expired, incompatible, corrupted, or ambiguous data to become verified results. Persistent storage is a supporting execution concern, not a replacement for the existing `DAGExecutor`, `JobExecutor`, `OperationResult`, or per-row `Evidence`.

Proposed synchronous execution order for an eligible handler invocation:

```text
DAG step with resolved typed inputs
    -> in-job memoization hit? --------------------> return existing typed result
    -> eligible for persistent cache?
         -> valid SQLite hit? ---------------------> return restored typed result
         -> absent / expired / incompatible ------> invoke actual handler
    -> if approved successful result: write SQLite entry (TTL)
    -> optionally retain in bounded job memo
    -> return result with original Evidence
```

- The caller still validates `EnrichmentPlan` and resolves registered provider handlers before beginning a job.
- Cache eligibility requires an explicit application-approved capability/provider binding **and** a positive source-appropriate TTL. No LLM-generated instruction can turn caching on.
- Neither SQLite nor its schema becomes part of the `DAGExecutor` interface. No Excel, LangGraph, cloud DB, or provider-specific persistence logic.
- The first implementation is sequential and synchronous, matching #34. Multi-process coordination, remote/shared caches, and migrations for arbitrary old schemas are not required.

## 2. Identity, versioning and cache keys

The persistent key **must reuse the same canonical operation identity** as in-job memoization, extended with explicit compatibility metadata; no second independent input-normalization algorithm.

Components:
1. Cache key format/version marker, e.g. `operation-key:v1`.
2. Capability ID and semantic capability version.
3. Selected provider ID and source/adapter compatibility version.
4. Non-secret provider configuration fingerprint/identifier (language, endpoint semantic changes, dataset/ontology policy if relevant); never include credentials.
5. Canonical, structurally typed inputs from #34: explicit distinct types; sorted input keys; complete `EntityRef` source/id; `Quantity` value/unit; `GeoPoint` coordinates; finite floats encoded consistently.
6. All behavior-affecting operation parameters, in canonical order.

Compute a SHA-256 digest over deterministic, versioned canonical serialization (UTF-8). Store a digest as lookup key; do not log raw operation inputs, tokens, or credentials. A key format change, capability/provider version change, or configuration change must yield a cache miss. No speculative equivalence between `"DE"` and `"Germany"` or case/whitespace variants.

**Important implementation dependency:** `memoization._operation_key` is currently private. Promote a single canonical key builder to a stable internal utility for both memory and SQLite. Avoid separate key functions that can silently disagree.

## 3. Persistence contract

Use the Python standard library `sqlite3`, with parameterized SQL and explicit transactions. Prefer one simple operations table, keyed by digest, with sufficient metadata for inspection and safe invalidation:

| Field | Meaning |
| --- | --- |
| `key_digest` | Primary key: SHA-256 of canonical versioned operation identity |
| `schema_version` | Persistent payload contract version |
| `result_json` | Explicitly tagged JSON-encoded successful typed result and Evidence |
| `created_at_epoch` | Creation time for diagnostics and audit |
| `expires_at_epoch` | Absolute UTC expiry timestamp |
| `provider_id` / `capability_id` | Non-sensitive troubleshooting metadata (optional, indexed only if needed) |

The SQLite record should not store API secrets or raw request payloads. Cache files may still contain **returned business/personal data** in `result_json`; file permissions, opt-in storage location and retention are security considerations. A credential leak must not be mitigated by simply hashing an input secret into a key.

Serialize `OperationResult` with an **explicit type discriminator** for each `RuntimeValue` variant: `str`, finite `float`, `GeoPoint`, `EntityRef`, and `Quantity`. Preserve `Evidence` exactly (source, reference and order), the numeric unit and the validation-relevant fields. Restore using the existing Pydantic domain models, not a permissive untyped dict or a guessed union. Never reclassify a failed result as success.

A cache store/load API should return a typed hit/miss without synthesizing provider results. The DB must be swappable for an in-memory/fake implementation in tests.

## 4. Validity, expiration and failures

- Each eligible (capability, provider) binding receives an explicit, positive `ttl_seconds` under application policy. No uniform hard-coded TTL for every source.
- Expiry is checked using an injected UTC wall-clock source. `now >= expires_at` means expired. A controlled clock enables deterministic tests without sleep.
- Cache only approved successful immutable `OperationResult` values; **do not persist failures**, including `NOT_FOUND`, `AMBIGUOUS`, timeouts, HTTP 429/5xx and transient network errors. Failure caching is not required for #35.
- On expiry, missing entry, version/config incompatibility, or unsupported typed value: treat as a miss and invoke the provider. Never serve stale data as verified.
- On a **corrupt entry payload**, treat that entry as a miss, record an invalid/corrupt-entry diagnostic, and remove/quarantine it safely. Do not silently return a corrupted value or change its Evidence.
- On unsupported *database schema version*, do **not** drop/migrate/recreate a populated DB silently. Raise an actionable compatibility error; document a safe reset/rebuild path.
- A genuine SQLite I/O/locking/storage failure must be distinguishable from a normal cache miss; do not suppress it as a successful provider operation. The fail-open/fail-closed operational policy can be decided in a dedicated testable boundary.
- No network refresh in the background; refresh occurs synchronously upon a miss. SQLite does not guarantee current external facts merely because TTL has not elapsed; provider-specific validity policy remains authoritative.

## 5. Interaction with job-scoped memoization and metrics

Current #34 accounting is:

`logical_operations = executed_handler_calls + reused_operations`

A SQLite hit does not call the underlying provider. Hence in #35 revise the model and its invariants to distinguish:

| Metric | Meaning |
| --- | --- |
| `logical_operations` | Every DAG operation reaching the handler boundary |
| `reused_operations` | In-memory, within-job memoization hits |
| `persistent_cache_hits` | SQLite hits served without an underlying provider handler call |
| `executed_handler_calls` | Actual underlying handler calls after both cache levels |
| `persistent_cache_misses` | DB lookups that were not usable, including expired/invalid entries; diagnostic counter |
| `persistent_cache_writes` | Successfully stored eligible successful results; diagnostic counter |
| `persistent_cache_invalid_entries` | Corrupt entries rejected; diagnostic counter |

For completed, successful job execution accounting:

`logical_operations = executed_handler_calls + reused_operations + persistent_cache_hits`

Misses and writes are **additional diagnostics**, not mutually exclusive operation categories; never add them to the sum.

If a persistent hit populates in-job memo, `memoized_results` may exceed the number of underlying handler calls; revise the old constraint `memoized_results <= executed_handler_calls` accordingly. All existing #34 tests and counters must remain unchanged when persistence is disabled.

**A handler call is not an HTTP request.** The actual provider may issue multiple network requests, and #36 separately measures and enforces network budgets, retry attempts, and rate limits.

## 6. Proposed implementation slices and acceptance tests

1. **Single key/codec contract:** reuse #34 canonical typed key, add explicit key/cache format versions, define type-tagged, validated `OperationResult` round-trips with Evidence. Cover all five runtime types and altered provider/parameters/config/version.
2. **SQLite adapter:** opt-in TTL policy, schema/version, typed `get/put`, expiry and corruption handling, injected clock, temp-file SQLite integration tests. Failures are never stored.
3. **End-to-end wiring:** adapt `JobMemoization`/`JobExecutor` for optional persistent storage without changing `DAGExecutor`. Verify two separate jobs reuse valid entries, an expired entry invokes the provider, a missing/invalid entry does not poison results, persistent vs in-job counters remain distinct, and cache-disabled behavior remains identical to #34.

All routine tests must be offline and use `tmp_path` SQLite files; no clock sleeps or live provider traffic. Finish with Ruff lint/format, Pyright and full pytest. Do not close #35 until these integration tests pass and the implementation PR is reviewed.

## 7. Explicitly deferred

- #36: retries, HTTP rate limits, real network request/attempt accounting, provider fallback and budgets.
- #37: final M2 performance benchmark against the planned scenarios and acceptance review.
- #44: provider API/ontology drift and revised semantic validation.
- M3: Excel writes and evaluation; M4/M5: LangGraph, web APIs, durable job orchestration, distributed workers.
