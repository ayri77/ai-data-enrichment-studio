import { describe, expect, it } from "vitest";
import type { PlanDraft, Preview } from "./api";
import {
    buildMixedRequest,
    columnGroups,
    couldOfferFallback,
    defaultMixedForm,
    fallbackOffer,
    formFromDraft,
    isFallbackRun,
    isMixedRun,
    MAX_CONTEXT,
    mixedTargets,
    partialColumns,
    searchedMapping,
    searchKind,
    unfilledWording,
} from "./fallbackModel";
import {
    mixedDraft,
    mixedJob,
    mixedReport,
    partialDraft,
} from "./fallbackFixtures";
import { columnLabels, describePlan } from "./planModel";
import {
    filterRows,
    lookupSummary,
    outputColumns,
    resultRows,
    searchedColumns,
    stepTrust,
} from "./resultsModel";
import type { OutputTarget } from "./workflowModel";

const column = (name: string, inferred_type: string, null_count = 0) => ({
    name,
    inferred_type,
    null_count,
    warnings: [],
});
const table = (columns: ReturnType<typeof column>[]): Preview => ({
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:H11",
    sample_limit: 5,
    profile: {
        row_count: 10,
        columns,
        sample_records: [],
        schema_warnings: [],
    },
});
// The mountains table of the tests: the two chosen columns are empty.
const preview = table([
    column("Mountain", "Text"),
    column("Country", "Text"),
    column("Rank", "Integer"),
    column("Born", "Date"),
    column("Notes", "Text", 10),
    column("Height", "Number", 10),
    column("First ascent", "Unknown", 10),
]);
const mapping: OutputTarget[] = [
    { mode: "existing", name: "Height", column: "E" },
    { mode: "existing", name: "First ascent", column: "F" },
];

describe("the defaults of the web search added to a plan", () => {
    it("searches exactly the columns the planner left unfilled, with the task as the question", () => {
        const form = defaultMixedForm(partialDraft, mapping, preview);
        expect(form.question).toBe(partialDraft.instruction);
        expect(Object.keys(form.settings)).toEqual(["First ascent"]);
        // An existing column that is empty is text; no unit.
        expect(form.settings["First ascent"]).toEqual({
            kind: "text",
            unit: "",
        });
        // The links go to the cells as notes.
        expect(form.sources).toBe("notes");
    });
    it("takes a number where the existing column holds numbers", () => {
        const form = defaultMixedForm(
            { ...partialDraft, unfilled_targets: ["Height"] },
            mapping,
            table([
                column("Mountain", "Text"),
                column("Height", "Number"),
                column("Rank", "Integer"),
            ]),
        );
        expect(form.settings.Height).toEqual({ kind: "number", unit: "" });
    });
    it("never lets a column the plan writes identify a row, whether the planner fills it or the search does", () => {
        const form = defaultMixedForm(partialDraft, mapping, preview);
        // Height is filled by the planner's steps, First ascent is searched.
        expect(form.context).not.toContain("Height");
        expect(form.context).not.toContain("First ascent");
        // A date column cannot identify a row, an empty column has nothing to say,
        // and the rest keeps the table's order.
        expect(form.context).toEqual(["Mountain", "Country", "Rank"]);
        // Even when the planner's column holds text and values, it stays out.
        const written = table([
            column("Mountain", "Text"),
            column("Height", "Text"),
            column("First ascent", "Text"),
            column("Country", "Text"),
        ]);
        expect(
            defaultMixedForm(partialDraft, mapping, written).context,
        ).toEqual(["Mountain", "Country"]);
    });
    it("takes at most as many identifying columns as one request does", () => {
        const names = Array.from({ length: 40 }, (_, i) => `Col ${i + 1}`);
        const wide = table(names.map((name) => column(name, "Text")));
        const form = defaultMixedForm(partialDraft, mapping, wide);
        expect(form.context).toHaveLength(MAX_CONTEXT);
        expect(form.context[0]).toBe("Col 1");
    });
    it("reads the columns from a mixed draft as well, when that is what it has", () => {
        expect(mixedTargets(partialDraft)).toEqual(["First ascent"]);
        expect(mixedTargets(mixedDraft)).toEqual(["First ascent"]);
        expect(defaultMixedForm(mixedDraft, mapping, preview).context).toEqual([
            "Mountain",
            "Country",
            "Rank",
        ]);
    });
});

