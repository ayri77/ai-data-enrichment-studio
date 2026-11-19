import { describe, expect, it } from "vitest";
import {
    columnChoices,
    conditionOf,
    conditionsOf,
    editPlan,
    endOfTable,
    firstRequest,
    initialTask,
    newCondition,
    noMatchNotice,
    oldRowsKept,
    proposedFields,
    requestOf,
    rowsFilename,
    rowsNeeds,
    runBlock,
    secondRun,
    shownPlan,
    sumOf,
    taskKind,
    tiles,
    withField,
    withOperator,
    type ConditionDraft,
} from "./rowsModel";
import {
    answer,
    candidates,
    count,
    grownPreview,
    mailSource,
    registerPreview,
} from "./rowsFixtures";

const draft = (over: Partial<ConditionDraft> = {}): ConditionDraft => ({
    id: 1,
    field_id: "received",
    op: "within_last_days",
    value: "30",
    ...over,
});

describe("the task", () => {
    it("has two optional parts and names what they make together", () => {
        expect(taskKind({ fill: true, add: false })).toBe("columns");
        expect(taskKind({ fill: false, add: true })).toBe("rows");
        expect(taskKind({ fill: true, add: true })).toBe("both");
        expect(taskKind({ fill: false, add: false })).toBe("none");
        expect(initialTask()).toMatchObject({
            fill: true,
            add: false,
            candidates: null,
            conditions: [],
        });
    });
    it("asks for the files, complete conditions and a table, and for a part when none is on", () => {
        const task = { ...initialTask(), fill: false, add: true };
        const needs = (over = {}) =>
            rowsNeeds({
                task: { ...task, ...over },
                source: mailSource,
                preview: registerPreview,
            });
        expect(needs()).toEqual(["files"]);
        expect(needs({ candidates })).toEqual([]);
        expect(
            needs({ candidates, conditions: [draft({ value: "" })] }),
        ).toEqual(["conditions"]);
        expect(rowsNeeds({ task, source: mailSource, preview: null })).toEqual([
            "preview",
            "files",
        ]);
        expect(needs({ fill: false, add: false })).toEqual(["part"]);
    });
});

describe("conditions", () => {
    it("start on the first field with a usable value", () => {
        expect(newCondition(mailSource, 4)).toEqual({
            id: 4,
            field_id: "received",
            op: "within_last_days",
            value: "30",
        });
    });
    it("take the operators of the field's kind and keep a value only where its kind is the same", () => {
        const text = withField(draft(), mailSource, "subject");
        expect(text).toMatchObject({
            field_id: "subject",
            op: "contains",
            value: "",
        });
        const date = withOperator(draft(), "on_or_after");
        expect(date).toMatchObject({ op: "on_or_after", value: "" });
        expect(
            withOperator(
                draft({ op: "on_or_after", value: "2026-11-01" }),
                "on_or_before",
            ),
        ).toMatchObject({ op: "on_or_before", value: "2026-11-01" });
    });
    it("become the server's values: a whole number of days, a real date, trimmed text", () => {
        expect(conditionOf(draft())).toEqual({
            field_id: "received",
            op: "within_last_days",
            value: 30,
        });
        expect(conditionOf(draft({ value: "0" }))).toBeNull();
        expect(conditionOf(draft({ value: "2.5" }))).toBeNull();
        expect(conditionOf(draft({ value: "99999" }))).toBeNull();
        expect(
            conditionOf(draft({ op: "on_or_after", value: "2026-11-01" })),
        ).toEqual({
            field_id: "received",
            op: "on_or_after",
            value: "2026-11-01",
        });
        expect(
            conditionOf(draft({ op: "on_or_after", value: "2026-02-30" })),
        ).toBeNull();
        expect(
            conditionOf(
                draft({
                    field_id: "subject",
                    op: "contains",
                    value: "  Streamly ",
                }),
            ),
        ).toEqual({ field_id: "subject", op: "contains", value: "Streamly" });
        expect(
            conditionOf(
                draft({ field_id: "subject", op: "contains", value: "  " }),
            ),
        ).toBeNull();
        expect(conditionsOf([draft(), draft({ value: "" })])).toBeNull();
        expect(conditionsOf([])).toEqual([]);
    });
});

