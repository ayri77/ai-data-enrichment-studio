# M2 — Provider Reliability, Request Budgets and Fallback Contract

**Status:** Incremental implementation for [Issue #36](https://github.com/ayri77/ai-data-enrichment-studio/issues/36). Bounded opt-in retries (#56) and shared request-budget/rate controls (current slice) follow this contract. Approved compatible fallback is still pending.

**Dependencies:** The [multi-row job engine](05-m2-job-execution.md) and [versioned SQLite cache](06-m2-persistent-cache.md) are implemented. The current `JsonTransportError` already preserves `kind`, HTTP `status_code`, and the raw `Retry-After` header; provider adapters currently catch it and convert it to `OperationResult`. #36 must retain the metadata at the boundary where retries occur, before that conversion.

## 1. Responsibility and control boundaries

One capability-handler call can make **multiple real HTTP requests** (particularly Wikidata entity resolution, country verification and ontology traversal). Consequently, enforcing only *handler invocation* limits cannot enforce actual network request budgets. Keep three distinct responsibilities:

```text
Validated EnrichmentPlan + approved provider binding
    -> JobExecutor / DAGExecutor (unchanged dependency semantics)
        -> in-job memo -> SQLite persistent cache
            -> approved provider handler (one logical operation)
                -> policy-enforced JsonTransport wrapper
                    -> source-specific JsonTransport (one physical attempt per get_json)
    -> explicit typed success/failure, source Evidence, job/request metrics
```

- **Transport policy:** bounded attempts, HTTP error classification, provider-specific rate pacing, request budget accounting, clock/sleep injection, and valid `Retry-After` parsing. It must be shared by *all* the approved handlers using a provider and across *all* rows in a job. Do not introduce independent per-Wikidata-handler counters.
- **Provider handler:** remains responsible for interpreting source semantics and converting exhausted transport failures into existing explicit `OperationResult` failures. Its data validations, P31/P279 traversal and source Evidence remain authoritative.
- **Approved fallback coordinator (separate slice):** may select an alternative handler only after a policy-defined terminal primary failure and only when capability outputs, units, semantics and validation are demonstrably equivalent.
- **Job policy integration:** create a fresh per-job budget/rate state; do not persist job counters into SQLite. Cache hits and in-job reuse do not execute `get_json` and therefore do not consume request attempts.

## 2. Explicit, validated application policy

Use deterministic Python-owned configuration, never LLM-authored endpoints, retry counts, providers or budgets. Proposed conceptual contracts (names to finalize during implementation):

| Policy field | Meaning |
| --- | --- |
| `provider_id` | Approved provider identity for all rate/budget counters |
| `max_attempts_per_request` | Strictly positive total transport attempts **including the first call** |
| `max_requests_per_job` | Strictly positive/zero permitted physical HTTP attempts per provider/job; 0 blocks outbound requests |
| `max_operations_per_job` | Optional cap on distinct uncached provider handler executions; independent of HTTP attempts |
| `min_interval_seconds` | Nonnegative spacing between outbound attempts for that provider |
| `base_backoff_seconds`, `max_backoff_seconds` | Bounded nonnegative exponential delay controls |
| `max_retry_after_seconds` | Hard cap for supported server retry hints |
| `max_total_delay_seconds` | Optional aggregate sleep budget per job/provider, including rate pacing and retries |
| `retryable_status_codes` | Explicit allowlist, defaulting to 408, 429, 500, 502, 503, 504 |
| `approved_fallbacks` | Optional ordered, registry-validated compatible providers, disabled by default |

All numeric fields must reject booleans, NaN, infinity and negative values where inappropriate. `max_attempts_per_request` must have a fixed finite upper bound. No zero-delay unbounded loops. Provider IDs must be recognized by `ProviderRegistry`; no dynamic URL injection.

**Request-budget meaning:** charge **every attempted outbound transport call before the underlying `get_json` invocation**, including retries, timeouts, HTTP errors and malformed payloads. A blocked attempt does not enter the transport. Requests made in nested Wikidata traversal share the same provider budget. This is not the same as the #34 `executed_handler_calls` counter.

An optional operation cap is checked before entering a provider handler (not per DAG step, cache hit or transport retry). Keep explicit distinction in policy and metrics.

## 3. Recoverable versus permanent failures

Classification occurs at the transport boundary, using structured data rather than parsing a provider's human-readable error message.

| Transport outcome | Default policy |
| --- | --- |
| Timeout or transient network error | Retry within hard bounds |
| HTTP 408 / 429 / 500 / 502 / 503 / 504 | Retry within hard bounds and applicable server hint |
| HTTP 400 / 401 / 403 / 404 and other unlisted codes | Permanent; one physical attempt, no retries |
| Invalid JSON / invalid Unicode | Permanent by default; do not automatically hammer a misbehaving source |
| Successfully decoded JSON that fails domain validation | **Never** retried by transport policy |
| `NOT_FOUND`, `AMBIGUOUS`, conflicting evidence, unsupported unit, invalid input | Permanent semantic outcomes; no retry or invented fallback success |
| Local deterministic capabilities such as Haversine | No HTTP retry/rate policy |

Retry classification and exhaustion should preserve the original transport metadata and provide a safe, structured terminal outcome. Keep existing provider-visible `PROVIDER_ERROR`/`INVALID_RESPONSE` where possible to avoid breaking compatible callers; distinct budget/rate exhaustion diagnostics can be introduced and tested. Do **not** hide a blocked request as `NOT_FOUND`.

### Retry delay and hints

- Use bounded deterministic exponential backoff (e.g. `min(max_backoff, base_backoff * 2**retry_index)`); jitter is optional and injectable, with a deterministic no-jitter test mode.
- Parse `Retry-After` as either nonnegative delta-seconds or a valid HTTP-date; compute dates against an injected UTC wall clock. Invalid/negative/unreasonably large hints must not disable hard delay/attempt limits.
- Honor a supported hint **within configured ceilings**; if the hint requires more waiting than allowed, produce a clear rate-limit/budget failure instead of ignoring the server or sleeping beyond the ceiling.
- Use injected **monotonic** time for elapsed/rate-spacing logic, an injected `sleep` callback and wall time only for absolute HTTP-date conversion. Do not sleep in tests.

## 4. Requests, budgets, fairness and observability

Keep a single provider/job accounting object shared by all calls and retry attempts. Job batch boundaries and different DAG capabilities must not reset counters.

| Metric | Precise definition |
| --- | --- |
| `transport_attempts` | Physical `JsonTransport.get_json` calls made after admission by policy, including failures and retries |
| `retry_attempts` | Admitted attempts beyond the first attempt of a logical transport request |
| `requests_blocked_by_budget` | Attempt proposals refused before the network call due to a hard limit |
| `rate_limit_waits` / `rate_limit_wait_seconds` | Pacing decisions and total injected waits under rate policy |
| `retry_after_waits` | Waits driven by valid server hints, when any |
| `transport_failures` | Terminal transport failures after classification and bounded attempts |
| `fallback_attempts` / `fallback_successes` | Separate approved fallback coordinator outcomes |

The existing job metric equality remains:

`logical_operations = executed_handler_calls + reused_operations + persistent_cache_hits`

Do **not** insert `transport_attempts` or `retry_attempts` into that sum. A single actual handler call can trigger multiple HTTP attempts. A SQLite/memory hit triggers **zero**. Record provider-specific and job-aggregate request statistics separately with a consistent aggregation scheme; never log request URLs, tokens, raw inputs or sensitive response bodies.

On budget exhaustion, the current row must expose a terminal failure and dependent DAG steps remain `DEPENDENCY_FAILED`. An unrelated row can still execute local/cached independent steps. A denied remote attempt does not silently reuse an expired cache record.

## 5. Fallback eligibility and cache isolation

Fallback is **off by default**. An approved fallback must:

1. Be explicitly registered and permitted for the same capability; no open-ended search or arbitrary user-provided endpoint.
2. Be compatible in output type, units, geographical/country interpretation, source constraints, trust policy and required source Evidence.
3. Trigger only on eligible terminal provider/transport failures, never on permanent semantic mismatches such as `AMBIGUOUS` or `NOT_FOUND`, unless a separately documented semantic policy explicitly authorizes such behavior.
4. Have its **own provider-specific request counters, rate policy and remaining budget**; it cannot bypass the job's global hard ceilings.
5. Preserve the **actual provider identity** in returned Evidence and any cache key.

**Crucial #35 integration hazard:** The current `JobMemoization` builds a memory/SQLite key from the configured selected provider before invoking its handler. Wrapping a fallback handler behind that provider identity could persist the *fallback's* success under the *primary provider's key*. Do not introduce fallback this way. Instead separate selected vs. effective provider identities at the execution/caching boundary, or place fallback outside provider-specific cache wrappers so each approved provider's result is stored under its own versioned key. Add an explicit regression test with two distinct providers and evidence.

Do not use fallback to conceal ambiguity or fabricate a missing value. No equivalent approved provider means explicit failure.

## 6. Test-first implementation slices

1. **HTTP policy contracts and retry classification:** typed validated policy, transport decorator, fake monotonic clock/UTC wall clock/sleeper, bounded attempts, `Retry-After` parsing, transient/permanent classification, no live traffic. Include a fake provider that makes several requests per operation to catch undercounting.
2. **Budget and rate integration:** share provider-specific/job-wide state across Wikidata auxiliary helper HTTP calls and Open-Meteo; maintain exact attempt metrics, enforce ceilings across rows/batches/retries, cache-hit bypass, explicit terminal failures and safe metadata. Preserve existing provider signatures by injecting a composed `JsonTransport`.
3. **Approved fallback and end-to-end verification:** registry-only compatible alternatives, post-exhaustion policy, source Evidence and effective-provider cache identity, counter isolation, 1,000-row regression and failure propagation. No fallback until a compatible second provider actually exists; if none is available, prove the absence of fallback is explicit rather than implementing a misleading fake production provider.

Run Ruff lint/format, Pyright and the full offline pytest suite on each slice. Keep #36 open until request-budget, retry, rate and fallback/no-compatible-provider acceptance checks are reviewed.

## 7. Deferred ownership

- #37: final M2 acceptance benchmark, measured timing, code-quality and provenance review.
- #44: external API and Wikidata ontology drift robustness audit; assess in #37.
- M3: workbook writing and numeric-value evaluation; M4/M5: LangGraph/durable distributed workers.
- Distributed quotas, shared external rate limiting, spending/payment accounting, real-time scheduling and background refreshing are out of scope for #36.
