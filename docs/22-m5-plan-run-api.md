# M5-2: Minimal Plan/Run API

Issue #100 adds a Python-only vertical slice to the existing local FastAPI
workspace. `LLMPlanner`, `PlanValidator`, `ExcelEnrichmentRunner`, `JobExecutor`,
registered audited providers and the safe XLSX writer remain authoritative.
There is no frontend or Agent R1 integration in this milestone.

## Reproduce offline

From the repository root (Python 3.12+ and uv):

```powershell
uv sync --locked --group dev
uv run --locked pytest tests/integration/test_plan_run_api.py -q
uv run --locked python scripts/demo_plan_run_api.py --generate
```

The demo uploads **both original ten-row samples** through `TestClient`, generates
plans through the real `LLMPlanner` using supplied model responses, validates,
explicitly approves the exact fingerprints, polls spawned workers and downloads
XLSX plus JSON reports to a new `artifacts/m5-plan-run/<opaque-id>/` directory.
Omit `--generate` to exercise deterministic existing-plan submission.

Only model and HTTP responses are fixtures; provider parsing/verification,
resolution audits, calculations, execution and workbook publication are real.
Reports are labeled `execution_mode: offline_fixture`. These deliberately sparse
fixtures yield **one successful row out of ten in each workbook**, with remaining
rows partial or failed/unresolved. London–Rome receives a calculated distance;
Everest receives 8848.86 m. Kyiv is ambiguous and Annapurna has no fixture match.
This is reproducibility evidence, not live coverage or academic accuracy evidence.

## Local server and opt-in live runs

The existing startup and Dataset API are preserved:

```powershell
uv run --locked uvicorn ai_data_enrichment.api:app --host 127.0.0.1 --port 8010
```

Network is disabled by default, even when an API key is present. To enable live
calls, set configuration **before** server startup in its shell:

```powershell
$env:ENRICHMENT_ALLOW_NETWORK = "1"
# Only needed for generated plans; use your chosen supported model and own key.
$env:OPENAI_MODEL = "<your-model>"
$env:OPENAI_API_KEY = "<your-key>"
uv run --locked uvicorn ai_data_enrichment.api:app --host 127.0.0.1 --port 8010
```

In a second shell, explicitly enable providers for a saved-plan run:

```powershell
uv run --locked python scripts/demo_plan_run_api.py --mode live --allow-network
# Additionally enable LLM generation (otherwise the saved JSON plans are used):
uv run --locked python scripts/demo_plan_run_api.py --mode live --allow-network --generate
```

Live results can be unresolved and can differ between runs. Mountains defaults
to `strict`; `--mountain-policy verified_only` explicitly opts into the existing
verified-only policy without changing candidate budgets or verification rules.
The policy is bound into the approved fingerprint. Capitals uses literal city
queries: Istanbul is never rewritten to Ankara or implicitly required to be a
country capital. No LLM-generated code is executed.

## HTTP contract

Use `/docs` for request schemas. Existing `/api/datasets` upload/preview routes
provide the dataset ID and actual worksheet/range; the range includes headers.

| Action | Route | Behavior |
| --- | --- | --- |
| Create draft | `POST /api/plans` | 201; generate with `LLMPlanner`, or validate a supplied `plan` |
| Read draft | `GET /api/plans/{plan_id}` | Plan JSON, binding, fingerprint, decision and validation |
| Validate | `POST /api/plans/{plan_id}/validate` | Semantic DAG/type/parameter checks and explicit column bindings |
| Approve | `POST /api/plans/{plan_id}/approve` | Requires `approved: true` and exact displayed `fingerprint` |
| Start | `POST /api/plans/{plan_id}/runs` | 202 with opaque job ID and initial `queued` state |
| Poll | `GET /api/jobs/{job_id}` | Status, timestamps, metrics, sanitized error and artifact URLs |
| Cancel | `POST /api/jobs/{job_id}/cancel` | 202; request cancellation, poll until terminal |
| Download | `GET /api/jobs/{job_id}/artifacts/result` | Separate enriched XLSX after successful publication |
| Report | `GET /api/jobs/{job_id}/artifacts/report` | JSON audit for every terminal job, including stops/failures |

Example draft request after uploading Capitals:

```json
{
  "dataset_id": "<uploaded-id>",
  "sheet_name": "Sheet1",
  "range_ref": "A1:E11",
  "instruction": "Calculate distances in km between the literal city pairs; target distance.",
  "output_columns": {"distance": "E"},
  "allow_network": true
}
```

