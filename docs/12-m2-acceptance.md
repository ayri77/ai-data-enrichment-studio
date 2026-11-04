# M2 Execution Engine — Final Acceptance Evidence (#37)

**Status:** Acceptance candidate in draft [PR #76](https://github.com/ayri77/ai-data-enrichment-studio/pull/76); do not close the epic before owner review and merge.  
**Date:** 2026-11-03  
**Precondition:** Provider contract audit #44 merged as PR #75. Resolution Foundation #61–#65 merged as PRs #69–#74.  
**Measured CI:** [GitHub Actions #41687157762](https://github.com/ayri77/ai-data-enrichment-studio/actions/runs/41687157762), commit `eacf4ec0f73f45dd1cb5b4f4fff4258f673b43b1`, Ubuntu GitHub-hosted runner, Python 3.12 via uv.

## 1. Measured benchmark method

Run from a fresh checkout after `uv sync --locked --group dev`:

```bash
uv run --locked python scripts/benchmark_m2.py --rows 1000
```

The same operation runs automatically as the **last step** in `.github/workflows/ci.yml`; the shorter `tests/integration/test_m2_acceptance_benchmark.py` runs the *identical scenario code* at 8 rows as part of pytest. This prevents a benchmark-only implementation from bypassing regression coverage.

The harness creates typed `InputRecord` instances, builds the existing mandatory scenario DAGs, validates each with the **real `PlanValidator` and `CapabilityRegistry`**, resolves handlers through `ProviderRegistry`, and invokes **real `JobExecutor` + `DAGExecutor`**, deterministic calculation, source-specific provider verification, shared `ResolutionEngine`, per-row audit, success-only memo and request-session transport policy.

Provider endpoints and responses are emulated **in-process**, with explicit allowed URLs/queries and no sockets, live API calls or LLM. `admitted_transport_attempts` counts admitted attempts through the same bounded transport wrapper that normally makes HTTP calls. It is **not a count of actual network traffic**. `fixture_transport_calls` separately counts the transport fixture calls, allowing detection of unexpectedly bypassed request admission. Measured time begins just before `JobExecutor.execute` and ends immediately after it returns; dataset construction, plan validation, fixture construction, Python startup, and coverage-related test runtime are excluded. Times depend on runner load.

The 1000-row workloads deliberately contain a **small number of repeated values** to verify deduplication and scalable audit linking:
- **Capitals:** 500 Hamburg→Berlin and 500 Berlin→Hamburg, DE for both; the Berlin response includes both the verified capital and the Schleswig-Holstein homonym. Opt-in ranked prominence chooses the capital, preserving alternatives.
- **Mountains:** 1000 rows asking for Mount Everest, Nepal; Wikidata search includes the verified mountain `Q513` and a non-mountain film alias. `P31`, `P17`/`P297`, elevation `P2044` and `Quantity(unit="m")` are exercised.
- **Adversarial controls:** one strict-policy Hamburg/Berlin row must leave the distance blocked pending review; one Everest row with an additional plausible but `P31`-unverified candidate must abstain, blocking the elevation dependency.

This benchmark is **not** a 1000-unique-entity capacity test, source factual-accuracy evaluation, live API latency benchmark, memory stress test, or guarantee for generic Excel datasets.

## 2. Reproducible measured result

Results from [CI #41687157762](https://github.com/ayri77/ai-data-enrichment-studio/actions/runs/41687157762). Durations are seconds, single run (not statistically benchmarked across repeated hosts).

| Scenario | Input rows | JobExecutor time (s) | Logical DAG operations | Executed handlers | In-job reuse | Admitted fixture transport attempts | Result rows | Resolution row-step audit links |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Capitals, ranked | 1000 | **0.140488** | 3000 | 4 | 2996 | **2** | 1000 success | 2000 |
| Mountains, strict verified | 1000 | **0.087348** | 2000 | 2 | 1998 | **5** | 1000 success | 1000 |
| Capitals, strict ambiguity | 1 | 0.000563 | 2 | 2 | 0 | 2 | 1 partial | 2 |
| Mountains, unchecked alternative | 1 | 0.000478 | 1 | 1 | 0 | 5 | 1 failed | 1 |

**Interpretation:** Capitals' 3000 logical steps comprise 2000 geocoding invocations and 1000 local geodesic calculations; only 2 unique source lookups and 2 ordered local calculations are executed. Mountains' 2000 logical steps comprise 1000 Wikidata entity resolutions and 1000 elevation requests; each unique operation executes once. A *single* mountain resolution invokes multiple source requests (search + candidate verification + country property), so transport attempts (5 for mountain lookup **and** its elevation request combined) must not be conflated with handler calls (2).

Evidence and decision invariants:
- Capitals: one source-verified `resolved` Hamburg and one `auto_resolved` Berlin, with Berlin capital QID/GeoNames selection `2950159` (alternative `2950158` remains visible). **2000 audit links**, 3000 step-level Evidence values; nonzero distance quantities in km.
- Mountains: one source-verified `resolved` Everest Q513 and explicitly `rejected` film alias, **1000 audit links**, 2000 step-level Evidence values; 8848.86 m fixture quantity.
- Strict Berlin: `needs_review` (not auto-chosen), `DEPENDENCY_FAILED` on distance, one partial input row and both attempted geocoding decisions linked.
- Unknown additional mountain: `incomplete_verification`, `selected=None`, `DEPENDENCY_FAILED` on elevation, no invented height or Evidence.

**Important:** Audited completion and entity identity do not prove a mountain's source elevation claim is correct or current. The fixture value `8848.86 m` is not independent ground truth, and successful source verification does not certify external factual correctness; independent value-level evaluation belongs to M3.

## 3. Code-quality and reliability acceptance

| Check | M2 evidence / disposition |
| --- | --- |
| Static quality | Ruff check and formatter pass; Pyright **0 errors, 0 warnings** on CI #41687157762 |
| Offline regression | **901 passed, 2 skipped**, measured coverage **93%** on CI #41687157762 |
| Provider boundary | Open-Meteo and Wikidata remain source-specific adapters. No arbitrary user-supplied URL, P/Q concept or execution code enters the universal Resolution Engine |
| Typed data flow | DAG passes `GeoPoint`/source-qualified `EntityRef`, then deterministic `Quantity(km)` / retrieved `Quantity(m)`; failed dependencies do not invent values |
| Candidate risk | Strict default/opt-in ranked policies are separate; ambiguity, source failure, incomplete type verification and no-match have distinguishable statuses and remain auditable |
| Correct source attribution | #44 rejects mismatched Wikidata entity IDs or malformed provider error envelopes before assigning success or Evidence |
| Bounded requests | ProviderRequestSession physical-transport attempts and operation caps remain application-owned. CI benchmark keeps two distinct counters and fails on budget bypass |
| Reuse and cache | #65 caches full result + report pairs with provider/policy/input/version identity; cross-job SQLite, corruption, TTL and per-row links tested earlier in the offline suite |
| Maintainability | Benchmark reuses existing smoke DAG/validator fixture factories, real provider registry and production execution path instead of duplicating the core engine |
| No unintended scope expansion | No Excel writer, human correction UI, LangGraph, automatic typo repair, real source requests, or live provider routing added by PR #76 |

### Explicit limitations / follow-up ownership

- **Live provider smoke:** There was no new network smoke in PRs #75 or #76. The existing `smoke_live_providers.py` exercises **legacy strict** handlers (and can legitimately return Berlin `AMBIGUOUS`). It does **not** verify opt-in ranked audited behavior against current live Open-Meteo/Wikidata responses. A separately approved, bounded real-source check is advisable before public deployment, but not a hidden requirement for these fully offline M2 acceptance fixtures.
- **High-cardinality throughput:** 1000 *unique* entities would increase physical calls and can hit provider quotas. This benchmark demonstrates the correctness and cost-saving potential of **repeated lookup** workloads only; no production latency SLA follows from its timings.
- **Incomplete jobs and repair:** The report v1 contract supports review status, while actual human clarification round-trips, safe manual overrides, selective reruns, cancellation, workbook-preserving writes, and independent numeric accuracy metrics remain M3–M5.
- **Runtime activation:** Audited services are available to Python code when the application explicitly configures `resolution_services` and `resolution_policies`; they are not automatically activated by the current `enrichment` CLI.
- **Numeric model:** Great-circle distances use spherical Haversine with Earth radius 6371.0088 km; comparisons to WGS84 ellipsoidal distances need an explicitly defined acceptance tolerance in M3.

## 4. Final M2 review gate

**Technical fixture-based acceptance is green at PR #76 head.** Completion of #37 and Epic #3 should happen **only after** owner review/merge of PR #76 and verification of the merged `main` CI. Once merged, mark the M2 items complete and open M3 execution+Excel output tasks. Do not describe workbook end-to-end enrichment or audited live API reliability as shipped features.
