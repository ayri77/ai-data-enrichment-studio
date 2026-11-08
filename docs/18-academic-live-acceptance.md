# Official Neoversity Academic Acceptance — Live Workbook Runs

**Recorded:** 2026-11-09. **Issue:** [#95](https://github.com/ayri77/ai-data-enrichment-studio/issues/95) (open).
**Implementation:** [PR #96](https://github.com/ayri77/ai-data-enrichment-studio/pull/96), merged to `main` at `19ac7e4`.
**Scope:** Actual 10-row `samples/mountains.xlsx` and `samples/capitals.xlsx`, live external providers, saved LLM-generated plans, separate output workbooks.
This is a record of observed CLI runs, **not** a reproducible network snapshot or broad accuracy study.

## Recorded live outcomes

| Dataset | Configuration | Successful rows | Partial | Failed | Main unresolved input |
| --- | --- | ---: | ---: | ---: | --- |
| Mountains | `--mountain-resolution-policy verified_only` | **9/10** | 0 | **1** | `Sheet1!11`: Annapurna |
| Capitals | Ordinary `geocode`, no `required_role` | **6/10** | **4** | 0 | Kyiv and Berlin homonyms |

The status totals are **per workbook row**, not per entity or per successful individual DAG step. A partial Capitals row does **not** contain a completed distance. The CLI output reported `DEPENDENCY_FAILED` for its downstream distance operation.

### Mountains: nine completed rows

The selected Wikidata entities in order were Mount Everest (`Q513`), K2 (`Q43512`), Kangchenjunga (`Q82019`), Lhotse (`Q168702`), Makalu (`Q169986`), Cho Oyu (`Q170089`), Dhaulagiri (`Q165440`), Manaslu (`Q170070`), and Nanga Parbat (`Q130736`). Elevation outputs were written in metres into the existing empty `height` column.

`Sheet1!11` (Annapurna) returned `INCOMPLETE_VERIFICATION`: no candidate was positively confirmed as the requested mountain in context. The search included `Q159621` with `hierarchy_verification_incomplete`. The pipeline did **not** silently interpret the input as Annapurna I or insert an unverified height. This remains a useful candidate for future user clarification.

**Safety limitation:** `verified_only` requires a complete search without provider failure and one positively verified eligible candidate, but it may accept that candidate when verification of *other* search matches is incomplete, including at the 12-candidate budget. The report retains `verification_complete=false`, the original `incomplete_reason`, unchecked alternatives and `basis="verified_only"`. This is a measured coverage policy, not proof of unique identity. The default `strict` policy remains unchanged.

### Capitals: six completed rows, four partial

The first LLM plan incorrectly added `required_role="country_capital"` despite the presence of Istanbul, which is not the capital of Türkiye. A clarified natural-language instruction regenerated a valid plan with empty `geocode.parameters`; neither city was silently replaced.

An initial live run completed 5/10 rows. The remaining technical input failure was `Unrecognized country: 'Turkey'`. PR #96 introduced shared exact country normalization (`Turkey` → `TR`) across ordinary/audited Open-Meteo resolution and mountain parsing. After this fix, the repeated 10-row live run completed **6/10**, with the remaining partial rows exclusively due to ambiguities:

| Location | Affected Excel rows | Provider outcome |
| --- | --- | --- |
| Kyiv, Ukraine | `Sheet1!2`, `Sheet1!6` | `AMBIGUOUS` |
| Berlin, Germany | `Sheet1!4`, `Sheet1!5` | `AMBIGUOUS` |

These four row-level problems reduce to **two repeated input/context queries**, which motivates grouped user confirmation rather than four separate questions. The unparameterized live geocoder provides source evidence at operation level; it does not currently offer the same full generic approval/resume flow as mountain audited resolution.

## Reproduction notes

Run from the repository root. The source workbooks are tracked, but the generated plans, output-column binding files, results and resolution reports used for these runs were retained **locally** under `temp/` and are **not committed**. Generate and validate equivalent plans using `enrichment plan generate` and `enrichment plan evaluate` before running these commands; see the [README](../README.md). Plan generation requires an explicitly configured model API key; executing an existing saved plan does not.

The following commands reproduce the *configuration* of the recorded execution once the local plans exist:

```powershell
# Output mappings, for the existing empty target cells:
'{"height":"C"}' | Set-Content temp/academic/mountains_columns.json -Encoding ascii
'{"distance":"E"}' | Set-Content temp/academic/capitals_columns.json -Encoding ascii

uv run --locked enrichment run samples/mountains.xlsx `
    --plan temp/academic/mountains_plan.json `
    --sheet Sheet1 --range A1:C11 `
    --columns-file temp/academic/mountains_columns.json `
    --max-rows 10 --mountain-resolution-policy verified_only `
    --output temp/academic/mountains_verified_only_recheck.xlsx --allow-network

uv run --locked enrichment run samples/capitals.xlsx `
    --plan temp/academic/capitals_plan_cities.json `
    --sheet Sheet1 --range A1:E11 `
    --columns-file temp/academic/capitals_columns.json `
    --max-rows 10 `
    --output temp/academic/capitals_live_recheck.xlsx --allow-network
```

Use fresh output filenames each time; never overwrite source files or previous audited reports. Live provider data, candidate ordering and availability may change, so coverage is not deterministic. Do not commit API keys or generated workbook/review artifacts.

## Remaining academic gate — do not close #95 yet

- [x] Official source workbooks were exercised with live providers and the correct physical output columns.
- [x] Safe empty-target writing, multi-country mountain context, explicit country normalization and city ambiguity handling are implemented and covered by automated tests.
- [x] PR #96 passed the GitHub Actions `quality` job; the local implementation reported Ruff/Pyright success, **1,207 passed, 2 skipped**.
- [ ] Archive per-row numerical comparisons against appropriately cited independent references, including the distances in `capitals_live_10_v2.xlsx`, with the university ±10% acceptance criterion. Do not equate executable success with independently verified accuracy.
- [ ] Record measured request counts/latency and verify source workbook immutability by checksum for the final live artifacts.
- [ ] Produce final surname-named enriched XLSX deliverables and a concise submission report. Keep unresolved cells unfilled unless approved and re-executed.

## Follow-on UI work

Continue with [M5 #6](https://github.com/ayri77/ai-data-enrichment-studio/issues/6) and [Review Workspace #68](https://github.com/ayri77/ai-data-enrichment-studio/issues/68): grouped distinct-entity questions, candidate/context/evidence display, explicit approval, scoped multi-row application and replay of dependent operations. Generic review-export quality evaluation remains [#66](https://github.com/ayri77/ai-data-enrichment-studio/issues/66); optional LLM suggestions remain [#67](https://github.com/ayri77/ai-data-enrichment-studio/issues/67). Do not silently classify ambiguous location names based on their prominence.
