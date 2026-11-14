import { expect, it } from "vitest";
import {
    dependencies,
    graphLayout,
    issueGroups,
    normalizeColumn,
    validMappings,
    type OutputTarget,
} from "./workflowModel";
import type { AuditReport, EnrichmentPlan, Preview } from "./api";

const preview: Preview = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "B2:C5",
    sample_limit: 5,
    profile: {
        row_count: 3,
        columns: [
            {
                name: "city",
                inferred_type: "Text",
                null_count: 0,
                warnings: [],
            },
            {
                name: "height",
                inferred_type: "Unknown",
                null_count: 3,
                warnings: [],
            },
        ],
        sample_records: [],
        schema_warnings: [],
    },
};
it("uses physical offsets and matching source headers; rejects duplicates and collisions even outside the selection", () => {
    const existing: OutputTarget = {
        mode: "existing",
        name: "height",
        column: "C",
    };
    const fresh: OutputTarget = { mode: "new", name: "elevation", column: "F" };
    const headers = ["id", "city", "height", "notes", "country"];
    expect(validMappings([existing], preview, headers)).toBe(true);
    expect(validMappings([fresh], preview, headers)).toBe(true);
    for (const mapping of [
        [existing, existing],
        [{ ...existing, name: "wrong" }],
        [{ ...fresh, column: "E" }],
        [{ ...fresh, name: "country" }],
        [{ ...fresh, name: "height" }],
        [{ ...fresh, column: "CW" }],
    ])
        expect(validMappings(mapping, preview, headers)).toBe(false);
    expect(normalizeColumn("6")).toBe("F");
    expect(normalizeColumn("100")).toBe("CV");
    expect(
        validMappings(
            [{ ...fresh, column: normalizeColumn("101") }],
            preview,
            headers,
        ),
    ).toBe(false);
});
it("reads only real typed step dependencies, independent of array order", () => {
    expect(
        dependencies({
            id: "distance",
            capability: "geodesic_distance",
            inputs: {
                point: { kind: "step_output", step_id: "city" },
                city: { kind: "column", name: "city" },
                constant: { kind: "literal", value: "city" },
            },
            parameters: {},
        }),
    ).toEqual([{ port: "point", step: "city" }]);
});
it("counts affected records once per code and separates dependency consequences", () => {
    const fail = (code: string) => ({
        step_id: "node",
        result: { status: "failure", error_code: code },
    });
    const report = {
        execution: {
            records: [
                {
                    record_id: "r1",
                    row_result: {
                        steps: [
                            fail("DEPENDENCY_FAILED"),
                            fail("AMBIGUOUS"),
                            fail("AMBIGUOUS"),
                        ],
                    },
                },
                { record_id: "r2", row_result: { steps: [fail("AMBIGUOUS")] } },
            ],
        },
    } as AuditReport;
    const groups = issueGroups(report);
    expect(
        groups.map((g) => [g.code, g.derived, g.records.size, g.operations]),
    ).toEqual([
        ["AMBIGUOUS", false, 2, 3],
        ["DEPENDENCY_FAILED", true, 1, 1],
    ]);
});

it("lays a plan out by what each step waits for, and centres the shorter columns", () => {
    const from = (id: string) => ({ kind: "step_output", step_id: id });
    const step = (id: string, inputs: Record<string, unknown> = {}) => ({
        id,
        capability: "any",
        inputs,
        parameters: {},
    });
    const plan = {
        version: "1",
        steps: [
            step("a"),
            step("b"),
            step("line", { point_a: from("a"), point_b: from("b") }),
            step("road", { point_a: from("a"), point_b: from("b") }),
            step("ratio", {
                numerator: from("road"),
                denominator: from("line"),
            }),
        ],
        outputs: [],
        assumptions: [],
    } as unknown as EnrichmentPlan;
    const { positions, width, height } = graphLayout(plan, {
        column: 100,
        row: 50,
        left: 0,
        top: 0,
    });
    // Two starts, two steps that use both, one step that joins them.
    expect(Object.fromEntries(positions)).toEqual({
        a: { x: 0, y: 0 },
        b: { x: 0, y: 50 },
        line: { x: 100, y: 0 },
        road: { x: 100, y: 50 },
        ratio: { x: 200, y: 25 },
    });
    expect([width, height]).toEqual([316, 100]);
    // The lowest row can be given less room than a whole row, and a canvas a
    // least size.
    expect(
        graphLayout(plan, { column: 100, row: 50, top: 10, last: 30 }).height,
    ).toBe(90);
    const least = graphLayout(plan, {
        column: 100,
        row: 50,
        minWidth: 520,
        minHeight: 270,
    });
    expect([least.width, least.height]).toEqual([520, 270]);
});
