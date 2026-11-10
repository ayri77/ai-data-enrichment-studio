# M5-1 — Data IDE UI Foundation

This implements the approved design handoff inside the existing Dataset Workspace
on PR #98, related to #99. It preserves the FastAPI routes, ingestion contracts,
profiling limits, and source workbook bytes. It does not implement M5-2.

## Audit and integration mapping

The initial audit found branch `feature/m5-dataset-workspace` at `5ea9905`, matching
the open, unmerged PR #98. Only earlier, untracked design-exploration files were
present. Those files are preserved locally and excluded from this update.

The archive's README, integration/validation guides, contracts, components,
interaction/accessibility guidance and tokens were inspected before editing the
application. Handoff ZIP SHA-256:
`4506A7A3ED063799991C6CF6C49B7092F487B56844C161706D43B78F480B9C50`.

| Handoff source | Integration decision |
| --- | --- |
| `tokens/tokens.css` | Reused Light/Dark semantic roles, typography, geometry and focus tokens under `.studio`; formatted for this repository. |
| `Studio.tsx` | Adapted shell, global preferences, provisional mark and document navigation to the existing app. Future workspace controls are disabled and explicitly unavailable. |
| `DatasetGrid` | Adapted selection/header styling to real `DatasetProfile` columns and bounded sample records. Removed fixed five-column schema, execution statuses, local fixture pagination and filters. |
| `ColumnInspector` | Adapted to API-provided inferred type, full selection row count, null count and selected rectangle. No assumed numeric `distance` column, fixed rows or verification status. Native width slider supports keyboard input. |
| `i18n.ts` | Reused relevant shared EN/UK/DE labels; added real upload, guided range, loading, validation, diagnostic and empty-state strings through React i18next. |
| `contracts.ts` | Presentation reference only. Did not import the Capitals-only `SourceColumn`, fixed `excelRow`, revision, execution or verification assumptions into production. Existing HTTP payloads remain intact. |
| `Workflow.tsx`, `Review.tsx`, `semantics.ts` | Future visual references only. No graph, execution trace, candidate staging, approval or export code is imported into the production entry point. |
| `demo/`, fixtures, reported totals, standalone HTML, prebuilt JS | Demonstration-only; excluded from production. No simulated values or actions are displayed. |

The plan was to establish shared presentation and preferences, adapt the real
Dataset route, then validate syntax, data preservation, localization and keyboard
behavior. The main risks were fixture leakage, sample indices mistaken for Excel
rows, client syntax mistaken for server table validation, and preferences resetting
the dataset. Tests and explicit UI labels address these risks.

## Implemented behavior

- One Data IDE shell and provisional logo, compact workbook source, command
  toolbar, semantic source table, column inspector and selection diagnostics.
- Shared Light/Dark tokens. Dark is the initial default; preference persists under
  `enrichment.theme`. No remote font download; IBM Plex falls back to system fonts.
- English is the initial default, with exactly English (`en`), Українська (`uk`)
  and Deutsch (`de`). Preference persists under `enrichment.language`. Unsupported
  saved languages fall back to English. Browser locale is not automatically used.
- Preferences are independent of dataset/selection/preview state. Switching theme
  or language does not upload, reprofile, discard the dataset, or reset the
  inspector. Blocked local storage does not prevent using either control.
- Guided selection chooses first/last Excel columns, the actual header row and
  last row. Column choices cover the current local API's 100-column envelope.
  Advanced A1 input retains absolute references, lowercase normalization,
  single-cell ranges, offset ranges and Excel syntax bounds.
- The two input modes share a draft. Returning from malformed advanced input to
  guided mode restores the last guided boundaries, clears the stale preview, and
  still requires explicit preview. Reversed/malformed syntax is rejected locally.
- Syntactic validity is not table validity. The unchanged backend still validates
  headers, worksheet existence, data contents and conservative resource limits.
  Both modes use the same real `/api/datasets/{id}/preview` endpoint.
- API errors retain the original machine-readable `code` and HTTP status on
  `WorkspaceApiError`. The UI localizes known codes and uses a localized generic
  fallback for unknown failures; it never echoes raw internal errors. Existing
  profiler diagnostics are mapped to localized presentation strings.
- Source values, worksheet names, column names and A1 identifiers are not
  translated. Zero, false and missing values stay distinct. No cell is written.
- Current API samples lack physical worksheet row IDs and omit wholly empty rows.
  The table therefore explicitly says **Sample index**, with a note that these
  are not Excel row numbers. Header letters and inspector **Selected cells** come
  from the actual selected range, not from sample ordinals.

## Runtime contracts and changed files

`api.ts` retains upload/preview/delete transport and response types. It adds only
structured error preservation. `useDataset.ts` owns the existing upload/replace/
selection/preview flow; `range.ts` checks frontend syntax without parsing XLSX.
`App.tsx` composes `StudioShell`, `RangeSelection`, `DatasetPreview` and the existing
adapted shadcn button. `preferences.ts`, `i18n.ts`, three locale JSON files,
`tokens.css`, and `styles.css` provide shared foundation behavior.

