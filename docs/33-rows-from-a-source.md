# Rows from a source

The application fills columns of the rows that are in a table. This note describes the core of
the second kind of task: adding new rows to a table from a source. The core is in
`src/ai_data_enrichment/rows/`. It uses no model and no network. Four routes make it reachable
(see "The routes") and the three steps of the interface use them (see "In the application").

## What the core does

`append_rows` takes a workbook, a plan and a list of candidates (one would-be row each) and
writes the new rows to a **copy** of the workbook. `preview_rows` makes the same decisions and
writes nothing; both return the same `RowsCount` for the same input. The copy is published like
the output of `write_excel_cells`: a new `.xlsx` file in a temporary file first, checked, then
linked into place; an existing file is never replaced and the source is never changed.

A plan names the source, the table (a range that includes the header row), which field goes to
which column, the key, what to do with a duplicate (`skip` or `add_marked`) and optional
conditions. Rows are written right after the last row of the table that holds a value, in the
order of the candidates. Every new cell takes the style of the cell above it (a table that has
only its header gives none, and the new rows are plain); text is always a
string cell, so a subject that starts with `=` stays text. With `add_marked` a duplicate is
written with a comment on its first key cell, "Possible duplicate of row N". An Excel table or
an auto filter that spans exactly the header to the last row is extended to the new rows.

## The registry of sources

`ROW_SOURCES` in `rows/sources.py` is a description table, like `BINDINGS` for capabilities. A
new source is one more `RowSource`. It declares:

- `source_id` and `label`; `connect` (only `files` so far) and `file_suffixes`;
- `fields`: for each a `field_id`, a `kind` (`moment` or `text`), a `label`, and the lower-case
  `headers` it is proposed for;
- `default_key`: the fields that together tell one row from another;
- `listing`: `given` when the source hands over its rows, so they can be counted;
- `local_only`: whether the rows are read on this machine only;
- a function that turns what it reads into `Candidate` values (for e-mail files,
  `mail_candidates`).

The one source is `mail_files`: Received, Sender and Subject of each message, key
Received + Sender. Its values are exactly those the mail workbook holds, masked. The text of the
mail is not part of a candidate.

## The counts

`RowsCount` holds numbers only: `in_source`, `left_out` (did not pass a condition), `without_key`
(passed, but a part of the key is empty; such a row is never added), `duplicates_in_table`,
`duplicates_in_source` (same key as an earlier candidate of the run), `added`, `written` and
`table_rows_without_key`. Always:

```
in_source = left_out + without_key + duplicates_in_table + duplicates_in_source + added
```

A second run with the same input on the result adds 0 rows: every candidate that had a key and
passed is then a duplicate in the table.

## How duplicates are found

The key compares values. A moment is compared to the minute in UTC. Text is compared after
Unicode normalisation (NFKC), with spaces collapsed and case ignored. A candidate and a table row
are the same row when every part of the key is equal.

## When a plan is refused

A plan is refused before anything is written, with a code and, where it helps, numbers.

- `rows_key_not_comparable`: a key column of kind moment holds, in an existing row, a value that
  is not a date-time cell (text that looks like a date, a number). Its count and the first row
  are given. Text dates are not parsed: the plan refuses rather than miss duplicates and add
  every row.
- `rows_no_room`: a cell the new rows would take is not empty or is part of a merged range, or
  the sheet has no rows left.
- Others: `rows_unknown_source`, `rows_unknown_field`, `rows_bad_condition`,
  `rows_column_outside_table`, `rows_column_used_twice`, `rows_key_empty`, `rows_key_not_mapped`,
  `rows_sheet_missing`.

## The routes

They are in `src/ai_data_enrichment/rows_api.py`, wired in by `create_app`, and are all under
`/api/`, so the access code covers them like the other routes. Errors are the usual
`{"error": {"code", "message"}}`.

**`GET /api/row-sources`** lists `ROW_SOURCES`: `{"sources": [{source_id, label, connect,
file_suffixes, fields: [{field_id, kind, label}], default_key, listing, available}]}`.
`available` is false for a `local_only` source unless `ENRICHMENT_ALLOW_MAIL=1` is set on a
local installation (the one switch that exists; a hosted app never has it).

