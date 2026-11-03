# Resolution Foundation — Hybrid Universal Entity Resolution

**Document ID:** ADR-RES-001  
**Status:** Architecture direction accepted (PR #69 merged); report contract v1 under review in #61  
**Date:** 2026-11-03  
**Owner:** M2 Execution Engine, related to #3 and #61  
**Dependencies:** existing M1 planner/validator, M2 provider contracts, #34–#36 reliability and cache

## 1. Decision and motivation

Adopt a **hybrid universal resolution framework**: approved provider adapters retrieve and verify domain-specific candidates; a shared, provider-independent layer evaluates normalized candidates with explicit context and a configurable decision policy; an optional LLM assistant may later propose clarifications and input repairs. **The LLM is not a trusted source of factual verification.** Preserve deterministic, explainable and resource-bounded operation.

This is *not* a decision to implement one universal scoring formula. The common parts are candidate/decision contracts, hard constraints, audit, configuration and lifecycle; ranking features and verification may be provider- or domain-specific.

### Live evidence motivating the work

- **Capitals:** Open-Meteo returns two exact-name Berlin entities in DE: GeoNames `2950159` (PPLC, State of Berlin) and `2950158` (PPL, Schleswig-Holstein). The current exact-name/country handler yields `AMBIGUOUS`, while Hamburg succeeds. A configurable prominence preference may choose the capital *with a visible alternative and an audit trail*, but an explicit Schleswig-Holstein context must override it.
- **Mountains:** Wikidata search returns `Q513` Mount Everest with `P31=Q8502` (mountain), `P17` for Nepal/China and an elevation claim. Checking subsequent search candidates triggers unrelated film class traversal and `INCOMPLETE_HIERARCHY`. An incomplete verification is **not** no match, and a present elevation claim is **not** yet a validated quantity.
- **Misspellings:** The primary exact-input search must remain observable. A separate future data-repair assistant may suggest `Hambourg` → `Hamburg` *after* recording the failed initial search; no silent input mutation.

## 2. Responsibility boundaries

| Component | Responsibility | Must not do |
| --- | --- | --- |
| Dataset profiling / LLM Planner | Detect known semantic columns and propose typed bindings in a declarative `EnrichmentPlan` | Invent arbitrary provider parameters/URLs or code |
| `PlanValidator` / capabilities | Validate optional input names/types and output dependencies | Rank factual candidates |
| Provider adapter (Open-Meteo, Wikidata) | Bounded retrieval, response-shape validation, provider-specific feature extraction and evidence verification | Hide competing candidates by unlogged popularity heuristics |
| Resolution Engine | Shared hard-constraint evaluation, pluggable ranked strategies, decision policy, report generation | Know provider JSON/P/Q/feature-code details or fabricate evidence |
| `DAGExecutor` / `JobExecutor` | Preserve typed execution, dependency isolation, memoization, request budgets and configuration | Treat tentative candidate proposals as verified success |
| Review/export layer (M3) | Export decisions/questions, ingest validated corrections, targeted rerun | Overwrite originals or quietly reassign IDs |
| LLM assistant (M4, opt-in) | Suggest interpretations, questions, scoped rules and repairs | Make unrestricted tool calls or authoritatively confirm facts |
| Review Workspace (M5) | Present decisions, alternatives and bulk actions | Duplicate business rules in UI |

The existing `CapabilityHandler.execute(inputs, parameters) -> OperationResult` boundary remains the execution contract. The first implementation should prefer a **separate structured resolution report/audit channel** over changing `OperationResult.status` to a multistate union. Exact wiring is a review decision in #61; it must support success-only memoization and preserve per-row traceability.

## 3. Minimal conceptual contracts (NOT final Pydantic API)

- `ResolutionContext`: entity-type intent, typed input field bindings and normalized optional attributes (initially `administrative_area` for geography). It distinguishes per-row facts from execution policy. Arbitrary spreadsheet header names map to **registered semantic keys**; they do not become arbitrary handler inputs.
- `ResolutionCandidate`: provider ID + source-local entity ID, display name, normalized entity category/role, supported normalized geographic attributes, verifiable source evidence, provider-specific ranking signals. Unknown attributes are unknown, not negative evidence.
- `CandidateSet`: candidates returned, query/context, whether retrieval and verification were **complete**, limits reached, provider diagnostics, provenance. Search-result order is **not** a correctness score.
- `ResolutionPolicy`: configured hard constraints, applicable ranking strategy and opt-in soft preferences; auto-accept, abstention and review rules; version/config ID. Provider request, page/hierarchy and operation budgets remain separately validated application policies.
- `ResolutionDecision`: selected candidate (if any), alternatives, reason/feature contributions, policy ID/version, completeness and human-review requirement, original query and source references.
- `ResolutionAudit`: stable operation identity, associated record IDs, user overrides, decision revision/history and redacted diagnostics. Deduplicate identical *operations* while retaining references from every affected row.

Proposed conceptual outcomes (names are **not** approved public enum values): `resolved`, `auto_resolved`, `needs_review`, `unresolved`, `incomplete_verification`, `provider_failure`. These describe resolution, **not** the operational `success|failure` contract.

## 4. Decision semantics

1. **Hard constraints**: explicit country, region/state (when supplied and verifiable), entity type and other contractual constraints reject contradictory candidates; no score can offset a contradiction.
2. **Soft ranking**: candidate prominence/administrative role/population/name specificity are optional and explainable features. Source rank or result order alone is not ground truth. Missing population or attributes must not be interpreted as zero significance.
3. **Configurable auto-selection**: a strict legacy-compatible policy can remain the default; the proposed prominence-based choice is an **opt-in policy** until independently evaluated and approved. All auto-decisions record alternatives and reasons and must be inspectable.
4. **Abstention**: remaining plausible ties -> `needs_review`; true exhausted no-match -> `unresolved`; truncated pages/hierarchy/attempt budgets -> `incomplete_verification` with observed candidates. Keep transport and malformed-data failures separate. An unverified candidate must not unlock downstream enrichment as a definitive entity.
5. **No fabricated confidence**: ranking weights/margins are heuristics, not calibrated probabilities. Numeric confidence may only be labeled probabilistic after measured calibration on independent data.
6. **LLM involvement**: optional second-stage proposal for difficult cases, never implicit string repair in primary search, no bypass of hard constraints or source evidence. LLM proposals must be schema-validated and may require user confirmation.
7. **Human correction**: choosing a source ID, adding a geographic context field or approving a dataset-scoped rule should produce a new decision revision; preserve the original source values and prior audit. Bulk rules must preview their affected scope.

## 5. Context and configuration boundaries

For example, a user spreadsheet may contain columns `City`, `Country` and `Bundesland`. The plan may bind `Bundesland` to **known optional** capability input `administrative_area`, verified by `PlanValidator`. Provider-specific geocoders may filter returned `admin1`/region codes using safe normalization; unsupported contexts produce explicit diagnostics.

Keep these distinct:
- **Row context**: city, country, administrative_area, source identity, etc. → typed `PlanStep.inputs`.
- **Resolution selection policy**: prominence preference, permitted auto-accept, ranking strategy, review behavior → validated application configuration, not arbitrary LLM commands.
- **Search/resource policy**: max candidates/pages/hierarchy nodes, transport attempts/rate/request/operation limits → hard-bounded application configuration, not user-supplied uncontrolled numeric plan parameters.

No arbitrary untyped `context: dict[str, Any]` forwarding to providers as a shortcut. Additional semantic inputs are introduced with tests and capability schema versions.

## 6. Cache, deduplication and evidence

- The current M2 job-memo/SQLite mechanism stores **approved successful `OperationResult`s**, keyed by capability, provider, typed inputs, parameters and configuration/versions. Do **not** cache ambiguous or transient failures as successful resolved identities.
- Candidate retrieval caching and resolution-decision caching have **different validity semantics**. Candidate caching is optional and only introduced when its bounds/staleness and provenance are tested; do not build a second generic cache prematurely.
- Any persisted **decision** must reflect normalized context, effective policy/ranking version, provider/version and verification completeness. Changing Berlin's state or policy must trigger a new decision; a prior candidate listing may remain reusable if valid.
- Preserve source `Evidence` end-to-end; reviewed or fallback selections must attribute the actual provider and selected source ID, not rewrite identity under a primary provider key.
- Identical operation inputs may deduplicate; every source record must still expose its decision/audit reference even if an HTTP request or handler call was reused. Never deduplicate away review notices.
- Avoid raw input data dumps, full HTTP URLs, headers, credentials, excessive candidate payloads and unrestricted PII in logs. Keep only bounded diagnostic attributes needed for review.

## 7. Verification and evaluation

Mandatory deterministic fixtures:
- **Berlin**: two exact-name results; configurable popularity policy selects the capital with explicit alternatives; explicit Schleswig-Holstein chooses `2950158` despite popularity; order independence; unknown/missing region preserved.
- **Everest**: `Q513` has verified mountain/country claims while unrelated `The Climbers` film should not consume broad ontology budgets without relevance; additional plausible unverified mountain still triggers abstention when uniqueness cannot be demonstrated.
- **Typo**: no silent fuzzy reinterpretation in primary lookup; later assistant proposal is separate.
- Same-name/different-region context, empty/malformed upstream responses, tie/ambiguous/no-match, truncated search, country conflicts, evidence retention, policy/version invalidation and cross-row dedup.
- Measure independent auto-match precision, false auto-match rate, coverage, review and incomplete-verification rates, requests per resolved entity, latency and budgets. Report denominators and avoid untested confidence percentages.

All routine CI tests are offline; live smoke is opt-in and bounded. M2 final review remains open until #37 and #44 are reconciled.

## 8. Rollout, scope and open review items

Use [Resolution delivery roadmap](09-resolution-rollout.md) and issues #61–#68. M2 is foundation and deterministic verified resolution; M3 is workbook-preserving manifest/export and offline review/quality evaluation; M4 is opt-in LLM clarification/repair; M5 is UI Review Workspace.

**Decide before implementation (#61):**
1. Exact side-channel/report interface from provider through memoized handler to each record.
2. Required vs optional normalized candidate attributes and versioned schemas.
3. Default strict policy versus opt-in prominence and which decisions qualify for immediate downstream execution.
4. Whether bounded candidate retrieval can be reused before decision caching, and safe invalidation.
5. Handling of uncertain search completeness without accidentally treating it as a verified unique answer.

**Not in this design PR:** runtime code changes, speculative score thresholds, LLM-based auto-repair, full UI, multi-provider entity reconciliation or arbitrary rules DSL.


## 9. First concrete contract (review candidate)

The schema and user-readable examples are specified in [Resolution Report Contract v1](10-resolution-report-contract.md). This is a separate resolution/result/audit format from `OperationResult` and introduces no automatic ranking or pause/resume behavior until later implementation issues. Confirm the report contract, including ranked opt-in, incomplete-search abstention, user-confirmed decisions, partial-run envelope, and record links, before integrating the adapters (#62–#65).