Focused frontend tests cover preferences and all locale resources, arbitrary
schemas, guided/manual ranges, preflight syntax errors versus server errors,
upload/drop/loading/retry/removal/replacement, zero/boolean/null handling, sample
labels, retained state during preference changes and selected token contrasts.
Python application code, routes, tests and workbook-processing code are unchanged.

## Local startup

Python 3.12+, uv and Node.js 24 are required. From the repository root:

```powershell
# Terminal 1
uv sync --locked --group dev
uv run --locked uvicorn ai_data_enrichment.api:app --host 127.0.0.1 --port 8010

# Terminal 2
cd frontend
npm ci
npm run dev
```

Open [the real application](http://127.0.0.1:5173). Upload `samples/capitals.xlsx`
and preview `Sheet1 / A1:E11`; replace with `samples/mountains.xlsx` and preview
`Sheet1 / A1:C11`. Both should show real headers, ten records in the selected
table and at most five aligned sample rows. Stop both processes with Ctrl+C.

## Verification on 2026-11-09

- Ruff lint/format and Pyright pass; **1,226 Python tests pass**, with two live
  LLM tests skipped by their existing explicit-opt-in gates (93% overall coverage).
- All **71 frontend tests** pass; TypeScript/production build, formatting and npm audit pass.
  Twenty-four token-pair tests check AA normal-text contrast in both themes.
- Browser verification used the actual Vite application and FastAPI server.
  Capitals renders `Capital_From`, `Country_From`, `Capital_To`, `Country_To`,
  `distance`; Mountains renders `Mountain`, `Country`, `height`. No fixtures are
  imported for these workflows.
- Changing the guided last row to 6 produced `A1:E6` and five selected records.
  Advanced `A2:E11` reached the backend and produced a localized
  `invalid_selection` error. Correcting it restored the preview.
- Workbook replacement cleared the old table and selected the new three-column
  schema. Language/theme switching preserved the loaded data and selection.
  A reload restored the non-default German + Light preferences.
- EN/UK/DE were inspected at 1440px, 390px and 320px widths. Narrow-page content
  width equals the client width; the source table and document navigation scroll
  within their own regions. German labels wrap rather than shrinking the page.
- Keyboard checks: Enter submits range changes; Enter selects a column; the
  inspector slider changes 300 → 310 → 300 with arrow keys. A focused table region
  scrolls horizontally with Left/Right. Focus rings use theme tokens.
- Source SHA-256 hashes remain unchanged:
  Capitals `9BEC067483D73D45302834307D2CBD090359C7C6A778CF2AC4CB9BD3D27298EC`;
  Mountains `19E1DC3BE01DCC4F6446375B894D13BDEEA85E5BE2FD8639AE8616E6EB658450`.

Actual running-application screenshots:

| Locale | Dark / desktop | Light / desktop | Narrow |
| --- | --- | --- | --- |
| English | [Capitals](screenshots/m5-1-foundation/en-dark-capitals-desktop.jpg) | [Capitals](screenshots/m5-1-foundation/en-light-capitals-desktop.jpg) | [Light](screenshots/m5-1-foundation/en-light-capitals-narrow.jpg) |
| Українська | [Capitals](screenshots/m5-1-foundation/uk-dark-capitals-desktop.jpg) | [Capitals](screenshots/m5-1-foundation/uk-light-capitals-desktop.jpg) | [Dark](screenshots/m5-1-foundation/uk-dark-capitals-narrow.jpg) |
| Deutsch | [Capitals](screenshots/m5-1-foundation/de-dark-capitals-desktop.jpg) | [Capitals](screenshots/m5-1-foundation/de-light-capitals-desktop.jpg) | [Light](screenshots/m5-1-foundation/de-light-capitals-narrow.jpg) |

[Mountains / Dark](screenshots/m5-1-foundation/en-dark-mountains-desktop.jpg),
[Mountains / Light](screenshots/m5-1-foundation/en-light-mountains-desktop.jpg),
[German validation](screenshots/m5-1-foundation/de-light-validation-narrow.jpg).

## Explicit placeholders and limits

- Plan, Execution, Review and Export are disabled and labeled **Not available**.
  Workflow Studio and Decision Workbench remain design references for later work.
  No simulation is reachable from the production app, and no demo approval can
  affect data or export.
- The mark is temporary. System fonts are used when IBM Plex is unavailable.
- Workbook/session bounds remain those documented in [M5-1](19-m5-dataset-workspace.md):
  10 MiB uploads, 16 worksheets, bounded ZIP/XML resources, temporary storage,
  five sampled records, 200 characters per sampled value and 12 KB profiles.
- Metadata-based default ranges are suggestions. Blank gaps and offset tables
  may require editing boundaries. The backend remains authoritative.
- Reloading the page resets the active UI dataset; only appearance/language
  preferences persist. Backend restart invalidates opaque dataset IDs.
- No physical Excel row mapping is supplied by the current preview API. No full
  worksheet editor, per-cell navigation, inline filters, pagination, value
  verification, provider execution, authentication or deployment is added.
- Contrast measurements and keyboard smoke checks are not a screen-reader audit
  or formal accessibility certification. A complete browser/assistive-technology
  matrix and product approval remain review work.

Stop for product review of UI Foundation; do not merge PR #98 or start M5-2.
