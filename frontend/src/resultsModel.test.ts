import { expect, it } from "vitest";
import type { AuditReport, EnrichmentPlan } from "./api";
import {
    columnStats,
    describeValue,
    filterRows,
    formatValue,
    inputColumns,
    outputColumns,
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
    expect(filterRows(rows, "partial")).toEqual([]);
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
        skipped: 0,
        min: 470.5,
        max: 2023.7941796667708,
        mean: (470.5 + 2023.7941796667708) / 2,
    });
    expect(columnStats(filterRows(rows, "empty"), "distance")).toEqual({
        filled: 0,
        total: 3,
        skipped: 0,
    });
    expect(formatValue(2023.7941796667708, "en", "km")).toBe("2,023.79 km");
    expect(formatValue(2023.7941796667708, "de", "km")).toBe("2.023,79 km");
    expect(formatValue(undefined, "en", "km")).toBe("—");
    // A coordinate is shown with five decimals; other numbers keep two.
    expect(formatValue(51.50853, "en", undefined, 5)).toBe("51.50853");
    expect(formatValue(-0.12574, "de", undefined, 5)).toBe("-0,12574");
    expect(formatValue(51.50853, "en")).toBe("51.51");
    expect(
        outputColumns({
            version: "1.0",
            steps: [],
            outputs: [
                { step_id: "c", value_field: "latitude", target_column: "lat" },
                {
                    step_id: "c",
                    value_field: "longitude",
                    target_column: "lon",
                },
                {
                    step_id: "d",
                    value_field: "value",
                    target_column: "km",
                    expected_unit: "km",
                },
            ],
            assumptions: [],
        } as EnrichmentPlan),
    ).toEqual([
        { key: "lat", label: "lat", unit: undefined, digits: 5, stepId: "c" },
        { key: "lon", label: "lon", unit: undefined, digits: 5, stepId: "c" },
        { key: "km", label: "km", unit: "km", stepId: "d" },
    ]);
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

it("reads a plain number a step returned, and names what a routing service said", () => {
    const roads: EnrichmentPlan = {
        version: "1.0",
        steps: [
            {
                id: "road",
                capability: "road_distance",
                inputs: {
                    point_a: { kind: "step_output", step_id: "from" },
                    point_b: { kind: "step_output", step_id: "to" },
                },
                parameters: { unit: "km" },
            },
            {
                id: "detour",
                capability: "ratio",
                inputs: {
                    numerator: { kind: "step_output", step_id: "road" },
                    denominator: { kind: "step_output", step_id: "straight" },
                },
                parameters: {},
            },
        ],
        outputs: [
            {
                step_id: "road",
                value_field: "value",
                target_column: "road",
                expected_unit: "km",
            },
            {
                step_id: "detour",
                value_field: "value",
                target_column: "detour",
            },
            // A plain number has no other field to read.
            { step_id: "detour", value_field: "unit", target_column: "odd" },
        ],
        assumptions: [],
    };
    const row = (
        number: number,
        road: ReturnType<typeof ok | typeof failed>,
        detour: ReturnType<typeof ok | typeof failed>,
    ) => ({
        row: {
            record_id: `Sheet1!${number}`,
            status: "partial" as const,
            row: number,
            inputs: {},
        },
        execution: {
            record_id: `Sheet1!${number}`,
            row_result: {
                steps: [
                    { step_id: "road", result: road },
                    { step_id: "detour", result: detour },
                ],
            },
        },
    });
    const codes = [
        "NO_ROUTE",
        "POINT_NOT_ON_ROAD",
        "ROUTE_TOO_LONG",
        "SOURCE_ACCESS_DENIED",
    ];
    const made = [
        row(2, ok({ value: 1870, unit: "km" }), ok(1.337)),
        ...codes.map((code, index) =>
            row(3 + index, failed(code), failed("DEPENDENCY_FAILED")),
        ),
    ];
    const result = resultRows(
        {
            rows: made.map((d) => d.row),
            execution: {
                provider_requests: {},
                records: made.map((d) => d.execution),
            },
        } as unknown as AuditReport,
        roads,
    );
    expect(result[0].outputs).toEqual({
        road: 1870,
        detour: 1.337,
        odd: undefined,
    });
    expect(describeValue(result[0].steps[1].value, "en")).toBe("1.34");
    // A distance is about two places, so the cause names the row, not one of them.
    expect(result.slice(1).map((r) => r.cause)).toEqual([
        { kind: "no_route", code: "NO_ROUTE", subject: null },
        { kind: "off_road", code: "POINT_NOT_ON_ROAD", subject: null },
        { kind: "too_far", code: "ROUTE_TOO_LONG", subject: null },
        { kind: "access", code: "SOURCE_ACCESS_DENIED", subject: null },
    ]);
});

