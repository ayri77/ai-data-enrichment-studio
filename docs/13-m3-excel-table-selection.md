# M3 — Excel Table Discovery and Selection Boundary

**Status:** Design proposal / review requested; **not implemented** (2026-11-04).  
**Parent:** [M3 Epic #4](https://github.com/ayri77/ai-data-enrichment-studio/issues/4).  
**Architecture context:** [Conceptual architecture](02-architecture.md), [domain model](03-domain-model.md), [M2 final acceptance](12-m2-acceptance.md).

## 1. Why this boundary exists

The current `inspect_workbook()` and `profile_worksheet()` support the academic examples by interpreting a worksheet's **first row as its header**. The inspector is a permissive summary; the profiler rejects missing/duplicate headers, scans the worksheet, and sends only bounded samples to the planner. Neither is a general detector of independent tables embedded at arbitrary locations. The completed M2 `JobExecutor` accepts normalized `InputRecord` values, **not Excel cells**, and never chooses a sheet or range.

M3 must not let the profiler analyze one region while the execution adapter reads another. We will establish **one explicit, shared table selection** before profiling, plan validation, row execution, review export, and workbook output. For conventional A1 header sheets, preserve compatibility with the existing behavior until a new reader is explicitly enabled.

## 2. Logical dataset and source coordinates

A **workbook** can contain many worksheets; a **worksheet** can contain zero, one or several logical tables. A logical dataset is a selected table or rectangular cell region, not necessarily the whole sheet.

A future `TableSelection` (working name, not yet a Python model) should identify:

- Source workbook reference and the exact worksheet name (plus stable file identity/checksum in the execution manifest).
- **Selection origin**: user-specified range, Excel structured table name, or a tool-proposed rectangular range confirmed for processing.
- Concrete cell coordinates for the rectangular header and data area; explicit first/last columns, header row, and bounded first/last data rows, with a documented exclusive/inclusive convention.
- Ordered, unique header labels and their physical column positions. The **same labels** must feed `DatasetProfile`, `EnrichmentPlan` column binding, `InputRecord` generation and output mapping.
- Stable per-record mapping to the **actual Excel row index**, even across blank rows. The execution `record_id` alone must not be assumed to encode all workbook mapping or to remain collision-free if sheet names contain punctuation.
- Input column selection used by a validated plan; selecting fewer columns for execution must **not** silently discard original columns from output preservation or change the profiled schema.
- A read policy for formula cells and other unsupported or non-scalar cell types; every unsupported case must have an explicit diagnostic or a documented skip rule.

This belongs in the Excel **adapter/selection layer**, not `EnrichmentPlan`, `JobExecutor`, provider protocols, or generic resolution policies.

## 3. Discovery vs selection vs execution (separate responsibilities)

1. **Workbook inspection:** enumerate worksheets, structured `Excel Tables` (table name + actual range when available), populated-area summaries and plausible rectangular ranges. A candidate is **a suggestion**, not a confirmed table.
2. **Table selection:** choose one discovered candidate or accept an explicit worksheet/range; validate its boundaries, header geometry, duplicates and overlapping/merged structure. If there are multiple plausible regions or uncertainty, **request user selection rather than silently choosing the first**.
3. **Profiling:** derive one `DatasetProfile` from the **selected table only**, with bounded samples/statistics. Do not use this LLM-facing sample as execution data; its values may be truncated.
4. **Record reading:** read the selected data rows (up to the job limit), convert supported cell types to `RuntimeValue | None`, retain a separate row-coordinate map, and never modify the source workbook.
5. **Output:** write only to explicitly authorized output cells in a **new workbook**, preserving all other supported source contents, formula text and formatting. Fail safely if preservation is not guaranteed for a workbook feature.

A named Excel Table is strong structural evidence of a region, but must not automatically override a conflicting explicit selection. Unformatted regions require conservative candidate detection, not an arbitrary `A1` or `max_row/max_column` assumption. An Excel worksheet's used range can contain titles, notes, merged cells, separated data islands and decorative formatting.

## 4. First delivery slice vs later support

| File shape | M3 first slice | Future enhancement |
| --- | --- | --- |
| Single conventional table with header at A1 | Keep legacy profiler behavior, then use selected range for execution | Discover as a candidate |
| Single rectangular table starting at B6 (not A1) | **Explicit user-specified range**, shared by profiler and reader | Automatic proposal with user confirmation |
| Excel structured Table with name and defined ref | Enumerate/select by table name after assessing `openpyxl` non-read-only metadata behavior | Improved automatic ranking |
| Several tables side by side/on one sheet | Require explicit table name/range; no guessed first table | Candidate extraction and optional contextual hints |
| Merged/multi-level headers, arbitrary report layouts | Fail with actionable `unsupported_structure` or require explicit normalized region | Deliberate transformation stage / human review |
| Hidden/filtered rows or formula-driven inputs | Document a consistent inclusion and formula policy before execution | Configurable audited options |
| Non-rectangular datasets / multiple distinct input tables | Out of first slice | Multi-source joins/relationships, separately designed |

**No auto-detection accuracy promise.** The initial two required workbooks may remain the simple worksheet-first-row case, but their format is **not** a universal contract.

## 5. Formula/value and type-policy decision to review

The existing profiler opens with `data_only=True` to inspect *cached results* of formula cells and infer value types; it **does not compute formulas**. `inspect_workbook()` uses `data_only=False` and sees formula expressions. The proposed `excel_records.py` local work currently also uses `data_only=False`, so a formula cell would appear as its expression rather than its numeric cached result.

We must explicitly decide before processing formula-dependent inputs:

- Whether formulas are permitted among **required input columns**, and which representation (formula expression vs potentially missing/stale cached value) is used.
- How to detect absent cached results and avoid presenting stale/unknown computations as verified facts.
- How to preserve **original formulas** in the separate output workbook without calculating or overwriting them.
- How conversion handles `int`/finite `float`/text/null and rejects or records unsupported booleans, time/date, Excel error cells and non-finite values based on the current `RuntimeValue` contract; do not silently stringify identifiers or truncate execution values.

Formula semantics must be consistent between profiling and execution. Do **not** quietly reuse the profiler's `_to_json_value` as an execution conversion: it is intentionally lossy because it truncates samples for the LLM.

## 6. Invariants and validation tests for M3

- A single confirmed table selection is used consistently by **profile, plan, job records and result writer**.
- Unsupported or ambiguous table layouts fail closed rather than enriching the wrong table.
- Source cells, other worksheets, unrelated regions and supported formatting are preserved; output is a **separate** artifact. No implicit writes to nonempty target cells.
- Record-to-original-row mapping survives empty rows, repeated values, row-level errors, per-job deduplication and decision review.
- At least these fixture categories: A1 table; B6 offset; two adjacent tables; no/duplicate headers; blank rows; formulas with and without saved cached results; malformed workbook; merged headers; preservation of an unrelated worksheet.
- Resource control: candidate scan and record reader are bounded and avoid sending the entire workbook to the planner. Large used ranges, styled empties and malicious ZIP/workbook input need explicit admission limits.
- A discovery confidence score (if added) must not be accepted as factual proof or trigger irreversible writes.

## 7. Implementation planning (not a claim of delivered functionality)

Proposed next small local steps, with owner review at each contract boundary:

1. Document and test the existing A1 header-validation behavior (shared `validate_excel_headers` is under **local development**, not on `main` as of this proposal).
2. Define an immutable, typed `TableSelection` and write tests for explicit A1/B6 selections, bounds and multiple logical tables.
3. Refactor the existing `profile_worksheet` to accept a selected region **without changing legacy A1 calls**, and reuse the same header-validation helper.
4. Implement `read_input_records` only for confirmed, valid selections, retaining a reversible row-coordinate map outside the core execution contract.
5. Add safe workbook output, resolution manifest, CLI and independent evaluation in subsequent M3 slices; only then consider more autonomous discovery.

**Design questions for review:** Does MVP need a named-Excel-Table selection before explicit range selection? Which shapes/merged headers are supported? Are hidden rows included by default? Can a formula be a required input value? Should the first slice select **one table per job** and leave multi-table joins for later? These remain **open** until agreed; this document records a proposed direction, not an implementation decision.
