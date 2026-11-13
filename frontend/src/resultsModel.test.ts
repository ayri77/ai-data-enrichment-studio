import { expect, it } from "vitest";
import type { AuditReport, EnrichmentPlan } from "./api";
import {
    columnStats,
    describeValue,
    filterRows,
    formatValue,
    inputColumns,
    resultRows,
    sortRows,
} from "./resultsModel";

const plan: EnrichmentPlan = {
    version: "1.0",
    steps: [
        {
            id: "from_city",
            capability: "geocode",
            inputs: {
                city: { kind: "column", name: "From" },
                country: { kind: "column", name: "Country" },
            },
            parameters: {},
        },
        {
            id: "distance",
            capability: "geodesic_distance",
            inputs: { a: { kind: "step_output", step_id: "from_city" } },
            parameters: { unit: "km" },
        },
    ],
    outputs: [
        {
            step_id: "distance",
            value_field: "value",
            target_column: "distance",
            expected_unit: "km",
        },
    ],
    assumptions: [],
};
const ok = (value: unknown) => ({
    status: "success" as const,
    value,
    error_code: null,
    message: null,
    evidence: [{ source: "open-meteo", reference: "geonames:1" }],
});
const failed = (error_code: string) => ({
    status: "failure" as const,
    value: null,
    error_code,
    message: "sanitized",
    evidence: [],
});
const record = (
    row: number,
    city: string,
    geocode: ReturnType<typeof ok | typeof failed>,
    distance: ReturnType<typeof ok | typeof failed>,
    chosen: string[] = [],
) => ({
    row: {
        record_id: `Sheet1!${row}`,
        status:
            distance.status === "success"
                ? ("success" as const)
                : ("failure" as const),
        row,
        inputs: { From: city, Country: "Ukraine" },
        user_chosen_steps: chosen,
    },
    execution: {
        record_id: `Sheet1!${row}`,
        row_result: {
            steps: [
                { step_id: "from_city", result: geocode },
                { step_id: "distance", result: distance },
            ],
        },
    },
});
const point = ok({ latitude: 50.4547, longitude: 30.5238 });
const data = [
    record(2, "Kyiv", point, ok({ value: 2023.7941796667708, unit: "km" }), [
        "from_city",
    ]),
    record(3, "Lviv", point, ok({ value: 470.5, unit: "km" })),
    record(4, "Odesa", failed("AMBIGUOUS"), failed("DEPENDENCY_FAILED")),
    record(5, "Xyz", failed("NOT_FOUND"), failed("DEPENDENCY_FAILED")),
    record(6, "Dnipro", failed("BRAND_NEW_CODE"), failed("DEPENDENCY_FAILED")),
];
const report = {
    rows: data.map((d) => d.row),
    execution: {
        provider_requests: {},
        records: data.map((d) => d.execution),
    },
} as unknown as AuditReport;
const rows = resultRows(report, plan);

it("joins source values, outputs and the root cause of an empty cell", () => {
    expect(inputColumns(report)).toEqual(["From", "Country"]);
    expect(rows[0]).toMatchObject({
        row: 2,
        filled: true,
        inputs: { From: "Kyiv" },
        outputs: { distance: 2023.7941796667708 },
        chosen: ["Kyiv"],
        automatic: [],
        cause: null,
    });
    // The dependency failure is a consequence; the geocode failure is the cause.
    expect(rows[2]).toMatchObject({
        filled: false,
        outputs: { distance: undefined },
        cause: { kind: "ambiguous", code: "AMBIGUOUS", subject: "Odesa" },
    });
    expect(rows[3].cause).toMatchObject({ kind: "not_found", subject: "Xyz" });
    // An unknown code is kept, not hidden behind a wrong explanation.
    expect(rows[4].cause).toEqual({
        kind: "other",
        code: "BRAND_NEW_CODE",
        subject: "Dnipro",
    });
    expect(rows[0].steps.map((s) => s.capability)).toEqual([
        "geocode",
        "geodesic_distance",
    ]);
});

it("names the other name a value was found under and who gave it", () => {
    const searched = (other_names: object) =>
        resultRows(
            {
                ...report,
                rows: report.rows!.map((r) =>
                    r.row === 2 ? { ...r, other_names } : r,
                ),
            } as AuditReport,
            plan,
        )[0].searched;
    expect(
        searched({ from_city: { searched_as: "Kiev", origin: "model" } }),
    ).toEqual([{ subject: "Kyiv", searchedAs: "Kiev", origin: "model" }]);
    // A report whose review record is gone still names the searched name.
    expect(searched({ from_city: { searched_as: "Kiev" } })).toEqual([
        { subject: "Kyiv", searchedAs: "Kiev", origin: null },
    ]);
    expect(rows[0].searched).toEqual([]);
});

it("names the source value behind a step that works on another step's result", () => {
    const step = (id: string, inputs: Record<string, unknown>) => ({
        id,
        capability: id,
        inputs,
        parameters: {},
    });
    const from = (id: string) => ({ kind: "step_output", step_id: id });
    const mountains = {
        ...plan,
        steps: [
            step("mountain", { name: { kind: "column", name: "Mountain" } }),
            step("elevation", { entity: from("mountain") }),
            step("other", { name: { kind: "column", name: "Other" } }),
            step("between", { a: from("mountain"), b: from("other") }),
        ],
        outputs: [],
    } as unknown as EnrichmentPlan;
    const cause = (failing: string) =>
        resultRows(
            {
                rows: [
                    {
                        record_id: "r",
                        status: "failure",
                        row: 2,
                        inputs: { Mountain: "Fuji", Other: "Elbrus" },
                    },
                ],
                execution: {
                    provider_requests: {},
                    records: [
                        {
                            record_id: "r",
                            row_result: {
                                steps: [
                                    {
                                        step_id: failing,
                                        result: failed("NOT_FOUND"),
                                    },
                                ],
                            },
                        },
                    ],
                },
            } as unknown as AuditReport,
            mountains,
        )[0].cause;
    // The elevation of the mountain found before it is about that mountain.
    expect(cause("elevation")).toMatchObject({ subject: "Fuji" });
    // A step between two results is about neither of them alone.
    expect(cause("between")).toMatchObject({ subject: null });
});