describe("the requests", () => {
    const conditions = [
        { field_id: "received", op: "within_last_days", value: 30 },
    ];
    it("ask for the proposals with the first request", () => {
        expect(
            firstRequest({
                candidatesId: "set-1",
                preview: registerPreview,
                conditions,
            }),
        ).toEqual({
            candidates_id: "set-1",
            sheet_name: "Register",
            range_ref: "A1:D4",
            mapping: null,
            key: null,
            on_duplicate: "skip",
            conditions,
        });
    });
    it("send exactly the plan shown afterwards, nothing proposed", () => {
        const shown = shownPlan("set-1", answer());
        expect(requestOf(shown)).toEqual({
            candidates_id: "set-1",
            sheet_name: "Register",
            range_ref: "A1:D4",
            mapping: { received: "A", sender: "B", subject: "C" },
            key: ["received", "sender"],
            on_duplicate: "skip",
            conditions: [],
        });
        expect(proposedFields(answer())).toEqual([
            "received",
            "sender",
            "subject",
        ]);
        expect(
            proposedFields(
                answer({ proposed: { mapping: false, key: false } }),
            ),
        ).toEqual([]);
    });
    it("make one change at a time, and a field without a column leaves the key", () => {
        const shown = shownPlan("set-1", answer());
        expect(
            editPlan(shown, { type: "column", field: "subject", column: "" })
                .mapping,
        ).toEqual({ received: "A", sender: "B" });
        const dropped = editPlan(shown, {
            type: "column",
            field: "sender",
            column: "",
        });
        expect(dropped.key).toEqual(["received"]);
        expect(
            editPlan(shown, { type: "column", field: "subject", column: "D" })
                .mapping.subject,
        ).toBe("D");
        expect(
            editPlan(shown, { type: "key", field: "subject", on: true }).key,
        ).toEqual(["received", "sender", "subject"]);
        expect(
            editPlan(shown, { type: "key", field: "sender", on: false }).key,
        ).toEqual(["received"]);
        expect(
            editPlan(shown, { type: "duplicate", value: "add_marked" })
                .on_duplicate,
        ).toBe("add_marked");
        // The plan it was made from is not changed.
        expect(shown.mapping.subject).toBe("C");
    });
    it("name the columns of the table with their letters", () => {
        expect(columnChoices(registerPreview)).toEqual([
            { letter: "A", name: "Received" },
            { letter: "B", name: "From" },
            { letter: "C", name: "Subject" },
            { letter: "D", name: "Amount" },
        ]);
        expect(columnChoices(null)).toEqual([]);
    });
});

describe("the numbers", () => {
    const ids = (c = count()) => tiles(c).map((tile) => tile.id);
    it("show three tiles for the simple case and the others only when they are not zero", () => {
        expect(ids()).toEqual(["in_source", "added", "duplicates"]);
        expect(ids(count({ left_out: 2, in_source: 12 }))).toEqual([
            "in_source",
            "added",
            "duplicates",
            "left_out",
        ]);
        expect(
            ids(
                count({
                    in_source: 14,
                    left_out: 1,
                    without_key: 2,
                    duplicates_in_source: 1,
                }),
            ),
        ).toEqual([
            "in_source",
            "added",
            "duplicates",
            "left_out",
            "without_key",
            "twice",
        ]);
        // Duplicates are shown even when there are none.
        expect(ids(count({ duplicates_in_table: 0, in_source: 7 }))).toContain(
            "duplicates",
        );
    });
    it("write the check out from the parts that are shown", () => {
        const simple = sumOf(count());
        expect(simple.parts.map((p) => [p.id, p.value])).toEqual([
            ["added", 7],
            ["duplicates", 3],
        ]);
        expect(simple).toMatchObject({ total: 10, holds: true });
        const more = sumOf(
            count({
                in_source: 12,
                left_out: 2,
                without_key: 0,
                duplicates_in_source: 0,
            }),
        );
        expect(more.parts.map((p) => p.id)).toEqual([
            "added",
            "duplicates",
            "left_out",
        ]);
        expect(more.holds).toBe(true);
    });
    it("do not hold when the server says so or the parts do not add up", () => {
        expect(sumOf(count({ holds: false })).holds).toBe(false);
        expect(sumOf(count({ added: 6 })).holds).toBe(false);
    });
    it("tell that nothing matched only when the table has rows and the source has something left", () => {
        const none = count({
            duplicates_in_table: 0,
            in_source: 10,
            added: 10,
            written: 10,
        });
        expect(noMatchNotice(none, 3)).toBe(true);
        expect(noMatchNotice(none, 0)).toBe(false);
        expect(noMatchNotice(count(), 3)).toBe(false);
        expect(noMatchNotice(null, 3)).toBe(false);
        // Everything was left out by the conditions: there is nothing to match.
        expect(
            noMatchNotice(
                count({
                    in_source: 5,
                    left_out: 5,
                    duplicates_in_table: 0,
                    added: 0,
                    written: 0,
                }),
                3,
            ),
        ).toBe(false);
    });
    it("block a run that is refused or would write nothing", () => {
        expect(runBlock(answer())).toBeNull();
        expect(
            runBlock(
                answer({
                    count: null,
                    refused: { code: "rows_no_room", count: null, row: null },
                }),
            ),
        ).toBe("refused");
        expect(
            runBlock(answer({ count: count({ added: 0, written: 0 }) })),
        ).toBe("nothing");
        // Duplicates added and marked are written, though none is new.
        expect(
            runBlock(answer({ count: count({ added: 0, written: 3 }) })),
        ).toBeNull();
    });
    it("say what a second run would do", () => {
        expect(secondRun(count({ added: 0, written: 0 }), "skip")).toEqual({
            kind: "none",
        });
        expect(secondRun(count({ added: 2, written: 2 }), "skip")).toEqual({
            kind: "some",
            rows: 2,
        });
        expect(
            secondRun(count({ added: 0, written: 10 }), "add_marked"),
        ).toEqual({
            kind: "marked",
            rows: 10,
        });
    });
});

