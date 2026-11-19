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
| What a plan may use | `GET /api/capabilities` | Every capability registered on this installation, in the order the planner is shown them: its contract (`id`, `version`, `description`, `inputs`, `parameters`, `output`), `source` — the id of the source its values come from, or `local` for a calculation — and `trust`, the kind of trust behind its values as a lowercase word: `computed` (calculated here), `record` (read from an identified record of the source), `resolved_name` (a name matched to a record of the source) or `service` (calculated by an outside service, with no record to check it against). and `category`, what a person wants to do with it (`lookup`, `distance`, `calculate`; declared with the binding). The response also has `categories`: the declared order of categories, restricted to those with at least one capability offered here (`distance` stays without a routing key because of the straight-line distance; `web_search` is never listed, since the web search is not offered to a plan). `source`, `trust` and `category` are not part of the contract the planner is shown; a client must show an entry without `category` (an older server) in one group, and treat a category it has no wording for as its id made readable; a client must treat a `trust` it does not know as "unknown kind". `GET /api/limits` carries the same ids with the bounds of one run |
| An extraction plan | `POST /api/extraction-plans` | 201, kind `extraction`; see "Values read out of mail texts". 403 `extraction_not_allowed` where it is not offered; 422 `extraction_plan_invalid` with the builder's message |
| A table from e-mails | `POST /api/mail-datasets?filename=…` | 201; the raw body is one `.zip` of `.eml` files or one `.eml`; the answer is that of an upload plus `mail` (counts). 403 `mail_not_allowed` unless the server sets `ENRICHMENT_ALLOW_MAIL=1` **and** is the local launcher; never on the hosted app. Details and errors: [the mail note](32-mail-extraction-probe.md), "In the application" |
| Sources of new rows | `GET /api/row-sources` | The sources new rows can come from, with their fields and whether each is `available` here (a local-only source needs `ENRICHMENT_ALLOW_MAIL=1` on a local installation) |
| Candidates of a source | `POST /api/row-sources/{source_id}/candidates?filename=…`, `DELETE /api/row-sources/candidates/{candidates_id}` | 201 `{candidates_id, source_id, count, skipped}` from the raw files; they are kept in memory only, at most four sets; DELETE answers 204 also for an unknown id. 404 `rows_unknown_source`, 403 `mail_not_allowed` |
| Preview new rows | `POST /api/datasets/{id}/rows/preview` | 200 with the plan, what was proposed, the counts and, when the core refuses the plan, `refused` instead of counts. Details: [rows from a source](33-rows-from-a-source.md#the-routes) |
| Append new rows | `POST /api/datasets/{id}/rows` | 201, a new dataset made from a copy plus `rows` (counts, first and last row, range); a refused plan is a 422 with its code. Details: [rows from a source](33-rows-from-a-source.md#the-routes) |
| The stored workbook | `GET /api/datasets/{id}/workbook` | The workbook of a dataset as an `.xlsx` attachment under the fixed name `workbook.xlsx` (the client names the file it saves); counted as the usage event `download`. 404 `dataset_not_found`; 403 `mail_dataset_no_download` for a table built from e-mails (its text column holds mail text, so it leaves the application only through an export that empties it). Details: [rows from a source](33-rows-from-a-source.md#in-the-application) |
| Task ideas from the model | `POST /api/task-ideas` | 200; up to three tasks that could be asked for with the selected table, in the language asked for; plans and writes nothing |
| Create draft | `POST /api/plans` | 201; generate with `LLMPlanner`, or validate a supplied `plan` |
| Create a universal-search draft | `POST /api/fallback-plans` | 201, the same draft as above with `"kind": "fallback"`; with `plan_id` (a planner draft that left columns unfilled) a draft with `"kind": "mixed"`; off unless the server switches it on (see "The universal search" and "A mixed plan" below) |
| Read draft | `GET /api/plans/{plan_id}` | Plan JSON, binding, fingerprint, decision and validation |
| Validate | `POST /api/plans/{plan_id}/validate` | Semantic DAG/type/parameter checks and explicit column bindings |
| Approve | `POST /api/plans/{plan_id}/approve` | Requires `approved: true` and exact displayed `fingerprint` |
| Start | `POST /api/plans/{plan_id}/runs` | 202 with opaque job ID and initial `queued` state |
| Poll | `GET /api/jobs/{job_id}` | Status, timestamps, metrics, sanitized error and artifact URLs; `progress` — `{rows_done, rows_total, steps}` once the worker has read the table, `null` before. It counts finished rows whatever their outcome; `steps` is `{<step_id>: {filled, failed, skipped}}` over those rows, one entry for every step of the plan and empty until the first row is finished. A step counts as `filled` for a row if its operation succeeded, `skipped` if it failed with `DEPENDENCY_FAILED`, `failed` otherwise: the audit of the finished run gives the same three numbers. `rows_done` and `rows_total` cover the rows that are executed: a row that was left out because every chosen cell already had a value (see "Existing values") is in neither, and `progress` stays `null` when no row is executed. A finished job also has `rows_skipped`, the number of such rows (0 when there are none) Both stay as they were when a run is cancelled or timed out, and carry no result — counts only, no values, names, record ids or error texts; partial results are still never published. The audit report of a job repeats the job's last `progress`, so it carries `steps` as well |
| Cancel | `POST /api/jobs/{job_id}/cancel` | 202; request cancellation, poll until terminal |
| Download | `GET /api/jobs/{job_id}/artifacts/result` | Separate enriched XLSX after successful publication |
| Report | `GET /api/jobs/{job_id}/artifacts/report` | JSON audit for every terminal job, including stops/failures |
| Review | `GET /api/jobs/{job_id}/review` | Ambiguous queries of a finished job, grouped once per query, with source candidates |
| Search under another name | `POST /api/jobs/{job_id}/review/{operation_id}/search` | 200; what the source confirms for another name of one reviewed query; writes nothing |
| Other names from the model | `POST /api/jobs/{job_id}/review/{operation_id}/suggestions` | 200; up to five names to search for; searches and writes nothing |
| Resume | `POST /api/jobs/{job_id}/resume` | 202; a **new** job that applies the user's decisions to the same approved plan |

`POST /api/task-ideas` with `{"dataset_id": …, "sheet_name": …, "range_ref": …,
"language": "en" | "uk" | "de", "allow_network": true}` asks the configured
language model what could be asked for with the selected table
(`task_ideas.py`) and answers `{"tasks": […]}` with at most three tasks. An
optional `"fill_columns": […]` names the headers of the columns chosen to be
filled; with two or more of them the model is asked, with a prompt of its own,
for exactly one task that fills them all. The first task that names every
chosen header as written is returned and nothing else; a task that leaves one
out is not returned at all, so the answer can be an empty list. The
model request carries what a planning request already carries — column names,
inferred types, the number of empty cells per column and the five sample rows of
the profile — together with the capability descriptions of the registry and the
language to write in. The reply is untrusted: only a JSON object with a list is
accepted, entries that are not printable text of at most 400 characters and
repeats are dropped, and any other shape is an error rather than an empty list.
The prompts ask for real facts in a person's words: never to invent, estimate or
fill a column "so that it differs", and never to name a capability, parameter or
other identifier of the registry. That rule lives in the prompt only (a keyword
filter for "invent" would depend on the language); in code, a task that names a
capability id or a registry identifier as an identifier (a name with an
underscore anywhere as a whole word; a plain word such as `ratio` or `unit` only
when quoted, next to `=` or followed by `(`; words that are headers of the table
or chosen columns excepted) is dropped and counted as `internal_names` in the
`task_ideas` usage event, beside `proposed`, `too_long`, `incomplete` and `shown`;
when all are dropped the answer is the empty list.
The kept answer is keyed by workbook, sheet, range, language, chosen columns and the
model that is configured when the request arrives, so an answer of another model is
never served (the reasoning effort follows from the model id in the catalog). An
optional `"again": true` skips the kept answer, asks the model once more, replaces the
kept answer with the new one and counts against the same limit of 20 requests per
workbook (shared by all models); at the limit it answers `task_idea_limit` (429) as any
request without a kept answer does, and the usage event carries `again` (0 or 1).
A task is only text for the instruction field: it is planned and validated like
a typed instruction, and nothing is planned until the user asks for a plan. The
answer is kept per workbook, sheet, range and language. It needs what planning
needs (`network_disabled`, `llm_not_configured`, `missing_api_key`); further
errors are `dataset_not_found` (404), `invalid_selection` (422),
`suggestions_busy` (409), `task_idea_limit` (429) after 20 model requests for
one workbook and `task_ideas_failed` (502). How well the proposed tasks plan has
not been measured, and neither have instructions in Ukrainian or German
([live planning reliability](26-live-planning-reliability.md) covers English
instructions only).

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

A plan made by the model may leave some of the chosen columns unfilled: when the
capabilities can produce some of the requested columns and not others, the planner
answers `ready` with a plan for the first and names the others. The draft then carries
`unfilled_targets` (a list of column names; `[]` for a plan that writes every chosen
column, for a supplied plan and for a fallback draft). `output_columns` stays as it was
chosen. The field is inside what the fingerprint covers, so it cannot be changed after
approval; the plan request has no such field, and a plan brought in `plan` must still
write every chosen column (`OUTPUT_TARGET_MISMATCH`). Validation, approval and run use
the chosen columns minus the unfilled ones; a name that is not a chosen column or that the
plan also writes is a validation error (`UNKNOWN_UNFILLED_TARGET`,
`UNFILLED_TARGET_ALSO_WRITTEN`). A draft stored without the field is read as having none.
The run writes only what the plan writes: an unfilled new column is not created in the
result workbook (no header), an unfilled existing column is left exactly as it is, and it
takes no part in deciding whether a row is already filled.

Approval body: `{"approved":true,"fingerprint":"<displayed-sha256>"}`.
Run body: `{"allow_network":true,"timeout_seconds":180}`. A run of a plan that searches the web
(kind `fallback` or `mixed`) gets `max_timeout_seconds` when the body has no `timeout_seconds`;
the interface leaves the field out for such a plan unless the person edited the limit.
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
`unfilled_targets` (the chosen columns the plan does not write; `[]` if none),
fingerprint, approval timestamp, source/result SHA-256, limits, execution mode,
ordered per-record outcomes, per-step values/errors/Evidence, aggregate metrics,
actual provider request counts and the existing source-linked resolution audit.
Its resolution job ID matches the API job ID. Row summaries use the existing
`success | partial | failure` classification and also carry the Excel `row`
number, the source `inputs` the plan used for that row (only columns referenced
by the plan), `user_chosen_steps` (steps whose entity the user chose during
review), `other_names` (per step, the name the source was asked for instead of
the row's value and, where the review recorded it, who gave that name) and
`auto_accepted_steps` (steps resolved under `verified_only` while
other matches stayed unconfirmed), so a result can be read without opening the
workbook; candidate decisions distinguish
ambiguity, unresolved matches, incomplete verification and provider errors.
Operation error codes are retained; failure messages are sanitized for download.
HTTP errors use `{"error":{"code":"...","message":"..."}}`; in particular
`network_disabled` (403), `llm_not_configured`, `missing_api_key` (422),
`planning_failed` (502), `approval_required`, `approval_mismatch`, `source_changed`
and `job_busy` (409) remain distinct. Review adds `review_not_available` (409),
`invalid_decision` and `no_decisions` (422).

### Existing values

A plan chooses the cells it fills. Whether a cell that already holds a value
keeps it is part of the plan request: `existing_outputs` is `keep` (the default,
also for plans made before the option existed) or `replace`. The source workbook
is never changed; the result is a copy.

**Keep.** An occupied target cell, a formula included, stays as it is. A record
for which **every** target cell of the plan's outputs holds a value is **not
executed at all**: none of its steps run, no source is asked and nothing is
counted against a request budget. The runner (`ExcelEnrichmentRunner.run`)
leaves such records out of what it hands to the executor, so the executor,
its metrics and its budgets never see them. A record with at least one empty
target cell is executed completely, as before; there is no pruning per step or
per column, so a row with some cells filled still costs all its lookups, and
the cells that already had a value are kept afterwards (`kept_outputs`).

**Replace.** Nothing is skipped. A new value is written over the earlier one
only where one was produced; the earlier value is recorded in `replaced_cells`
and a row's `replaced_outputs`. A row whose lookup failed keeps what it had.

A run without the keep/replace choice (the command line, the benchmarks) is
unchanged: an occupied target cell is refused before anything runs, and no
record is skipped.

**What the outcome counts.** Status, `metrics` (`total_rows`, `successful_rows`,
`partial_rows`, `failed_rows` and the operation counts), `progress`, the
`rows` list, `execution.records` and the review all cover the executed rows
only. A skipped row never lowers the outcome: the job is `needs_review` only
because of an executed row. If every row is skipped the job is `completed`
with `metrics.total_rows` 0, `progress` `null`, the workbook is published with
its values unchanged, and `rows_skipped` is the number of selected rows. A
skipped row cannot appear in Review, and a resume ("fill in the remaining
rows") starts from the same workbook with the same plan and choice, so it skips
the same rows.

**The audit.** The report gains one top-level list, `skipped_rows`, one entry per
skipped row in sheet order (`[]` when none). Nothing else changes: `rows`,
`kept_outputs`, `replaced_outputs`, `preserved_cells` and `replaced_cells` keep
their meaning, and `preserved_cells` still names the kept cells of the skipped
rows too.

```json
"skipped_rows": [
  {
    "record_id": "Sheet1!2",
    "row": 2,
    "inputs": {"Mountain": "Mount Everest", "Country": "Nepal/China"},
    "reason": "already_filled",
    "kept_outputs": ["height"],
    "kept_values": {"height": 8848.86}
  }
]
```

`reason` is `already_filled` (every target cell held a value, so the row was not
looked up); `kept_outputs` lists the target columns, `kept_values` the value
each cell holds (a number, a flag or text; a formula is recorded as its text).
`inputs` are the same plan-referenced source values a `rows` entry has. The
`run_finished` usage event gains the number `rows_skipped`.

Limit: a run takes the first `N` rows of the table (`MAX_RUN_ROWS`), and skipped
rows count against `N`.

### Where each value came from

**In the audit.** The report gains two top-level fields, both additive. They are
written by the run worker, so they are present only for a run that finished
(`completed` or `needs_review`); a failed, cancelled or timed-out job has neither.

`step_provenance` states, once per step of the approved plan and in plan order
(not per row), what the step is and what stands behind its values. `source` and
`trust` are those of the capability's binding (`capabilities/bindings.py`), the
same values `GET /api/capabilities` gives; the evidence of each value (`Evidence`
in `execution.records`) is unchanged.

```json
"step_provenance": [
  {"step_id": "find", "capability": "resolve_entity", "source": "wikidata", "trust": "resolved_name"},
  {"step_id": "height", "capability": "lookup_property", "source": "wikidata", "trust": "record"}
],
"sources_in_workbook": {"mode": "sheet", "applied": true}
```

`sources_in_workbook` says which choice the run had (`mode`, below) and whether
the workbook got it (`applied`: `true` when `mode` is `none` too; `false` when
adding the notes or the sheet failed). The choice is also in `approved_plan`.

**In the workbook (optional).** The plan request takes `sources_in_workbook`:
`none` (the default, also for plans made before the option existed), `notes` or
`sheet`; any other value is a 422. Like `existing_outputs` it is part of the
approved plan, so it is covered by the fingerprint and carried into a resume.
`none` writes the workbook exactly as before.

- `notes`: every cell this run wrote gets a cell note (an Excel comment, author
  "AI Data Enrichment Studio") with three lines: `Source:` the source's readable
  name (or its id where there is no name; `calculated here` for a local
  calculation), `Reference:` the references of the value's evidence (omitted
  where there is none), and `Kind of trust:` in words, for example "a name matched
  to a record of the source". An earlier note on a cell the run writes is
  **replaced**; this happens only in this mode.
- `sheet`: a worksheet `Sources` is added after the user's sheets (`Sources (2)`,
  `Sources (3)` and so on where the name is taken, compared without regard to
  case) with a header row and one row per written cell, in sheet order: `Sheet`,
  `Cell`, `Column` (the output's header), `Value`, `Capability`, `Source`,
  `Reference`, `Kind of trust`. The user's own sheets, their notes included, are
  not altered beyond the written cells.

Only cells this run wrote are described. A kept cell (it already held a value), a
row that was skipped, a cell left empty because the step failed or the source has
no value, and the header cells get nothing. The texts are English; they are not
localized, because a workbook is a file that gets passed on. Limits: the note and
the sheet say what the audit says, for the cell as written; a value that the
source returned and the run could not write is not in them. A value a person
types over later is no longer described by its note.

**Failure.** The notes and the sheet are added in a separate step after the values
are written. If that step fails (for example the workbook cannot be saved again),
the run does not fail: the workbook holds the values without notes or sheet, the
exception is logged on the server, and the audit says `"applied": false`. The
failed step is never half done: the workbook is replaced only by a complete,
reopened file.

### The universal search (fallback)

For a task no registered capability covers, a plan of one step, `web_lookup`, asks a
model with web search for several columns of each row (`capabilities/fallback.py`,
`providers/web_lookup*.py`; what a measurement of the idea showed is in
[the universal lookup probe](31-universal-lookup-probe.md)). **It is off by default.**
Every test of this route and of the worker uses a fake client inside the worker process;
the owner ran it live through the application twice (2026-11-16 and 2026-11-17, single
trials recorded in the changelog and in the probe note), and no value was checked
against a reference.
Every row costs one model request with web search, so model tokens and a search fee, and
the values are the model's claims, not records.

**Switching it on.** `ENRICHMENT_ALLOW_WEB_LOOKUP=1` (any other value is off),
`ENRICHMENT_WEB_LOOKUP_MODEL` (default `gpt-6-luna`, one short model id; reasoning effort
`low` is fixed in code) and the server's OpenAI key, the planner's: from the environment
or the provider settings. A run is also a live run: the server's network gate and the
request's own consent apply as for every run. `GET /api/limits` gains one boolean,
`web_lookup`: true only with the setting on, a key, and the network gate open (or an
offline server). Nothing else in that answer changes, and the planner, the plan repair,
the suggested tasks and `GET /api/capabilities` never mention the capability.

**Route.** `POST /api/fallback-plans`, body (extra fields are a 422):

```json
{
  "dataset_id": "<uploaded-id>", "sheet_name": "Mountains", "range_ref": "A1:D7",
  "question": "Find facts about this mountain.",
  "context_columns": ["Mountain", "Country", "Rank"],
  "targets": [{"column": "Height", "kind": "number", "unit": "m"},
              {"column": "Climbed in winter", "kind": "yes_no"}],
  "output_columns": {"Height": "E", "Climbed in winter": "F"},
  "existing_outputs": "keep", "sources_in_workbook": "notes"
}
```

`output_columns`, `existing_outputs`, `sources_in_workbook` and the range mean what they
mean in a plan request, and the table is checked and profiled by the same code (up to the
same rows and columns). The answer is the draft of `POST /api/plans`, with
`"kind": "fallback"` and the one-step plan the server built; its `assumptions` say that
each value is found by an AI model with web search, one request per row, and is not
verified. Approval, run, progress, review, download and the report use the existing routes.
Refusals: `web_lookup_disabled` (403, setting off, nothing is read or stored),
`web_lookup_no_key` (422), `fallback_plan_invalid` (422, with the reason in `message`:
a column that does not exist, a type that does not fit, a task too long, and so on),
`invalid_selection`, `dataset_not_found`, `request_too_large` (413, 64 KiB) as for a plan.

**The kind.** A planner draft has no `kind` (it is `planner`); a fallback draft has
`"fallback"`, a mixed one `"mixed"`. Only the server sets it, and it is inside what the
fingerprint covers. The gate (`plan_gate`, before validation, approval and every run) knows
three kinds and the shape of plan each one has: `planner`, no `web_lookup` step; `fallback`,
the lookup step and nothing else; `mixed`, the lookup step and other steps. A plan with
`web_lookup` is refused (`web_lookup_plan_refused`, 422) wherever a plan could enter on the
normal path: a supplied `plan`, what the planner returns, and a stored draft whose kind does
not agree with the shape of its plan. Before a run the draft's fingerprint is computed again from what is stored,
so a draft changed after approval is refused (`approval_mismatch`). A run is refused when
the setting was switched off or the key removed after the draft was made.

**The run.** One request per row, one after another (nothing runs in parallel), each with a
30 second timeout, no retries, and the same row limit as every run. The job's time limit is
`max_timeout_seconds` unless the run request names `timeout_seconds`. The worker begins no
request that could end later than 20 seconds before that limit (counted from its own
start, which comes a little after the supervisor's); the rows not asked then fail with `DEADLINE_REACHED`, and every row answered
before is kept and written, so the job ends as an ordinary finished run, not as `timed_out`.
**Kept answers.** A row's successful answer is kept in the plan's value cache and taken over
by a later job of the same plan, exactly as a value read from a source is (see "A resume is a
whole run" below, `RESULT_REUSE_SECONDS` in `run_limits.py`; the lookup's lifetime is that
constant, not a copy of it). So running the approved plan again (the run route, "Retry same
approved plan") after a run that left rows `DEADLINE_REACHED` asks and pays only for those rows;
a row answered before is neither asked nor counted against the job's budget, and a kept answer is
served even after the deadline. What a key is made of: the capability, its source
(`openai_web_search`), the configuration id (prompt version, model, reasoning effort), the whole
task text (question, context columns, each target's column, kind and unit) and the row's context
values; a change in any of them is another key and asks again. Two identical rows of one job
still cost one request. **What is kept:** a found answer, and the answer "nothing found" of a row
(`NOTHING_FOUND`: the search ran and no value could be named for any column), for the same
lifetime and under the same key. That answer is stored as a found record in which no slot has a
value, which a found record never is, and is read back as the failure `NOTHING_FOUND`; the
row ends as it did (the failure's message is stored in the quote of that one empty slot). Every
other failure is a failure of delivery or of the input, not an answer, and is asked (and paid
for) again by every later run: `DEADLINE_REACHED`, `PROVIDER_ERROR`, `SOURCE_ACCESS_DENIED`,
`REQUEST_REFUSED`, `RESPONSE_INCOMPLETE`, `INVALID_RESPONSE`, `INVALID_INPUT`, `MISSING_VALUE`.
So a row for which nothing can be found is asked once for the life of the plan, not on every
run; to ask it again, build the plan again (a plan built again is a new plan and has no kept
answers) or change the task. Only the search is kept this way: for every other capability a
failure, including a name that was not found, is still asked again. The cache belongs to the plan (its own directory beside the dataset, in
the application's temporary workspace directory, which is removed when the application ends):
another plan, even with the same question and table, does not see these answers. Keys are digests; the file holds the found values, their
links and quotes, never the API key, and nothing of it is logged. The report's `web_lookup` block
counts only requests actually made and has `answers_kept`: the number of rows a kept answer
(found, or "nothing found") settled in that job (the repeat of a kept row counts too); it is 0 where the worker did not
finish and could not say (a stopped run). `execution.metrics.persistent_cache_hits`
still counts the operations read from the file (a kept answer repeated in one job is one hit and
one in-memory reuse), so the two differ by those repeats and, in a plan with other steps, by
their hits.

**Resume.** A resume of a lookup job stays refused. It is not because it would repeat the
lookups (it no longer would, for the rows kept). A resume exists to apply the user's decisions on
names that need review, and a lookup plan has no name to resolve: its jobs have no resolution
report and Review lists nothing, so the resume route answers 422 and `start_job` refuses a
previous job or selections for such a draft (409, a second guard the route does not reach).
Running the plan again is what continues it. A plan that mixes lookup and source steps does
have names to resolve and is resumed (see "A mixed plan").
The key is handed to the worker process with its task and the client is made there; it is
never written to a file, a log line, the audit or an error.

**The audit.** A fallback job and its report have `"kind": "fallback"`. The report also has

```json
"web_lookup": {"configuration_id": "openai-web-lookup-p1-gpt-6-luna-low",
               "requests": 5, "input_tokens": 5000, "output_tokens": 1000, "web_searches": 10,
               "answers_kept": 0}
```

(numbers only; failed requests are counted; `answers_kept` is the number of rows a kept answer
of an earlier run settled, see "Kept answers"; present for every terminal job of this kind,
from the shared counters when the worker did not finish) and each entry of `rows` has
`ai_found`: for each value written from the search, `{"column", "basis": "stated" |
"concluded", "links": <number>}`. The links themselves are in the step evidence and in the
provenance, not repeated. A cell that kept its earlier value and a column nothing was found
for are not listed. `step_provenance` names `web_lookup`, source `openai_web_search`, trust
`ai_found`. Operation codes of the lookup are `NOTHING_FOUND`, `DEADLINE_REACHED`,
`REQUEST_REFUSED` and `RESPONSE_INCOMPLETE` among others; the interface has no texts for
them yet. The job status rules are unchanged: a run in which every row had a value written
is `completed`; a row with `NOTHING_FOUND` is a failed row, so the job is `needs_review`,
although there is nothing to decide in Review (resume is refused for such a job; run the plan
again, which asks only for the rows that were not answered, see "Kept answers": a row with
`NOTHING_FOUND` is not asked again, so the job stays `needs_review` with the same rows).

**The usage summary.** The spend is one `model` event with `purpose: "web_lookup"`, the
model, `requests`, `input_tokens`, `output_tokens` and `web_searches`; `GET /api/usage`
sums `web_searches` per visitor beside the other numbers. The summary also says whether it is kept across restarts (`persistent`, `since`, `skipped_lines`, and a `reason` when it is not); see [hosted demo](27-hosted-demo.md), "Usage summary". A run that times out, is
cancelled or whose worker dies still records the requests that had returned (the worker
copies the four numbers to shared memory after each request); a request still in flight
when the worker is stopped cannot be counted, although the provider may bill it.

### A mixed plan

When the planner answers `ready` with `unfilled_targets` (see "The planner may name the columns
it has no source for" in the changelog), the columns it filled have a source and the others
have none. A **mixed** plan fills both: the planner's steps for the first, ONE `web_lookup`
step for the others. The server builds it (`build_mixed_plan` in `capabilities/fallback.py`);
the planner is never shown the capability and is not changed.

**Route.** `POST /api/fallback-plans` with `plan_id`:

```json
{
  "plan_id": "<a planner draft with a non-empty unfilled_targets>",
  "question": "How old is this mountain, in millions of years?",
  "context_columns": ["Mountain", "Country"],
  "targets": [{"column": "age", "kind": "number", "unit": "million years"}],
  "sources_in_workbook": "notes"
}
```

* The dataset, sheet, range, output columns, `existing_outputs` and the mountain policy come
  from the planner draft. **The body must not repeat them**: `dataset_id`, `sheet_name`,
  `range_ref`, `output_columns` and `existing_outputs` with a `plan_id` are refused
  (`fallback_plan_invalid`, "do not send ..."), the simpler of rejecting and requiring
  equality. `sources_in_workbook` may be sent; omitted, the planner draft's choice is kept.
* `question` may be omitted: it is then the planner draft's instruction.
  `context_columns` and the kind and unit of each target mean what they mean for a fallback
  draft.
* `targets` must name exactly the draft's `unfilled_targets` (any order); a missing, extra or
  repeated name is `fallback_plan_invalid` with the reason.
* The answer is a NEW draft (201): `"kind": "mixed"`, the combined plan, `unfilled_targets: []`,
  `output_columns` of the planner draft (every requested column), `searched_targets` (the
  columns the lookup fills, in the order of the request, so that an interface can say which
  columns are searched) and `planner_plan_id`. The draft's `instruction` stays the planner
  draft's (so the validator's intent checks still see the user's task); the lookup's question
  is in the plan's task text. All three fields are inside the fingerprint.
* The planner draft stays stored and untouched. The store holds at most 32 plans and makes
  room by dropping the oldest one without a running job; for a mixed draft the planner draft
  it is made from is never dropped (`make_room_for_plan(keep=...)`; if it is the only one that
  could go, the request is `workspace_full`, 409, and nothing changes). Room is made only
  after every other check has passed, so a refused request drops nothing.
* The setting, the key and the offline stand-in are checked first, exactly as for a fallback
  draft (`web_lookup_disabled`, `web_lookup_no_key`).

New refusals (all 422, each with a text in the three interface languages): `plan_not_found`
(404, as for every plan route), `mixed_base_not_planner` (the draft is a fallback or mixed
one), `mixed_nothing_unfilled`, `mixed_base_invalid` (the planner draft does not validate now),
and `fallback_plan_invalid` for the rest: targets that are not the unfilled ones, a target the
planner's plan already writes, and **a context column that any output of the combined plan
writes** (its cells may be empty or change during the run), besides whatever a fallback draft
refuses.

**The plan.** The planner's steps, their order and its output mappings are kept byte for byte;
one step is appended, `web_lookup`, built as for a fallback plan (the task text is a literal,
`ctx_n` are bound to the columns, one output per target). Its id is `lookup`, or the first free
of `lookup_2`, `lookup_3`, ...; the plan's assumptions are the planner's and the lookup's
sentence. The same inputs give the same bytes. The real `PlanValidator` validates it
(`fallback_registry(plan, columns, offered)` and a policy whose output targets are every
requested column). `fallback_registry` accepts a plan with exactly one `web_lookup` step and
nothing else, or with exactly one `web_lookup` step and steps of offered capabilities; for the
second shape the lookup's inputs are only the task and columns, no other step uses its output,
and no other output writes a column the lookup fills or a column it reads. `plan_shape` says
which shape a plan has.

**The run.** A mixed job goes through the same machinery as a planner job (request policies,
the plan's reuse caches, review, resume selections) with the lookup bound in addition: the
worker builds the plan's registry, the lookup client from the key and the task's
`LookupSettings`, and calls the one executor factory with `registry=` and `web_lookup=` (see
`ExecutorFactory`). The lookup's budget, deadline and 30 second request timeout are those of a
fallback job, and so is the time limit (the longest, unless the request names one). The job and
its report have `"kind": "mixed"`.

* **A row** is judged by the existing rules: each step succeeds or fails independently, and a
  row is `success` only when every step of the plan did. Values are written per successful
  step's output, not per row: a row whose source step needs review gets its searched column
  written (and its source column empty) if the lookup succeeded, and the other way round. That
  row is not a successful row, so a job with such rows ends `needs_review`.
* **Review** shows the name choices of the source steps as for a planner job; there is nothing
  to decide for the lookup step.
* **Resume** applies the choices and re-executes the whole plan as a new job of the same plan.
  The lookup step is answered from the plan's kept answers (see "Kept answers"): no request
  for a row answered by an earlier job of the plan, including a row answered "nothing found"; only
  rows with no kept answer (a failure of delivery or of the input is never kept) are asked again. The report's `web_lookup.answers_kept` says how many rows were
  taken over. A pure fallback job is still not resumable.
* **The report** has, besides the planner's fields, `kind: "mixed"`, `searched_targets`,
  `unfilled_targets: []`, the `web_lookup` block, `ai_found` per row for the searched columns
  only, and `step_provenance` for every step, so each column carries its own kind of trust:
  the source steps `resolved_name` / `record`, the lookup `ai_found`. With
  `sources_in_workbook` each written cell's note or `Sources` row names its own source and
  kind of trust. The usage summary has the planner's `model` event (purpose `plan`) and the
  lookup's (purpose `web_lookup`), as for the two separate drafts.

The interface uses it: see "The universal search" in [the interface](24-five-workspace-ui.md#the-universal-search).

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
  {"operation_id": "<third>", "source": "open-meteo", "entity_id": "756135", "name": "Warszawa"}
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

A resume is a whole run of the plan over the same rows, but it does not ask the
sources again for what an earlier run of the same plan confirmed. Each plan has
a directory beside its runs with two SQLite caches (`orchestration/live_executor.py`,
`reuse`): verified entities with their resolution reports, and values read from
a source record. A later run of that plan — a resume or **Retry same approved
plan** — takes those over for `RESULT_REUSE_SECONDS` (one hour, `run_limits.py`).
Only successes are kept (the one exception is the universal search's answer "nothing found"):
a name without a confirmed match is asked for again, a
user's own selection never comes from a cache and is verified against the source
each time, and distances are computed, not kept. The universal search's answers are
kept the same way (see "Kept answers"), also in the values cache. Plans do not share the caches,
so another policy or another task never reuses a decision made under this one.
The report says how much was taken over (`execution.metrics.persistent_cache_hits`)
and counts only the requests actually sent (`execution.provider_requests`); a
reused entity keeps its full resolution report and row links in the audit.

The report of a resumed job also has `review_assistance` (`null` for an ordinary
run), so that a value found under another name can be traced to where the name
came from:

- `other_names` — one entry per name used: `job_id` of the reviewed job,
  `operation_id`, `name` (the row's value), `searched_as` and `origin`.
  `origin` is `model` when the model had proposed exactly this name for this
  item before it was used (compared without regard to case and spacing) and
  `user` otherwise. It is decided from the server's own record of the model's
  answers, never from the request; the server cannot tell a proposed name that
  was clicked from the same name typed by hand.
- `model_requests` — every request to the model for other names made while that
  job was under review, used or not: `job_id`, `operation_id`, `name`, `model`
  (`offline_fixture` for injected answers), `asked_at`, `outcome`
  (`answered` | `failed`) and the names it `proposed` after filtering.

A further resume carries both lists forward, because its result still rests on
the earlier names; a new name for the same row value replaces the earlier entry.
Model requests of a review that was never resumed are in no report, and the
record does not survive a server restart. Requests for task ideas belong to no
job and are not audited.

During the new run each approved query is searched again and the approved
candidate is accepted only if the source still verifies it; the decision is then
audited as `basis: user_override`. Otherwise the operation fails with
`APPROVAL_NOT_REVERIFIED` and the cell stays empty.

**Another name.** A name in the workbook can be one the source does not know:
another language, another spelling, a typo. Two routes serve it.

`POST /api/jobs/{job_id}/review/{operation_id}/search` with
`{"name": "Warszawa", "allow_network": true}` asks the source once, at once, for
that name. It uses the lookup, the country of the reviewed query, the checks and
the decision policy a job would use (`resolution/other_name.py`, `search_under`),
and the provider limits of one job. The answer is a review item for the same
operation: `query.name` is the row's value, `query.searched_as` the name
searched, `candidates` what the source returned with the usual `selectable`
flag, and `status` the outcome (`resolved` for one confirmed match,
`needs_review` for several, `unresolved` for none, `provider_failure` when the
source did not answer). A search writes nothing and changes neither the job nor
its review. The server keeps its own record of the latest search per operation.
Errors: `invalid_search` (422) for an unknown operation, one that cannot use
another name, a blank name, a name over 512 characters or the row's own value
again; `review_not_available` (409); `network_disabled` (403);
`search_busy` (409) while another search is running; `search_limit` (429) after
100 searches for one job; `search_failed` (502).

`POST /api/jobs/{job_id}/review/{operation_id}/suggestions` with
`{"allow_network": true}` asks the configured language model under which other
names the source may list the query (`spellings.py`) and answers
`{"operation_id": …, "names": […]}` with at most five names. The model request
carries the name as written, the kind of place, the recognized country, the
reference source and a name already tried; nothing else from the workbook. It
is asked for other names of the same place (English, local, transliterations)
and, where the name stands for a whole whose parts the source lists separately
(a massif and its summits), for the name of the principal part. The
reply is untrusted: only a JSON object with a list is accepted, entries that are
not one printable line of at most 100 characters, repeats and the row's own
value are dropped, and any other shape is an error rather than an empty list.
A proposal is only text to search for: it goes through the search route above
like a typed name, and nothing is written unless the user then chooses a
confirmed match. The answer is kept per operation, so asking again costs no
request. It needs what planning needs (`network_disabled`, `llm_not_configured`,
`missing_api_key`); further errors are `invalid_search` (422),
`review_not_available` (409), `suggestions_busy` (409), `suggestion_limit` (429)
after 40 model requests for one job and `suggestions_failed` (502).

A resume decision may then carry the chosen candidate together with the name
that found it: `{"operation_id": …, "source": …, "entity_id": …, "name": …}`.
The server accepts it only if that candidate is a confirmed, not excluded
candidate of its own record of the latest search under exactly that name
(`build_searched_selections`); a candidate sent with a name that was not
searched, or that an newer search replaced, is an `invalid_decision`. The new
job verifies the candidate again under the given name and audits it as
`basis: user_override`, also when the search confirmed only one match: the row's
own value did not find it, so it is the user's choice.

A decision with `name` alone is accepted too: the new job then searches under
that name itself and its policy decides (one confirmed match is written and
marked as the user's decision, several come back as a review item). The
interface does not use this form.

In both forms the workbook is not changed, the audit keeps the row's value in
`query.name` and the name searched in `query.searched_as`, and the choice stays
in force in later rounds of the same chain. A step whose inputs do not come
straight from workbook columns cannot be given another name.

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

### Values read out of mail texts

Offered only when the server is the local launcher with `ENRICHMENT_ALLOW_MAIL=1` and holds an
OpenAI key; a live run also needs the network gate and the run's own consent. `GET /api/limits`
gains the boolean `text_extraction`, true only then; the hosted app answers 403
`extraction_not_allowed` and says false. The model is `ENRICHMENT_TEXT_EXTRACTION_MODEL`
(default `gpt-6-luna`; one short model id).

`POST /api/extraction-plans` takes `dataset_id`, `question`, `text_column`, `targets` (`column`,
`kind` of `text`, `number`, `date`, `yes_no`, `one_of`, and `options` for a choice), optional
`sheet_name` and `range_ref` (both or neither) and `sources_in_workbook` (default `notes`). The
server builds the plan and sets the kind `extraction`; the body carries no plan, kind or model.
The planner, the task ideas and the AI web search still refuse a mail table. A supplied plan or a
planner-returned plan of this shape, an extraction step with other steps, and a draft of this kind
where extraction is unavailable are refused (`extraction_plan_refused`). Validate, approve and run
are the ordinary routes; the run's timeout defaults to the server maximum, the row limit is
unchanged, and a run cannot be resumed.

**Status.** A row whose answer is `NOT_APPLICABLE` (the text is not about the task) is settled:
the step tally and the job counts show it as `not_applicable`, apart from successful and failed
rows. A run where every row is successful or `NOT_APPLICABLE` is `completed`; otherwise
`needs_review`. The rule is narrow: only the `text_extraction` capability and only that code.
Such a row is never kept in the value cache, so running again asks those rows again. A text that
is about the task but gave no value that could be traced (`NOTHING_FOUND`) is a failed row as
for every other plan, so such a run ends `needs_review` although there is nothing to choose.

**Report.** The section `text_extraction` holds the configuration id, request, token and
answer counts, rows by outcome (`found`, `nothing_found`, `not_applicable`, `failed`), values
kept by basis (`literal`, `derived`), values dropped, and `text_column_emptied`. Each value is
shown with its basis and quote and no link. If the job did not finish, only numbers are given.

**The text does not travel on.** In the report the text input of a row is a fixed marker with its
length (`[text not kept: N characters]`). Logs name an exception class only; usage events hold
counts and fixed words; errors are fixed sentences, except the 422 of the plan route, which
names the columns the request itself gave; the value cache holds a digest, not the text.
The quotes are the evidence and are in the report and, with `notes` or `sheet`, in the result
workbook by design. For a table built from mail the result workbook has its text column emptied.
Verified by an end-to-end test with a sentinel and fakes; nothing was run against a real model.

### A table from e-mails

`ENRICHMENT_ALLOW_MAIL=1` (any other value is off) makes `POST /api/mail-datasets` available
on a local installation; `GET /api/limits` gains the boolean `mail`, true only then. The
dataset it creates is an ordinary one (one sheet, `Mail`, columns `Received`, `From`,
`Subject`, `Text`), but the planner never sees it: `POST /api/plans`, `POST /api/task-ideas`
and `POST /api/fallback-plans` answer `mail_dataset_no_planner` (409) for it, before any model
gate or call. Error codes of the route: `mail_not_allowed` (403), `invalid_mail_file_type` (422),
`mail_upload_too_large` (413, over 32 MiB), `invalid_mail_archive` (422),
`mail_too_many` (422, over 500 `.eml` files), `mail_entry_too_large` (413, over 10 MiB a
mail), `mail_total_too_large` (413, over 64 MiB in all) and `no_mail_found` (422). The
preview of such a dataset also carries `"mail": true`. See
[the mail note](32-mail-extraction-probe.md).

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
