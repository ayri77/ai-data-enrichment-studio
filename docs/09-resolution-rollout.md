# Resolution Foundation — Delivery Plan and Milestone Boundaries

**Status:** Architectural direction approved via merged PR #69; staged implementation in progress (2026-11-03)  
**Architecture:** [ADR-RES-001](08-resolution-foundation.md)  
**Parent epics:** [M2 #3](https://github.com/ayri77/ai-data-enrichment-studio/issues/3), [M3 #4](https://github.com/ayri77/ai-data-enrichment-studio/issues/4), [M4 #5](https://github.com/ayri77/ai-data-enrichment-studio/issues/5), [M5 #6](https://github.com/ayri77/ai-data-enrichment-studio/issues/6)

## 1. Present baseline (not proposed implementation)

At merge of PR #60 (`main` `58615516f83411d51c1eba4d809e84bf47b8adfe`):
- M1 plan generation and deterministic validation complete.
- M2 has typed `OperationResult`, DAG execution, approved providers, Haversine, Open-Meteo/Wikidata, per-job reuse, optional SQLite, bounded transport retries/rate/request policies and approved fallback contracts.
- Deterministic offline and cached smoke runs exist; controlled 1,000-row in-memory job test exists in #34.
- **Known live mismatch:** Berlin exact-name geocoding returns `AMBIGUOUS`; Wikidata Everest resolves an early mountain candidate but later produces `INCOMPLETE_HIERARCHY` while verifying other search matches. These are unresolved product/algorithm decisions, not proof that remote providers failed.
- The existing public CLI does **not** execute a user workbook through output enrichment; no workbook-preserving result writer, end-to-end numeric truth evaluation, Review Workspace or LLM repair exists.
- #37 was closed after PR #60 while its final live follow-up/quality audit was still noted as outstanding. Reopen/retain it as the final M2 gate; #44 provider drift audit remains open.

## 2. Delivery sequence and issue ownership

| Order | Issue | Milestone | Deliverable / gate |
| --- | --- | --- | --- |
| 0 | [#61](https://github.com/ayri77/ai-data-enrichment-studio/issues/61) | M2 | Approve minimal normalized candidate/decision/report contract, policy boundaries, abstention semantics; documentation PR reviewed before code |
| 1 | [#62](https://github.com/ayri77/ai-data-enrichment-studio/issues/62) | M2 | Typed optional context binding (e.g. administrative area), candidate adapters and offline fixtures; backwards-compatible plan validation |
| 2 | [#63](https://github.com/ayri77/ai-data-enrichment-studio/issues/63) | M2 | Shared deterministic decision policy + Open-Meteo geographic ranking, audit and Berlin scenarios |
| 3 | [#64](https://github.com/ayri77/ai-data-enrichment-studio/issues/64) | M2 | Wikidata bounded class verification, non-premature uniqueness, Everest/film/incomplete trace |
| 4 | [#65](https://github.com/ayri77/ai-data-enrichment-studio/issues/65) | M2 | Correct cache/policy context identity, decision provenance through memo and rows, regression + 1,000-row job/metrics |
| 5 | [#44](https://github.com/ayri77/ai-data-enrichment-studio/issues/44) | M2 | Complete provider API/semantic drift audit; no unverified confident outcomes |
| 6 | [#37](https://github.com/ayri77/ai-data-enrichment-studio/issues/37) | M2 | Human acceptance review of end-to-end typed scenarios, measured controlled benchmark, code quality, CI; close only when evidence meets M2 Definition of Done |
| 7 | [#66](https://github.com/ayri77/ai-data-enrichment-studio/issues/66) | M3 | Excel-preserving status/provenance, structured review manifest, importable validated overrides, CLI and independent selective-resolution metrics |
| 8 | [#67](https://github.com/ayri77/ai-data-enrichment-studio/issues/67) | M4 | Optional budgeted LLM clarification/typo proposal, human approval, conditional re-execution in LangGraph |
| 9 | [#68](https://github.com/ayri77/ai-data-enrichment-studio/issues/68) | M5 | Web Review Workspace, grouped decisions, bulk-rule preview and audit history |

M3 still owns **all** mandatory Excel/preservation/numeric evaluation tasks beyond #66. M4 still owns bounded orchestration and recovery beyond #67. M5 still owns deployable app, organization scenario, history and access controls beyond #68. New resolution issues **extend** these epics; they do not silently replace prior deliverables.

## 3. Explicit non-goals and milestones

### M2: Foundation only
Implement a generic decision interface proven on two concrete providers; keep protocol and plan changes minimal, strictly validated and backwards-compatible. No web UI, no arbitrary LLM prompt-to-tool execution, no silent input repair, no universal knowledge graph. Retain deterministic HTTP budgets, rate limits, Evidence and source-specific verification.

### M3: Reviewable artifacts without UI
Export candidate/decision data and safe grouped clarification questions as machine-readable reports, write workbook results only to authorized targets, read structured user overrides and recompute affected dependencies. A human-review round-trip must work through CLI/files before M5 GUI. Verify results against *independent* references with documented tolerances and false-auto-match counts.

### M4: Optional LLM
Assist with unknown header semantics, question phrasing, human-approved scoped rules and post-failure typo proposals. Never change the outcome of the original exact-input lookup invisibly; never trust LLM confidence as ground truth. Explicit model/usage budgets; structured, schema-validated output and bounded re-execution.

### M5: User-facing Review Workspace
Show original value, selected candidate, credible alternatives, reason, policy, completeness and evidence; scoped manual override and group actions with previews; preserve source and historical decisions. UI must use the same application services as CLI.

## 4. Required integration / compatibility gates

- Existing `CapabilityRegistry`, `ProviderRegistry`, `PlanValidator`, `DAGExecutor`, `JobExecutor`, transport/session limits and success-only caching remain functional.
- `OperationResult(status=success|failure)` is not naively replaced by a single multi-state result. Candidate reporting is a distinct concept; no tentative choice becomes a downstream definitive success by accident.
- A state/country input and effective selection configuration must affect decision identity. A repeated provider response may be reused only under documented freshness and provider semantics.
- Provider request attempts and handler operations remain separately counted. One verification can use several HTTP calls.
- Identical resolved lookups may be deduplicated, but no row loses its decision/audit link; repeated ambiguous entries may be grouped for review.
- Errors, malformed source data, incomplete hierarchy and no matching candidate remain distinguishable.
- Source files are never changed in place; future user corrections are explicit and auditable.
- CI remains network-free and tests outcomes on adversarial/realistic fixtures; bounded live smoke requires user opt-in.

## 5. Verification matrix

| Case | Expected result |
| --- | --- |
| Berlin, DE, prominence policy enabled, no state | Auto-select prominent capital with trace of Berlin/Schleswig-Holstein alternative, flagged as auto-resolved |
| Berlin, DE + Schleswig-Holstein | Choose matching non-capital entity (GeoNames `2950158`) even if less prominent |
| Berlin, DE, strict existing policy | Review/ambiguous until explicitly resolved; no silent heuristic |
| Several Neustadt matches, insufficient context | Review with alternatives, do not fabricate a unique entity |
| Everest `Q513` + irrelevant `The Climbers` | Preserve verified candidate, avoid unbounded irrelevant ontology traversal; do not falsely claim complete uniqueness |
| Plausible unverified second mountain | Mark incomplete/review rather than definitive success |
| Hambourg typo | First search outcome records no exact match; separate later assistant may propose Hamburg |
| Source error or budget exhaustion | Structured provider/incomplete failure, bounded retries, independent rows continue |
| New optional state column/policy/version | Plan validation and cache identity correct, old city selection not silently reused |
| 1,000-row repeated workload | Every row has outcome and traceable review reference; bounded calls and measured latency |
| Future Excel output | Source preserved; reviewed and unreviewed decisions visible and reproducible |

## 6. Work method and release gates

- Work in **small issue → feature branch → draft PR → offline tests/CI → review → merge** slices. User reviews changes before code implementation and before merge; no implicit auto-merge.
- PR #69 (architecture/rollout documents) has merged. Review the concrete report schema in #61 before integration into providers or JobExecutor. No runtime behavior change in that contract-only slice.
- After each slice: update contract/documentation only to match actual behavior, not to imply a planned feature exists.
- Finalize M2 only after #44 + #37 audit/Definition of Done; do not close based solely on a number of completed issues.
- Avoid arbitrary schedule estimates; limit scope based on actual CI, benchmark and review results.

## 7. Questions reserved for the #61 design review

1. How does a memoized `OperationResult` carry a *separate*, per-operation and per-row ResolutionReport? Wrapper/side channel vs a result-envelope adapter must be tested before implementation.
2. What minimum candidate/context normalization is sufficient for both Open-Meteo and Wikidata without a universal `Any` dictionary?
3. Which auto-selected candidates can flow downstream immediately, and which must await explicit user approval? Choose the default policy on evidence.
4. Should a candidate retrieval cache exist in M2 or stay deferred until profiling proves need?
5. Which policy identifiers and versions must enter SQLite keys, and how are existing entries safely invalidated?


## 8. Report contract baseline

See [Resolution Report Contract v1](10-resolution-report-contract.md) for the machine-readable format that will drive M3 review exports, M4 optional clarification and M5 UI. The initial PR only adds strict validation models and offline tests; existing providers, caching, DAG execution and CLI are unchanged. Defer data producer/consumer wiring and cancellation mechanics to #62–#68.
