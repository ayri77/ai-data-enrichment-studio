import { describe, expect, it } from "vitest";
import i18n from "./i18n";
import type { PlanDraft, Preview } from "./api";
import { capabilityTitle, capabilityProvenance } from "./capabilities";
import {
    buildFallbackRequest,
    contextBlocker,
    defaultKind,
    formProblems,
    isFallbackRun,
    couldOfferFallback,
    fallbackOffer,
    offersFallback,
    readLookupTask,
    targetNames,
    type FallbackForm,
} from "./fallbackModel";
import { fallbackDraft, lookupJob, lookupPlan } from "./fallbackFixtures";
import {
    causeKind,
    filterRows,
    foundSlots,
    lookupSummary,
    resultRows,
} from "./resultsModel";
import { runOutcome } from "./runModel";
import type { OutputTarget } from "./workflowModel";
import { lookupReport } from "./fallbackFixtures";

const column = (name: string, inferred_type: string) => ({
    name,
    inferred_type,
    null_count: 0,
    warnings: [],
});
const preview: Preview = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:G11",
    sample_limit: 5,
    profile: {
        row_count: 10,
        columns: [
            column("Mountain", "Text"),
            column("Country", "Text"),
            column("Rank", "Integer"),
            column("Born", "Date"),
            column("Height", "Number"),
            column("First ascent", "Unknown"),
            column("Climbed in winter", "Text"),
        ],
        sample_records: [],
        schema_warnings: [],
    },
};
const mapping: OutputTarget[] = [
    { mode: "existing", name: "Height", column: "E" },
    { mode: "existing", name: "First ascent", column: "F" },
    { mode: "new", name: " Notes ", column: "H" },
];
const form = (change: Partial<FallbackForm> = {}): FallbackForm => ({
    question: "  Find the first ascent  ",
    context: ["Country", "Mountain"],
    settings: {},
    sources: "notes",
    ...change,
});
const refused: PlanDraft = {
    ...fallbackDraft,
    kind: undefined,
    plan: null,
    planning_status: "unsupported",
};

describe("where the search is offered", () => {
    it("only after the planner could not build a plan, and only when the server has it", () => {
        expect(offersFallback(refused, true)).toBe(true);
        expect(
            offersFallback({ ...refused, planning_status: "ambiguous" }, true),
        ).toBe(true);
        expect(offersFallback(refused, false)).toBe(false);
        expect(offersFallback(null, true)).toBe(false);
        // Any other state of a draft: a plan exists, or the model gave no reason.
        expect(offersFallback(fallbackDraft, true)).toBe(false);
        expect(offersFallback({ ...refused, plan: lookupPlan }, true)).toBe(
            false,
        );
        expect(
            offersFallback({ ...refused, planning_status: "other" }, true),
        ).toBe(false);
        expect(
            offersFallback({ ...refused, planning_status: "ready" }, true),
        ).toBe(false);
    });
    it("also when the planner's plan failed the check, with the reason named", () => {
        const invalid: PlanDraft = {
            ...refused,
            plan: { ...lookupPlan, steps: [] },
            planning_status: "ready",
            validation: { valid: false, errors: [] },
        };
        expect(offersFallback(invalid, true)).toBe(true);
        expect(fallbackOffer(invalid, true)).toBe("invalid");
        expect(fallbackOffer(refused, true)).toBe("refused");
        expect(
            fallbackOffer({ ...refused, planning_status: "ambiguous" }, true),
        ).toBe("refused");
        // Never for a plan that passed, for a draft of the search itself, where the
        // server cannot do it, or with no draft.
        const valid = { ...invalid, validation: { valid: true, errors: [] } };
        expect(offersFallback(valid, true)).toBe(false);
        expect(
            offersFallback(
                { ...fallbackDraft, validation: invalid.validation },
                true,
            ),
        ).toBe(false);
        expect(offersFallback(invalid, false)).toBe(false);
        expect(offersFallback(null, false)).toBe(false);
        expect(fallbackOffer(null, true)).toBeNull();
        // The limits are read again for exactly the cases that may offer it.
        expect(couldOfferFallback(invalid)).toBe(true);
        expect(couldOfferFallback(refused)).toBe(true);
        expect(couldOfferFallback(valid)).toBe(false);
        expect(
            couldOfferFallback({ ...refused, planning_status: "other" }),
        ).toBe(false);
        expect(couldOfferFallback(fallbackDraft)).toBe(false);
        expect(couldOfferFallback(null)).toBe(false);
    });
    it("knows a run of the search from its job or from its plan", () => {
        expect(isFallbackRun(lookupJob, null)).toBe(true);
        expect(isFallbackRun(null, fallbackDraft)).toBe(true);
        expect(isFallbackRun({ ...lookupJob, kind: undefined }, refused)).toBe(
            false,
        );
        expect(isFallbackRun(null, null)).toBe(false);
    });
});