it("reads older reports without source values or row numbers", () => {
    const old = {
        rows: [{ record_id: "excel-row-2", status: "partial" }],
    } as AuditReport;
    expect(inputColumns(old)).toEqual([]);
    expect(resultRows(old, plan)[0]).toMatchObject({
        row: null,
        inputs: {},
        filled: false,
        chosen: [],
        automatic: [],
        cause: null,
        steps: [],
    });
    expect(resultRows(null, null)).toEqual([]);
});

it("filters by outcome and sorts with empty values last in both directions", () => {
    expect(filterRows(rows, "filled").map((r) => r.row)).toEqual([2, 3]);
    expect(filterRows(rows, "empty").map((r) => r.row)).toEqual([4, 5, 6]);
    expect(filterRows(rows, "chosen").map((r) => r.row)).toEqual([2]);
    const auto = resultRows(
        {
            ...report,
            rows: report.rows!.map((r) =>
                r.row === 3 ? { ...r, auto_accepted_steps: ["from_city"] } : r,
            ),
        },
        plan,
    );
    expect(auto[1].automatic).toEqual(["Lviv"]);
    expect(filterRows(auto, "automatic").map((r) => r.row)).toEqual([3]);
    const by = (key: string, descending = false) =>
        sortRows(rows, { key, descending }, "en").map((r) => r.row);
    expect(by("out:distance")).toEqual([3, 2, 4, 5, 6]);
    expect(by("out:distance", true)).toEqual([2, 3, 4, 5, 6]);
    expect(by("in:From")).toEqual([6, 2, 3, 4, 5]);
    expect(by("row", true)).toEqual([6, 5, 4, 3, 2]);
    expect(rows.map((r) => r.row)).toEqual([2, 3, 4, 5, 6]); // Not mutated.
});

it("summarizes numeric columns and formats values for reading", () => {
    expect(columnStats(rows, "distance")).toEqual({
        filled: 2,
        total: 5,
        min: 470.5,
        max: 2023.7941796667708,
        mean: (470.5 + 2023.7941796667708) / 2,
    });
    expect(columnStats(filterRows(rows, "empty"), "distance")).toEqual({
        filled: 0,
        total: 3,
    });
    expect(formatValue(2023.7941796667708, "en", "km")).toBe("2,023.79 km");
    expect(formatValue(2023.7941796667708, "de", "km")).toBe("2.023,79 km");
    expect(formatValue(undefined, "en", "km")).toBe("—");
    expect(formatValue("Kyiv", "en")).toBe("Kyiv");
    expect(describeValue({ latitude: 50.4547, longitude: 30.5238 }, "en")).toBe(
        "50.4547, 30.5238",
    );
    expect(describeValue({ source: "wikidata", entity_id: "Q513" }, "en")).toBe(
        "wikidata:Q513",
    );
    expect(describeValue({ value: 8848.86, unit: "m" }, "en")).toBe(
        "8,848.86 m",
    );
});

it("names a row's own unusable value as the cause", () => {
    const own = [
        record(
            2,
            "London",
            failed("UNRECOGNIZED_COUNTRY"),
            failed("DEPENDENCY_FAILED"),
        ),
        record(
            3,
            "L".repeat(600),
            failed("NAME_TOO_LONG"),
            failed("DEPENDENCY_FAILED"),
        ),
    ];
    const made = resultRows(
        {
            rows: own.map((d) => d.row),
            execution: {
                provider_requests: {},
                records: own.map((d) => d.execution),
            },
        } as unknown as AuditReport,
        plan,
    );
    expect(made[0].cause).toEqual({
        kind: "country",
        code: "UNRECOGNIZED_COUNTRY",
        subject: "London",
    });
    expect(made[1].cause).toMatchObject({ kind: "too_long" });
});

it("tells a kept or replaced earlier value from a value this run filled in", () => {
    const column = plan.outputs[0].target_column;
    const withExisting = {
        ...report,
        rows: report.rows!.map((row) =>
            row.row === 2
                ? { ...row, kept_outputs: [column] }
                : row.row === 3
                  ? { ...row, replaced_outputs: { [column]: 99999 } }
                  : row,
        ),
    } as AuditReport;
    const [kept, replaced, other] = resultRows(withExisting, plan);
    // A value was found, yet the cell holds what it held: not filled by this run.
    expect(kept.kept).toEqual([column]);
    expect(kept.outputs[column]).toBeCloseTo(2023.79, 1);
    expect(kept.filled).toBe(false);
    expect(replaced.replaced).toEqual({ [column]: 99999 });
    expect(replaced.filled).toBe(true);
    expect(other.kept).toEqual([]);
    expect(other.replaced).toEqual({});
    // A report written before this was recorded reads as before.
    expect(rows[0].kept).toEqual([]);
    expect(rows[0].filled).toBe(true);
});
