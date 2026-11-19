# Rows from a source

The application fills columns of the rows that are in a table. This note describes the core of
the second kind of task: adding new rows to a table from a source. The core is in
`src/ai_data_enrichment/rows/`. It uses no model and no network. Four routes make it reachable
(see "The routes"); there is no interface yet.

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

## What does not exist yet

- No interface: the routes are not used by the application's screens.
- No saved recipe: a plan is not stored.
- One source only, e-mail files.
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