describe("the columns of the form", () => {
    it("lets only text and number columns identify a row, and says why for the others", () => {
        for (const type of ["Text", "Integer", "Number"])
            expect(contextBlocker(type)).toBeNull();
        expect(contextBlocker("Date")).toBe("date");
        expect(contextBlocker("DateTime")).toBe("date");
        expect(contextBlocker("Boolean")).toBe("boolean");
        expect(contextBlocker("Mixed")).toBe("mixed");
        expect(contextBlocker("Unknown")).toBe("empty");
        expect(contextBlocker("Something new")).toBe("other");
    });
    it("proposes Number for an existing column of numbers and Text otherwise", () => {
        expect(defaultKind("Integer")).toBe("number");
        expect(defaultKind("Number")).toBe("number");
        for (const type of ["Text", "Unknown", "Mixed", "Boolean", undefined])
            expect(defaultKind(type)).toBe("text");
    });
    it("takes the columns to fill from the failed plan request, trimmed", () => {
        expect(targetNames(mapping)).toEqual([
            "Height",
            "First ascent",
            "Notes",
        ]);
        expect(
            targetNames([{ mode: "existing", name: "", column: "" }]),
        ).toEqual([]);
    });
});

describe("the request", () => {
    it("is built from the state of the page and from nothing else", () => {
        expect(
            buildFallbackRequest({
                preview,
                range: "A1:G6",
                mapping,
                existingOutputs: "replace",
                form: form({
                    settings: { Height: { kind: "number", unit: " m " } },
                }),
            }),
        ).toEqual({
            dataset_id: "source",
            sheet_name: "Sheet1",
            // The rows of the run, not the whole table.
            range_ref: "A1:G6",
            question: "Find the first ascent",
            // In the table's own order, whatever order they were ticked in.
            context_columns: ["Mountain", "Country"],
            targets: [
                // The default of an existing column of numbers; the unit trimmed.
                { column: "Height", kind: "number", unit: "m" },
                { column: "First ascent", kind: "text" },
                { column: "Notes", kind: "text" },
            ],
            output_columns: { Height: "E", "First ascent": "F", Notes: "H" },
            existing_outputs: "replace",
            sources_in_workbook: "notes",
        });
    });
    it("sends a unit only for a number, and only when it is not blank", () => {
        const request = buildFallbackRequest({
            preview,
            mapping,
            existingOutputs: "keep",
            form: form({
                settings: {
                    Height: { kind: "number", unit: "   " },
                    "First ascent": { kind: "text", unit: "m" },
                    Notes: { kind: "yes_no", unit: "m" },
                },
            }),
        });
        expect(request.range_ref).toBe("A1:G11");
        expect(request.targets).toEqual([
            { column: "Height", kind: "number" },
            { column: "First ascent", kind: "text" },
            { column: "Notes", kind: "yes_no" },
        ]);
    });
    it("never lets a column to fill identify the row", () => {
        const request = buildFallbackRequest({
            preview,
            mapping,
            existingOutputs: "keep",
            form: form({ context: ["Mountain", "Height"] }),
        });
        expect(request.context_columns).toEqual(["Mountain"]);
        expect(
            formProblems(form({ context: ["Height"] }), mapping, preview),
        ).toContain("context");
    });
    it("says what still stops the form", () => {
        expect(formProblems(form(), mapping, preview)).toEqual([]);
        expect(
            formProblems(form({ question: "  " }), mapping, preview),
        ).toEqual(["question"]);
        expect(formProblems(form({ context: [] }), mapping, preview)).toEqual([
            "context",
        ]);
        expect(
            formProblems(
                form({
                    settings: {
                        Height: { kind: "number", unit: "x".repeat(31) },
                    },
                }),
                mapping,
                preview,
            ),
        ).toEqual(["unit"]);
        expect(
            formProblems(
                form(),
                [{ mode: "existing", name: "", column: "" }],
                preview,
            ),
        ).toContain("targets");
    });
});

describe("the plan read back", () => {
    it("gives the question, the columns and the kinds of a fallback plan", () => {
        expect(
            readLookupTask(lookupPlan, fallbackDraft.output_columns),
        ).toEqual({
            question: "Find when each mountain was first climbed",
            context: ["Mountain", "Country"],
            columns: [
                { header: "Height", kind: "number", unit: "m", letter: "E" },
                {
                    header: "First ascent",
                    kind: "text",
                    unit: null,
                    letter: "F",
                },
                {
                    header: "Climbed in winter",
                    kind: "yes_no",
                    unit: null,
                    letter: "G",
                },
            ],
        });
    });
    it("reads nothing from another plan or from a task it cannot read", () => {
        expect(readLookupTask(null)).toBeNull();
        expect(
            readLookupTask({
                ...lookupPlan,
                steps: [{ ...lookupPlan.steps[0], capability: "geocode" }],
            }),
        ).toBeNull();
        for (const value of ["not json", "[]", '{"question":1}', "null"])
            expect(
                readLookupTask({
                    ...lookupPlan,
                    steps: [
                        {
                            ...lookupPlan.steps[0],
                            inputs: { task: { kind: "literal", value } },
                        },
                    ],
                }),
            ).toBeNull();
    });
});

