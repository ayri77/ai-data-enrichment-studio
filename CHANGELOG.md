# Changelog

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
