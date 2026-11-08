# M3 — Excel Table Discovery and Selection Boundary

**Status:** Partially implemented — explicit table selection, profiling,
record reading, and safe Excel output delivered in M3 (PRs #78–#79).
Automatic table discovery and advanced workbook support remain future work.
**Updated:** 2026-11-07.
**Parent:** [M3 Epic #4](https://github.com/ayri77/ai-data-enrichment-studio/issues/4).  
**Architecture context:** [Conceptual architecture](02-architecture.md), [domain model](03-domain-model.md), [M2 final acceptance](12-m2-acceptance.md).

## Implementation Status (2026-11-07)

The initial M3 Excel adapter is implemented and merged into `main`.

**Implemented:**

- Explicit rectangular table selection through `TableSelection`.
- Shared selected-region boundaries for dataset profiling and record reading.
- Header validation and preservation of physical Excel row coordinates.
- Typed `InputRecord` generation with bounded row processing.
- Mapping successful execution results to explicitly configured output columns.
- Pre-execution validation of output cells.
- Creation of a separate enriched workbook without overwriting existing
  non-empty cells.
- Preservation tests for supported source values, formulas, formatting,
  unrelated regions, and worksheets.
- CLI integration with the validated execution DAG and audited resolution.

**Not yet implemented or fully validated:**

- Automatic discovery and ranking of logical tables.
- Selection by Excel structured Table name.
- Formula evaluation or use of formula results as execution inputs.
- General-purpose handling of merged or multi-level headers.
- Complete preservation guarantees for unsupported Excel features.
- Full Capitals end-to-end acceptance and independent numeric evaluation.
- End-to-end Excel benchmarking with 1,000 rows.

The following sections retain the original architectural reasoning.
Implemented decisions should be read together with this status section;
remaining proposals are not claims of existing functionality.

## 1. Why this boundary exists

The original workbook inspector and worksheet profiler primarily supported
conventional tables with headers in the first row. They did not provide
a reliable boundary for independent tables located at arbitrary positions.

M3 introduced an explicit `TableSelection` contract shared by profiling,
record reading, and workbook output. The user selects a worksheet and
rectangular range; the application validates the selection and retains
physical Excel coordinates throughout execution.

`JobExecutor` continues to operate on normalized `InputRecord` values
without depending on Excel-specific structures.

Automatic table discovery remains a separate future capability.

## 2. Logical dataset and source coordinates

A **workbook** can contain multiple worksheets, and a worksheet
can contain multiple logical tables.

The initial M3 implementation uses an explicit `TableSelection`
to identify one rectangular region within a worksheet.

### Implemented contract

`TableSelection` is an immutable Python dataclass containing:

- `sheet_name`: the explicitly selected worksheet.
- `range_ref`: an Excel range including the header row.
- `bounds`: physical column and row boundaries.
- `header_row` and `first_data_row`: derived row coordinates.

The selected region is shared between dataset profiling and
execution input reading.

`read_input_batch()` converts supported input cells into typed
`InputRecord` objects and maintains a separate mapping from each
record identifier to its physical Excel row.

This mapping is used when writing execution results back into
the corresponding rows of a new workbook.

### Additional responsibilities

- Output columns are explicitly mapped to Excel column letters.
- Output targets are validated before provider execution.
- Required input columns are derived from the validated plan.
- Formula cells and unsupported values in required inputs are
  rejected rather than silently converted.
- Source workbook preservation is handled by the output adapter.

### Deferred extensions

- Automatic table discovery.
- Selection using Excel structured Table names.
- Stable selection identities independent of worksheet naming.
- Support for multiple input tables and joins.
- Advanced workbook structures and configurable formula handling.

Workbook checksums and approval provenance are currently handled
by the resolution approval workflow, not by `TableSelection`.

Excel-specific concerns remain within the ingestion, selection,
and output layers. `JobExecutor` operates on normalized records.

## 3. Discovery vs selection vs execution

The architecture separates workbook inspection, explicit table
selection, profiling, record reading, execution, and output.

### Current M3 workflow

1. **Workbook inspection:** inspect available worksheets and their
   basic structure without modifying the source file.

2. **Explicit table selection:** the caller supplies a worksheet
   name and rectangular range through `TableSelection`.

3. **Dataset profiling:** `profile_worksheet()` analyzes the
   selected region and produces a bounded `DatasetProfile`.

4. **Record reading:** `read_input_batch()` extracts the required
   input columns into typed records while preserving physical
   worksheet row references.

5. **Plan validation:** the deterministic validator checks the
   enrichment plan against the selected dataset profile.

6. **Output preflight:** proposed output cells are validated
   before execution. Existing non-empty cells cannot be
   overwritten by the current writer.

7. **Execution:** `JobExecutor` processes the validated DAG
   without depending on Excel-specific representations.

8. **Workbook output:** successful results are projected into
   explicitly mapped columns in a separate XLSX file.

### Future discovery workflow

Automatic table discovery remains a separate concern.

A future discovery component may enumerate structured Excel
Tables and propose candidate rectangular regions.

Discovery results must be treated as suggestions. Ambiguous
regions require explicit selection rather than arbitrary
automatic acceptance.

The execution engine must continue receiving one validated
selection, regardless of whether it originated from a user
or a future discovery component.

## 4. First delivery slice vs later support

| File shape | Current M3 support | Deferred |
| --- | --- | --- |
| Conventional A1 table | Supported through explicit range selection; legacy profiling remains available | Automatic discovery |
| Offset rectangular table (e.g. B6) | Supported with shared profiling and record-reading boundaries | Automatic range proposal |
| Named Excel Table | Not supported by name | Table enumeration and selection |
| Multiple tables on one worksheet | One explicitly selected rectangular region per job | Automatic detection and multi-table workflows |
| Merged or multi-level headers | Not generally supported | Explicit structure normalization and diagnostics |
| Formula-driven input columns | Formulas in required input fields are rejected | Defined cached-value policy |
| Non-rectangular datasets | Not supported | Multi-source data transformations |

**No auto-detection accuracy promise.** The initial two required workbooks may remain the simple worksheet-first-row case, but their format is **not** a universal contract.

## 5. Formula and input type policy

The initial M3 implementation uses a conservative execution policy.

- Profiling reads cached formula values (`data_only=True`) but does not
  calculate formulas.
- Execution reads original cell values (`data_only=False`).
- Formula and Excel error cells in required input columns are rejected.
- Formula cells outside required input columns do not become execution
  inputs and are preserved within the supported workbook contract.
- Supported execution input values are text, finite numbers, and null.
- Boolean, date/time, and unsupported object values are rejected.
- Large integers that cannot be represented exactly as floats are rejected.

The profiler and execution reader intentionally use different conversion
rules. Profile samples are bounded and may be truncated for LLM consumption;
execution inputs must preserve their supported values.

Future work may introduce an explicit cached-formula-value policy with
staleness and availability checks. Formula evaluation is not implemented.

## 6. Acceptance invariants and validation coverage

The following invariants define the intended acceptance contract.
The initial M3 implementation covers explicit table selection,
row-coordinate mapping, safe output targets, and preservation of
supported workbook content through automated tests.

Not all listed scenarios are fully validated. In particular, advanced
Excel structures, resource admission limits, and the complete
1,000-row Excel benchmark remain subject to further acceptance work.

## 7. Delivery history and remaining work

### Delivered

- PR #78: Explicit TableSelection, shared header validation,
  selected-region profiling, and typed input records.
- PR #79: Safe Excel output, end-to-end execution CLI, audited
  resolution reports, and approval-based resume for Wikidata mountains.

### Remaining M3 work

1. Complete the Capitals Excel end-to-end acceptance scenario.
2. Add explicit destination-unit validation for Quantity values (#20).
3. Finalize the supported workbook preservation contract (#21).
4. Implement independent numeric and entity-resolution evaluation (#66).
5. Execute and document a 1,000-row Excel benchmark.
6. Update the M3 acceptance report and close Epic #4 after verification.

### Deferred capabilities

Automatic table discovery, named-table selection, complex worksheet
normalization, multi-table joins, and configurable formula execution
are outside the initial M3 delivery scope.

The original architectural proposal informed PRs #78 and #79.
Further extensions must preserve the separation between Excel adapters,
the deterministic execution engine, and provider-specific resolution.

