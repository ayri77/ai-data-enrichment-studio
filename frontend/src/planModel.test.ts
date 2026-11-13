import { expect, it } from "vitest";
import type { EnrichmentPlan, PlanDraft } from "./api";
import { describePlan } from "./planModel";

function draft(plan: EnrichmentPlan | null, columns = {}): PlanDraft {
    return {
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:E11",
        instruction: "",
        output_columns: columns,
        mountain_resolution_policy: "strict",
        plan_id: "plan",
        fingerprint: "a".repeat(64),
        source_sha256: "b".repeat(64),
        status: "draft",
        approved_at: null,
        planning_status: plan ? "ready" : "unsupported",
        reason: null,
        validation: { valid: !!plan, errors: [] },
        plan,
    };
}
const column = (name: string) => ({ kind: "column", name });
const output = (step_id: string) => ({ kind: "step_output", step_id });

it("puts a two-city distance into three numbered lines and says where it is written", () => {
    const lines = describePlan(
        draft(
            {
                version: "1.0",
                // Listed out of order on purpose: what a step needs comes first.
                steps: [
                    {
                        id: "distance",
                        capability: "geodesic_distance",
                        inputs: {
                            point_a: output("from_city"),
                            point_b: output("to_city"),
                        },
                        parameters: { unit: "km" },
                    },
                    {
                        id: "from_city",
                        capability: "geocode",
                        inputs: {
                            city: column("Capital_From"),
                            country: column("Country_From"),
                        },
                        parameters: {},
                    },
                    {
                        id: "to_city",
                        capability: "geocode",
                        inputs: {
                            city: column("Capital_To"),
                            country: { kind: "literal", value: "Germany" },
                        },
                        parameters: { required_role: "country_capital" },
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
            },
            { distance: "E" },
        ),
    );
    expect(lines.map((l) => [l.number, l.id, l.action])).toEqual([
        [1, "from_city", "geocode"],
        [2, "to_city", "geocode"],
        [3, "distance", "distanceIn"],
    ]);
    expect(lines[0].inputs).toEqual([
        { port: "city", ref: { kind: "column", name: "Capital_From" } },
        { port: "country", ref: { kind: "column", name: "Country_From" } },
    ]);
    // A value written into the plan is the same for every row and is shown as such.
    expect(lines[1].inputs[1].ref).toEqual({
        kind: "literal",
        value: "Germany",
    });
    expect(lines[1].notes).toEqual(["capital"]);
    expect(lines[0].writes).toEqual([]);
    // Steps are referred to by the number a person sees, not by their id.
    expect(lines[2].inputs).toEqual([
        { port: "point_a", ref: { kind: "step", number: 1 } },
        { port: "point_b", ref: { kind: "step", number: 2 } },
    ]);
    expect(lines[2].values).toEqual({ unit: "km" });
    expect(lines[2].writes).toEqual([
        { name: "distance", letter: "E", unit: "km" },
    ]);
});

it("describes a mountain lookup and keeps a step the interface has no words for", () => {
    const lines = describePlan(
        draft(
            {
                version: "1.0",
                steps: [
                    {
                        id: "resolve",
                        capability: "resolve_entity",
                        inputs: {
                            name: column("Mountain"),
                            country: column("Country"),
                        },
                        parameters: { entity_type: "mountain" },
                    },
                    {
                        id: "elevation",
                        capability: "lookup_property",
                        inputs: { entity: output("resolve") },
                        parameters: { property: "elevation" },
                    },
                    {
                        id: "later",
                        capability: "summarize_text",
                        inputs: { text: output("missing") },
                        parameters: {},
                    },
                ],
                outputs: [
                    {
                        step_id: "elevation",
                        value_field: "value",
                        target_column: "height",
                    },
                ],
                assumptions: [],
            },
            { height: "C" },
        ),
    );
    expect(lines.map((l) => l.action)).toEqual([
        "mountain",
        "elevation",
        "other",
    ]);
    expect(lines[1].writes).toEqual([
        { name: "height", letter: "C", unit: "" },
    ]);
    // Never hidden: an unknown capability is named as the plan names it.
    expect(lines[2].values).toEqual({ capability: "summarize_text" });
    // A reference to a step that is not in the plan is not invented.
    expect(lines[2].inputs).toEqual([
        { port: "text", ref: { kind: "unknown" } },
    ]);
});

it("returns nothing for a refusal and survives a cycle", () => {
    expect(describePlan(draft(null))).toEqual([]);
    const lines = describePlan(
        draft({
            version: "1.0",
            steps: [
                {
                    id: "a",
                    capability: "geocode",
                    inputs: { city: output("b") },
                    parameters: {},
                },
                {
                    id: "b",
                    capability: "geocode",
                    inputs: { city: output("a") },
                    parameters: {},
                },
            ],
            outputs: [],
            assumptions: [],
        }),
    );
    expect(lines.map((l) => l.id).sort()).toEqual(["a", "b"]);
});

it("words a road distance with what computed it, and a ratio by its two values", () => {
    const point = (id: string, city: string, country: string) => ({
        id,
        capability: "geocode",
        inputs: { city: column(city), country: column(country) },
        parameters: {},
    });
    const lines = describePlan(
        draft(
            {
                version: "1.0",
                steps: [
                    {
                        id: "detour",
                        capability: "ratio",
                        inputs: {
                            numerator: output("road"),
                            denominator: output("straight"),
                        },
                        parameters: {},
                    },
                    point("from", "From", "Country_From"),
                    point("to", "To", "Country_To"),
                    {
                        id: "straight",
                        capability: "geodesic_distance",
                        inputs: {
                            point_a: output("from"),
                            point_b: output("to"),
                        },
                        parameters: { unit: "km" },
                    },
                    {
                        id: "road",
                        capability: "road_distance",
                        inputs: {
                            point_a: output("from"),
                            point_b: output("to"),
                        },
                        parameters: { unit: "km" },
                    },
                ],
                outputs: [
                    {
                        step_id: "straight",
                        value_field: "value",
                        target_column: "straight",
                        expected_unit: "km",
                    },
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
                ],
                assumptions: [],
            },
            { straight: "E", road: "F", detour: "G" },
        ),
    );
    // What a step needs comes first: two cities, two distances, their ratio.
    expect(lines.map((l) => l.action)).toEqual([
        "geocode",
        "geocode",
        "distanceIn",
        "roadDistanceIn",
        "ratio",
    ]);
    // A road distance says that a service computed it; the others do not.
    expect(lines.map((l) => l.notes)).toEqual([[], [], [], ["routing"], []]);
    expect(lines[3].values).toEqual({ unit: "km" });
    expect(lines[4].inputs).toEqual([
        { port: "numerator", ref: { kind: "step", number: 4 } },
        { port: "denominator", ref: { kind: "step", number: 3 } },
    ]);
    expect(lines.slice(2).map((l) => l.writes)).toEqual([
        [{ name: "straight", letter: "E", unit: "km" }],
        [{ name: "road", letter: "F", unit: "km" }],
        [{ name: "detour", letter: "G", unit: "" }],
    ]);
});