describe("the rows the table had", () => {
    it("are the same when every cell is the same", () => {
        expect(oldRowsKept(registerPreview, grownPreview)).toEqual({
            rows: 3,
            same: true,
        });
    });
    it("are not when one cell differs, a row is gone or a column is", () => {
        const values = grownPreview.rows!.values.map((row) => [...row]);
        values[1][3] = 999;
        const changed = {
            ...grownPreview,
            rows: { ...grownPreview.rows!, values },
        };
        expect(oldRowsKept(registerPreview, changed)?.same).toBe(false);
        const moved = {
            ...grownPreview,
            rows: {
                row_numbers: grownPreview.rows!.row_numbers.map((n) => n + 1),
                values: grownPreview.rows!.values,
            },
        };
        expect(oldRowsKept(registerPreview, moved)?.same).toBe(false);
        const narrower = {
            ...grownPreview,
            rows: {
                ...grownPreview.rows!,
                values: grownPreview.rows!.values.map((row) => row.slice(0, 3)),
            },
        };
        expect(oldRowsKept(registerPreview, narrower)?.same).toBe(false);
    });
    it("are not judged when one of the two tables is not at hand", () => {
        expect(oldRowsKept(null, grownPreview)).toBeNull();
        expect(oldRowsKept(registerPreview, null)).toBeNull();
        expect(
            oldRowsKept({ ...registerPreview, rows: undefined }, grownPreview),
        ).toBeNull();
    });
});

describe("names and the end of the table", () => {
    it("name the new file after the known one, once", () => {
        expect(rowsFilename("register.xlsx")).toBe("register-rows-added.xlsx");
        expect(rowsFilename("register-rows-added.xlsx")).toBe(
            "register-rows-added.xlsx",
        );
        expect(rowsFilename("a/b.XLSX")).toBe("a-b-rows-added.xlsx");
        expect(rowsFilename(undefined)).toBe("dataset-rows-added.xlsx");
    });
    it("take the last rows and mark those the run wrote", () => {
        const end = endOfTable(grownPreview, { first: 5, last: 11 }, 8);
        expect(end.map((row) => row.row)).toEqual([4, 5, 6, 7, 8, 9, 10, 11]);
        expect(end.filter((row) => row.added)).toHaveLength(7);
        expect(end[0].added).toBe(false);
        expect(
            endOfTable(grownPreview, { first: null, last: null }).some(
                (r) => r.added,
            ),
        ).toBe(false);
        expect(endOfTable(null, { first: 5, last: 11 })).toEqual([]);
    });
});
