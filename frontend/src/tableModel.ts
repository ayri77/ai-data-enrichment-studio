import type { Preview } from "./api";

export type Cell = string | number | boolean | null;
/** One data row of the selected table with its worksheet row number. */
export interface TableRow {
    row: number;
    cells: Cell[];
}
export type Sort = { column: number; descending: boolean } | null;
/** How many rows can be shown at once; the person chooses, or asks for all. */
export const SHOWN_ROWS = [20, 50, 100];
export const ALL_ROWS = Number.MAX_SAFE_INTEGER;

/**
 * Every row the server sent. A response without rows (an older server, a test
 * fixture) falls back to the profile's sample, numbered in order.
 */
export function tableRows(preview: Preview): TableRow[] {
    const names = preview.profile.columns.map((column) => column.name);
    if (preview.rows)
        return preview.rows.values.map((cells, index) => ({
            row: preview.rows!.row_numbers[index],
            cells,
        }));
    return preview.profile.sample_records.map((record, index) => ({
        row: index + 1,
        cells: names.map((name) => record[name] ?? null),
    }));
}

/**
 * The rows one run takes from the table: its first ``count`` data rows. The
 * answer is the range to send, header included, and the worksheet row of the
 * last of them. Null when the count is not a whole number of rows the table
 * has, or when those rows span more of the worksheet than a run accepts
 * (empty rows in between count towards the span).
 */
export function runRows(
    preview: Preview | null,
    count: number,
    maxRows: number,
) {
    const range = preview?.range_ref.match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/);
    if (!preview || !range || !Number.isInteger(count) || count < 1)
        return null;
    const header = Number(range[2]);
    const numbers = preview.rows?.row_numbers;
    const total = numbers?.length ?? preview.profile.row_count;
    if (count > total || count > maxRows) return null;
    // Without the rows themselves, the table is taken to have no gaps.
    const last = numbers ? numbers[count - 1] : header + count;
    if (last - header > maxRows) return null;
    return { range: `${range[1]}${header}:${range[3]}${last}`, last };
}
/** How many rows a run takes when nothing else was asked for. */
export const defaultRunCount = (preview: Preview | null, maxRows: number) =>
    Math.min(
        preview?.rows?.row_numbers.length ?? preview?.profile.row_count ?? 0,
        maxRows,
    );

/**
 * Worksheet rows in which a column already holds a value. Empty when the
 * column is not in the table or the server sent no rows.
 */
export function occupiedRows(preview: Preview | null, column: string) {
    const index =
        preview?.profile.columns.findIndex((c) => c.name === column) ?? -1;
    if (!preview?.rows || index < 0) return [];
    return preview.rows.values.flatMap((cells, row) =>
        cells[index] === null || cells[index] === undefined
            ? []
            : [preview.rows!.row_numbers[row]],
    );
}

/** Rows in which some cell contains the text, ignoring case. */
export function filterRows(rows: TableRow[], query: string, locale = "en") {
    const text = query.trim().toLocaleLowerCase(locale);
    if (!text) return rows;
    return rows.filter((row) =>
        row.cells.some(
            (cell) =>
                cell !== null &&
                String(cell).toLocaleLowerCase(locale).includes(text),
        ),
    );
}

/**
 * Sort by one column. Numbers come before text and compare as numbers, text
 * compares as a person reads it ("row 2" before "row 10"), and empty cells
 * stay at the end in both directions. Rows that compare equal keep their
 * worksheet order.
 */
export function sortRows(rows: TableRow[], sort: Sort, locale = "en") {
    if (!sort) return rows;
    const collator = new Intl.Collator(locale, {
        numeric: true,
        sensitivity: "base",
    });
    const rank = (cell: Cell) =>
        cell === null ? 2 : typeof cell === "number" ? 0 : 1;
    const compare = (a: Cell, b: Cell) => {
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
        if (typeof a === "number" && typeof b === "number") return a - b;
        return collator.compare(String(a), String(b));
    };
    return [...rows].sort((left, right) => {
        const a = left.cells[sort.column] ?? null;
        const b = right.cells[sort.column] ?? null;
        // Empty cells are not part of the order being asked for.
        if (a === null || b === null)
            return a === b ? left.row - right.row : a === null ? 1 : -1;
        const order = compare(a, b);
        return order === 0
            ? left.row - right.row
            : sort.descending
              ? -order
              : order;
    });
}

/** Ascending, then descending, then the worksheet order again. */
export function nextSort(sort: Sort, column: number): Sort {
    if (sort?.column !== column) return { column, descending: false };
    return sort.descending ? null : { column, descending: true };
}
