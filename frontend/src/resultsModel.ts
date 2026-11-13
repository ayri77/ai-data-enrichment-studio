import type { AuditReport, EnrichmentPlan, ReviewValue } from "./api";

type Scalar = string | number | boolean;
export type Cause =
    | "ambiguous"
    | "not_found"
    | "unverified"
    | "provider"
    | "missing_input"
    | "country"
    | "too_long"
    | "not_reverified"
    | "no_route"
    | "off_road"
    | "too_far"
    | "access"
    | "other";
const CAUSES: Record<string, Cause> = {
    AMBIGUOUS: "ambiguous",
    NOT_FOUND: "not_found",
    INCOMPLETE_VERIFICATION: "unverified",
    INCOMPLETE_SEARCH: "unverified",
    INCOMPLETE_HIERARCHY: "unverified",
    UNVERIFIED_TYPE: "unverified",
    CONTEXT_UNVERIFIED: "unverified",
    NON_MOUNTAIN_CLASS: "unverified",
    PROVIDER_ERROR: "provider",
    INVALID_RESPONSE: "provider",
    RETRY_EXHAUSTED: "provider",
    PROVIDER_OPERATION_BUDGET_EXCEEDED: "provider",
    MISSING_VALUE: "missing_input",
    UNRECOGNIZED_COUNTRY: "country",
    NAME_TOO_LONG: "too_long",
    APPROVAL_NOT_REVERIFIED: "not_reverified",
    NO_ROUTE: "no_route",
    POINT_NOT_ON_ROAD: "off_road",
    ROUTE_TOO_LONG: "too_far",
    SOURCE_ACCESS_DENIED: "access",
};
export interface ResultStep {
    stepId: string;
    capability: string | null;
    ok: boolean;
    value: unknown;
    errorCode: string | null;
    evidence: { source: string; reference: string }[];
}
export interface ResultRow {
    recordId: string;
    /** Excel row number; null for reports written before it was recorded. */
    row: number | null;
    status: "success" | "partial" | "failure";
    inputs: Record<string, ReviewValue>;
    /** Keyed by target column; undefined means the cell stays empty. */
    outputs: Record<string, Scalar | undefined>;
    filled: boolean;
    /** Output columns whose cell already held a value and was left as it was. */
    kept: string[];
    /** Output columns whose earlier value was replaced, with that value. */
    replaced: Record<string, Scalar>;
    /** Source values whose match the user chose during review. */
    chosen: string[];
    /** Source values found under another name, and who gave that name. */
    searched: {
        subject: string | null;
        searchedAs: string;
        origin: "user" | "model" | null;
    }[];
    /** Source values accepted automatically although other matches stayed unconfirmed. */
    automatic: string[];
    /** The first real failure in the row, not its downstream consequences. */
    cause: { kind: Cause; code: string; subject: string | null } | null;
    steps: ResultStep[];
}
export interface ResultColumn {
    key: string;
    label: string;
    unit?: string;
}

const scalar = (value: unknown): value is Scalar =>
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean";

/**
 * The source value a step looked up, e.g. the city name for a geocode step.
 * A step that works on the result of another one (the elevation of the
 * mountain found before it) is about that step's source value.
 */
function subject(
    step: EnrichmentPlan["steps"][number] | undefined,
    inputs: Record<string, ReviewValue>,
    steps: Map<string, EnrichmentPlan["steps"][number]>,
    depth = 0,
): string | null {
    const refs = Object.values(step?.inputs ?? {}).filter(
        (raw): raw is Record<string, unknown> =>
            !!raw && typeof raw === "object",
    );
    for (const raw of refs) {
        if (raw.kind === "column" && typeof raw.name === "string") {
            const value = inputs[raw.name];
            if (value !== null && value !== undefined) return String(value);
        }
    }
    // Only a step with a single source: a distance between two places is
    // about neither of them alone.
    const sources = refs.filter(
        (raw) => raw.kind === "step_output" && typeof raw.step_id === "string",
    );
    if (sources.length === 1 && depth < 8)
        return subject(
            steps.get(sources[0].step_id as string),
            inputs,
            steps,
            depth + 1,
        );
    return null;
}

export function inputColumns(report: AuditReport | null): string[] {
    const names: string[] = [];
    for (const row of report?.rows ?? [])
        for (const name of Object.keys(row.inputs ?? {}))
            if (!names.includes(name)) names.push(name);
    return names;
}
export const outputColumns = (plan: EnrichmentPlan | null): ResultColumn[] =>
    (plan?.outputs ?? []).map((output) => ({
        key: output.target_column,
        label: output.target_column,
        unit: output.expected_unit,
    }));

