# Five-workspace Data Enrichment Studio

Implements the immediate UI foundation in [#112](https://github.com/ayri77/ai-data-enrichment-studio/issues/112). The application now has five real, freely navigable workspaces. It uses the existing FastAPI Plan/Run routes and Python execution pipeline without backend changes.

## Run and demonstrate

Use the backend environment and frontend startup commands in [the E2E-0 setup](23-e2e0-browser.md#start-the-real-application). The regular application has no fixture selector or simulated execution fallback.

1. **Dataset:** upload an original sample, select its worksheet/range, and preview. Enter the enrichment instruction. Choose an existing output by letter/header (`E · distance` for Capitals, `C · height` for Mountains). Alternatively choose **New column**, supply its physical letter or one-based index and a unique header. New destinations must follow all known worksheet columns, even when some lie outside the selected range. The backend still validates bindings and preserves occupied cells. The task panel also asks what to do **if the source confirms exactly one match** while other matches with the same name could not be confirmed: *fill it in automatically* (the UI default; this is the existing `verified_only` policy) or *ask me to confirm* (`strict`). The API and CLI default stays `strict`; the UI always sends its choice explicitly. Next to **Build Plan** the screen lists what is still missing (preview, selection limit, instruction, output column, external-request consent, provider readiness). The same list enables the button, so the two cannot disagree; a missing model or key links straight to Provider / Model settings.
2. Opt in to external requests for AI planning/execution. **Build Plan** requests generation and validation, then opens Plan. Alternatively expand **Use an existing plan**, paste a supplied plan, and choose **Submit and validate plan**; this validation works offline. Named plan outputs must match the selected headers. Existing demo plans are in `demo/`.
3. **Plan:** inspect the actual typed DAG, select a node to see its bindings, capability, parameters and outputs/units, and check validation and assumptions. **Approve / Continue to Execution** approves the exact server fingerprint and opens Execution. It does not start a job. Diagnostics exposes the fingerprint and full source binding.
4. **Execution:** choose **Start bounded run**. Status comes from real polling; the static DAG does not claim node execution progress. Counts appear only when the backend reports them. Cancellation remains a request until the server confirms a terminal status. Navigate to any workspace while the job runs; input mutations stay locked and polling continues. Completion stays on Execution with **Review issues** or **View output**.
5. **Review:** settle ambiguous names and fill the remaining rows. The screen is built on the two review routes ([plan/run API](22-m5-plan-run-api.md), "Review and resume") and has two layers. *First layer:* one card per ambiguous source query (not per row) with the affected rows, the candidates as a single choice, plain distinguishing facts (region, country, role, population, coordinates), a link to an independent page (map or Wikidata), a **Recommended** badge with its reason when the backend suggests one, and an explicit **Leave empty**. Names without any match are listed separately with nothing to choose. *Second layer* (closed `details`, marked `data-advanced`): operation ID, provider, decision and reason codes, record → step links, per-candidate verification and evidence, the matches that cannot be chosen (collapsed, with the reason for each), and the earlier per-error-code summary, where `DEPENDENCY_FAILED` remains a derived issue. **Accept recommendations** fills only undecided cards and never overwrites a choice. **Fill in remaining rows** needs at least one chosen candidate and the network opt-in; it starts a new job on the same approved plan, moves the earlier job to Diagnostics history and returns to Execution. Choices live above the workspace bodies, so navigation and language changes keep them; a new run, a resume or any binding edit clears them. Unknown codes are shown verbatim rather than hidden.
6. **Export (results):** check the results row by row, then download the actual XLSX and JSON audit separately. *First layer:* each row shows its Excel row number, the source values the plan used, the output value rounded for reading with its unit (the exact value is the cell tooltip; workbook and audit keep full precision), and a result note. An empty cell says why in plain words (the first real failure in the row, not its `DEPENDENCY_FAILED` consequences) and links to Review when a choice can still be made there. Values that exist only because of a user choice are highlighted and labelled with the chosen name. Values accepted automatically while other matches stayed unconfirmed say so and have their own filter. A filter (all / filled / empty / after your choice), sorting by any column (empty values stay last) and a short summary per output column (filled count; min, max and average for numeric columns) change only what is shown. *Second layer* (row toggle, `data-advanced`): every step of the row with its returned value and source record, plus the record ID and the `success | partial | failure` classification. The table is built from the audit, not from a re-read of the result workbook.

Policy and timeout live in a shared **Advanced policy and limits** disclosure. A policy change invalidates approval and requires a newly validated/approved plan. **Retry same approved plan** starts a distinct job with the unchanged approved binding; it never implies reviewed resume. Status/report retry only recovers polling. Editing source selection, instruction, mapping, supplied plan, consent or policy clears the current downstream binding/results. The last eight job/report/plan snapshots remain in session-only Diagnostics history and cannot silently become the current result.

The shared controller stays mounted above workspace bodies. This persistence covers navigation, language and theme changes within the current browser session. Browser reload still loses the UI session and does not cancel a backend job. There is no new durable storage or job recovery API.

## Layout and accessibility

The shell, navigation and workspace actions stay inside the desktop viewport. Source configuration, table, column inspector, graph, node inspector, summaries and Diagnostics scroll within bounded panels. At narrow widths or short heights, the page can scroll so controls remain reachable. Navigation uses ordinary keyboard-accessible buttons with current/completed semantics and focuses the workspace heading after a change. The native Diagnostics dialog traps focus, closes with Escape and restores focus to its trigger. Status labels accompany semantic colors. EN/UK/DE and both existing themes share the same workflow state.

Technical settings, the selected-node inspector and Diagnostics are marked with `data-advanced` and use the same controller as the primary actions. A future Simple/Advanced switch can alter their visibility without implementing another planning/execution path.

The Column Inspector width slider resizes the desktop pane from 260–420px; the source table scrolls within its remaining space. Compact split layouts (up to 1000px wide) use a fixed 180px inspector, and stacked layouts hide the resize control because it does not apply. Browser regression coverage checks rendered pane widths and document overflow across desktop, intermediate, compact, mobile and short viewports.

## Provider setup and deferred work

**Provider / Model settings** now exposes non-secret readiness, a supported OpenAI model picker, and masked key apply/replace/remove actions. Keys stay in backend process memory. See [the trusted local launcher, access boundary, and acceptance steps](25-provider-settings.md). The separate network-consent state and server gate remain authoritative.

Advanced DAG editing/strategy tooling remains in #101; actual node events and playback in #102; candidate Review/Resume in #103/#68. Autonomous agent visualization, advanced analytics and a complete Simple/Advanced product mode are outside this slice. Live Mountains acceptance remains deferred as agreed; these tests do not assert independent live academic accuracy.

## Verification and evidence

```powershell
# Repository root
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest -m "not live" --cov=ai_data_enrichment --cov-report=term-missing
uv run --locked python scripts/benchmark_m2.py --rows 1000

cd frontend
npm run format:check
npm test
npm run build
npm run test:e2e
```

Browser tests start an isolated real API on 8011 and Vite on 5174. Only model/provider responses are injected offline. Both original workbooks still traverse upload, selection, real planning/validation, exact approval, spawned Python execution and actual downloads. Downloaded workbooks are independently inspected with openpyxl for values, original cell/style preservation and unresolved empty cells. Offline fixtures intentionally yield **1 successful / 3 partial / 6 failed** Capitals records and **1 successful / 9 failed** Mountains records, not the previously observed live baselines. All result screens explicitly identify offline fixture execution.

Additional acceptance covers navigation while a real job's status response is delayed (the response remains unmodified), no cancellation on navigation, localization/theme preservation, stale-state invalidation, offline supplied-plan validation, invalid plans, new-column coordinates outside the selected range, header/coordinate collisions, real output at `F` without shifting existing `E`, keyboard modal access and a 390px mobile flow.

For each of the five workspaces and both files, the browser suite checks document width/height and action visibility at **1366×768, 1440×900 and 3440×1440** and captures screenshots. Other tests cover terminal errors, cancellation, status retry, same-plan retry, late responses and source-bound approval. These are integration and UI acceptance evidence, not a substitute for independent numeric/source evaluation or a recorded live submission demo.

Local verification on this branch: **1,262 Python tests passed, 2 live tests skipped; 101 frontend tests passed; all 5 browser acceptance cases passed.** Ruff lint/format, Pyright, the 1,000-row controlled benchmark, frontend formatting and the production build also passed.

## Desktop screenshots (offline integration evidence)

| Workspace | 1366×768 | 1440×900 | 3440×1440 |
| --- | --- | --- | --- |
| Dataset | [Open](screenshots/issue-112/capitals-dataset-1366x768.png) | [Open](screenshots/issue-112/capitals-dataset-1440x900.png) | [Open](screenshots/issue-112/capitals-dataset-3440x1440.png) |
| Plan | [Open](screenshots/issue-112/capitals-plan-1366x768.png) | [Open](screenshots/issue-112/capitals-plan-1440x900.png) | [Open](screenshots/issue-112/capitals-plan-3440x1440.png) |
| Execution | [Open](screenshots/issue-112/capitals-execution-1366x768.png) | [Open](screenshots/issue-112/capitals-execution-1440x900.png) | [Open](screenshots/issue-112/capitals-execution-3440x1440.png) |
| Review | [Open](screenshots/issue-112/capitals-review-1366x768.png) | [Open](screenshots/issue-112/capitals-review-1440x900.png) | [Open](screenshots/issue-112/capitals-review-3440x1440.png) |
| Export | [Open](screenshots/issue-112/capitals-export-1366x768.png) | [Open](screenshots/issue-112/capitals-export-1440x900.png) | [Open](screenshots/issue-112/capitals-export-3440x1440.png) |

Mountains at 1440×900: [Dataset](screenshots/issue-112/mountains-dataset-1440x900.png), [Plan](screenshots/issue-112/mountains-plan-1440x900.png), [Execution](screenshots/issue-112/mountains-execution-1440x900.png), [Review](screenshots/issue-112/mountains-review-1440x900.png), [Export](screenshots/issue-112/mountains-export-1440x900.png).

![Dataset](screenshots/issue-112/capitals-dataset-1440x900.png)
![Plan](screenshots/issue-112/capitals-plan-1440x900.png)
![Execution](screenshots/issue-112/capitals-execution-1440x900.png)
![Review](screenshots/issue-112/capitals-review-1440x900.png)
![Export](screenshots/issue-112/capitals-export-1440x900.png)

Provider / Model settings now support [backend-owned ephemeral local setup](25-provider-settings.md) (#113), replacing the original not-reported placeholder.
