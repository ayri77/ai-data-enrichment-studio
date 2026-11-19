# Rows from a source

The application fills columns of the rows that are in a table. This note describes the core of
the second kind of task: adding new rows to a table from a source. The core is in
`src/ai_data_enrichment/rows/`. It uses no model and no network, and nothing in the
application calls it yet.

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

## What does not exist yet

- No route and no interface; the core cannot be reached from the application.
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