it("shows a row that was not looked up with its kept value, counted apart from filled and empty", () => {
    const withSkipped = {
        ...report,
        skipped_rows: [
            {
                record_id: "Sheet1!7",
                row: 7,
                inputs: { From: "Lutsk", Country: "Ukraine" },
                reason: "already_filled",
                kept_outputs: ["distance"],
                kept_values: { distance: 1234.5 },
            },
            {
                record_id: "Sheet1!1",
                row: 1,
                inputs: { From: "Rivne" },
                reason: "already_filled",
                kept_outputs: ["distance"],
                kept_values: { distance: "=1+1" },
            },
        ],
    } as unknown as AuditReport;
    const all = resultRows(withSkipped, plan);
    // Sheet order, the skipped rows among the others.
    expect(all.map((r) => r.row)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(all[0]).toMatchObject({
        skipped: true,
        filled: false,
        status: "skipped",
        kept: ["distance"],
        outputs: { distance: "=1+1" },
        cause: null,
        steps: [],
    });
    expect(all[6].outputs).toEqual({ distance: 1234.5 });
    expect(inputColumns(withSkipped)).toEqual(["From", "Country"]);
    // Neither filled nor empty: its own filter.
    expect(filterRows(all, "skipped").map((r) => r.row)).toEqual([1, 7]);
    expect(filterRows(all, "filled").map((r) => r.row)).toEqual([2, 3]);
    expect(filterRows(all, "empty").map((r) => r.row)).toEqual([4, 5, 6]);
    expect(filterRows(all, "all")).toHaveLength(7);
    // The kept values are not part of the summary of what the run found.
    expect(columnStats(all, "distance")).toMatchObject({
        filled: 2,
        total: 5,
        skipped: 2,
        min: 470.5,
    });
    // A report from before the field existed reads as before.
    expect(rows.every((r) => !r.skipped)).toBe(true);
});

it("shows a population without decimals and a timezone as text", () => {
    const facts = {
        version: "1.0",
        steps: [],
        outputs: [
            { step_id: "c", value_field: "population", target_column: "pop" },
            { step_id: "c", value_field: "timezone", target_column: "tz" },
        ],
        assumptions: [],
    } as EnrichmentPlan;
    const columns = outputColumns(facts);
    expect(columns).toEqual([
        { key: "pop", label: "pop", unit: undefined, digits: 0, stepId: "c" },
        { key: "tz", label: "tz", unit: undefined, stepId: "c" },
    ]);
    const shown = (index: number, value: number | string) =>
        formatValue(value, "en", columns[index].unit, columns[index].digits);
    expect(shown(0, 3426354)).toBe("3,426,354");
    expect(shown(0, 1702139.4)).toBe("1,702,139");
    expect(formatValue(3426354, "de", undefined, 0)).toBe("3.426.354");
    expect(shown(1, "Europe/Berlin")).toBe("Europe/Berlin");
});

it("reads a field the source does not give as an empty cell with its reason", () => {
    const facts = {
        version: "1.0",
        steps: [
            {
                id: "city",
                capability: "geocode",
                inputs: {
                    city: { kind: "column", name: "From" },
                    country: { kind: "column", name: "Country" },
                },
                parameters: {},
            },
        ],
        outputs: [
            { step_id: "city", value_field: "latitude", target_column: "lat" },
            {
                step_id: "city",
                value_field: "population",
                target_column: "pop",
            },
        ],
        assumptions: [],
    } as EnrichmentPlan;
    const rome = {
        record_id: "Sheet1!7",
        status: "success" as const,
        row: 7,
        inputs: { From: "Rome", Country: "Italy" },
        empty_outputs: {
            pop: { field: "population", code: "VALUE_NOT_GIVEN" },
        },
    };
    const withGap = {
        rows: [rome],
        execution: {
            provider_requests: {},
            records: [
                {
                    record_id: "Sheet1!7",
                    row_result: {
                        steps: [
                            {
                                step_id: "city",
                                result: ok({
                                    latitude: 41.89193,
                                    longitude: 12.51133,
                                    population: null,
                                    timezone: "Europe/Rome",
                                }),
                            },
                        ],
                    },
                },
            ],
        },
    } as unknown as AuditReport;
    const [row] = resultRows(withGap, facts);
    // The other output of the step is still there; only the one cell is empty.
    expect(row.outputs).toEqual({ lat: 41.89193, pop: undefined });
    expect(row.filled).toBe(false);
    expect(row.status).toBe("success");
    expect(row.cause).toBeNull();
    expect(row.gaps).toEqual([
        { column: "pop", field: "population", subject: "Rome" },
    ]);
});

it("calls a row partly filled when some output cells were written and some were not", () => {
    const two: EnrichmentPlan = {
        ...plan,
        outputs: [
            {
                step_id: "from_city",
                value_field: "latitude",
                target_column: "lat",
            },
            {
                step_id: "from_city",
                value_field: "longitude",
                target_column: "lon",
            },
        ],
    };
    const some = (
        n: number,
        value: Record<string, number>,
        geocode: Parameters<typeof record>[2] = ok(value),
    ) => record(n, "C" + n, geocode, failed("DEPENDENCY_FAILED"));
    const set = [
        some(2, { latitude: 1, longitude: 2 }),
        some(3, { latitude: 1 }),
        some(4, {}, failed("NOT_FOUND")),
    ];
    const rs = resultRows(
        {
            rows: set.map((d) => d.row),
            execution: {
                provider_requests: {},
                records: set.map((d) => d.execution),
            },
        } as unknown as AuditReport,
        two,
    );
    expect(rs.map((r) => [r.filled, r.partial])).toEqual([
        [true, false],
        [false, true],
        [false, false],
    ]);
    expect(filterRows(rs, "partial").map((r) => r.row)).toEqual([3]);
    expect(filterRows(rs, "empty").map((r) => r.row)).toEqual([4]);
    expect(filterRows(rs, "filled").map((r) => r.row)).toEqual([2]);
});
