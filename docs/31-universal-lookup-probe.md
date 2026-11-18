# Universal lookup probe

`scripts/experiment_web_lookup.py` is a measurement script. It is **not part of the
application**: nothing in `src/` calls it, it writes into no workbook, and it adds no
capability to the planner. It exists to answer one question before any application
code is written.

## Why it exists

The product fills columns of an Excel table with values from registered sources; a
language model only plans. A task that no registered capability covers is refused.
The owner wants a fallback for those tasks: a model with web search finds the value
for each row and returns it with source links. A colleague's course project does
this per row. The probe tests whether that is workable with the provider the product
already uses (OpenAI, Responses API, `gpt-6-luna`).

## The design this informs

Decided by the owner on 2026-11-15. None of it is built.

- Stage 1 (the planner with registered capabilities) stays untouched and is never
  told about the fallback.
- When the planner refuses a task and the setting is on, the **user** starts the
  fallback from the refusal screen.
- Values found by the model are written immediately, but marked as found by AI and
  not verified. Their sources are kept (cell notes and/or a separate sheet, as an
  option) and the lookup is always logged in the audit.
- Only uncertain rows go to Review.
- Numbers with units are supported.
- The user chooses which columns are sent, or "all columns".
- The fallback is off by default.

## Evidence that shaped it

Each point was checked in the code on 2026-11-15.

- The planner's refusal is a status plus free text:
  `PlanningResponse` has `status: Literal["ready", "unsupported", "ambiguous"]` and a
  free-text `reason` (`src/ai_data_enrichment/planning/models.py`). There is no
  machine-readable cause, so the application cannot tell "no capability exists" from
  a wrong refusal.
- Refusals have been wrong: `docs/26-live-planning-reliability.md` records a refusal
  although registered producers could derive the inputs, and "the observed false
  refusal about optional required_role". This is why the fallback is started by the
  user and not triggered automatically by a refusal.
- Evidence today is only a source name and a reference string:
  `Evidence(source, reference)` in `src/ai_data_enrichment/execution/models.py`. It has
  no link, quote or retrieval date, so web sources do not fit it without a change.
- The kind of trust is not shown anywhere yet: `Trust` (computed, record,
  resolved name, service) is declared in `src/ai_data_enrichment/capabilities/bindings.py`,
  but outside that file it is read only in `plan_run_worker.remote_sources` to list
  the sources a run contacts. No API field or interface text shows it. A new kind,
  "found by AI, not verified", would be the first one a user sees.

## What the official documentation says

Read on 2026-11-15.

Confirmed:

- The Responses API has the tool `{"type": "web_search"}` with `search_context_size`
  `low`, `medium` or `high`; the page says this "does not set an exact token count or
  guarantee a specific number of sources or citations".
  <https://developers.openai.com/api/docs/guides/tools-web-search>
- `include: ["web_search_call.action.sources"]` returns the full list of URLs
  consulted, "often more than the cited URLs"; answers carry `url_citation`
  annotations (`url`, `title`, `start_index`, `end_index`). Same page. The create
  reference lists the include value:
  <https://developers.openai.com/api/reference/resources/responses/methods/create>
- With `tool_choice: "auto"` search is optional; `"required"` makes it run. Same
  guide, Limitations.
- `gpt-6-luna` lists "Web search: Supported", "Structured outputs: Supported" and
  `reasoning.effort` of none, low, medium (default), high, xhigh and max.
  <https://developers.openai.com/api/docs/models/gpt-6-luna>
  (The web search guide itself names `gpt-6-astra` as its recommended model and does
  not mention `gpt-6-luna`.)
- Pricing: `gpt-6-luna` $0.10 input and $0.50 output per 1M tokens (standard tier);
  web search $10.00 per 1k calls; search content tokens are billed at model rates.
  <https://developers.openai.com/api/docs/pricing>
- The web search guide advises keeping public web search and private lookups in
  separate requests because of prompt injection, and says the 128k search context is
  capped. Not relevant to the probe's data, relevant to the design.

