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

For in-app model/key setup without environment credentials, use the new
[trusted local launcher and Provider Settings](25-provider-settings.md).
The environment-only entry point below remains available.

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
| Poll | `GET /api/jobs/{job_id}` | Status, timestamps, metrics, sanitized error and artifact URLs; `progress` — `{rows_done, rows_total}` once the worker has read the table, `null` before. It counts finished rows whatever their outcome, stays as it was when a run is cancelled or timed out, and carries no result: partial results are still never published |
| Cancel | `POST /api/jobs/{job_id}/cancel` | 202; request cancellation, poll until terminal |
| Download | `GET /api/jobs/{job_id}/artifacts/result` | Separate enriched XLSX after successful publication |
| Report | `GET /api/jobs/{job_id}/artifacts/report` | JSON audit for every terminal job, including stops/failures |
| Review | `GET /api/jobs/{job_id}/review` | Ambiguous queries of a finished job, grouped once per query, with source candidates |
| Resume | `POST /api/jobs/{job_id}/resume` | 202; a **new** job that applies the user's decisions to the same approved plan |

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
`success | partial | failure` classification and also carry the Excel `row`
number, the source `inputs` the plan used for that row (only columns referenced
by the plan), `user_chosen_steps` (steps whose entity the user chose during
review) and `auto_accepted_steps` (steps resolved under `verified_only` while
other matches stayed unconfirmed), so a result can be read without opening the
workbook; candidate decisions distinguish
ambiguity, unresolved matches, incomplete verification and provider errors.
Operation error codes are retained; failure messages are sanitized for download.
HTTP errors use `{"error":{"code":"...","message":"..."}}`; in particular
`network_disabled` (403), `llm_not_configured`, `missing_api_key` (422),
`planning_failed` (502), `approval_required`, `approval_mismatch`, `source_changed`
and `job_busy` (409) remain distinct. Review adds `review_not_available` (409),
`invalid_decision` and `no_decisions` (422).

## Review and resume

A job that ends in `needs_review` usually contains queries for which the source
returned several verified candidates and the strict policy refused to guess. The
two routes let a user settle those queries and fill the affected rows without
re-planning. They are thin: all rules live in the existing approval and resume
code ([resolution foundation](08-resolution-foundation.md), section 10).

`GET /api/jobs/{job_id}/review` is available for `completed` and `needs_review`
jobs and reads only the stored report:

```json
{
  "job_id": "<job>",
  "previous_job_id": null,
  "affected_rows": 2,
  "items": [
    {
      "operation_id": "<opaque>",
      "capability": "geocode",
      "provider_id": "open-meteo",
      "query": {"name": "Kyiv", "entity_type": "city", "context": {"country_code": "UA"}},
      "status": "needs_review",
      "reason_codes": ["multiple_eligible_candidates"],
      "search_complete": true,
      "affected": [
        {"record_id": "Sheet1!2", "row": 2, "step_id": "from_city"},
        {"record_id": "Sheet1!6", "row": 6, "step_id": "to_city"}
      ],
      "candidates": [
        {
          "source": "open-meteo", "entity_id": "703448", "label": "Kyiv",
          "selectable": true, "blocker": null,
          "recommended": false, "recommendation_reasons": [],
          "attributes": {"latitude": 50.45, "longitude": 30.52, "country_code": "UA"},
          "evidence": []
        }
      ]
    }
  ],
  "informational": []
}
```

- **One item per query, not per row.** Identical queries are executed once, so
  one decision covers every row and step that used the query (`affected`).
- **`items`** are the queries a user can decide: status `needs_review` or
  `incomplete_verification` with at least one selectable candidate.
  **`informational`** lists other non-resolved queries (nothing found, provider
  error); they are shown for context and cannot be approved.
- **`other_name`** says whether searching the query under another name could
  change its outcome: true for `needs_review`, `unresolved` and
  `incomplete_verification`, except when the row's country was not recognized.
  It is independent of the list an entry is in: a name with no match and a name
  with several can both be searched under another one.
  **`query.searched_as`** is the name the source was searched for when the user
  already gave one; `query.name` is always the row's own value.
- **`selectable` / `blocker`.** A candidate is selectable only if the source
  verified it and did not exclude it. Otherwise `blocker` is `unverified`,
  `excluded` or `type`. The same function (`approval_blocker`) guards the
  resume, so the list cannot offer something the resume would reject.
