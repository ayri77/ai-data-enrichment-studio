import { expect, it } from "vitest";
import type { Preview } from "./api";
import {
    defaultRunCount,
    filterRows,
    nextSort,
    occupiedRows,
    runRows,
    sortRows,
    tableRows,
    type TableRow,
} from "./tableModel";

const rows: TableRow[] = [
    { row: 2, cells: ["Kyiv", 2952301, "row 10"] },
    { row: 3, cells: ["lviv", null, "row 2"] },
    { row: 4, cells: ["Odesa", 1010537, null] },
    { row: 5, cells: ["Äänekoski", "unknown", "Row 2"] },
];
const order = (sorted: TableRow[]) => sorted.map((row) => row.row);

it("takes every row the server sent, with its worksheet row number", () => {
    const preview = {
        profile: {
            columns: [{ name: "city" }, { name: "population" }],
            sample_records: [{ city: "Kyiv", population: 1 }],
        },
        rows: {
            row_numbers: [4, 6],
            values: [
                ["Kyiv", 1],
                ["Lviv", null],
            ],
        },
    } as unknown as Preview;
    expect(tableRows(preview)).toEqual([
        { row: 4, cells: ["Kyiv", 1] },
        { row: 6, cells: ["Lviv", null] },
    ]);
    // Without rows, the sample is what there is to show.
    expect(tableRows({ ...preview, rows: undefined })).toEqual([
        { row: 1, cells: ["Kyiv", 1] },
    ]);
});

it("filters by text in any cell, ignoring case", () => {
    expect(order(filterRows(rows, "  ODE "))).toEqual([4]);
    expect(order(filterRows(rows, "row 2"))).toEqual([3, 5]);
    expect(order(filterRows(rows, "2952"))).toEqual([2]);
    expect(filterRows(rows, "   ")).toBe(rows);
    expect(filterRows(rows, "nothing")).toEqual([]);
});

it("sorts numbers as numbers, text as read, and keeps empty cells last", () => {
    // Numbers first and by value, then text; the empty cell stays last.
    expect(order(sortRows(rows, { column: 1, descending: false }))).toEqual([
        4, 2, 5, 3,
    ]);
    expect(order(sortRows(rows, { column: 1, descending: true }))).toEqual([
        5, 2, 4, 3,
    ]);
    // "row 2" before "row 10"; equal text keeps the worksheet order.
    expect(order(sortRows(rows, { column: 2, descending: false }))).toEqual([
        3, 5, 2, 4,
    ]);
    expect(order(sortRows(rows, { column: 0, descending: false }))).toEqual([
        5, 2, 3, 4,
    ]);
    expect(sortRows(rows, null)).toBe(rows);
    // The input is not reordered in place.
    expect(order(rows)).toEqual([2, 3, 4, 5]);
});

it("cycles a column through ascending, descending and the original order", () => {
    const up = nextSort(null, 1);
    expect(up).toEqual({ column: 1, descending: false });
    const down = nextSort(up, 1);
    expect(down).toEqual({ column: 1, descending: true });
    expect(nextSort(down, 1)).toBeNull();
    expect(nextSort(down, 0)).toEqual({ column: 0, descending: false });
});

it("finds the worksheet rows in which a column already holds a value", () => {
    const preview = {
        profile: {
            columns: [{ name: "city" }, { name: "distance" }],
            sample_records: [],
        },
        rows: {
            row_numbers: [5, 6, 7, 9],
            values: [
                ["Hamburg", null],
                ["Berlin", null],
                ["Paris", 99999],
                ["Rome", "=1+1"],
            ],
        },
    } as unknown as Preview;
    expect(occupiedRows(preview, "distance")).toEqual([7, 9]);
    expect(occupiedRows(preview, "city")).toEqual([5, 6, 7, 9]);
    // A column that is not in the table, or no rows from the server: nothing to say.
    expect(occupiedRows(preview, "height")).toEqual([]);
    expect(occupiedRows({ ...preview, rows: undefined }, "distance")).toEqual(
        [],
    );
    expect(occupiedRows(null, "distance")).toEqual([]);
});

it("takes the first rows of the table for a run, within what a run accepts", () => {
    const preview = {
        range_ref: "C4:G1004",
        profile: { row_count: 1000, columns: [], sample_records: [] },
        rows: {
            row_numbers: Array.from({ length: 1000 }, (_, index) => index + 5),
            values: [],
        },
    } as unknown as Preview;
    // A long table does not stop a run: its first rows are taken.
    expect(defaultRunCount(preview, 20)).toBe(20);
    expect(runRows(preview, 20, 20)).toEqual({ range: "C4:G24", last: 24 });
    expect(runRows(preview, 3, 20)).toEqual({ range: "C4:G7", last: 7 });
    // More than a run accepts, none, or not a whole number: nothing to send.
    expect(runRows(preview, 21, 20)).toBeNull();
    expect(runRows(preview, 0, 20)).toBeNull();
    expect(runRows(preview, 2.5, 20)).toBeNull();
    expect(runRows(null, 5, 20)).toBeNull();
    // A short table is taken whole.
    const short = {
        ...preview,
        range_ref: "A1:B4",
        rows: { row_numbers: [2, 3, 4], values: [] },
    } as unknown as Preview;
    expect(defaultRunCount(short, 20)).toBe(3);
    expect(runRows(short, 3, 20)).toEqual({ range: "A1:B4", last: 4 });
    expect(runRows(short, 4, 20)).toBeNull();
    // Empty rows in between count towards the span a run accepts.
    const gaps = {
        ...short,
        range_ref: "A1:B60",
        rows: { row_numbers: [2, 3, 59], values: [] },
    } as unknown as Preview;
    expect(runRows(gaps, 2, 20)).toEqual({ range: "A1:B3", last: 3 });
    expect(runRows(gaps, 3, 20)).toBeNull();
    // Without the rows themselves the table is taken to have no gaps.
    const bare = { ...short, rows: undefined } as unknown as Preview;
    expect(
        defaultRunCount({ ...bare, profile: { row_count: 3 } } as Preview, 20),
    ).toBe(3);
    expect(
        runRows({ ...bare, profile: { row_count: 3 } } as Preview, 2, 20),
    ).toEqual({
        range: "A1:B3",
        last: 3,
    });
});