describe("the wording of the step", () => {
    it("names the step from the locales, and an unknown id as before", async () => {
        await i18n.changeLanguage("en");
        expect(capabilityTitle(i18n.t, "web_lookup")).toBe(
            "Search the web with AI (not verified)",
        );
        expect(capabilityTitle(i18n.t, "currency_rate")).toBe("Currency rate");
        await i18n.changeLanguage("de");
        expect(capabilityTitle(i18n.t, "web_lookup")).toBe(
            "Mit KI im Web suchen (nicht überprüft)",
        );
        await i18n.changeLanguage("en");
    });
    it("knows what stands behind its values although the server never lists it", () => {
        expect(capabilityProvenance([], "web_lookup")).toEqual({
            source: "openai_web_search",
            trust: "ai_found",
        });
        expect(capabilityProvenance([], "geocode")).toBeUndefined();
    });
});

describe("the results of a run", () => {
    const rows = resultRows(lookupReport, lookupPlan);
    it("reads the values from the answer of the step and the basis from ai_found", () => {
        expect(rows.map((row) => row.outputs)).toEqual([
            { Height: 8849, "First ascent": "1953", "Climbed in winter": "No" },
            {
                Height: undefined,
                "First ascent": "1954",
                "Climbed in winter": "Yes",
            },
            {
                Height: undefined,
                "First ascent": undefined,
                "Climbed in winter": undefined,
            },
        ]);
        expect(rows[0].found).toEqual({
            Height: { basis: "stated", links: 1 },
            "First ascent": { basis: "concluded", links: 2 },
            "Climbed in winter": { basis: "stated", links: 1 },
        });
        expect(rows[0].concluded).toEqual(["First ascent"]);
        expect(rows[1].concluded).toEqual([]);
        expect(rows.every((row) => row.lookup)).toBe(true);
    });
    it("filters the rows with a concluded value and counts them", () => {
        expect(filterRows(rows, "concluded").map((row) => row.row)).toEqual([
            2,
        ]);
    });
    it("counts the rows asked, the values found and those concluded", () => {
        expect(lookupSummary(rows)).toEqual({
            asked: 3,
            found: 5,
            concluded: 1,
        });
        expect(lookupSummary([])).toEqual({ asked: 0, found: 0, concluded: 0 });
    });
    it("gives a plain cause for each code of the search", () => {
        expect(rows[2].cause).toMatchObject({
            kind: "nothing_found",
            code: "NOTHING_FOUND",
        });
        const kinds = (code: string, capability: string | null) =>
            causeKind(code, capability);
        expect(kinds("DEADLINE_REACHED", "web_lookup")).toBe("deadline");
        expect(kinds("REQUEST_REFUSED", "web_lookup")).toBe("request_refused");
        expect(kinds("RESPONSE_INCOMPLETE", "web_lookup")).toBe(
            "response_incomplete",
        );
        expect(kinds("VALUE_NOT_GIVEN", "web_lookup")).toBe("no_value");
        // The routing service uses this code too, with its own meaning.
        expect(kinds("SOURCE_ACCESS_DENIED", "web_lookup")).toBe(
            "lookup_access",
        );
        expect(kinds("SOURCE_ACCESS_DENIED", "road_distance")).toBe("access");
        expect(kinds("SOMETHING_NEW", "web_lookup")).toBe("other");
    });
    it("leaves a gap without a slot name", () => {
        expect(rows[1].gaps).toEqual([
            { column: "Height", field: "value_1", subject: "K2" },
        ]);
    });
    it("reads the answer of a step for the detail layer", () => {
        const slots = foundSlots(
            lookupReport.execution!.records[0].row_result.steps[0].result.value,
        );
        expect(Object.keys(slots ?? {})).toEqual([
            "value_1",
            "value_2",
            "value_3",
        ]);
        expect(foundSlots({ value: 3 })).toBeNull();
        expect(foundSlots(null)).toBeNull();
    });
});

describe("what a run says in Basic", () => {
    it("does not ask for choices of a search run that needs review", () => {
        const needs = { ...lookupJob, status: "needs_review" as const };
        const outcome = runOutcome(needs, lookupReport, lookupPlan, null, true);
        expect(outcome?.more).toEqual([{ key: "lookupEmpty", values: {} }]);
        // Without the flag the old sentences stay as they were.
        const other = runOutcome(needs, lookupReport, lookupPlan, null);
        expect(other?.more).toEqual([]);
    });
});