**`POST /api/row-sources/{source_id}/candidates?filename=…`** takes the raw file (or a `.zip`) as
the body, exactly like `POST /api/mail-datasets`: same suffix check, same size bounds, read in
memory. Answer 201 `{candidates_id, source_id, count, skipped}`. 404 `rows_unknown_source`; 403
`mail_not_allowed` for a local-only source that is not available; `invalid_mail_file_type` for
a wrong suffix. **`DELETE /api/row-sources/candidates/{candidates_id}`** answers 204, also when
the set does not exist.

Candidates live in memory only, in a small store of the application: at most four sets, the
oldest is dropped when a fifth comes, and all are gone on restart. A set holds the date, the
sender and the subject of each mail, not its text, and is never written to disk, logged or
counted.

**`POST /api/datasets/{dataset_id}/rows/preview`** takes JSON (unknown fields are refused):

```
{"candidates_id": "…", "sheet_name": "Register", "range_ref": "A1:D40",
 "mapping": {"received": "A", "sender": "B"} | null,   // field id -> column letter
 "key": ["received", "sender"] | null,
 "on_duplicate": "skip" | "add_marked",                // default skip
 "conditions": [{"field_id": "received", "op": "within_last_days", "value": 30}]}
```

A `null` mapping is proposed from the header row of the selection, a `null` key is the default key
of the source. Condition values: `within_last_days` a whole number, `on_or_after` and
`on_or_before` a `YYYY-MM-DD` string, `contains` a string. The clock is the server's, in UTC. The
answer is 200:

```
{"plan": {source_id, sheet_name, range_ref, mapping, key, on_duplicate, conditions},
 "proposed": {"mapping": bool, "key": bool},
 "count": {…the fields of RowsCount…, "holds": bool} | null,
 "refused": {"code", "count", "row"} | null}
```

A plan the core refuses is a normal answer of the preview: `count` is null and `refused` says
why, with the numbers the error carries (for `rows_key_not_comparable` the number of cells and the
first row) or null. An empty key is refused the same way (`rows_key_empty`). Only a request that
cannot be read is an HTTP error: 404 `dataset_not_found` or `rows_candidates_not_found`, 422
`invalid_selection` (a bad sheet or range) or `worksheet_limit`.

**`POST /api/datasets/{dataset_id}/rows`** takes the same body, but `mapping` and `key` are
required: the run is exactly what the person saw and nothing is proposed. It appends to a copy
and registers it as a **new** dataset; the source dataset stays. Answer 201: what an upload
gives (`dataset_id` of the new dataset and the worksheet metadata) and `"rows": {"count": {…,
"holds"}, "first_row", "last_row", "sheet_name", "range_ref"}`, where `range_ref` is the table
after the append, header included. A refused plan is an **error** of the run: 422 with the same
code as the preview's `refused.code` (`rows_unknown_source`, `rows_unknown_field`,
`rows_bad_condition`, `rows_column_outside_table`, `rows_column_used_twice`, `rows_key_empty`,
`rows_key_not_mapped`, `rows_key_not_comparable`, `rows_no_room`, `rows_sheet_missing`,
`rows_too_many`), and no dataset is made. The plan is checked before room is made for the copy,
so a refused run drops no other dataset either. Making room never drops the dataset that is
appended to; when nothing else can be dropped the answer is 409 `workspace_full`. A source
that is a table of e-mails gives a table of e-mails.

The table has to stay within the limits of every table (5,001 rows counted from the top of the
sheet, 100 columns, 100,000 cells in the range): the core takes `max_last_row` and refuses with
`rows_too_many` when the last row it would write lies beyond it; the routes pass the last row
that still passes those limits. A finished run is counted as one usage event, `rows_append`,
with numbers only (`in_source`, `added`, `written`, `duplicates`).

## In the application

The interface offers this task only where the server lists a source as `available` (a local
installation with `ENRICHMENT_ALLOW_MAIL=1`). Elsewhere, the hosted demo included, the first step
has no new heading and no new control, and the page asks `GET /api/row-sources` once and nothing
else. The code is in `frontend/src/rowsModel.ts` (the task and the numbers as plain functions),
`useRows.ts` (the state, kept inside the workflow) and `components/RowsTask.tsx`, `RowsPlan.tsx`
and `RowsResults.tsx`.