Not confirmed:

- **Web search together with a JSON-schema answer.** The structured outputs guide
  (<https://developers.openai.com/api/docs/guides/structured-outputs>) and the web
  search guide do not say whether `text.format` of type `json_schema` works in the
  same request as the `web_search` tool. The model page lists both features
  separately. The script's default (`--structured json_schema`) tests it; if the API
  rejects the request, the summary counts it as "rejected with HTTP 400" and prints the
  first message. `--structured json_object` and `--structured none` are the
  alternatives (the shape is then asked for in words and checked by the script).
- **`max_tool_calls`.** It exists in the `openai` SDK pinned in `uv.lock` (3.41.0,
  `Responses.create(max_tool_calls=...)`, documented there as "the maximum number of
  total calls to built-in tools that can be processed in a response", further calls
  "will be ignored"). The reference page shows the field in the response example, but
  its description was not read on the page, and the web search guide does not
  mention it. It is sent by default as 3; `--max-tool-calls 0` does not send it.
- How search content tokens are counted in `usage.input_tokens`. The estimate assumes
  they are included.

## How to run it

Needs `OPENAI_API_KEY` (in an ignored `.env` or the shell). The cell values of each
row are sent to the provider, and answers, quotes and links are written under
`artifacts/web-lookup-experiments/<timestamp>-<id>/` (`rows.jsonl`, `summary.json`).
`artifacts/` is ignored by git; do not commit or share those files unreviewed.

`--dry-run` prints the request for the first row, sends nothing and needs no key.
Add it to either command to look first.

1. The mountains probe, a number in metres with ground truth:

   ```
   uv run --env-file .env python -m scripts.experiment_web_lookup \
     --workbook samples/probe/mountains_probe.xlsx \
     --question "Find the elevation of this mountain above sea level." \
     --answer number --unit m --expected-column expected_height_m
   ```

2. A text answer on the owner's own file (replace the names with real ones; leave
   `--expected-column` out when there is no ground truth):

   ```
   uv run --env-file .env python -m scripts.experiment_web_lookup \
     --workbook path/to/own_file.xlsx --sheet Sheet1 --rows 20 \
     --question "Find the official website of this company." \
     --context-columns Company,City --answer text
   ```

The probe workbook `samples/probe/mountains_probe.xlsx` is built by
`scripts/make_probe_workbook.py`. Ten rows are the mountains of
`samples/mountains.xlsx`, with `expected_height_m` copied from
`samples/check/mountains_enriched.xlsx`. Five rows carry **invented** names
(Mount Quelbarith, Kharnogorsk Peak, Tzadlungma, Vorlenhorn, Mount Ashtrevel) that do
not denote real mountains; their expected cell is empty, because the correct outcome
is no answer.

Other options: `--model`, `--reasoning-effort` (default low), `--search-context-size`
(default low), `--search required|auto`, `--tolerance` (percent, default 10),
`--concurrency`, `--timeout`, and the three `--price-*` options.

## What the summary means

- **answered / nulls**: the model gave a value / returned null.
- **shape failures**: the reply was not JSON of the requested shape (the reasons are
  in `rows.jsonl`); for a number with `--unit`, a different unit counts as one.
- **transport errors**: the request failed or the response did not complete.
  **rejected with HTTP 400** is the subset where the API refused the request itself,
  which is how an unsupported combination would show.
- **rows with no API-reported citation**: replies for which the search calls returned
  no sources and the answer carried no `url_citation`. A row passes as soon as the
  search returned anything, so this line does not say that the answer relied on a
  page; see "Second run" below.
- **rows claiming a source the API did not report**: the model named a link that the
  search never returned (compared without fragment, `utm_*` parameters, trailing slash
  and `www.`). A warning sign for invented links.
- **match / mismatch / no answer**: against `--expected-column`. Numbers match within
  the tolerance; text matches ignoring case and whitespace, never as a substring.
