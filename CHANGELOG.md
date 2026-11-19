# Changelog

## Unreleased

- The Overview lists what the system can do as a catalog: category tiles that filter, capability cards grouped by category, each card opening in place (what it needs, what it gives, an example; the contract in Advanced), and the AI web search as a category of its own. The category is declared with the binding on the server (`GET /api/capabilities` gains `category` per entry and `categories`), so a new capability appears in the right group without a change to the interface; the planner is not told.
- A probe outside the application for building a table from e-mails (first task: subscriptions
  and recurring payments): `scripts/experiment_mail_extraction.py` reads `.eml` files, cleans and
  masks their text, asks a model once per message for several columns with a quote for each value,
  and writes a workbook and a rows file. Every mark is computed by code. No live request has been
  sent and nothing was measured on real mail. See [the probe](docs/32-mail-extraction-probe.md).
- The probe can read the person's own Gmail mailbox, read-only (`--gmail-query`, scope
  `gmail.readonly` only), as a second source next to `.eml` files; the sign-in uses Google's
  libraries from the optional `mail` extra (`uv sync --extra mail`), `--save-eml` keeps the
  messages for a repeat run, `--gmail-sign-out` removes the local token. Not run against a
  real mailbox yet.
- The folder `credentials/` at the top of the repository is ignored by git, and the mail probe
  no longer warns about an OAuth client file that git ignores. The probe's note says how to write
  the path in `.env`: `uv` stops reading the file at a value with single backslashes.
- After the first run of the mail probe on real mail: of the plain-text and the HTML part of a
  message the one that says more is used (receipts of one sender had their amounts only in the
  HTML part), and the letter case of a quote is no longer compared with the text (another form of
  a word is still dropped). The run is recorded in
  [the probe's note](docs/32-mail-extraction-probe.md).

- E-mails can become an ordinary dataset on a local installation: `POST /api/mail-datasets` takes
  a `.zip` of `.eml` files or one `.eml` and builds a table with one row per mail (`Received`,
  `From`, `Subject`, `Text`), masked, off unless `ENRICHMENT_ALLOW_MAIL=1` and never offered by the
  hosted app. The mail files are read in memory; only the table made of them is kept, in the
  temporary workspace. The planner, the task ideas and the AI web search refuse such a table
  (`mail_dataset_no_planner`). No extraction and no interface yet. See
  [the note](docs/32-mail-extraction-probe.md).
- The capability layer of the extraction from texts (`text_extraction`), by the pattern of the AI
  web search: a request with the text column and typed target columns (text, number, date,
  yes/no, a choice), the one-step plan the server builds from it, the registry for that plan, a
  handler that maps the probe's outcomes onto a step (a text that is not about the task is a failed
  step and is never kept), and the executor binding. Unit-tested offline with a fake client and
  one end-to-end run over the invented fixture mails. There is no route, no run worker and no
  interface yet, the planner is not told about it, and a plan of this kind is refused wherever a
  plan comes in. `FoundValue.basis` also takes `literal` and `derived`. See
  [the note](docs/32-mail-extraction-probe.md).
- The extraction from texts can be planned, approved and run through the API (no interface):
  `POST /api/extraction-plans` builds the one-step plan from a text column and typed target
  columns (201, kind `extraction`; 403 `extraction_not_allowed`; 422 `extraction_plan_invalid`),
  and the ordinary validate, approve and run routes carry it through the same worker and executor
  path. It is offered only on a local installation with `ENRICHMENT_ALLOW_MAIL=1`, an OpenAI key
  and, for a live run, the network gate (`GET /api/limits` gains `text_extraction`; model:
  `ENRICHMENT_TEXT_EXTRACTION_MODEL`); the hosted app refuses it. A supplied or planner-returned
  extraction plan, and an extraction step mixed with others, are still refused. A text that is not
  about the task (`NOT_APPLICABLE`) is a settled row, counted separately in the step tally and the
  job counts; a run whose rows are all successful or such is `completed`, and a later run asks
  those rows again. The report gains a `text_extraction` section (values with their quote and
  mark, counts of requests, tokens and dropped values). The text does not travel on: the report
  shows a marker with the length instead of the text, logs, usage events, errors and the value
  cache hold none of it, and the result workbook of a table built from mail has its text column
  emptied (quotes in the report and the workbook notes are by design). Tested offline with fakes
  only; nothing has been run on real mail or a real model through the application. See
  [the plan/run API](docs/22-m5-plan-run-api.md) and [the note](docs/32-mail-extraction-probe.md).