For Mountains, use `A1:C11`, `{"height":"C"}` and request metres. Include an
`EnrichmentPlan` in `plan` to skip model generation; example JSON plans are in
`demo/capitals_api_plan.json` and `demo/mountains_api_plan.json`. Both paths receive
the same independent validation. Ambiguous/unsupported model decisions retain
`plan: null`, their reason and `validation.valid: false`; they cannot be approved.
Invalid semantic plans remain inspectable drafts and cannot be approved or run.
Drafts cannot be edited in place: submit a new draft and approve it again.

Approval body: `{"approved":true,"fingerprint":"<displayed-sha256>"}`.
Run body: `{"allow_network":true,"timeout_seconds":180}`.
Both server configuration and each live planning/run request must opt in.
No missing configuration or provider failure falls back to fixtures.

## States, results and immutability

Jobs move `queued → running → completed | needs_review | failed | cancelled |
timed_out`. `completed` means every DAG step succeeded for every accepted row;
`needs_review` means the run published an XLSX but at least one row contains
unresolved/failed operations, including provider failures. It does not imply that
a Review/Resume workflow is available. `failed` means no result was published
because the worker or workbook workflow failed. Partial DAG successes remain
visible in the report, and unresolved outputs stay empty.

The report includes the exact approved plan, selection, output mapping,
fingerprint, approval timestamp, source/result SHA-256, limits, execution mode,
ordered per-record outcomes, per-step values/errors/Evidence, aggregate metrics,
actual provider request counts and the existing source-linked resolution audit.
Its resolution job ID matches the API job ID. Row summaries use the existing
`success | partial | failure` classification; candidate decisions distinguish
ambiguity, unresolved matches, incomplete verification and provider errors.
Operation error codes are retained; failure messages are sanitized for download.
HTTP errors use `{"error":{"code":"...","message":"..."}}`; in particular
`network_disabled` (403), `llm_not_configured`, `missing_api_key` (422),
`planning_failed` (502), `approval_required`, `approval_mismatch`, `source_changed`
and `job_busy` (409) remain distinct.

The uploaded workbook is stored byte-for-byte. Approval is bound to its SHA-256,
dataset ID, worksheet/range, full plan, instruction, mapping and resolution policy.
The source hash is checked again before execution and after workbook publication.
The safe writer writes a different workbook, preserves nonempty cells and formulas,
and never invents values for failed operations. Existing nonempty output cells are
skipped and listed in `preserved_cells`; other empty destinations can still fill.
Workbook-specific target checks happen again before any provider executes.
Other CLI/library callers retain the runner's default strict collision behavior.

## Bounds, cancellation, retention and limitations

- Local single-user server, one active run; additional starts return `job_busy`.
  In-process identities plus one spawned worker per run, with polling.
- Up to 20 selected data rows, 12 DAG steps, columns A:CV, 64 KiB serialized plan
  request, 32 retained plans and 16 retained jobs. Existing upload limits remain.
- Wall time: 180 seconds by default, configurable per run from 1 to 300 seconds,
  including worker startup. Each provider has 400 HTTP attempts (including retries),
  240 operation starts and 15 seconds of total rate/retry waiting. Existing bounded
  searches and provider timeouts remain in force. LLM calls use a 30-second SDK
  timeout, no retries, 4096 output tokens and the bounded existing profile.
- Cancellation and wall timeout terminate/reap the worker before reporting a
  terminal state. In-flight remote requests may already have reached providers.
  No partial workbook or execution report is published as a completed result;
  a stop/failure report preserves the binding, error and timestamps instead.
- Uploads, jobs and artifacts live in the server's temporary workspace until
  normal shutdown/restart; shutdown stops workers before deleting files. Dataset
  deletion returns `dataset_in_use` while retained plans reference it. Download
  anything needed before restarting; there is no persistent queue or resume.
  Abrupt OS/process termination can leave temporary files for manual cleanup.
- Offline demos retain their explicitly downloaded copies under ignored
  `artifacts/`; remove the printed run directory when no longer needed.
- Run one Uvicorn process on loopback, without multiple workers. No authentication,
  browser E2E wiring (#104), frontend redesign, DAG UI, Review/Resume, Agent R1
  integration or Wikidata optimization is included. Live acceptance is opt-in and
  was not required for the offline suite.