describe("the request for a mixed plan", () => {
    const form = defaultMixedForm(partialDraft, mapping, preview);
    it("names the planner draft, the question, the columns and the kinds, and nothing of the table", () => {
        expect(
            buildMixedRequest({
                planId: "plan-partial",
                targets: ["First ascent"],
                preview,
                mapping,
                form,
            }),
        ).toEqual({
            plan_id: "plan-partial",
            question: partialDraft.instruction,
            context_columns: ["Mountain", "Country", "Rank"],
            targets: [{ column: "First ascent", kind: "text" }],
            sources_in_workbook: "notes",
        });
    });
    it("keeps the table's order, trims the question and sends a unit only for a number", () => {
        const body = buildMixedRequest({
            planId: "p",
            targets: ["Height", "First ascent"],
            preview,
            mapping,
            form: {
                question: "  How old?  ",
                context: ["Rank", "Mountain", "Height"],
                settings: {
                    Height: { kind: "number", unit: " million years " },
                    "First ascent": { kind: "text", unit: "ignored" },
                },
                sources: "none",
            },
        });
        expect(body.question).toBe("How old?");
        // The searched column is never an identifying one.
        expect(body.context_columns).toEqual(["Mountain", "Rank"]);
        expect(body.targets).toEqual([
            { column: "Height", kind: "number", unit: "million years" },
            { column: "First ascent", kind: "text" },
        ]);
        expect(body.sources_in_workbook).toBe("none");
        for (const field of [
            "dataset_id",
            "sheet_name",
            "range_ref",
            "output_columns",
            "existing_outputs",
        ])
            expect(body).not.toHaveProperty(field);
    });
});

describe("reading a mixed draft back into the form", () => {
    it("returns the choices the plan was built from", () => {
        expect(formFromDraft(mixedDraft)).toEqual({
            question: partialDraft.instruction,
            context: ["Mountain", "Country", "Rank"],
            settings: { "First ascent": { kind: "text", unit: "" } },
            sources: "notes",
        });
    });
    it("keeps a unit and a kind the person chose", () => {
        const plan = structuredClone(mixedDraft.plan!);
        const lookup = plan.steps.find((s) => s.id === "lookup")!;
        const task = JSON.parse(
            (lookup.inputs!.task as { value: string }).value,
        );
        task.columns = [
            {
                slot: "value_1",
                header: "First ascent",
                kind: "number",
                unit: "year",
            },
        ];
        (lookup.inputs!.task as { value: string }).value = JSON.stringify(task);
        expect(formFromDraft({ ...mixedDraft, plan })!.settings).toEqual({
            "First ascent": { kind: "number", unit: "year" },
        });
    });
    it("says nothing for a planner draft, which has no search part", () => {
        expect(formFromDraft(partialDraft)).toBeNull();
    });
    it("restricts the page's choice of columns to the searched ones", () => {
        expect(
            searchedMapping(
                ["First ascent"],
                mapping,
                partialDraft.output_columns,
            ),
        ).toEqual([{ mode: "existing", name: "First ascent", column: "F" }]);
        // An entry the page no longer has comes from the draft.
        expect(
            searchedMapping(["First ascent"], [], partialDraft.output_columns),
        ).toEqual([{ mode: "new", name: "First ascent", column: "F" }]);
    });
});

