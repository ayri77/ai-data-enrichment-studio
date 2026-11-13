# Resolution Report Contract v1 — Issue #61

**Status:** Implemented — Resolution Report v1 is integrated with the
M2 execution engine (#61–#65). M3 adds persisted Excel audit reports
and approved Wikidata mountain resolution resume (PR #79).

**Date:** 2026-11-03
**Updated:** 2026-11-07  
**Architecture:** [ADR-RES-001](08-resolution-foundation.md)  
**Follow-up wiring:** #62–#65 (M2), #66 (M3), #67 (M4), #68 (M5)

## 1. Why a separate result format?

The existing `OperationResult` models execution as `success | failure`, carries a typed `RuntimeValue` and source `Evidence`, and drives DAG dependencies and **success-only** memoization. Do **not** replace that contract with a multi-state resolution status.

Resolution has a different question: *which candidate, if any, identifies the entity; how certain is the completeness of verification; what did policy or the user decide; and what still needs review?* The new report models that question without changing execution semantics.

The Resolution Report contract is implemented and integrated into
the execution engine.

Provider-specific resolution services return structured candidate
sets, verification results, decisions, and Evidence. The JobExecutor
can attach audited resolution reports to execution results while
preserving the original OperationResult success/failure semantics.

M3 extends this foundation with persisted JSON audit reports,
validated user approval manifests, and review-based resume for
Wikidata mountain resolution.

This does not provide general-purpose workflow checkpointing,
automatic clarification, or unrestricted entity correction.

## 2. Implemented payload hierarchy

| Type | Key fields / intent |
| --- | --- |
| `ResolutionAttribute` | Named typed `Text/Int/Float/Bool` fact, optional evidence. No arbitrary JSON, no implicit fuzzy normalization. |
| `ResolutionQuery` | Original name + entity type + named, registered context facts (e.g., country code, admin area). |
| `ResolutionCandidate` | Source-qualified `EntityRef`, label, normalized type, attributes, source Evidence, verification state (`verified/rejected/unchecked`). |
| `CandidateSet` | Provider, query, candidate list, search and verification completeness, bounded incomplete reason and provider error code. |
| `CandidateAssessment` | Decision-layer per-candidate `eligible/excluded/unchecked`, rank and reason codes; it does **not** rewrite provider facts. |
| `ResolutionPolicy` | Policy ID + version; `strict` (default) or `ranked`, opt-in auto-accept and prominence. Search/HTTP budgets live elsewhere. |
| `ResolutionDecision` | Resolution status, selected source ID if any, unique/ranked/user choice basis, machine reason codes and readable explanation. |
| `ResolutionReport` | Versioned candidate set + policy + candidate assessments + decision; cross-field safety validation. |
| `ResolutionOperationAudit` | Stable caller-provided operation identity, revision, report and **all** record/step links sharing it. |
| `ResolutionJobReport` | Job/run identity, optional prior run, completion/review/cancellation state and row progress plus operation audit entries. |

The `operation_id` is an opaque caller-provided identity; its
derivation is not prescribed by the v1 report schema.

M2 #65 implemented policy-aware operation identities and audited
cache integration. The `previous_job_id` field links processing
passes, including M3 approval-based resume.

### Resolution statuses

| Status | Meaning | Safe downstream interpretation |
| --- | --- | --- |
| `resolved` | Verified selection, unique or confirmed by user | Can produce a successful typed operation if source evidence and required checks pass |
| `auto_resolved` | Verified, uniquely top-ranked candidate accepted under **explicit ranked policy** | May produce success under effective approved policy; audit alternatives and reason |
| `needs_review` | Multiple plausible candidates or unresolved decision | No final value; dependent DAG steps must not consume tentative selection |
| `unresolved` | Search and verification completed; no acceptable match | Negative result, not a typo correction; potential later repair proposal |
| `incomplete_verification` | Search/page/hierarchy/verification limit prevented a definitive conclusion | Retain observed candidates, abstain until additional approved context or manual confirmation |
| `provider_failure` | HTTP/API/source failure interrupted discovery or verification | Technical error, not evidence that the entity is absent |

**Facts for telling namesakes apart.** A Wikidata mountain candidate carries, when the source has them, the attributes `description` (the text the search returned with the hit, at most 160 characters), `latitude` / `longitude` (P625, Earth only) and `elevation_m` (P2044 in metres). They come from the responses verification already fetched and cost no request. Each is recorded only when it is unambiguous — one best-ranked statement — and is otherwise left out without an error. They are for a person choosing between several verified candidates on Review; no decision reads them, and the value a plan writes still comes from its own `lookup_property` step.

**Which country names are recognized.** `normalize_country_code` first looks the cell up among English ISO names and alpha-2/alpha-3 codes (`pycountry`), then among the country names of the Unicode CLDR in all its languages (`babel`): "Russia", "Росія", "Russland", "Deutschland", "UK", "Holland". Case, spacing, character width and the shape of the apostrophe are ignored; nothing else is. A name is accepted only when it stands for exactly one country: a misspelling is not corrected, and a name that means different countries in different languages ("Kongo") is not recognized. Not covered: short forms that CLDR lists only as alternatives ("США", "Great Britain") and territories without an ISO 3166-1 code (Kosovo). The table is built once per process, on the first name the English lookup does not know (about 0.7 s).

**A row's own unusable value.** A cell the lookup cannot use costs that row, never the run, and no request is made for it. The operation fails with `UNRECOGNIZED_COUNTRY` (a country that is not recognized, or a malformed slash-separated list) or `NAME_TOO_LONG` (a name over 512 characters). Its audit entry has no candidates and no provider error, status `incomplete_verification`, and the reason code `country_not_recognized` or `name_too_long` in both `decision.reason_codes` and `candidate_set.incomplete_reason`; the query keeps the name and, as context `country`, the country exactly as written. A cell holding only whitespace is an empty cell (`MISSING_VALUE`), decided by the executor before any handler runs. Services raise `UnusableInput` (`resolution/input_errors.py`) for these cases; `AuditedResolutionHandler` turns it into the failed operation and its audit entry.

A candidate's `verified` flag means *the provider supplied evidence supporting its identity and checked attributes*, **not** that all competing candidates were exhausted. Those are distinct `CandidateSet` completeness flags.

A user may explicitly select an already verified candidate even from an incomplete candidate set; that selection is labeled `user_override` and the partial-search metadata remains visible. An *unchecked* candidate cannot be promoted to a definitive result just because the user selected its search-list position; it must be verified first.

## 3. Example — Berlin auto-selection with alternatives

Illustrative v1 payload (shortened only by omitting optional fields):

```json
{
  "schema_version": "1.0",
  "candidate_set": {
    "query": {
      "name": "Berlin",
      "entity_type": "city",
      "context": [{"name": "country_code", "value": "DE"}]
    },
    "provider_id": "open-meteo",
    "candidates": [
      {
        "reference": {"source": "open-meteo", "entity_id": "2950159"},
        "label": "Berlin",
        "entity_type": "city",
        "verification": "verified",
        "attributes": [
          {"name": "country_code", "value": "DE"},
          {"name": "administrative_area", "value": "Berlin"},
          {"name": "administrative_role", "value": "country_capital"}
        ],
        "evidence": [{"source": "open-meteo", "reference": "geonames:2950159"}]
      },
      {
        "reference": {"source": "open-meteo", "entity_id": "2950158"},
        "label": "Berlin",
        "entity_type": "city",
        "verification": "verified",
        "attributes": [
          {"name": "country_code", "value": "DE"},
          {"name": "administrative_area", "value": "Schleswig-Holstein"}
        ],
        "evidence": [{"source": "open-meteo", "reference": "geonames:2950158"}]
      }
    ],
    "search_complete": true,
    "verification_complete": true
  },
  "policy": {
    "policy_id": "geo_prominence",
    "version": "1.0",
    "strategy": "ranked",
    "auto_accept_ranked": true,
    "prefer_prominent": true
  },
  "decision": {
    "status": "auto_resolved",
    "selected": {"source": "open-meteo", "entity_id": "2950159"},
    "basis": "ranking_policy",
    "reason_codes": ["prominent_capital", "exact_name_country"],
    "explanation": "The capital was preferred over another exact-name city."
  },
  "assessments": [
    {
      "reference": {"source": "open-meteo", "entity_id": "2950159"},
      "eligibility": "eligible",
      "rank": 1,
      "reason_codes": ["prominent_capital"]
    },
    {
      "reference": {"source": "open-meteo", "entity_id": "2950158"},
      "eligibility": "eligible",
      "rank": 2,
      "reason_codes": ["exact_name_country"]
    }
  ]
}
```

The example is a **proposed policy outcome**, not current Open-Meteo runtime behavior or a measured probability. Provider metadata must be normalized and checked before constructing such a report.

### Strict vs ranked UI settings (M5 guidance)

- **Strict / `strict` (default):** “When two places have the same name and country, ask me to choose.” Result: Berlin remains `needs_review`. Clear for high-accuracy tasks.
- **Ranked without auto-accept:** “Suggest the most prominent place, but let me approve.” Result: show candidates, rationale, recommendation and `needs_review` status. Candidate ordering is *not* a verified final answer.
- **Ranked with auto-accept + prominence:** “Select a well-supported prominent place automatically, but show me where a choice was made.” Result: `auto_resolved` with alternatives. An explicit state/region is a hard constraint and overrides popularity.
- **Additional column:** “Use `Bundesland` as the administrative region.” Binding is through a validated `administrative_area` input (implemented in #62); this is **input context**, not ranking preference.
- **Search depth:** separate bounded application/search policy. More depth may increase cost; it does **not** turn incomplete source evidence into proven uniqueness.

No arbitrary percentage confidence threshold is part of this contract. `rank` and `reason_codes` are **explanations**, not calibrated probabilities.

## 4. Review → Approve → Resume workflow

The initial M3 implementation supports a validated human-in-the-loop
resume workflow for audited Wikidata mountain resolution.

1. **Initial execution:** The job searches for an entity and records
   candidate alternatives, verification results, and source Evidence.
   If verification is incomplete, the job enters `awaiting_review`.

2. **Human approval:** The user selects a source-verified candidate.
   An approval manifest binds the decision to the original workbook
   checksum, source job, record, step, and operation identity.

3. **Resume execution:** The CLI accepts `--approvals-file` and
   `--source-report`. It validates the approval and re-verifies the
   selected Wikidata entity against the current source data.

4. **New audited result:** A new job references the original job
   through `previous_job_id`. A successful approved selection records
   `decision.basis = user_override` and retains supporting Evidence.

The approved candidate bypasses text-based entity discovery, but not
source verification. An approval cannot force acceptance of an entity
that fails the required checks.

This is a new execution using validated prior decisions, not restoration
of a suspended JobExecutor or LangGraph checkpoint.

### Deferred lifecycle capabilities

Grouped clarification questions, general-purpose corrections, selective
dependent-step replay, and durable execution checkpoints are not part
of the initial M3 resume implementation.

The following cancellation envelope is a conceptual contract example,
not an artifact produced by the current CLI.

Example partial-run envelope:

```json
{
  "schema_version": "1.0",
  "job_id": "job-1",
  "status": "cancelled",
  "total_rows": 100,
  "processed_rows": 20,
  "operations": []
}
```

The `cancelled` status is defined by the report contract, but
interactive cancellation and restoration of partially executed jobs
are not implemented.

The current resume workflow starts a new job and retains provenance
through `previous_job_id`. Durable checkpoints and conditional recovery
belong to M4.

## 5. Important invariants and deferred work

- Immutable Pydantic models with strict unknown-field rejection and JSON round-trip.
- Verified candidates require `Evidence`; a final selection must reference a verified candidate in the same report.
- Ranked auto-selection requires opt-in and a **single eligible rank-1 candidate**, plus complete source/verification exploration (unless an explicit user override).
- No `unresolved` when retrieval/verification is incomplete; no `provider_failure` without a provider error code.
- Candidate assessments cannot name absent candidates; duplicate identities/attributes/record links and impossible job row counts are invalid.
- No raw provider payloads, credentials, large unbounded logs or sensitive source fields should be dumped into resolution attributes. Future producers must enforce allowlisted/sanitized fields and candidate count limits.
- Ranking features, provider adapters, cache key derivation, audit transport through `JobExecutor`, file persistence, user responses, LLM proposals, cancellation mechanics and UI are separately owned by #62–#68.


## 6. First optional context and provider normalization slice (#62)

> **Implementation history:** Sections 6–8 document the incremental
> delivery of provider normalization, ranking, and mountain verification.
> These components were subsequently integrated with JobExecutor in
> M2 (#65, PR #74). M3 PR #79 added CLI-based audited Excel execution
> and approved Wikidata mountain resume.
 
**Implementation status:** The `geocode` capability contract becomes v1.1 with optional `administrative_area: Text`, recognized and type-checked through the existing `PlanValidator`. A plan can bind a workbook column called `Bundesland` to that input without changing its physical header. The legacy `city` + `country` geocoder remains available and still fails closed on multiple exact-name matches.

**Provider-specific Open-Meteo semantics:** Without an administrative area, the existing strict result selection is unchanged. If supplied, the region acts as a hard filter on matching provider `admin1` fields (case/space-insensitive name or recognized ISO 3166-2 region code for the same country). An unknown `admin1` among exact matches yields `CONTEXT_UNVERIFIED` rather than pretending uniqueness. Unsupported region aliases are **not** silently fuzzy-corrected. An explicitly mapped but blank cell currently yields `MISSING_VALUE` before the handler; the user may omit the optional input mapping if it is not available. Graceful per-row omission of blank optional context would require an explicit executor contract update.

`normalize_open_meteo_candidates` is a **pure adapter helper**, with source-qualified GeoNames IDs, checked coordinates, region, population and known administrative roles; unrecognized feature codes do not imply an invented classification. Saturating the bounded 10-result page records `search_complete=False`. The candidate sets were initially produced by provider adapters. PR #74 subsequently integrated audited resolution results into
JobExecutionResult through JobExecutor.

**Wikidata semantics:** `normalize_wikidata_search_candidates` turns exact-label/alias search matches into `unchecked` source-qualified candidates. A result such as `Q513` is not yet a verified mountain, and a film matching the search term is not classified as a mountain merely because the user requested one. Source-specific P31/P279/P17 verification and report integration were subsequently implemented in #64 and #65. Search-completion metadata stays separate from identity verification.

**Cache isolation:** The unchanged operation-key logic already includes the typed `administrative_area` input when supplied, preventing a Berlin/State of Berlin result from colliding with Berlin/Schleswig-Holstein. Provider configuration/capability versions must be updated deliberately for persistent reuse.

No LLM ranking, generalized rule language, automatic input repair, Excel/CLI review workflow, or web UI is introduced in #62.


## 7. Shared ranking and opt-in geocoding resolution (#63)

The first shared `ResolutionEngine.resolve(candidate_set, policy, strategy)` accepts already-normalized `CandidateSet` instances, applies common acceptance/abstention rules, and returns a validated `ResolutionReport`. Its first plug-in is `GeographicRankingStrategy`: it compares source-normalized entity classes, country and explicit administrative region **as hard constraints** and optionally ranks surviving verified geographic candidates by administrative role.

**Decision policy in code:**
- `strategy="strict"` by default: two verified Berlin candidates stay `needs_review`, even if one is a capital.
- `strategy="ranked", prefer_prominent=True, auto_accept_ranked=False`: ranks candidates and exposes the recommendation, but does **not** automatically select.
- `strategy="ranked", prefer_prominent=True, auto_accept_ranked=True`: auto-selects only if **one candidate has a distinct verified administrative role** (for example country capital vs a regular settlement), no required contextual fact is unverified, and the source search and verification are complete. Records all candidates, assessments, rank and reason codes. Hard region overrides prominence.
- When all candidates have recorded comparable population, population may order recommendations within the *same* administrative role. **Population alone does not authorize auto-acceptance**, because we have not calibrated any threshold for acceptable identity risk. Missing population never counts as zero and never creates a decisive ranking.
- Same-name tied settlements, unchecked entity types, missing region evidence, incomplete search pages and remote API problems abstain with separate review/incomplete/provider-failure outcomes.
- Search-result position and numeric LLM confidence are not used. No typo correction during original lookup.

**Opt-in provider API:** `OpenMeteoResolutionService(transport).resolve(inputs=..., policy=...)` returns an immutable `GeocodingResolutionOutcome` containing **both** a typed `OperationResult` and the corresponding `ResolutionReport` from a single bounded Open-Meteo request. Source-specific API fields are normalized in `open_meteo_candidates.py`; the shared engine does not interpret `PPLC`, `PPLA` or GeoNames payloads. Common geographic region matching now lives in `resolution/geography.py`.

**Integration boundary:** The original #63 implementation kept the
audited resolution service separate from the legacy CapabilityHandler
interface to avoid losing structured resolution reports.

PR #74 integrated the paired OperationResult and ResolutionReport
into JobExecutor, preserving per-record audit links and policy-aware
cache identities. Legacy strict handlers remain available.

This design supports future UI examples directly: *Ask me to choose* (strict); *Suggest, don't fill* (ranked without auto-accept); *Automatically prefer a demonstrable capital and tell me* (opt-in ranked auto-accept), plus *Use this Bundesland column* as a context hard constraint. No new UX is delivered by #63.


## 8. Wikidata mountain verification and candidate audit (#64)

**Opt-in provider service:** `WikidataMountainResolutionService(transport).resolve(inputs, policy)` produces `WikidataResolutionOutcome(operation, report)` with a source-qualified, reviewable `CandidateSet`; the existing `WikidataMountainResolver.execute` remains a strict `OperationResult` handler. The service was integrated with JobExecutor and the paired resolution cache in #65. PR #79 subsequently added public CLI execution and approved resume for audited Wikidata mountain resolution.

**Bounded discovery:** `wikidata_search.fetch_wikidata_search` performs up to three pages of up to 50 hits, preserving observed hits and the search-complete flag. It is shared by strict and opt-in flows. If search pages cannot be exhausted, the opt-in service reports `incomplete_verification` with all observed exact name/alias matches kept `unchecked`; the strict legacy handler continues to return `INCOMPLETE_SEARCH`. Neither guesses uniqueness from page 1.

**Candidate semantics:** `wikidata_verification.verify_wikidata_mountain_candidate` preserves the existing P31 best-ranked statement, bounded P279 hierarchy, P17 and country P297 verification, including multi-country mountains and sourced Evidence. A narrow direct-type exclusion for **P31=Q11424 (film)** avoids climbing obviously incompatible film ontology branches; it only applies when *all* best-ranked direct P31 classes are in the explicit, curated non-mountain set. Mixed or unknown direct types still receive normal bounded verification. The rule is a documented semantic shortcut, not an Everest-QID special case and not a broad inferred disjointness engine.

The opt-in service performs provider lookups for at most **12 exact-label/alias candidates** per operation (an application-owned maximum, with smaller allowed bounded overrides). The budget counts only candidates that may need a request. A candidate without a recorded class or without a country is decided from the claims fetched for the whole candidate list and stays `unchecked` at no cost. The ISO codes of all countries the candidates refer to are read in one request per operation and remembered for the run, and the country is checked before the class: a namesake whose countries are not the requested ones is set aside without a request and without a class walk (it stays `unchecked` with `country_or_type_unverified`, as before — a recorded country elsewhere is still not treated as proof), and a candidate in a requested country that is typed as a mountain directly is verified without one. What remains for the budget is the class walk of candidates in a requested country that are typed as something else — a street, but also a massif or a volcano. On live data for twelve test names (2026-11-13) 138 candidates had a class and a country; 88 were typed as mountains directly, most of them in other countries, and each of them used to cost a request for its country's code and a place in the budget. Exact labels are verified first, aliases second, and within each group in the order the source's search returned them. The search rank is not evidence and selects nothing; it only decides which candidates the budget is spent on first. (Until 2026-11-13 ties were ordered by QID as text, which is arbitrary: on live data for "Mont Blanc" with France/Italy, 18 candidates needed lookups, the summit of the Alps, Q583, was the first search result but fifteenth in that order, and the only mountain examined within the budget was a namesake in the French Southern and Antarctic Lands, which `verified_only` then accepted as the single confirmed match.) Every observed candidate remains in the report as `verified`, `rejected`, or `unchecked`; verification budget exhaustion and P279 limits are **not** `NOT_FOUND`. Under `verified_only`, a single verified candidate is accepted next to candidates that were examined without a verdict, but not when the budget ended before every candidate was examined (`candidate_verification_budget_exceeded`): which candidates were reached would then decide the answer. That case is `needs_review` with `single_positively_verified_candidate` and `candidates_not_examined`; the verified candidate stays selectable, so the review offers it and a person confirms it. A missing country claim or unverifiable P31 is not evidence that the mountain is in the wrong country or non-mountain; such matches remain unchecked in the report.

**Decision policy:** The provider-independent `ResolutionEngine` and `MountainRankingStrategy` accept verified mountain+country identities, reject explicitly disproven candidates, and abstain on remaining unknowns. For review, a label match may precede a search-alias match, but **neither label order nor search position authorizes automatic identity selection**. Even with `auto_accept_ranked=True`, two verified mountain candidates remain `needs_review`. A fully verified and unique mountain identity may be selected under strict policy. Existing elevation parsing and quantity Evidence are unchanged.

**Structured verification codes:** The source checker distinguishes `DIRECT_NON_MOUNTAIN_CLASS`, `NON_MOUNTAIN_CLASS`, `SOURCE_ENTITY_MISSING` and `UNVERIFIED_TYPE` without parsing human-readable messages. The public strict handler maps these internal semantic codes back to its original `NOT_FOUND` contract; the audit service uses the stable codes to classify rejected vs unchecked candidates.

**Failures:** Transport, API-schema, malformed-claim and provider errors are distinct from incomplete hierarchy and genuine exhausted no-match. When a later candidate verification fails, a source-error report retains earlier verified/observed candidates but does not produce a final successful selection. No unbounded retries are introduced; existing external HTTP request budgets remain available.

**M2 completion:** Provider semantic drift validation (#44) and
audited job integration (#65) were completed and merged. M2 final
acceptance (#37) includes deterministic offline benchmarks, regression
tests, and provenance checks. Live source requests remain opt-in.


## 9. Audited job execution and paired cache (#65)

**Status:** Implemented in M2 as an explicitly configured
JobExecutor capability. PR #79 extends this foundation with
CLI-driven audited Excel execution and validated approval-based
resume for Wikidata mountain resolution.

The legacy strict Open-Meteo and Wikidata handlers remain available.
Audited provider services must be explicitly configured; general-purpose
approval workflows are not yet implemented.

- `JobExecutor.execute(..., job_id=..., previous_job_id=...)` returns the ordinary `JobExecutionResult` with an **optional** `resolution_report: ResolutionJobReport`. With no audited services, the field stays `None`; existing callers work unchanged.
- Each genuinely invoked DAG resolution step is linked to its source `record_id` and `step_id` (not every skipped dependency). Exact repeated operations with identical report content are grouped into one `ResolutionOperationAudit` with multiple `record_links`. Distinct failures for the same query are **not merged if their reports differ**.
- A matched, audited success may produce typed output for dependent nodes. `needs_review`, `unresolved`, `incomplete_verification` and `provider_failure` do not expose a tentative value; dependent nodes receive `DEPENDENCY_FAILED`, while independent branches continue.
- In-job reuse is success-only and caches the **pair** `OperationResult + ResolutionReport`. `max_memoized_results` is shared across standard and audited handlers for a job. Failure reports are retained for the job-level manifest but **not** added to result caches.
- Optional cross-job storage uses `SQLiteResolutionCache`, a dedicated local DB with strict schema, provider verification, expiry and corruption invalidation. A legacy `SQLiteOperationCache` must remain a **separate database file** because its schema can persist only the operation value and Evidence. Audited persistent hits return the full report or are treated as a miss; a bare legacy cached value can never unlock an audited step.
- Cache identities use the canonical typed input/parameter operation hash, explicit provider/capability versions, application configuration, and a hash of the entire `ResolutionPolicy` plus audited engine version. Changing region, version or effective policy cannot silently reuse a prior selection. These hashes must not disclose original inputs through logs.
- Provider request-session **operation** admission remains outside cache misses; if an admission is denied, the job records a structured `provider_failure` report rather than reusing an earlier decision. Audit fallback is explicitly rejected until a source-qualified paired fallback contract exists.
- A fully iterated job may end `completed`, `awaiting_review` or `failed` depending on its audited decisions; each status retains all processed rows. The `cancelled` status exists in the v1 contract, but **actual user-initiated stop/resume behavior and incomplete-row artifacts are not implemented by #65** and remain later workflow work.
- Offline 1000-row testing confirms operation reuse, distinct regional contexts, stable per-row provenance and expected DAG dependencies. Benchmarks and production live-provider smoke remain separately scoped to #37/#44.

The M2 audited execution and paired-cache infrastructure is complete.
M3 PR #79 adds Excel output, persisted resolution reports, and
validated human-approved resume for Wikidata mountains.

General-purpose review exports, grouped clarification questions,
selective dependent-step replay, and independent resolution-quality
evaluation remain open under M3 #66. Optional LLM-assisted
clarification belongs to M4 #67; the interactive Review Workspace
belongs to M5 #68.