- **coverage**: rows with an expected value that got an answer, divided by all such
  rows. **precision when answered**: matches divided by those answered rows.
- **empty expected cell**: null is the correct outcome ("correct no answer"); any value
  is a "false answer".
- **search calls**, tokens, seconds, and the **estimated cost** at the printed prices
  (taken from the pricing page on 2026-11-15; change them with the `--price-*` options).

## What the result answers

Stated as questions; the probe does not predict the answers.

- Does the API accept web search and a JSON-schema answer in one request with this
  model? If not, do `json_object` or `none` give a usable shape?
- How often does the model answer, and how often is the answer right where the truth
  is known? What is the coverage and precision when answered?
- Does it return null for the invented mountains, or does it guess?
- Does every answer come with links the API itself reported? How often does a claimed
  link not appear among the reported ones?
- How many search calls, tokens, seconds and dollars does one row take? Is the cost of
  a table of the usual size acceptable? Does `--max-tool-calls` change it?
- Is the `confident` flag informative: do wrong answers carry `confident: false`?

## What it cannot show

- Quality on tasks without ground truth. The mountains have known heights; the
  owner's own file is only as good as its expected column, or is not judged at all.
- Whether a `quote` really appears on the cited page. The script does not fetch
  pages; the quote is the model's claim. Only whether the link was reported by the
  search is checked.
- Whether the source is a good one (official, current), or whether a page's content
  could have steered the model (prompt injection through search results).
- Stability: each row is asked once. Repeating the run shows the spread; one run does
  not.

## First run (2026-11-15, by the owner)

One run of the mountains probe: `gpt-6-luna`, effort `low`, `search_context_size` low, answer requested as a JSON schema, search required, at most three tool calls per request. One run of fifteen rows is an observation, not a measurement.

| What | Result |
| --- | --- |
| Requests accepted with web search and a JSON-schema answer together | 15 of 15 (no HTTP 400, no shape failure, no transport error) |
| Ten real mountains | 9 answered, all 9 within tolerance; 1 left empty |
| Five invented names | 5 of 5 left empty, each with a reason; no value was made up |
| Rows for which the search reported sources | 15 of 15 (20 to 38 URLs per row); no answer carried a `url_citation` |
| Rows naming a source the API did not report | 1 (see below) |
| Search calls | 17, 1.13 per row |
| Tokens | 136,979 in (about 9,100 per row), 2,998 out |
| Time | 83 s in total, about 5.5 s per row, one request at a time |
| Estimated cost | 0.185 USD, about 1.2 cents per row; the search fee is 0.17 of it |

What the rows show:

- Eight of the nine answers equal the expected value exactly; Everest came back as 8848.86 against an expected 8849. Each answer carried a quote containing the number.
- **Nanga Parbat came back as 8126 m**, with a quote, against 8215 in the committed check file `samples/check/mountains_enriched.xlsx`. The two differ by about 1 %, so the default tolerance of 10 % counted a match. The check file's value is therefore in doubt; what the verified path (Wikidata) returns for this mountain has to be looked at before either is called right.
- **Makalu was left empty on purpose**: the model reported that two official sources give 8,485 m and 8,463 m and set `confident` to false. This is the instructed behaviour, and it is the kind of row the design sends to Review.
- The one "source the API did not report" is a link with percent-encoded non-Latin characters on the Makalu row. Whether the model altered the address or the two differ only in encoding was not examined.

What this answers, and what it does not:

- The request shape works on this model: web search and a schema-shaped answer in one request. The API returned the list of pages the search found; it did not mark which of them the answer used (corrected after the second run; the first version of this line said "with citations").
- On this task the model declined to answer where it should have. Famous mountains are an easy case; nothing here says how it behaves on obscure objects, on text answers, or on names that resemble real ones more closely.
- Cost and time are dominated by the search call and by about nine thousand input tokens of search content per row. At one request at a time, twenty rows take about two minutes, which is close to the default time limit of a run.
- The estimate assumes the search content is inside the reported input tokens; it has not been compared with the provider's bill.

