# M3 Core MVP — Acceptance and Explicit Deferrals

**Status:** Pending final PR #89 CI gate and sign-off.  
**Scope:** Academic MVP Excel execution, deterministic validation, reproducible
evaluation and documented constraints. This report does **not** assert
production readiness or universal entity-resolution accuracy.

## Delivered and verifiable

| Core acceptance criterion | Evidence | Disposition |
| --- | --- | --- |
| Select a logical Excel table and preserve physical row identity | `TableSelection`, `ExcelEnrichmentRunner`, integration tests (#78, #79) | Implemented |
| Validate a saved plan and execute approved provider DAG | Capitals CLI E2E (#81), mountain Excel execution (#79) | Implemented |
| Write results without modifying input and preserve unrelated cells/formulas | `validate_excel_targets`, `write_excel_cells`, workbook tests | Implemented in **empty-target-only** mode |
| Preserve source evidence, candidate decisions and original row/step audit links | Resolution reports and CLI E2E tests | Implemented |
| Human approval/resume | Workbook/job-bound Wikidata mountain approval demo | Implemented for mountains, **not** a universal review engine |
| Explicit numeric units | Optional `OutputMapping.expected_unit`; rejects wrong `Quantity.unit` before output publication | Implemented **when declared**; legacy plans without unit declarations remain accepted |
| Repeatable 1,000-row Excel execution benchmark | `scripts/benchmark_m3_excel.py`, `benchmarks/scenarios/`, offline CI/tests (#82) | Implemented; repeated queries, not 1,000 distinct entities |
| External reference checks for both academic examples | `benchmarks/references/m3_external_smoke.json`, `test_capitals_excel_e2e.py`, `test_m3_external_reference.py` | **Two curated example-level checks**, not statistical validation |

## External reference evaluation

The acceptance fixtures are **separate** from the independent public
references. Each sample represents a specific named entity, not an
accuracy estimate for all possible inputs.

| Scenario | Public reference | Tested value / tolerance | What is validated |
| --- | --- | --- | --- |
| Berlin → Kyiv | Berlin City Hall coordinates from Berlin Business Location Center; Kyiv coordinates from University of North Texas verified catalog | Reference 1203.98937 km, tolerance ±5 km | One actual output of offline Capitals CLI E2E, approximate geodesic consistency across independently recorded location coordinates |
| Mount Everest | Official Nepal–China joint 8 Dec 2020 measurement (published by China's Ministry of Foreign Affairs) | 8848.86 m, tolerance ±0.01 m | One checked-in **prior live-reviewed** workbook artifact plus explicit unit; **not** a fresh network run |

Sources:
- Berlin City Hall: https://www.businesslocationcenter.de/en/business-location/berlin-at-a-glance/surface-area-of-berlin
- Kyiv: https://digital.library.unt.edu/explore/locations/p10266/
- Everest: https://www.mfa.gov.cn/zyxw/202012/t20201208_348872.shtml

The two comparisons use a total denominator of **two** independent reference
examples, one per mandatory scenario. A pass is **2/2 checked examples**
when the corresponding tests pass; this is not an auto-match precision rate,
false-match rate, recall estimate or population coverage statistic.
Everest's 8848.86 m is the official 2020 snow-surface elevation, not
a guarantee that all Wikidata height claims or later versions agree.

## Safety and known limitations

- **No overwrite permission:** existing nonempty target cells, including
  formulas, are rejected before provider execution, and the writer checks
  again before publishing. Missing authorization features are intentionally
  **not** emulated by automatic overwrite. This covers core MVP preservation;
  advanced explicit overwrite permissions and broader mixed-type cases
  remain enhancement issue #21.
- **Unit contract is opt-in for compatibility:** `expected_unit="km"`
  must match a successful `Quantity.value`. A mismatch rejects the output.
  The system does not infer units from names, automatically convert them or
  require unit declarations in legacy plan JSON. Strict required-unit mode
  and conversion are deferred under #20.
- **Review extension is not complete:** existing structured audited JSON
  reports and scoped Wikidata approvals are available, but grouped CLI
  questions, generic bulk overrides and broad quality dashboards remain #66
  (and later #67/#68 in M4/M5).
- **Entity-resolution quality:** no large hand-labeled validation set is
  claimed. Provider outages, ambiguous geographic names and incomplete
  Wikidata type verification must abstain/fail visibly.
- **Workbook contract:** supported .xlsx structures are preserved within
  the tested openpyxl writer contract; arbitrary spreadsheets, formula
  evaluation and automatic multi-table discovery are not promised.

## Reproduction / completion gate

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest -q
uv run --locked python scripts/benchmark_m3_excel.py --rows 1000
```

Record the successful PR CI and merge before marking this report
accepted. M4 may reuse the deterministic `PlanValidator` and
`JobExecutor` directly; there is no need to expand benchmark
simulators, audit schema or Excel writer before the first graph slice.

## M3 Core MVP exit decision

Close Epic #4 when PR #89 CI is green and merged **with these explicit
deferrals**. Keep #20, #21, #22 and #66 open for their remaining
non-core-MVP scope, with no requirement to implement them before M4-1.
