# M2 Provider Contract and Semantic Drift Audit (#44)

**Status:** Reviewed source interfaces on `main` after PR #74; targeted compatibility fixes
and offline regression fixtures are in draft PR #75 (2026-11-03). This document is
an audit of **adapter assumptions**, not a claim that live provider datasets are
correct or that a fresh network smoke has been completed.

## 1. Scope and risk taxonomy

- **API/schema drift:** JSON envelope, field type, keying or numeric/unit format
  changes; fail with `INVALID_RESPONSE`/`PROVIDER_ERROR` and no fabricated value.
- **Entity/ontology drift:** Wikidata merges/redirects, changes in P31/P279/P17,
  missing subclass paths or direct type restrictions; do not attribute another
  QID's claims to the requested source identity or turn unknown into false
  `NOT_FOUND` or verified success.
- **Source data drift:** New GeoNames rows, admin roles, country labels, changed
  population or Wikidata elevation values. Cached *resolved* values are only as
  current as the provider and configured TTL; a successful source lookup is not
  independently verified ground truth.

## 2. Risk-ranked findings and mitigations

| Risk | Boundary / files | Failure mode | Mitigation / state |
| --- | --- | --- | --- |
| **High** | Wikidata `wbgetentities` in `wikidata_verification.py`, `wikidata_country.py`, `wikidata_ontology.py`, `wikidata.py` | A response keyed by requested QID can contain an actual entity `id` for another QID after a merge/redirect. Source evidence could then be incorrectly attributed to the old QID. | **Fixed in PR #75:** central provider helper compares returned `id` when supplied with requested QID; mismatch returns explicit incomplete verification / hierarchy, never follow redirect implicitly or claim value. Malformed IDs are invalid responses. |
| **High** | Open-Meteo `open_meteo.py` and `open_meteo_resolution.py` | A changed non-boolean `error` flag could silently fall through to the ordinary no-results path. | **Fixed in PR #75:** malformed `error` field returns `INVALID_RESPONSE`; boolean `true` remains `PROVIDER_ERROR`; no results with omitted `results` remains allowed. |
| **High** | Shared Resolution Engine `resolution/engine.py`, normalized adapters, audited SQLite | A truncated page, unknown ontology branch, broken cache entry or context switch could create a plausible wrong auto-selection. | Already controlled by #62–#65: incomplete search/verification abstains; policy/context/version in cache identity; selected source, Evidence and coordinates/QID cross-validated on stored cache pairs. |
| **Medium** | Open-Meteo normalized GeoNames feature codes, `open_meteo_candidates.py` | Administrative-role interpretation can change as GeoNames data changes; `PPLC` ranking is not a probability. | Curated roles confined to adapter, promotion to auto-resolve opt-in only, all homonyms and source evidence retained; no uncalibrated population threshold. A later source refresh can change a selection. |
| **Medium** | Wikidata `P31`/`P279` types + `P17`/`P297`, `wikidata_constants.py` | Ambiguous or incomplete class hierarchy, missing country, ranking changes; sole P31=film short-circuit might become outdated. | Bounded hierarchy and candidate budgets, mixed type remains unchecked, direct-film exclusion deliberately tiny. Class and country IDs are adapter-specific constants. Live ontology drift still requires periodic optional manual smoke; never bypass missing verification. |
| **Medium** | Wikidata `wbsearchentities`, `wikidata_search.py` | Too many aliases or page continuation drift, incomplete candidate enumeration. | 3 bounded pages of 50, explicit continuation validation, 12 candidate verification cap. Partial discovery is `incomplete_verification`, not proof of uniqueness. |
| **Medium** | `http_transport.py`, `retry_transport.py`, `request_policy.py` | Network/timeout/429/5xx, malformed JSON, excessive Retry-After or unbounded API requests. | Typed transport failures, finite retries and total wait, provider-specific physical request caps. Live diagnostic uses a descriptive User-Agent; arbitrary caller headers are injectable, not automatic. |
| **Low** | `geodesic.py` | Spherical Haversine produces slightly different distances from ellipsoidal WGS84 geodesic calculations. | Deterministic, documented radius `6371.0088 km`, same-point/antipodal bounds and `Quantity(unit=km)`. Ellipsoidal accuracy is an independent evaluation/requirements decision in M3, **not** a provider drift incident. |
| **Low** | `wikidata_parsing.py` / `wikidata.py` elevation | Multiple equal-rank P2044 claims or incompatible units; missing/changed quantities. | Explicit `AMBIGUOUS` / `UNSUPPORTED_UNIT` / `INVALID_RESPONSE`; no silent unit conversion or arbitrary choice; preserve Wikidata Evidence. |

**Known limitation:** For backward-compatible minimal offline fixtures, a
`wbgetentities` entity dictionary with no `id` field is still accepted based
on the response object's key. A **present but different** source ID is explicitly
rejected. A future stricter contract requiring `id` everywhere would need a
coordinated fixture migration and separate review, not an untested release change.

**No automatic redirect-following yet:** A detected QID mismatch abstains; it
does not silently change the canonical `EntityRef` or resolve a new QID.
A later review workflow can propose a source-reverified replacement with a
new decision and evidence rather than rewriting the prior result.

## 3. Verification matrix (offline by default)

- Matched, redirected and malformed source QIDs at mountain verification,
  country ISO verification, bounded class traversal, elevation lookup.
- Malformed Open-Meteo error envelopes fail closed in strict and audited paths;
  valid `error=true` remains a provider failure.
- Existing tests cover extra fields, invalid coordinates, saturation/continuation,
  unknown class, missing P17/P297, multiple best-ranked elevation statements,
  multi-country mountains, transport and Retry-After budgets.
- Existing #65 integration verifies 1,000 input records, source-qualified
  provenance across memo/SQLite, region-specific choices and per-row links.
- Final measured scenario benchmarks, independently validated latency and code
  quality review belong to [#37](https://github.com/ayri77/ai-data-enrichment-studio/issues/37).

## 4. Optional bounded live diagnostics (not CI)

Use the existing developer-only script **only when explicitly authorized**;
it still invokes the legacy strict resolution path, not an audited ranked
`JobExecutor` configuration:

```bash
uv run python scripts/smoke_live_providers.py --scenario capitals --mode live --allow-network --diagnose
uv run python scripts/smoke_live_providers.py --scenario mountains --mode live --allow-network --diagnose
```

Per execution, the script accepts **one record**, no LLM; Open-Meteo is capped
at **4** physical attempts and Wikidata at **16**, paced and retry-free. It logs
sanitized public metadata only. The smoke is useful for identifying provider
contract changes; **it is not a truth evaluation** of source heights or
geodesic distances, and is not proof that the opt-in ranked audited path was
exercised. No live requests were made for PR #75.

## 5. External API contract references

- [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api):
  `results` omitted when empty, `error: true` error envelope, feature code
  and admin fields, country filter, result limit.
- [Wikibase Action API](https://www.mediawiki.org/wiki/Wikibase/API):
  `wbgetentities` response shape, `error.code`, entity IDs and optional
  fields. The interface can resolve redirects, so code must not assume a
  returned entity `id` always equals the caller's requested QID.
- [Wikidata Data Access](https://www.wikidata.org/wiki/Help:Data_access):
  use `wbgetentities` for small groups of entity metadata. This project
  retains bounded paging and caps entity verifications.

**Review/exit criteria:** PR #75 clean Ruff/Pyright/full offline pytest,
documented risk dispositions, no apparent success from an unverifiable or
redirected entity, and explicit acknowledgement of deferred dynamic API
validation. Only then close #44 and continue to final #37 benchmark acceptance.