## Second run (2026-11-15, by the owner): several text columns at once

One run on the owner's own test workbook, which is not in the repository: eight phones,
each described by brand, exact model and a variant or region note. The question named
six columns to fill (battery capacity, IP rating, eSIM, microSD, wireless charging and
a link to the evidence), with `--answer text`. Same settings as the first run. There is
no expected column, so **no answer was judged**: nobody compared the values with the
pages. Eight rows asked once are an observation.

| What | Result |
| --- | --- |
| Rows answered | 8 of 8, none left empty, all with `confident: true` |
| Shape failures, transport errors, HTTP 400 | 0 |
| Search calls | 12, 1.5 per row |
| Tokens | 133,468 in (about 16,700 per row), 3,735 out |
| Time | 69 s in total, about 8.6 s per row, one request at a time |
| Estimated cost | 0.135 USD: about 1.7 cents per row, about 0.3 cents per requested cell |
| Search-reported URLs per row | 34 to 66; `url_citation` annotations: 0 in every row |
| Rows naming a source the API did not report | 1 (see below) |

What the rows show (evidence):

- **Six values came back as one string.** The script asks for one value per row, so the
  model packed the six answers into it as `Column: value` pairs. One row separated them
  with ` | `, seven with `; `. A product cannot split such a string reliably.
- **The values are not in one form.** Support columns came back as "Yes", "No",
  "Yes (up to 1.5 TB)", "Yes, 15 W magnetic wireless charging (Qi2)" and
  "No (European XT2309-2 specifies dual nano-SIM)"; one IP rating as "IPX5/IPX8, IP6X".
  The question passed to the script did not ask for Yes/No; the instruction text inside
  the workbook does, but the script never sees it.
- **Three "No" answers are conclusions from silence**, by the model's own explanation:
  wireless charging "is not listed" (two rows) and eSIM is "No" because the
  specification lists two physical SIM slots. All three carry `confident: true`. One
  flag for the whole row cannot say "five values were stated and one was concluded".
- **Some sources do not fit the row.** One eSIM answer rests on an independent review, not on the manufacturer, one
  microSD answer on a product sheet hosted by a carrier outside Europe for a row that
  says "EU retail version", and a row that says "do not mix regions" lists a support
  page of another region among its sources.
- **Nothing was left empty**, although the workbook's guide says that not every cell
  can be filled and one row asks to "check exact regional variant". The model chose one
  regional page and answered with confidence. Whether any value is wrong is unknown.
- **One `quote` field cannot support six values.** Three rows put several quotes into
  it, and one of them is a stitched summary, not a quotation.
- **The API marked no citations.** No answer in either run carried a `url_citation`.
  What the API reports is everything the search found: forum threads, encyclopedia
  pages and unrelated articles included (the first reported URL of one row is a review
  of a different phone). "The link was reported by the API" therefore means "the link
  was among the search results", not "the answer relied on this page".
- **The one "unreported" source is the same page with another query string.** The model
  named a manufacturer page without parameters; the search reported it three times,
  each with a tracking parameter. In the first run the difference was percent-encoding.
  Both are artefacts of the comparison. Dropping every query string would be wrong too:
  another row's source needs its `?sku=` parameter to point at the right variant.

What follows for the design (recommendations, not built):

- One request per row with **one answer field per requested column**, each with its own
  value, its own source and its own mark, instead of one value and one flag per row.
- A **declared kind per column**: text, number with a unit, or yes/no. Free text in a
  yes/no column is a shape failure, not an answer.
- A per-value mark that separates **stated on the page** from **concluded** (for
  example from absence). Concluded values belong in Review, not among the filled cells.
- The source check needs a second, weaker level (same address without the query
  string) reported separately from an exact match.
- Asking for several columns in one request is what makes the price per cell low. This
  run paid for 12 searches and answered for 48 cells; one request per cell would have
  paid for at least 48.

