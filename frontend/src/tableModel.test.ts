import { expect, it } from "vitest";
import type { Preview } from "./api";
import {
    filterRows,
    nextSort,
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