/** Join the published row list with step results from the same audit. */
export function resultRows(
    report: AuditReport | null,
    plan: EnrichmentPlan | null,
): ResultRow[] {
    const records = new Map(
        (report?.execution?.records ?? []).map((r) => [r.record_id, r]),
    );
    const planSteps = new Map((plan?.steps ?? []).map((s) => [s.id, s]));
    return (report?.rows ?? []).map((row) => {
        const inputs = row.inputs ?? {};
        const steps: ResultStep[] = (
            records.get(row.record_id)?.row_result.steps ?? []
        ).map((step) => ({
            stepId: step.step_id,
            capability: planSteps.get(step.step_id)?.capability ?? null,
            ok: step.result.status === "success",
            value: step.result.value,
            errorCode: step.result.error_code,
            evidence: step.result.evidence ?? [],
        }));
        const outputs: ResultRow["outputs"] = {};
        for (const output of plan?.outputs ?? []) {
            const step = steps.find((s) => s.stepId === output.step_id);
            // A step returns either a structured value with named fields or a
            // plain one, which is its own "value".
            const field = !step?.ok
                ? undefined
                : step.value && typeof step.value === "object"
                  ? (step.value as Record<string, unknown>)[output.value_field]
                  : output.value_field === "value"
                    ? step.value
                    : undefined;
            outputs[output.target_column] = scalar(field) ? field : undefined;
        }
        const subjects = (ids: string[] | undefined) =>
            (ids ?? []).flatMap((id) => {
                const value = subject(planSteps.get(id), inputs, planSteps);
                return value ? [value] : [];
            });
        const failed = steps.find(
            (s) => !s.ok && s.errorCode !== "DEPENDENCY_FAILED",
        );
        return {
            recordId: row.record_id,
            row: row.row ?? null,
            status: row.status,
            inputs,
            outputs,
            // Filled by this run: a kept cell holds its earlier value, not
            // the one that was found.
            filled:
                Object.keys(outputs).length > 0 &&
                Object.entries(outputs).every(
                    ([column, value]) =>
                        value !== undefined &&
                        !(row.kept_outputs ?? []).includes(column),
                ),
            kept: row.kept_outputs ?? [],
            replaced: row.replaced_outputs ?? {},
            chosen: subjects(row.user_chosen_steps),
            searched: Object.entries(row.other_names ?? {}).map(
                ([id, found]) => ({
                    subject: subject(planSteps.get(id), inputs, planSteps),
                    searchedAs: found.searched_as,
                    origin: found.origin ?? null,
                }),
            ),
            automatic: subjects(row.auto_accepted_steps),
            cause: failed
                ? {
                      kind: CAUSES[failed.errorCode ?? ""] ?? "other",
                      code: failed.errorCode ?? "UNKNOWN",
                      subject: subject(
                          planSteps.get(failed.stepId),
                          inputs,
                          planSteps,
                      ),
                  }
                : null,
            steps,
        };
    });
}

export type RowFilter = "all" | "filled" | "empty" | "chosen" | "automatic";
export function filterRows(rows: ResultRow[], filter: RowFilter) {
    if (filter === "filled") return rows.filter((r) => r.filled);
    if (filter === "empty") return rows.filter((r) => !r.filled);
    if (filter === "chosen") return rows.filter((r) => r.chosen.length > 0);
    if (filter === "automatic")
        return rows.filter((r) => r.automatic.length > 0);
    return rows;
}
/** `key` is "row", "status", "in:<column>" or "out:<column>". */
export interface Sort {
    key: string;
    descending: boolean;
}
function sortValue(row: ResultRow, key: string): Scalar | null {
    if (key === "row") return row.row;
    if (key === "status") return row.status;
    if (key.startsWith("in:")) return row.inputs[key.slice(3)] ?? null;
    return row.outputs[key.slice(4)] ?? null;
}
/** Empty values stay at the end in both directions; ties keep sheet order. */
export function sortRows(rows: ResultRow[], sort: Sort, locale: string) {
    const collator = new Intl.Collator(locale, { numeric: true });
    return [...rows].sort((a, b) => {
        const x = sortValue(a, sort.key);
        const y = sortValue(b, sort.key);
        if (x === null || y === null)
            return x === y ? (a.row ?? 0) - (b.row ?? 0) : x === null ? 1 : -1;
        const order =
            typeof x === "number" && typeof y === "number"
                ? x - y
                : collator.compare(String(x), String(y));
        return (
            (sort.descending ? -order : order) || (a.row ?? 0) - (b.row ?? 0)
        );
    });
}
export interface ColumnStats {
    filled: number;
    total: number;
    min?: number;
    max?: number;
    mean?: number;
}
/** Numeric summary only when every filled value of the column is a number. */
export function columnStats(rows: ResultRow[], column: string): ColumnStats {
    const values = rows
        .map((r) => r.outputs[column])
        .filter((v) => v !== undefined);
    const numbers = values.filter((v) => typeof v === "number");
    const stats: ColumnStats = { filled: values.length, total: rows.length };
    if (numbers.length && numbers.length === values.length) {
        stats.min = Math.min(...numbers);
        stats.max = Math.max(...numbers);
        stats.mean = numbers.reduce((sum, v) => sum + v, 0) / numbers.length;
    }
    return stats;
}
/** Readable on screen; the workbook and the audit keep full precision. */
export function formatValue(
    value: Scalar | null | undefined,
    locale: string,
    unit?: string,
) {
    if (value === undefined || value === null) return "—";
    if (typeof value !== "number") return String(value);
    const text = new Intl.NumberFormat(locale, {
        maximumFractionDigits: 2,
    }).format(value);
    return unit ? `${text} ${unit}` : text;
}
/** Compact, readable form of a step value for the detail layer. */
export function describeValue(value: unknown, locale: string): string {
    if (value === null || value === undefined) return "—";
    if (scalar(value)) return formatValue(value, locale);
    const v = value as Record<string, unknown>;
    if (typeof v.latitude === "number" && typeof v.longitude === "number")
        return `${v.latitude.toFixed(4)}, ${v.longitude.toFixed(4)}`;
    if (typeof v.source === "string" && typeof v.entity_id === "string")
        return `${v.source}:${v.entity_id}`;
    if (scalar(v.value))
        return formatValue(
            v.value,
            locale,
            typeof v.unit === "string" ? v.unit : undefined,
        );
    return JSON.stringify(value);
}
