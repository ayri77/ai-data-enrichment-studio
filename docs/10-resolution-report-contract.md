# Resolution Report Contract v1 — Issue #61

**Status:** Review candidate (models implemented in `resolution/models.py`; not connected to execution)  
**Date:** 2026-11-03  
**Architecture:** [ADR-RES-001](08-resolution-foundation.md)  
**Follow-up wiring:** #62–#65 (M2), #66 (M3), #67 (M4), #68 (M5)

## 1. Why a separate result format?

The existing `OperationResult` models execution as `success | failure`, carries a typed `RuntimeValue` and source `Evidence`, and drives DAG dependencies and **success-only** memoization. Do **not** replace that contract with a multi-state resolution status.

Resolution has a different question: *which candidate, if any, identifies the entity; how certain is the completeness of verification; what did policy or the user decide; and what still needs review?* The new report models that question without changing execution semantics.

This is **not a runtime feature** yet. The classes validate/serialize structured payloads; provider adapters and the job executor do not currently produce them. The integration and audit transport are owned by #62–#65.

## 2. Proposed payload hierarchy

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

The `operation_id` is an opaque key supplied by the caller. Its stable cryptographic derivation is **not** part of the v1 schema; #65 must account for typed context, effective policy/config, provider/adapter versions and input privacy before persisting or reusing it. The `revision` tracks an operation's decision update, and `previous_job_id` links a second processing pass; M3/M4 will define durable history/override behavior.

### Resolution statuses

| Status | Meaning | Safe downstream interpretation |
| --- | --- | --- |
| `resolved` | Verified selection, unique or confirmed by user | Can produce a successful typed operation if source evidence and required checks pass |
| `auto_resolved` | Verified, uniquely top-ranked candidate accepted under **explicit ranked policy** | May produce success under effective approved policy; audit alternatives and reason |
| `needs_review` | Multiple plausible candidates or unresolved decision | No final value; dependent DAG steps must not consume tentative selection |
| `unresolved` | Search and verification completed; no acceptable match | Negative result, not a typo correction; potential later repair proposal |
| `incomplete_verification` | Search/page/hierarchy/verification limit prevented a definitive conclusion | Retain observed candidates, abstain until additional approved context or manual confirmation |
| `provider_failure` | HTTP/API/source failure interrupted discovery or verification | Technical error, not evidence that the entity is absent |

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
- **Additional column:** “Use `Bundesland` as the administrative region.” Binding is through a validated `administrative_area` input (planned #62); this is **input context**, not ranking preference.
- **Search depth:** separate bounded application/search policy. More depth may increase cost; it does **not** turn incomplete source evidence into proven uniqueness.

No arbitrary percentage confidence threshold is part of this contract. `rank` and `reason_codes` are **explanations**, not calibrated probabilities.

## 4. Example — unresolved first pass, clarification second pass

1. First job scans its input as supplied. It may find Berlin twice and record `needs_review`; or Mount Everest `Q513` and an unchecked alternative, recording `incomplete_verification`. A genuine exact-search no-match remains `unresolved` rather than silently corrected.
2. The review analyzer groups identical **operation+context+policy** decisions into one question linked to every affected record; it may ask for a state column, direct source ID or a scoped rule. These questions and answers are an **M3 export/import feature**, not implemented in #61.
3. After user input and plan validation, a second job may refer to `previous_job_id`, produce a new report revision and execute only affected operations/dependencies when supported. The chosen source must be evidence-backed.
4. If the user ends the workflow before answering, keep a partial result and `cancelled` or `awaiting_review` job status. Unprocessed rows are **not** counted as failed or silently filled.

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

This shape **represents** cancellation; it does not add pause/abort/resume behavior to the current sequential `JobExecutor`. An exported artifact must also carry the existing `JobExecutionResult` values and metrics when execution runs; the new resolution manifest complements it rather than replacing it. M3 owns file output and manifest assembly.

## 5. Important invariants and deferred work

- Immutable Pydantic models with strict unknown-field rejection and JSON round-trip.
- Verified candidates require `Evidence`; a final selection must reference a verified candidate in the same report.
- Ranked auto-selection requires opt-in and a **single eligible rank-1 candidate**, plus complete source/verification exploration (unless an explicit user override).
- No `unresolved` when retrieval/verification is incomplete; no `provider_failure` without a provider error code.
- Candidate assessments cannot name absent candidates; duplicate identities/attributes/record links and impossible job row counts are invalid.
- No raw provider payloads, credentials, large unbounded logs or sensitive source fields should be dumped into resolution attributes. Future producers must enforce allowlisted/sanitized fields and candidate count limits.
- Ranking features, provider adapters, cache key derivation, audit transport through `JobExecutor`, file persistence, user responses, LLM proposals, cancellation mechanics and UI are separately owned by #62–#68.


## 6. First optional context and provider normalization slice (#62)

**Implementation status:** The `geocode` capability contract becomes v1.1 with optional `administrative_area: Text`, recognized and type-checked through the existing `PlanValidator`. A plan can bind a workbook column called `Bundesland` to that input without changing its physical header. The legacy `city` + `country` geocoder remains available and still fails closed on multiple exact-name matches.

**Provider-specific Open-Meteo semantics:** Without an administrative area, the existing strict result selection is unchanged. If supplied, the region acts as a hard filter on matching provider `admin1` fields (case/space-insensitive name or recognized ISO 3166-2 region code for the same country). An unknown `admin1` among exact matches yields `CONTEXT_UNVERIFIED` rather than pretending uniqueness. Unsupported region aliases are **not** silently fuzzy-corrected. An explicitly mapped but blank cell currently yields `MISSING_VALUE` before the handler; the user may omit the optional input mapping if it is not available. Graceful per-row omission of blank optional context would require an explicit executor contract update.

`normalize_open_meteo_candidates` is a **pure adapter helper**, with source-qualified GeoNames IDs, checked coordinates, region, population and known administrative roles; unrecognized feature codes do not imply an invented classification. Saturating the bounded 10-result page records `search_complete=False`. These candidate sets are not yet emitted in `JobExecutionResult` (#65).

**Wikidata semantics:** `normalize_wikidata_search_candidates` turns exact-label/alias search matches into `unchecked` source-qualified candidates. A result such as `Q513` is not yet a verified mountain, and a film matching the search term is not classified as a mountain merely because the user requested one. The source-specific P31/P279/P17 checks and the report wiring remain #64/#65. Search-completion metadata stays separate from identity verification.

**Cache isolation:** The unchanged operation-key logic already includes the typed `administrative_area` input when supplied, preventing a Berlin/State of Berlin result from colliding with Berlin/Schleswig-Holstein. Provider configuration/capability versions must be updated deliberately for persistent reuse.

No LLM ranking, generalized rule language, automatic input repair, Excel/CLI review workflow, or web UI is introduced in #62.
