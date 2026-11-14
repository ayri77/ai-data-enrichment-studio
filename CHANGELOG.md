# Changelog

## Unreleased

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