Still unanswered: how often such answers are right. That needs a workbook of this kind
with an expected column checked by hand, which is the benchmark planned as step 6.

## Third run (2026-11-16, by the owner): the application's own request

The probe's columns mode (`--target`) sends the request of
`providers/web_lookup.py` through the real `WebLookupHandler`: one request per row, a
strict schema with one answer object per column, and the strict reply reader. Same
workbook as the second run (eight phones), five target columns: battery capacity
(number, mAh), IP rating (text), eSIM, microSD and wireless charging (yes/no). Two runs:
the first three rows, then all eight. Again there is no expected column, so **no value
was judged**; one pre-filled cell (an IP rating) agreed in both runs.

| What | First 3 rows | All 8 rows |
| --- | --- | --- |
| Requests accepted by the API | 3 of 3 | 8 of 8 |
| Rows with a result | 2 (one `NOTHING_FOUND`) | 8 |
| Values found | 10 of 15 | 37 of 40 |
| Found as stated / concluded | 9 / 1 | 34 / 3 |
| Not found, by reason | `no_reported_link` 5 | `no_value` 3 |
| Links kept (exact / by address), dropped | 13 / 0, 5 | 41 / 1, 0 |
| Searches | 4 | 16, 2 per row |
| Tokens in / out | 43,638 / 1,451 | 153,950 / 3,958 |
| Time per row | 9.4 s | 9.2 s (6.6 to 13.0 s) |
| Estimated cost | 1.5 cents per row | 2.2 cents per row, about 0.5 cents per found value |

What the rows show (evidence):

- **The API accepts the request shape** on `gpt-6-luna`: 11 requests, no refusal, no
  shape failure.
- **Every column has its own typed value.** Yes/no columns hold `Yes` or `No` and
  nothing else, which the second run could not deliver.
- **The model now separates stated from concluded.** The three "No" answers it marked
  `concluded` rest on a quote that does not mention the feature (a charging type, a
  list of SIM slots, a list of storage sizes). In the second run answers of this kind
  were given as plain confident values.
- **The model now leaves cells empty.** Three cells have no value; the second run
  answered "No" in all three.
- **The two rows asked in both runs agree**: 10 of 10 values are the same; the text of
  one IP rating is worded differently ("IPX5/IPX8 water resistant; IP6X dust proof"
  against "IPX5/IPX8 and IP6X").
- **One row was lost in the first run and fine in the second.** All five of its values
  were dropped because the one link named for each was not among the search results
  (one search in that request, two in the later one). The run files did not record the
  link, so it is unknown whether the rule was right to drop it.
- **"Stated" is sometimes generous.** One "Yes" for wireless charging rests on a line
  about a charging stand in a declaration of conformity. One battery capacity for a row
  that says "Europe; do not mix regions" is quoted from the maker's page of another
  country (for the same model number).
- **One kept link carried a search-engine tracking parameter** (`?msockid=…`) and
  matched only at the weaker level.
- **Twenty rows do not fit the default time limit.** At 9 to 13 s per row, one request
  at a time, 20 rows take 3 to 4.5 minutes; the default limit of a run is 180 s and the
  highest is 300 s.

What was changed after this run (in code, not measured yet):

- The reply reader records every link it drops with one reason, and the probe stores
  and prints them, so the lost row can be explained the next time it happens.
- Known tracking parameters are ignored when links are compared and removed from the
  link that is kept. A parameter that can select content (`?sku=…`) still has to match
  exactly.
- `--repeat N` asks every row N times and reports where a value is always the same,
  where it differs and where it is sometimes missing.

### Repeat run (same day): three rows, each asked three times

Run with `--repeat 3` on the first three rows after the changes above: 9 requests, all
answered, 44 of 45 values found (38 stated, 6 concluded), 20 searches, 10.8 s per
request (7.0 to 15.7 s), about 2.5 cents per request by estimate.

