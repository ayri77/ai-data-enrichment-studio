import { expect, it } from "vitest";
import {
    dependencies,
    issueGroups,
    normalizeColumn,
    validMappings,
    type OutputTarget,
} from "./workflowModel";
import type { AuditReport, Preview } from "./api";

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