- **`recommended`** is a suggestion, never a decision. It is set when exactly
  one candidate is selectable (`only_selectable_candidate`), or, for cities, when
  the existing ranked geographic strategy has a single leader
  (`country_capital_role`, `administrative_seat_role`, `population_recorded`).
  Equal candidates get no recommendation. Nothing is applied until the user
  sends a decision.

`POST /api/jobs/{job_id}/resume` requires a `needs_review` job and a body of
1 or more decisions, at most one per `operation_id`:

```json
{"decisions": [
  {"operation_id": "<opaque>", "source": "open-meteo", "entity_id": "703448"},
  {"operation_id": "<other>"},
  {"operation_id": "<third>", "name": "Warszawa"}
]}
```

The body also takes `allow_network` and `timeout_seconds` exactly like Start; a
live resume contacts the providers again and therefore needs `allow_network: true`.

A decision with `source` and `entity_id` approves that candidate; a decision
without both leaves the query empty on purpose. Items that are not mentioned are
also left as they were. The server expands each approval to all affected
records, validates it against the stored report (listed, verified, not excluded,
same workbook hash, same plan) and starts a **new job** on the same approved
plan. The response and the new job carry `previous_job_id` and
`review_decisions` (both `null` for an ordinary run); the report stores them too. The first job, its workbook and its report are never
modified, so a different choice can be made from the same starting point.

During the new run each approved query is searched again and the approved
candidate is accepted only if the source still verifies it; the decision is then
audited as `basis: user_override`. Otherwise the operation fails with
`APPROVAL_NOT_REVERIFIED` and the cell stays empty.

**Another name.** A decision with `name` (and no candidate) asks for the rows of
that operation to be looked up under this name instead of the cell's value: a
name the source knows in another language or spelling. Nothing is searched by
the request itself. The new job asks the same source for the given name, with
the row's country and the same checks and decision policy
(`resolution/other_name.py`); the source is asked once per operation, however
many rows share it. One confirmed match is written and the row is marked as the
user's decision (`user_chosen_steps`); several matches come back as an ordinary
review item, whose chosen candidate is re-verified under the same given name; no
match leaves the cell empty, and another name can be given. The workbook is not
changed. The audit entry keeps the row's value in `query.name` and the name
searched in `query.searched_as`. The server rejects a name for an operation that
is already resolved, failed at the provider or has an unrecognized country, a
blank name, a name over 512 characters, the row's own value again, and a step
whose inputs do not come straight from workbook columns. A name given in one
round stays in force in later rounds of the same chain, like a chosen candidate.

Errors: `review_not_available` when the job is not in a reviewable state,
`invalid_decision` for an unknown or duplicate operation, a candidate that
cannot be approved or a name that cannot be used, `no_decisions` when every decision leaves its query empty
(a new job would change nothing). Messages do not contain record identifiers.

Known limits: the resume recomputes the whole selected table (up to
`MAX_RUN_ROWS`), not only the affected rows; approved results are searched once
per affected record; the review state is in memory with the job.

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
- Retention: at most 8 workbooks, 32 plans and 16 jobs are kept. When a limit is
  reached the oldest finished item gives way instead of asking for a restart: the
  oldest finished job with its artifacts; the oldest plan without a running job;
  the oldest workbook together with its plans and finished jobs. Nothing bound to
  a running job is ever dropped; only if nothing can be dropped does the request
  return `workspace_full` (409). A dropped plan, job or artifact answers 404, so
  download anything that must outlive later runs.
- Wall time: 180 seconds by default, configurable per run from 1 to 300 seconds,
  including worker startup. Each provider has 400 HTTP attempts (including retries),
  240 operation starts and 15 seconds of total rate/retry waiting. Existing bounded
  searches and provider timeouts remain in force. LLM calls use a 30-second SDK
  timeout, no SDK transport retries, 4096 output tokens per call and the bounded
  existing profile. Generation has at most two validation-feedback repairs (three
  completions total); authentication/transport failures stop immediately. See
  [planning reliability](26-live-planning-reliability.md) for diagnostics and limits.
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
  frontend redesign, DAG UI, Agent R1 integration or Wikidata
  optimization is included. Browser E2E-0 wiring and acceptance instructions are
  documented in [23-e2e0-browser.md](23-e2e0-browser.md). Live acceptance is opt-in and
  was not required for the offline suite.