| Over the three asks of a row | Values (of 15) |
| --- | --- |
| Always the same value | 13 |
| Values differ | 1 |
| Sometimes missing | 1 |
| Never found | 0 |

- **The values that differ are one fact in three wordings**: an IP rating given as
  "IPX5/IPX8, IP6X", "IPX5/IPX8 (water resistance); IP6X (dust protection)" and
  "IPX5/IPX8 and IP6X". Numbers and yes/no values never differed. A text column is
  therefore not reproducible to the letter unless the question fixes a form.
- **The basis never changed between the asks of a row** (14 values found in all three
  asks). Between runs it did: wireless charging of one phone was "stated" in the
  morning run (from a support table) and "concluded" in all three asks here.
- **The one dropped link explains itself.** The model named an article under the path
  of this phone's support pages. The search had reported that article number under
  two other paths of the same site in earlier runs, and not under this one here. The
  address was most likely composed by the model, so the rule did what it is for: the
  value was left out in that ask and found in the other two.
- **The row that was lost in the morning was answered three times out of three.** Its
  loss is still unexplained: one in five asks of that row so far.
- **One request reported four `web_search_call` items** although the request limits
  tool calls to three. The probe counts every such item as a paid search; whether all
  of them are billed is unknown, so the estimate may be too high.

What the application step has to settle: bounded parallel requests or a separate time
limit for a fallback run. The rule "no reported link, no value" stays as it is: the
only dropped link seen so far was one the search had not returned.


## First mixed run through the application (2026-11-17)

The first live run of a mixed plan, started from the interface and not from the probe.
The table has ten mountains. The task, in Ukrainian, asks for the height in metres and
the "known geological age in millions of years". Planner model `gpt-6-luna`; lookup
configuration `openai-web-lookup-p1-gpt-6-luna-low`.

| What | Result |
| --- | --- |
| Plan kind | `mixed` |
| Planner steps | the height; the age column was named as having no source |
| Lookup step | one, added by the server for the age column (the switch was on) |
| Height | 10 of 10 rows from Wikidata records, one of them after a name choice in Review ("Annapurna" → "Annapurna I") |
| Age | 5 of 10 rows with a value, 5 with `NOTHING_FOUND` |
| Kind of the age column | `text` (the default for an empty column), so the five cells hold sentences, not numbers |

What the five age values answer:

| Mountain | Value | What it is the age of |
| --- | --- | --- |
| Everest | about 450 | the limestone of the summit |
| K2 | 115–120 | a zircon age of a gneiss from the south face |
| Dhaulagiri | about 50 | the start of the Himalayan collision |
| Annapurna | about 22 | the start of the Himalayan collision, from another page |
| Nanga Parbat | 10–13 | the start of the formation of the dome |

- Four of the five cells say themselves that this is not the age of the mountain.
- Four are marked `concluded`. The K2 cell is marked `stated`, although its own text says
  that the age of the mountain is not known as one number; the quote is about a rock
  sample. The basis mark is the model's own, and it was wrong here.
- The question has no single defined answer. The run shows what the search does with such
  a question, not how correct it is. No value was checked against a reference.

The resume after the name choice:

| What | Value |
| --- | --- |
| Lookup requests | 6 |
| Answers taken from the kept ones (`answers_kept`) | 4 |
| Input tokens | 62,237 |
| Output tokens | 886 |
| Web searches | 6 |
| Time limit of the job | 180 s |

What followed from it, as the two changes of the pull request that records this run:

- The interface sent `timeout_seconds` (180) with every run, so a run that searches never
  had the longest limit the server gives it; it now leaves the field out for such a plan
  unless the person edited it.
- Five of the six requests of the resume ended in `NOTHING_FOUND`. That answer was not
  kept, so every rerun and resume of the plan asked such rows again. (The audit of the
  first job was not looked at; that the same five rows had no answer there is likely, not
  shown.) The answer is now kept for the plan's lifetime like a found one; a failure of
  delivery or of the input still is not.