- `scripts/mail_register_via_app.py` drives a running local application through its public API
  (upload a folder of `.eml` files, plan, approve, run, poll, download) to accept the extraction
  on real mail before there is an interface. It prints numbers and codes only and writes
  `register.xlsx`, `report.json` and `summary.json` under the ignored `artifacts/`. Tested
  against the real application with a fake model, in process and once as a real server process;
  not run on real mail. See [the note](docs/32-mail-extraction-probe.md).
- Fixed: the text of a long mail was cut back to the last line break before the limit, which can
  be far from it. Two real mails of about 17,000 characters kept 290, because their next line
  alone was longer than the limit. The cut now takes a line or word boundary only in the last
  fifth of the allowance. The first run of real mail through the application is recorded in
  [the note](docs/32-mail-extraction-probe.md).
- The interface can build a table from e-mails and fill columns from them, on a local installation only (the entry appears when `limits.mail` is true, the extraction when `limits.text_extraction` is true; on the hosted demo neither). The first step gets "Build a table from e-mails": `.eml` files are packed in the browser into one stored zip under neutral names and posted to `POST /api/mail-datasets`, then counts and the table are shown. A table of mails has an editor (question, columns with kind and options) instead of the planner, builds a plan through `POST /api/extraction-plans`, and runs and shows results with "read in the text" / "worked out from the text" marks, quotes, and a mail that is not about the task as its own outcome. No backend change. Tested offline with a stand-in for the model; not run with a real model. See [the note](docs/32-mail-extraction-probe.md#in-the-application-the-interface).
- A probe outside the application for reading mails with a local model: `--ollama-model NAME` on
  `scripts/experiment_mail_extraction.py` sends the same instructions, text and JSON schema to
  Ollama's `POST /api/chat` (`providers/text_extraction_ollama.py`, imported by the script only; no
  binding, setting or route) and every check and mark is still computed by the existing reader. No
  OpenAI key is read; the address must be a loopback one unless `--ollama-allow-remote` is given. A
  prompt that may not have fit the context window is reported as its own failure instead of being
  answered from half a text. `scripts/compare_mail_runs.py BASE_DIR OTHER_DIR` compares two runs
  (outcomes, columns filled and equal, marks, drops, seconds, tokens) in numbers only. Tested
  offline with a fake HTTP opener; no local model was run, so nothing is known yet about its
  quality. See [the note](docs/32-mail-extraction-probe.md#local-model-probe-ollama).

## 0.4.0 — beta (2026-11-18)

A task the system has no source for, or has a source for only in part, can now be
searched on the web by an AI model, as an option that is off by default. The
values it finds are marked as not verified and keep their links; the columns the
sources can fill are still filled from the sources. Besides that, the planner can
name the columns it cannot fill, the answers of the search are kept while a plan
lives, usage statistics survive a restart, and planning was measured again with a
verdict on the route of each plan.

### What is new

**The universal search**

- A task no registered capability covers can be searched on the web with an AI
  model. It is off unless the server sets `ENRICHMENT_ALLOW_WEB_LOOKUP=1` and has a
  key (the network gate and the consent of the request apply as for every run);
  `ENRICHMENT_WEB_LOOKUP_MODEL` names the model (default `gpt-6-luna`, reasoning
  effort `low` is fixed in code). `GET /api/limits` gains `web_lookup`, true only
  when the setting is on, a key exists and the network gate is open. The planner,
  the plan repair, the suggested tasks and `GET /api/capabilities` never mention
  the capability. `POST /api/fallback-plans` makes a draft of kind `fallback` with
  one step, `web_lookup`; approval, run, review, download and the audit use the
  existing routes. A plan with `web_lookup` is refused on the normal route
  (`web_lookup_plan_refused`), also when it is supplied or comes from the planner.
  Before a run the draft's fingerprint is computed again from what is stored, and a
  draft changed after approval is refused (`approval_mismatch`).
- What is asked and what is sent: one request per row, one after another, each
  with web search, a time limit of 30 s and no retry. Each request holds the
  question and the values of the identifying columns of that row, and those values
  go to OpenAI. The plan names the columns before anything runs, and approving the
  plan is the consent to send them. Each row costs model tokens and a search fee.
- What comes back: one typed answer per requested column. A column is `text`,
  `number` with a unit, or `yes_no` (the cell holds `Yes` or `No`). A value counts
  only with a link that the search itself reported; a value whose link the search
  did not return is dropped. Known tracking parameters are ignored when links are
  compared and removed from the link that is kept; a parameter that can select
  content (`?sku=…`) must still match exactly. Each value has a basis, `stated` (on
  the page) or `concluded` (for instance from the absence of a statement), set by
  the model. A column with no value is left empty with a reason; a row in which no
  column has a value ends as `NOTHING_FOUND`.
- Where the links go: the request field `sources_in_workbook` (`none`, the
  default; `notes`; `sheet`) makes the result workbook say where each written value
  came from, in a note on the cell or on an added `Sources` sheet. The note and
  sheet texts are English only. `GET /api/capabilities` gains `trust` (`computed`,
  `record`, `resolved_name`, `service`) beside `source`, the audit report gains
  `step_provenance` (capability, source and trust of each step) and
  `sources_in_workbook`, and the searched columns carry the kind of trust "found by
  AI, not verified" (`ai_found`). Results say under each output column how its
  values were obtained, and the overview words each capability from `trust` and its
  source.
- In the interface it is one switch on the task step, "If the system has no source
  for the task or for some of its columns, search the web with AI for them", off by
  default and remembered in the browser. It appears only where `web_lookup` is on.
  The planner has no runnable plan when it refused the task or when its plan failed
  the check; with the switch on, the interface then asks `POST /api/fallback-plans`
  at once with defaults (the task as the question, the columns to fill as `number`
  where the existing column holds numbers and as `text` otherwise, every non-empty
  text or number column that is not filled as an identifying column, notes for the
  links) and shows the search plan as the plan, with a notice of why, the columns
  that are sent, a button "Change what is sent and the kinds of values" that opens
  a form, and the planner's own answer one click away. With the switch off the
  refusal view has one short block with one button that does the same. Plan, Run and
  Results carry a "not verified" banner; Results mark the values concluded by the
  model and filter them ("Concluded by AI"); Review says there is nothing to choose.
- The audit report gains `web_lookup` (`configuration_id`, requests, input and
  output tokens, `web_searches`, `answers_kept`) and `ai_found` per row (column,
  basis, number of links). The usage summary gains a `model` event with `purpose`
  `web_lookup` and `web_searches`. The interface reads `GET /api/limits` again
  after the provider settings are saved or the key is removed, and after the planner
  refuses a task, so the offer needs no page reload.
- A measurement script, `scripts/experiment_web_lookup.py`, with a probe workbook
  (`samples/probe/mountains_probe.xlsx`) and
  [a note](docs/31-universal-lookup-probe.md), tests the idea outside the
  application. With `--target` it sends the request of the application's own lookup
  handler; `--repeat N` asks every row N times.

**Mixed plans**

- The planner may name the columns it has no source for. Its answer gains
  `unfilled_targets` (empty by default; only with `ready`; no blank or repeated
  names). When the context lists output targets it is told to answer `ready` with a
  plan for the targets it can fill and to list the others, only when no capability
  can produce them. The columns a plan must write are the requested ones minus the
  listed ones (`apply_unfilled_targets`, used by the planner loop and by the
  server's validation; wrong names are the diagnostics `UNKNOWN_UNFILLED_TARGET`,
  `UNFILLED_TARGET_ALSO_WRITTEN` and `UNFILLED_TARGETS_WITHOUT_TARGETS`, repaired
  through the loop). The draft stores `unfilled_targets` inside its fingerprint; the
  run writes only what the plan writes, so an unfilled new column is not created and
  an unfilled existing column is left as it is; the report lists them. The plan view
  gets a notice above the steps and Results one line. The planner is also told that
  cells of a target column that already hold a value are kept by the run (the cause
  of the one baseline refusal on `mountains_gaps`), and the abstention challenge now
  allows partial plans. The planner is not told what happens to the listed columns.
- A mixed plan fills both kinds of columns. `POST /api/fallback-plans` accepts
  `plan_id`, a planner draft with `unfilled_targets`, and answers a new draft of
  kind `mixed`: the planner's plan unchanged plus one `web_lookup` step
  (`build_mixed_plan`) for exactly those columns, with `searched_targets` and
  `planner_plan_id` inside the fingerprint. The planner draft is kept and never
  dropped to make room for it. New error codes: `mixed_base_not_planner`,
  `mixed_nothing_unfilled`, `mixed_base_invalid`. The gate knows three kinds of
  plan, `planner` (no `web_lookup` step), `fallback` (the lookup step and nothing
  else) and `mixed` (the lookup step and other steps). A mixed job runs through the
  normal executor assembly (request policies, reuse caches, review, resume) with the
  lookup bound in addition. It can be reviewed and resumed, and the lookup is
  answered from kept answers; a job of the whole-task search still cannot be resumed.
- In the interface the same switch covers a plan the planner could only partly make.
  With the switch on, the mixed plan is built as soon as such a draft arrives. With
  it off, the partial plan is shown with its notice for the columns without a source,
  a button "Search the web with AI for these columns" and the checkbox "Do this
  automatically from now on"; the partial plan can be approved and run as it is. The
  mixed plan has a notice naming the columns of the sources and the searched ones,
  the search as a marked step in the picture and the list, the usual button for what
  is sent and the kinds (restricted to the searched columns, built again from the same
  planner draft), and a link "Use only the system's sources", which fetches the
  stored planner draft with `GET /api/plans/{id}`. A refusal keeps the partial plan,
  with the server's message and the form. Review is that of a planner job. Results
  take each column's trust from `step_provenance`, mark only the searched cells and
  name the searched columns; the Advanced spend line adds `answers_kept`, also for
  the search of the whole task.

**Kept answers**

- The answers of the search are kept for the lifetime of one plan. A found answer
  (the codec stores a found record with its evidence; the format string is
  unchanged because only a new value type was added) and the answer "nothing found"
  (`NOTHING_FOUND`, stored as a found record without a value and read back as the
  same failure) are written to the plan's value cache and taken over by the next job
  of the same plan for `RESULT_REUSE_SECONDS`. Running the plan again after
  `DEADLINE_REACHED` asks and pays only for the rows not answered; `answers_kept`
  counts the rows a kept answer settled. A failure of delivery or of the input
  (`PROVIDER_ERROR`, `DEADLINE_REACHED`, `INVALID_RESPONSE` and the others) is still
  asked again, and so is every other capability. Another plan sees nothing, and a
  plan built again is a new plan. The key is made of the capability, its source, the
  configuration id, the whole task text and the row's context values. The fallback
  worker now opens the plan's caches like every other run.

**Planner measurement**

- `scripts/experiment_planner.py` now judges the route of each valid plan, not only
  its validity. The expected route of each case is data in `scripts/planner_routes.py`
  (capability, field, parameters, where each input comes from, unit); a deviation has
  a code (`WRONG_CAPABILITY`, `WRONG_FIELD`, `LITERAL_INPUT`, `MISSING_TARGET`,
  `TARGET_HANDED_AWAY`, `TARGET_NOT_HANDED_AWAY` and others) and the headline reads
  "N of M: valid and on the expected route". `--routes-only` judges a saved run
  without calling the model. Two cases were added, `mountains_age` and
  `capitals_mayor`, whose second column no capability can fill. The check knows these
  cases and no others and does not run the plan.
- A test (`tests/unit/test_planner_surface_frozen.py`) compares everything the
  planning model is shown (capability descriptions with and without the routing key,
  the response schema, the instruction texts and one complete request) with committed
  snapshots and says when a change needs a new measurement.

**Usage statistics**

- The usage summary survives a restart and opens on one's own machine without setup.
  Events can be kept in a file (`UsageStore`; one JSON object per line, appended per
  event, replayed through the same counting at start, so totals, the events by kind
  and the latest 300 events come back; a line that is not a valid event is skipped
  and counted as `skipped_lines`; above 5 MB the file is rewritten once as a totals
  record plus the latest 300 events; a failed write logs one warning without content
  and the counting goes on in memory with `persistent: false`, `reason:
  "write_failed"`). The local launcher keeps it by default in the user's state folder
  (`usage/events.jsonl`, not in the working directory); a hosted application keeps
  nothing unless `ENRICHMENT_USAGE_FILE` names a file (which lasts only until the next
  restart on an ephemeral disk); `ENRICHMENT_USAGE_FILE=off` switches it off. The file
  holds exactly the events as before: numbers and fixed words, tested after a whole
  plan, run and download with marker texts in the file name, the cells and the task.
  `GET /api/usage` opens without a code only when the launcher started the app and
  neither access codes nor an admin code are set (an admin code is required as before,
  and a hosted application never answers without it); the response gains `persistent`,
  `since`, `process_started_at`, `skipped_lines` and `reason`; the usage page says
  "kept since" or "cleared by a restart", and Advanced mode links to it.

**Suggested tasks**

- "Suggest a task" could propose to invent values and could name internal functions:
  for mountains with a column that no capability can fill (geological age) it
  suggested filling that column with "an invented approximate age" and wrote
  `resolve_entity`, `lookup_property` and their parameters into the text for the
  person, and the planner followed it (live run, 2026-11-16). The prompts for one and
  for several columns now ask for real facts in a person's words, forbid inventing,
  estimating or filling a column "so that it differs", and say to ask plainly for the
  real value of a column the capabilities do not cover. A suggestion that names a
  capability id or another identifier of the registry is dropped (if all are dropped
  the interface shows its usual "no suggestion" text), and the `task_ideas` usage
  event counts them as `internal_names`. The kept suggestion is now keyed by the model
  as well (after changing the model in the settings the old model's text was still
  served), and a "Suggest again" button beside the shown suggestion or the "none" text
  sends `again` to `POST /api/task-ideas`, which asks the model anew, replaces the kept
  answer and counts against the same per-workbook limit (`task_idea_limit` as before;
  the usage event gains `again`).

**Fixes**

- The local launcher dropped new settings: it rebuilt the settings from two named
  fields, so `ENRICHMENT_ALLOW_WEB_LOOKUP` and `ENRICHMENT_WEB_LOOKUP_MODEL` never
  reached a locally started server and `GET /api/limits` said `web_lookup` false. It
  now copies every setting of the environment, and only the network gate can also be
  opened by the flag. `.env.example` names the two settings. The hosted entry was not
  affected.
- A row with some values written and some not is "Partly filled", for every kind of
  plan; "Empty" means that nothing was written.
- A run of a plan that searches the web started from the interface no longer gets a
  shorter limit than the server gives it. The interface sent `timeout_seconds: 180`
  with every run and resume, so the longest limit (`max_timeout_seconds`) never
  applied to a `fallback` or `mixed` plan; for such a plan it now leaves the field out
  until the person edits the time limit, and the Advanced field shows the limit that
  will apply. Planner plans send 180 as before. The resume route already applied the
  same default as the start route; a test now says so.
- A mixed run that ends in "needs review" no longer says its results hold only
  verified values.

### Measured

- Planning with a verdict on the route, `gpt-6-luna` at effort `low`, ten runs per
  case, 2026-11-17 ([live planning reliability](docs/26-live-planning-reliability.md),
  "Baseline with a route verdict" and "Naming the columns it cannot fill"). Before the
  change: 59 valid plans of 60, all on the expected route, on six cases; one refusal,
  on `mountains_gaps`. After the change: 80 of 80 valid and on the expected route on
  eight cases (the six and the two new ones); no column that has a source was handed
  away, and the two mixed cases (`mountains_age`, `capitals_mayor`) named exactly the
  column without a source in every run. The measurement judges the route of each plan;
  it does not run the plan or compare values. Not measured: other models, tasks with
  more than one column without a source.
- The search as the application sends it, measured with the probe script and not
  through the application ([universal lookup probe](docs/31-universal-lookup-probe.md),
  "Third run" and "Repeat run"), `gpt-6-luna` at effort `low`: on a table of 8 rows
  and 5 columns, 37 of 40 values were found (34 stated, 3 concluded), 9.2 s per row
  (6.6 to 13.0 s), two searches per row, an estimated 2.2 cents per row. Asking three
  rows three times each, 13 of 15 values were the same in all three requests, one was
  worded three ways (an IP rating) and one was found in only some of the requests;
  10.8 s per request, an estimated 2.5 cents. The cost is an estimate from token counts
  and the printed prices; it was not compared with the provider's bill. No value was
  checked against a reference, so these numbers say how often a value came back, not
  how often it was right.

### Checked by the owner since 0.3.0

Single live trials, not measurements:

- The universal search through the application on a table of 8 phones with 5
  columns (2026-11-16): 38 of 40 cells filled, numbers as numbers, `Yes` and `No`,
  notes with source, basis and quote. Whether the values are correct was not checked.
- The first mixed plan through the application (2026-11-17,
  [record](docs/31-universal-lookup-probe.md)): the height came from Wikidata for 10
  of 10 mountains, including one name choice in Review and a resume; the searched
  column had a value for 5 of 10 and `NOTHING_FOUND` for the rest. The five values
  answer different questions (the age of a rock, the start of the Himalayan collision),
  and one `stated` mark was wrong.
- The usage summary survived a restart of the local server.

### Not verified

- The correctness of any value found by the search: there is no reference set.
- The estimated cost of a lookup request against the provider's bill.
- The two changes of the last pull request, the time limit of a run that searches and
  the kept answer "nothing found", in a live run.
- A run of the whole-task search after the execution paths were unified (the server
  change for mixed plans), in a live run.
- The universal search on a hosted instance: it is not enabled on the demo.
- A mixed plan that also contains `road_distance`.
- The Ukrainian and German texts of the new screens were not read by a native speaker.
- The usage file on macOS and Linux (the rule for the state folder; the owner's machine
  is Windows), with a real 5 MB file or a long-running server, and on an ephemeral host.
- "Suggest a task" with the changed prompts on a live model: it was tested against a
  fake model only.

### Known limits

- An empty or new column is searched as `text`. Number with a unit and yes/no have to
  be set by hand under "Change what is sent…".
- Rows are asked one after another, about 9 s each in the probe runs (6.6 to 13.0 s).
  With the row limit of 20 and the limit of 300 s this fits, with little room.
- The mark `stated` or `concluded` is the model's own and can be wrong (seen once).
- Rows are searched independently, and nothing compares the answers of neighbouring
  rows, so a question without one defined answer gives values that cannot be compared.
- A text column is not reproduced to the letter: the same fact came back in three
  wordings in the repeat run.
- A job stays "needs review" while any row is not completely filled, also when there
  is nothing to choose. A row for which nothing was found is not asked again for the
  life of the plan; to ask it again, build the plan again or change the task. The kept
  answers are in the application's temporary workspace and are gone when it ends.
- The usage file is off on a hosted instance by default, and there is no database
  backend.
- The limits of 0.3.0 still apply, except that the usage summary of a local launcher
  is no longer cleared by a restart.

## 0.3.0 — beta (2026-11-15)

More can be filled from what the sources already return, and less is looked
up: facts of a found city go into columns, a partly filled column is completed
without asking again for the rows that have their values, and a run shows what
each step is doing while it goes on.

### What is new

- More facts about a found city can be written into columns: the output of
  `geocode` now also declares `population` (a number) and `timezone` (text), taken
  from the same response as the coordinates, with no further request. A place the
  source holds no value for leaves that one cell empty; its other cells are written,
  and the audit and Results say that the source has no value (`VALUE_NOT_GIVEN`).
  The values are what GeoNames data, served by Open-Meteo, holds for the matched
  place: populations can be old, and nothing checks them against a second source.
  The elevation of a place record is deliberately not offered: it would compete
  with the elevation of a mountain, which has its own verified path. The version of
  `geocode` is now 1.2, so results cached under 1.1, which lack the values, are not
  reused: the first run after the change asks the source again.
- The latitude and the longitude of a city can be written into two columns: the
  output of `geocode` now declares the fields `latitude` and `longitude` (numbers,
  decimal degrees), so one step fills two columns from a single lookup. Both cells
  stay empty for a city that is not found or still waits in Review; the rules for
  existing values hold for both columns. The plan in words says which field goes
  into which column, Results show a coordinate with five decimals, and the
  overview says so. The coordinates are those of the place the source matched;
  nothing checks them against a second source. The capability's version stays
  1.1.
- In keep mode a row whose every chosen cell already has a value is no longer
  looked up: none of its steps run, no source is asked, and it never lowers the
  outcome or reaches Review. Rows with some cells empty are looked up in full;
  replace mode skips nothing. The audit gains `skipped_rows` and the job
  `rows_skipped`; `progress` and the counts cover the executed rows. Execution
  and Results say which rows were not looked up. New example "Mountains: fill
  only the missing heights". One run still takes the first rows of the table,
  skipped or not.
- While a run goes on, the picture and the list of steps on Execution (and the
  nodes of the Advanced graph) show what each step has done over the rows
  finished so far. The job's `progress` gains `steps`, counts of `filled`,
  `failed` and `skipped` per step; it is the row counter split by step, not an
  event stream (#102 stays open).
- Internal: the built-in capabilities are declared once in
  `capabilities/bindings.py` (source, kind of trust, reuse, and for a name
  lookup its name input and approved parameters); the executor assembly, the
  run worker and the resolution, other-name and resume code read it instead of
  comparing capability ids. No change in behaviour, in what the planner is
  shown or in any response.
- A short page in Ukrainian for the course mentors:
  [docs/for-mentors.uk.md](docs/for-mentors.uk.md).

### Measured

- Planning with the contracts of this release (six capabilities, `geocode`
  1.2), `gpt-6-luna` at effort `low`, 2026-11-15: 60 valid plans of 60 on six
  tasks — capitals, mountains, capitals with three columns, mountains with
  gaps, coordinates into two columns, population
  ([live planning reliability](docs/26-live-planning-reliability.md)). The
  measurement counts valid plans; it does not compare a plan with an expected
  one.

### Checked by the owner since 0.2.0

Single live trials, not measurements:

- the usage summary at `/#usage` on the hosted instance;
- the example with missing heights: the filled rows were not looked up;
- a population column for the capitals was filled from the live source;
- "Suggest a task" proposed a fitting task for a partly filled column and for
  three chosen columns.

### Not verified

- The `timezone` field in a live response.
- The step counts growing during a live run, and the live counts in Advanced.
- A run started under an earlier version and resumed under this one (its
  saved results have the earlier shape).
- The failure answers of the routing service, and planning without a routing
  key (five capabilities), as in 0.2.0.

### Known limits

- A run still takes the first rows of the table, so rows that are skipped
  because they are filled count against the row limit. A row with some of its
  chosen cells empty is looked up in full.
- Coordinates, population and timezone are what the geocoding source holds for
  the matched place. Nothing checks them against a second source, and
  population figures can be old.
- The step counts during a run are the row counter split by step, updated when
  a row finishes; there is no event stream.
- The limits of 0.2.0 still apply.

## 0.2.0 — beta (2026-11-14)

The second release shows more of what the approach can do: a plan that
branches and fills several columns, a start page that says what the system is
and lists what a plan may be built from, the plan as a picture, and a hosted
demo that tells its owner what was used. The execution pipeline, validation
and resolution of 0.1.0 are unchanged.

### What is new

- **An overview page** opens first: what the system is, how a table is filled,
  what to keep in mind, and the list of what a plan may be built from. The list
  is the server's (`GET /api/capabilities`), so a capability added there is
  listed without a change to the interface; Basic words each one plainly,
  Advanced adds its contract exactly as the planning model is shown it and the
  source that answers for it. The short "what it can do" list on the first step
  and after a refused plan is the same list.
- **Driving distance by road** between two points, computed by openrouteservice
  on OpenStreetMap data. Offered only when `OPENROUTESERVICE_API_KEY` is set;
  without it nothing changes.
- **Ratio** of two quantities of the same unit.
- **A plan can branch and fill several columns**, for example straight-line
  distance, distance by road and how many times longer the road is. With a
  routing key this is a third example with a prepared plan.
- **Basic mode fills several columns:** each is chosen with the same plain
  question, and new columns are placed after the sheet's last one.
- **Suggest a task** proposes one task that fills all chosen columns when
  several are chosen, instead of up to three alternatives. A task that leaves a
  chosen column out is not shown.
- **Basic mode draws the plan** above its description in words: which step
  uses the result of which, in the same words and numbers as the list, with
  the column each step fills. No identifiers and no inspector; selecting a step
  points at its line in the list.
- **What each step did:** after a run, Execution shows for every step of the
  plan how many rows got a value, how many did not and how many never reached
  it — on the picture and in words in Basic, on the nodes of the graph in
  Advanced, with the failure codes and the sources of the values for the
  selected step. Read from the audit of the run; not live progress.
- **A code per visitor and a usage summary** for a hosted demo:
  `ENRICHMENT_ACCESS_CODES` gives every visitor a labelled code, and the holder
  of `ENRICHMENT_ADMIN_CODE` sees at `/#usage` what each label used — visits,
  plans, runs, rows, requests to the AI with tokens, requests to each source.
  Numbers and fixed words only; table contents, file names and task texts are
  never recorded.
- The planner is told that a row without a verified value keeps its cells
  empty, so "leave unresolved results empty" is no longer a reason to refuse a
  task. Validation is unchanged.

### Measured

- Planning with six capabilities (a routing key set), `gpt-6-luna` at effort
  `low`: 30 valid plans of 30 on three tasks — capitals, mountains, and
  capitals with three result columns
  ([live planning reliability](docs/26-live-planning-reliability.md)). Before
  the sentence above was added to the planner's guidance the same measurement
  gave 27 of 30.
- The three-column example once against the live routing service: ten pairs of
  capitals, all rows filled after two city names were chosen in Review
  ([road distance and ratio](docs/30-road-distance-and-ratio.md)). One run is
  an observation, not a measurement.

### Not verified

- The failure answers of the routing service (no route, no road near a point,
  route too long, rejected key, rate limit) were never seen live.
- Planning with five capabilities — an installation without a routing key — was
  not measured.
- "Suggest a task" for several columns was tried on a live model, not measured.
- Visitor codes and the usage summary on a hosted instance.
- The plan picture for a plan deeper than three levels, and on a phone screen.

### Known limits

- Still cities and mountains. Adding a capability means code in several places;
  only its listing and its source are declared once
  ([design note](docs/29-capability-extension-design.md)).
- A hosted demo is one workspace shared by all visitors: one run at a time, and
  a new workbook can push out the oldest one. Codes say who is counted; they do
  not separate visitors.
- The usage summary lives in memory and is cleared by a restart; the events
  remain as lines in the server log.
- A road distance has no source record to check it against. The free plan of
  the routing service allows 2,000 routes a day and 40 a minute, and runs do
  not pace themselves to it.
- The limits of 0.1.0 on rows per run, storage, the model provider and the
  language of instructions still apply.

## 0.1.0 — MVP (2026-11-13)

The first release: a spreadsheet column is filled from open sources through a
plan the user approves, with an audit of every value. Both sample tasks run end
to end in the browser.

### What it does

- **Three tasks:** coordinates of a city, distance between two cities, elevation
  of a mountain. Sources: Open-Meteo geocoding and Wikidata.
- **Five workspaces:** Dataset, Plan, Execution, Review, Results, in Basic and
  Advanced modes; English, Ukrainian and German; light and dark.
- **Dataset:** the table is found on the sheet and shown whole, with a text
  filter and sorting; the task is typed or proposed by the AI; a run takes the
  first rows of the table.
- **Plan:** a language model composes registered operations into a plan; the
  plan is validated against the table, the operation contracts and the
  instruction, and runs only after approval of exactly that plan and source.
- **Review:** where a name has several matches the user chooses, with country,
  coordinates, population or elevation and a map; a name the source does not
  know can be searched under another name, typed or proposed by the AI. Every
  choice is verified against the source again.
- **Results:** each row says whether it was filled and why not; the filled
  workbook is a separate copy; a JSON audit records how each value was
  obtained, the user's choices and where another name came from.
- **A resume asks only for what is open:** values an earlier run of the same
  plan confirmed are taken over without asking the sources again; the audit
  says how many.
- **Existing values are kept** unless the user chooses to replace them; each
  replaced cell is listed in the audit with its earlier value.
- **Hosted demo:** one process behind a shared access code, with a Docker image
  and a Render blueprint.
- **Command line:** the same pipeline as `enrichment`, with benchmarks and
  experiment scripts.

### Measured

- Planning: 10 of 10 valid plans on each sample task with `gpt-6-luna` at
  effort `low` (the default model) and with `gpt-4.1-mini`
  ([live planning reliability](docs/26-live-planning-reliability.md)).
- The two 10-row samples against live sources
  ([record of 2026-11-09](docs/18-academic-live-acceptance.md)).

### Known limits

- Cities and mountains only; a new source or operation means writing code.
- One run fills up to 20 rows by default and stops after 300 seconds; runs are
  not parallel.
- Nothing is stored: workbooks, plans, runs and keys are gone after a restart.
- One model provider; instructions were measured in English only.
- Resolution accuracy has not been measured on a labelled sample.

### Not in this release

A visual view of execution, an agent mode, review at scale, unit conversion,
and runs of rows other than the first ones of a table.
