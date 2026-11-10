# E2E-0: Dataset Workspace → Plan/Run API

**Historical baseline:** the stacked UI described below is superseded by the
[five-workspace architecture and current walkthrough](24-five-workspace-ui.md).
The startup commands and offline fixture explanation remain applicable.

Partially addresses #104 (E2E-0). The existing Dataset Workspace now includes a
minimal Plan/Run panel below the source preview. DAG visualization, event replay,
Review/Resume and Agent R1 remain out of scope. Issue #104 stays open.

## Start the real application

Python 3.12+, uv and Node 24 are required. From the repository root:

```powershell
uv sync --locked --group dev
# Explicit opt-in for external LLM/provider requests; set before server startup.
$env:ENRICHMENT_ALLOW_NETWORK = "1"
$env:OPENAI_MODEL = "<your-supported-model>"
$env:OPENAI_API_KEY = "<your-key>"
uv run --locked uvicorn ai_data_enrichment.api:app --host 127.0.0.1 --port 8010
```

In a second shell:

```powershell
cd frontend
npm ci
npm run dev
```

Open [the local workspace](http://127.0.0.1:5173). Keep one Uvicorn process on
loopback. The normal application has no fixture selector, mock execution or
fallback to synthetic success. The network checkbox starts unchecked. Both
server configuration and the checkbox are required for live generated plans and
runs. Existing-plan submission can validate without a model or network; executing
it still requires network opt-in. Never put keys in frontend configuration.

## Reproducible manual acceptance (repeat for both files)

1. Upload the original sample. Select `Sheet1` and preview the full table:

   | Source | Range, including headers | Instruction example | Explicit mapping |
   | --- | --- | --- | --- |
   | `samples/capitals.xlsx` | `A1:E11` | Calculate distances in km between the literal city pairs, including Istanbul; target distance. | `distance` → `E` |
   | `samples/mountains.xlsx` | `A1:C11` | Fill height with mountain elevations in metres; leave unresolved values empty. | `height` → `C` |

2. Enter the instruction and output mapping, then opt in to external requests.
   Keep the mountain resolution policy `strict`, or explicitly select
   `verified_only` if that is the desired policy. It is bound into the fingerprint.
   The UI accepts 1–20 selected data rows within A:CV; the server remains the
   authoritative validator. Timeout accepts 1–300 seconds (default 180).
3. Choose **Generate and validate plan**. Alternatively expand **Use an existing
   plan**, paste `demo/capitals_api_plan.json` or `demo/mountains_api_plan.json`,
   and choose **Submit and validate plan**. Inspect capabilities, typed input
   references, parameters, output projections/units, assumptions, source selection,
   validation diagnostics, source hash and full server plan. **Validate again**
   calls the backend validate/read routes. Invalid or missing plans cannot be
   approved or run. No approval or run request is sent automatically.
4. Review the displayed SHA-256 fingerprint. Click **Approve this fingerprint**;
   this sends `approved: true` with exactly that server fingerprint. Click
   **Start bounded run** separately. Source, instruction, mapping and policy edits
   invalidate the downstream draft, approval and results. Input/source controls
   are locked during API mutations and active runs, preventing detached jobs in
   this view. Theme and EN/UK/DE language changes preserve the workflow.
5. Observe actual `queued` / `running` status and terminal status. There is no
   estimated progress or invented result count. A status/report network failure
   offers **Retry status / report**, keeping the current job ID. Cancellation is
   an explicit request; the UI continues polling until the server confirms a
   terminal state. Polling aborts on unmount and stops after terminal status/report.
6. Inspect actual row metrics, provider counters, per-record steps/values/Evidence,
   and the unresolved/operation-error list. `needs_review` means unresolved
   records remain, even when an XLSX was published. No automatic candidate approval
   or Review/Resume is provided. Unresolved output cells stay empty. Preserved
   occupied cells and source-checksum verification are reported from the audit.
7. Download **enriched XLSX** and **JSON audit**. Buttons use the job's published
   artifact availability; failed, cancelled and timed-out jobs offer an audit but
   no workbook. Binary XLSX bytes are downloaded without JSON conversion. Verify
   the audit's source/result SHA-256 against the original and downloaded files.
   Source values, formulas and formatting remain preserved by the existing writer.
8. For error acceptance, start the server without network opt-in and attempt a
   generated plan: expect `network_disabled`. With network enabled but no model,
   expect `llm_not_configured`; with a model but no key, expect `missing_api_key`.
   These remain distinct localized codes. For validation blocking, submit the
   Capitals saved plan with `distance` mapped to occupied column `A`: validation
   must fail and approval/run remain disabled.

Artifacts and IDs belong to the in-memory local backend session. Download before
restarting. Reloading the browser loses the current UI workflow; it does not
cancel a backend run. The server enforces one active job and retained plan/job
limits. There is no new persistence or resume mechanism. See
[the API contract](22-m5-plan-run-api.md) for bounds and lifetime details.

## Automated evidence without external calls

```powershell
# Repository root
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest -m "not live" --cov=ai_data_enrichment
uv run --locked python scripts/benchmark_m2.py --rows 1000

cd frontend
npm ci
npm run format:check
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

On Linux CI, use `npx playwright install --with-deps chromium`. Playwright starts
its own Vite server on 5174 and the **test-only**
`tests.browser.fixture_server:app` on 8011; these ports must be free. It never
reuses a running production server. `API_PROXY_TARGET` changes only the Vite dev
proxy target for this test. The production API startup remains unchanged.

The browser test server explicitly injects the existing `FixturePlanClient` and
`offline_executor` adapters. Only model/HTTP responses are fixtures. Source upload,
profiling, LLMPlanner parsing, typed validation, exact approval, spawned execution,
provider verification, calculations, audit and safe XLSX publication use the real
pipeline. Reports and the UI prominently say `offline_fixture`.

Both original ten-row files run through browser controls and real HTTP boundaries.
Tests verify unapproved-run rejection, mismatched fingerprint rejection, explicit
approval body, queued and terminal states, ten actual row outcomes, both browser
downloads, source/result checksums, preservation of original cell values/styles,
literal Istanbul, empty unresolved Kyiv/Annapurna, language/theme switching and
stale-state clearing. A third browser case blocks an invalid saved plan and resets
it when source selection changes. Focused frontend tests cover failed/cancelled/
timed-out jobs, missing plans, bounded inputs, polling retry/cleanup and distinct
localized configuration errors. Existing Python API tests also cover cancellation,
timeouts, configuration gates, source immutability and bounded workers.

The intentionally sparse offline responses produce **1 successful row out of 10
in each file**, with partial/failed rows and terminal `needs_review`. They are not
the previously observed live 6/10 or 9/10 baselines. Browser artifacts, screenshots
and request records are under ignored `frontend/test-results/`. The downloaded
London–Rome value is checked using an independent spherical-law-of-cosines
calculation (10 m tolerance); Everest is checked against the supplied fixture
value 8848.86 m. This is integration evidence, **not independent live academic
accuracy or coverage evidence**. External live browser acceptance, independent
numeric-source evaluation and a recorded live demo remain separate opt-in work.
