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
  no sources and the answer carried no `url_citation`.
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
| Rows with citations reported by the API | 15 of 15 (20 to 38 source URLs per row) |
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

- The request shape works on this model: web search and a schema-shaped answer in one request, with citations.
- On this task the model declined to answer where it should have. Famous mountains are an easy case; nothing here says how it behaves on obscure objects, on text answers, or on names that resemble real ones more closely.
- Cost and time are dominated by the search call and by about nine thousand input tokens of search content per row. At one request at a time, twenty rows take about two minutes, which is close to the default time limit of a run.
- The estimate assumes the search content is inside the reported input tokens; it has not been compared with the provider's bill.