describe("which wording a plan with columns without a source gets", () => {
    it("offers the search only for a plan that can be run, on a server that has it", () => {
        expect(partialColumns(partialDraft)).toEqual(["First ascent"]);
        expect(unfilledWording(partialDraft, true)).toBe("offer");
        // The server cannot search: the notice only says what stays as it is.
        expect(unfilledWording(partialDraft, false)).toBe("plain");
        // A plan that failed the check cannot be built on.
        const invalid = {
            ...partialDraft,
            validation: { valid: false, errors: [] },
        };
        expect(partialColumns(invalid)).toEqual([]);
        expect(unfilledWording(invalid, true)).toBe("plain");
    });
    it("says nothing for a plan that fills every column, for a mixed plan and for no plan", () => {
        expect(
            unfilledWording({ ...partialDraft, unfilled_targets: [] }, true),
        ).toBeNull();
        expect(
            unfilledWording(
                { ...partialDraft, unfilled_targets: undefined },
                true,
            ),
        ).toBeNull();
        expect(unfilledWording(mixedDraft, true)).toBeNull();
        expect(unfilledWording(null, true)).toBeNull();
        expect(partialColumns(mixedDraft)).toEqual([]);
        expect(partialColumns(null)).toEqual([]);
    });
    it("is not the offer of a search for the whole task, but reads the limits again", () => {
        expect(fallbackOffer(partialDraft, true)).toBeNull();
        expect(couldOfferFallback(partialDraft)).toBe(true);
        expect(couldOfferFallback(mixedDraft)).toBe(false);
    });
    it("tells the three kinds of plan apart", () => {
        expect(searchKind(partialDraft)).toBe("planner");
        expect(searchKind({ kind: "fallback" })).toBe("fallback");
        expect(searchKind(mixedDraft)).toBe("mixed");
        expect(searchKind(null)).toBe("planner");
        expect(isMixedRun(mixedJob, null)).toBe(true);
        expect(isMixedRun(null, mixedDraft)).toBe(true);
        expect(isMixedRun(null, partialDraft)).toBe(false);
        // A mixed run is not a whole-task search: its review has real items.
        expect(isFallbackRun(mixedJob, mixedDraft)).toBe(false);
    });
});

describe("the two groups of columns of a mixed plan", () => {
    it("names the columns the sources fill and those the web search fills, with their letters", () => {
        const groups = columnGroups(mixedDraft);
        expect(groups.filled).toEqual([{ name: "Height", letter: "E" }]);
        expect(groups.searched).toEqual([
            { name: "First ascent", letter: "F" },
        ]);
        expect(columnLabels(groups.searched)).toBe("“First ascent” (F)");
        expect(columnLabels([...groups.filled, ...groups.searched])).toBe(
            "“Height” (E), “First ascent” (F)",
        );
    });
    it("has nothing searched in a planner draft", () => {
        expect(columnGroups(partialDraft)).toEqual({
            filled: [{ name: "Height", letter: "E" }],
            searched: [],
        });
    });
});

describe("a mixed plan described to a person", () => {
    it("lists the planner's steps and the search as a step of its own, with no slot name", () => {
        const lines = describePlan(mixedDraft);
        expect(lines.map((line) => line.action)).toEqual([
            "mountain",
            "elevation",
            "webLookup",
        ]);
        const lookup = lines[2];
        // The value is written to its column; "value_1" is no word for a person.
        expect(lookup.writes).toEqual([
            { name: "First ascent", letter: "F", unit: "" },
        ]);
    });
});

describe("the results of a mixed run", () => {
    const rows = resultRows(mixedReport, mixedDraft.plan);
    it("knows which columns the search fills, and what the audit says for each step", () => {
        expect(searchedColumns(mixedDraft.plan)).toEqual(["First ascent"]);
        expect(searchedColumns(partialDraft.plan)).toEqual([]);
        const trust = stepTrust(mixedReport);
        expect(trust.get("elevation")).toEqual({
            source: "wikidata",
            trust: "record",
        });
        expect(trust.get("lookup")).toEqual({
            source: "openai_web_search",
            trust: "ai_found",
        });
        const columns = outputColumns(mixedDraft.plan);
        expect(columns.map((c) => [c.key, c.stepId])).toEqual([
            ["Height", "elevation"],
            ["First ascent", "lookup"],
        ]);
    });
    it("marks only the searched values and counts them", () => {
        expect(rows.map((row) => Object.keys(row.found))).toEqual([
            ["First ascent"],
            ["First ascent"],
            [],
        ]);
        expect(rows[0].outputs).toEqual({
            Height: 8849,
            "First ascent": "1953",
        });
        expect(rows[1].outputs).toEqual({
            Height: undefined,
            "First ascent": "1954",
        });
        expect(lookupSummary(rows)).toEqual({
            asked: 3,
            found: 2,
            concluded: 1,
        });
        // The row with one concluded value is what the filter finds.
        expect(filterRows(rows, "concluded").map((r) => r.row)).toEqual([2]);
    });
});
