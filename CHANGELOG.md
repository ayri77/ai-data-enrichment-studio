# Changelog

## Unreleased

- The universal search can be asked for through the API and the run worker, no interface
  yet: `POST /api/fallback-plans` makes a draft with `"kind": "fallback"` and one step,
  `web_lookup`, which asks a model with web search for several columns of each row; approval,
  run, review, download and the audit use the existing routes. It is off unless the server sets
  `ENRICHMENT_ALLOW_WEB_LOOKUP=1` (and has a key); `GET /api/limits` gains `web_lookup`. Each
  row costs one model request and a search fee; the report gains `web_lookup` (requests, tokens,
  searches) and `ai_found` per row, and the usage summary a `web_lookup` model event and
  `web_searches`. A plan with `web_lookup` is refused on the normal route, from the planner
  and as a supplied plan. Tested only against a fake client: no live run has been made.
- The kind of trust behind a value is now shown instead of staying in the code:
  `GET /api/capabilities` gains `trust` (`computed`, `record`, `resolved_name`,
  `service`) beside `source`, and the audit report gains `step_provenance`
  (capability, source and trust of each step) and `sources_in_workbook`. Results
  says under each output column how its values were obtained, and the overview
  words each capability from `trust` and its source; a kind this interface does
  not know is shown as its source. A new request field `sources_in_workbook`
  (`none`, the default; `notes`; `sheet`) makes the result workbook say where each
  written value came from, in a note on the cell or on an added `Sources` sheet.
  The planner is not shown `trust`. The note and sheet texts are English only.
- A measurement script, `scripts/experiment_web_lookup.py`, with a probe workbook and
  [a note](docs/31-universal-lookup-probe.md), tests whether a model with web search can fill
  a column for tasks no registered capability covers. It is not part of the application.

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
