import type { EnrichmentPlan, PlanDraft } from "./api";
import { stepDepths } from "./workflowModel";

// A plan put into words for a person who will never open the graph. This is
// presentation only: it reads the plan the server validated and adds nothing
// to it. A capability the interface has no words for is still listed, under
// its own name, so the description never hides a step.

export type PlanRef =
    | { kind: "column"; name: string }
    | { kind: "step"; number: number }
    | { kind: "literal"; value: string }
    | { kind: "unknown" };

export interface PlanWrite {
    name: string;
    letter: string;
    unit: string;
}

export interface PlanLine {
    id: string;
    number: number;
    capability: string;
    /** Locale key under `plan.do.*`. */
    action: string;
    values: Record<string, string>;
    inputs: { port: string; ref: PlanRef }[];
    /** Locale keys under `plan.note.*`. */
    notes: string[];
    writes: PlanWrite[];
}

type Step = EnrichmentPlan["steps"][number];

function text(value: unknown) {
    return typeof value === "string" ? value : "";
}

function action(step: Step): Pick<PlanLine, "action" | "values" | "notes"> {
    const p = step.parameters ?? {};
    switch (step.capability) {
        case "geocode":
            return {
                action: "geocode",
                values: {},
                notes: p.required_role === "country_capital" ? ["capital"] : [],
            };
        case "geodesic_distance":
            return {
                action: text(p.unit) ? "distanceIn" : "distance",
                values: { unit: text(p.unit) },
                notes: [],
            };
        case "resolve_entity":
            return p.entity_type === "mountain"
                ? { action: "mountain", values: {}, notes: [] }
                : {
                      action: "entity",
                      values: { type: text(p.entity_type) },
                      notes: [],
                  };
        case "lookup_property":
            return p.property === "elevation"
                ? { action: "elevation", values: {}, notes: [] }
                : {
                      action: "property",
                      values: { property: text(p.property) },
                      notes: [],
                  };
        default:
            return {
                action: "other",
                values: { capability: step.capability },
                notes: [],
            };
    }
}

export function describePlan(draft: PlanDraft): PlanLine[] {
    const plan = draft.plan;
    if (!plan) return [];
    const depths = stepDepths(plan);
    // What a step needs comes first; otherwise the model's own order is kept.
    const ordered = plan.steps
        .map((step, index) => ({ step, index }))
        .sort(
            (a, b) =>
                depths.get(a.step.id)! - depths.get(b.step.id)! ||
                a.index - b.index,
        )
        .map((x) => x.step);
    const numbers = new Map(ordered.map((s, i) => [s.id, i + 1]));
    const reference = (raw: unknown): PlanRef => {
        if (!raw || typeof raw !== "object") return { kind: "unknown" };
        const ref = raw as Record<string, unknown>;
        if (ref.kind === "column" && typeof ref.name === "string")
            return { kind: "column", name: ref.name };
        if (ref.kind === "step_output" && typeof ref.step_id === "string") {
            const number = numbers.get(ref.step_id);
            return number ? { kind: "step", number } : { kind: "unknown" };
        }
        if (ref.kind === "literal" && "value" in ref)
            return {
                kind: "literal",
                value:
                    typeof ref.value === "string"
                        ? ref.value
                        : JSON.stringify(ref.value),
            };
        return { kind: "unknown" };
    };
    return ordered.map((step) => ({
        id: step.id,
        number: numbers.get(step.id)!,
        capability: step.capability,
        ...action(step),
        inputs: Object.entries(step.inputs ?? {}).map(([port, raw]) => ({
            port,
            ref: reference(raw),
        })),
        writes: plan.outputs
            .filter((o) => o.step_id === step.id)
            .map((o) => ({
                name: o.target_column,
                letter: draft.output_columns[o.target_column] ?? "",
                unit: o.expected_unit ?? "",
            })),
    }));
}