**Dataset.** The task has two optional parts, not two modes. *Rows* has "Fill the rows that are in
the table" (on by default; the number of rows of a run belongs under it) and "Add new rows from a
source" (off). *Columns* shows what it always showed while the first is on, and one line ("No
column is filled in this task.") otherwise. With both on the plan cannot be built yet and the page
says to add the rows first and then continue with the result. With only the second on the task
text is optional and is not read. The source block holds the source (a select filled from the
route), the files, the fields the source gives, and the conditions of "take only"
(a field, an operator of its kind and a value of the right type). The files are packed in the
browser exactly as the e-mail entrance packs them (neutral names, no file name is sent) and read by
the server into candidates; choosing again replaces the set and forgets the old one with the
DELETE route. A new workbook, or removing it, resets the task and forgets the candidates.

**Plan.** "Build Plan" asks the preview route with no mapping and no key, so the server proposes
both, and opens the plan. The first card shows the source, the conditions in words, the mapping
(a select of the table's columns for each field, or "not written"; a mark "proposed" where the
server chose), the duplicate key (a checkbox per mapped field) and what to do with a duplicate.
Every change asks the preview again with the explicit mapping and key and shows the new counts; an
answer that a later change has overtaken is ignored. The counts are one sentence (of N in the
source, X already in the table, Y will be added) and, only when not zero, the rows left out by the
conditions, without a key and twice in the source. A refused plan shows an alert in the card with
the text of its code and the numbers it carries, and the run is off; so it is when nothing would
be written. When the table has rows, something is left after the conditions and not one row
matches, a notice (not an error) says to check how the dates are written. The second card says no
column is filled; the third says how the run will be checked. "Approve and run" sends exactly the
plan shown.

**Results.** Three tiles (in the source, rows added, duplicates skipped, or "added and marked" with
that setting), and more only when their number is not zero. A banner "The check holds" writes the
sum out from the parts shown (for example "7 added + 3 duplicates = 10 in the source"); when the
server says it does not hold, an error banner stands there instead. The banner also says how many
rows the table had and that none has changed: the interface compares, cell by cell, the rows it
read from the table before the run with the same rows of the new table (the values as the preview
route shows them, in the table's columns); a difference turns the banner into an error, and the
line is left out when the two cannot be compared. Right after the run the interface asks the preview route about the **new** table with the same plan and says what a second
run on the same files would add (0 is the good answer; with "add it and mark it" duplicates are
added again by design). The last rows of the new table are read with the ordinary preview route and
the written ones are marked. "Download workbook" saves the new table under the name the client
knows plus `-rows-added`; "Continue with this table" makes it the table of the first step with a
fresh task. Execution and Review are not steps of this task and are marked unavailable.

**The download route.** `GET /api/datasets/{dataset_id}/workbook` serves the stored workbook as an
`.xlsx` attachment under the fixed name `workbook.xlsx` (the client names the file it saves) and is
counted as the usage event `download`, with no details. 404 `dataset_not_found`. A table built from
e-mails is refused with 403 `mail_dataset_no_download`: its text column holds mail text, and such a
table leaves the application only through an export that empties it. The access code covers the
route like every route under `/api/`.

**What stays on this computer.** The files are read by the application on the machine it runs on;
no model is asked and nothing is sent to a service. The candidates (date, sender and subject of each
mail, never its text) live in memory only. The workbook that is downloaded holds only what the
person's own table held and the new rows' Received, From and Subject, as the application writes
them for a table of mails.

## What does not exist yet

- No saved recipe: a plan is not stored, and the screens have no "Save recipe".
- Rows and columns cannot be combined in one run: the person adds the rows, then continues with
  the result and fills columns there.
- No list of the skipped duplicates; only their number is shown.
- One source only, e-mail files; "found by a description" is a disabled choice.
- The task text is not read for rows: the source and the conditions are set in the panel.
- The mail text is not kept in the new rows.
- Ranges of conditional formatting and data validation and formulas in neighbouring columns are
  not extended to the new rows. Only an Excel table or an auto filter that spans the table is.
- A register whose dates were typed by hand in local time will not match dates written in UTC;
  such a row is not recognised as a duplicate. The same holds for a register that keeps the day
  without the time: its cells are date-time cells, so the plan is not refused, and no mail
  matches them. The counts hold in both cases; what shows the fault is `duplicates_in_table`
  being 0 where rows were expected to match.
- A totals row at the end of the table is taken for a data row: the new rows are written below
  it.
