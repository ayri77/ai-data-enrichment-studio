import type { AuditReport, EnrichmentPlan, ReviewValue } from "./api";
import { LOOKUP_CAPABILITY, readLookupTask } from "./fallbackModel";

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
    | "no_value"
    // The universal search: what happened to one row's request.
    | "nothing_found"
    | "deadline"
    | "request_refused"
    | "response_incomplete"
    | "lookup_access"
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
    VALUE_NOT_GIVEN: "no_value",
    NOTHING_FOUND: "nothing_found",
    DEADLINE_REACHED: "deadline",
    REQUEST_REFUSED: "request_refused",
    RESPONSE_INCOMPLETE: "response_incomplete",
};
// A code that means something else for the universal search than for the
// routing service, which is the one that also uses it.
const LOOKUP_CAUSES: Record<string, Cause> = {
    SOURCE_ACCESS_DENIED: "lookup_access",
};
/** The kind of cause for a failed step's code. */
export function causeKind(code: string | null, capability: string | null) {
    return (
        (capability === LOOKUP_CAPABILITY
            ? LOOKUP_CAUSES[code ?? ""]
            : undefined) ??
        CAUSES[code ?? ""] ??
        "other"
    );
}
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
    /** "skipped": not looked up, every chosen cell already had a value. */
    status: "success" | "partial" | "failure" | "skipped";
    inputs: Record<string, ReviewValue>;
    /** Keyed by target column; undefined means the cell stays empty. */
    outputs: Record<string, Scalar | undefined>;
    filled: boolean;
    /**
     * Some output cells were written by this run and some were not. A row with
     * no written cell is empty; a kept or skipped row is neither.
     */
    partial: boolean;
    /**
     * The row was left out of the run: every chosen cell already had a value.
     * It is neither filled nor failed, and its outputs are the kept values.
     */
    skipped: boolean;
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
    /**
     * Output columns left empty although the step succeeded, because the source
     * holds no value for that field of the found place.
     */
    gaps: { column: string; field: string; subject: string | null }[];
    /** The first real failure in the row, not its downstream consequences. */
    cause: { kind: Cause; code: string; subject: string | null } | null;
    steps: ResultStep[];
    /** The row was filled by the universal search, not by the planner's steps. */
    lookup: boolean;
    /**
     * Values written from a web search, by column: whether a page states the
     * value or the model concluded it, and how many links it rests on.
     */
    found: Record<string, { basis: "stated" | "concluded"; links: number }>;
    /** The columns whose value the model concluded. */
    concluded: string[];
}
export interface ResultColumn {
    key: string;
    label: string;
    unit?: string;
    /** Decimals shown for a number; a coordinate needs more than a measurement. */
    digits?: number;
    /** The capability of the step that fills the column, when the plan has it. */
    capability?: string;
}

/** Five decimals are about a metre on the ground. */
const COORDINATE_DIGITS = 5;
/** A count of people has no decimals. */
const COUNT_DIGITS = 0;

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
    for (const row of [
        ...(report?.rows ?? []),
        ...(report?.skipped_rows ?? []),
    ])
        for (const name of Object.keys(row.inputs ?? {}))
            if (!names.includes(name)) names.push(name);
    return names;
}
export const outputColumns = (plan: EnrichmentPlan | null): ResultColumn[] =>
    (plan?.outputs ?? []).map((output) => ({
        key: output.target_column,
        label: output.target_column,
        // The universal search keeps a unit in its task, not in the output.
        unit:
            output.expected_unit ??
            readLookupTask(plan)?.columns.find(
                (column) => column.header === output.target_column,
            )?.unit ??
            undefined,
        capability: plan?.steps.find((step) => step.id === output.step_id)
            ?.capability,
        ...(output.value_field === "latitude" ||
        output.value_field === "longitude"
            ? { digits: COORDINATE_DIGITS }
            : output.value_field === "population"
              ? { digits: COUNT_DIGITS }
              : {}),
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
    // Rows that were not looked up hold the values they already had.
    const skipped: ResultRow[] = (report?.skipped_rows ?? []).map((row) => ({
        recordId: row.record_id,
        row: row.row,
        status: "skipped",
        inputs: row.inputs ?? {},
        outputs: Object.fromEntries(
            (plan?.outputs ?? []).map((output) => {
                const value = row.kept_values?.[output.target_column];
                return [
                    output.target_column,
                    scalar(value) ? value : undefined,
                ];
            }),
        ),
        filled: false,
        partial: false,
        skipped: true,
        kept: row.kept_outputs ?? [],
        replaced: {},
        chosen: [],
        searched: [],
        automatic: [],
        gaps: [],
        cause: null,
        steps: [],
        lookup: false,
        found: {},
        concluded: [],
    }));
    const lookup = (plan?.steps ?? []).some(
        (step) => step.capability === LOOKUP_CAPABILITY,
    );
    const executed: ResultRow[] = (report?.rows ?? []).map((row) => {
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
                : foundSlots(step.value)
                  ? foundSlots(step.value)![output.value_field]?.value
                  : step.value && typeof step.value === "object"
                    ? (step.value as Record<string, unknown>)[
                          output.value_field
                      ]
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
            partial:
                Object.keys(outputs).length > 0 &&
                Object.entries(outputs).some(
                    ([column, value]) =>
                        value !== undefined &&
                        !(row.kept_outputs ?? []).includes(column),
                ) &&
                !Object.entries(outputs).every(
                    ([column, value]) =>
                        value !== undefined &&
                        !(row.kept_outputs ?? []).includes(column),
                ) &&
                (row.kept_outputs ?? []).length === 0,
            skipped: false,
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
            gaps: Object.entries(row.empty_outputs ?? {}).map(
                ([column, gap]) => ({
                    column,
                    field: gap.field,
                    subject: subject(
                        planSteps.get(
                            (plan?.outputs ?? []).find(
                                (o) => o.target_column === column,
                            )?.step_id ?? "",
                        ),
                        inputs,
                        planSteps,
                    ),
                }),
            ),
            cause: failed
                ? {
                      kind: causeKind(failed.errorCode, failed.capability),
                      code: failed.errorCode ?? "UNKNOWN",
                      subject: subject(
                          planSteps.get(failed.stepId),
                          inputs,
                          planSteps,
                      ),
                  }
                : null,
            steps,
            lookup,
            found: Object.fromEntries(
                (row.ai_found ?? []).map((item) => [
                    item.column,
                    { basis: item.basis, links: item.links },
                ]),
            ),
            concluded: (row.ai_found ?? [])
                .filter((item) => item.basis === "concluded")
                .map((item) => item.column),
        };
    });
    // Sheet order; reports without row numbers keep the order they have.
    return [...executed, ...skipped].sort(
        (a, b) => (a.row ?? 0) - (b.row ?? 0),
    );
}

export type RowFilter =
    | "all"
    | "filled"
    | "partial"
    | "empty"
    | "skipped"
    | "chosen"
    | "automatic"
    | "concluded";
export function filterRows(rows: ResultRow[], filter: RowFilter) {
    if (filter === "filled") return rows.filter((r) => r.filled);
    // A row that was not looked up is not an empty one: its cells have values.
    if (filter === "partial") return rows.filter((r) => r.partial);
    if (filter === "empty")
        return rows.filter((r) => !r.filled && !r.partial && !r.skipped);
    if (filter === "skipped") return rows.filter((r) => r.skipped);
    if (filter === "chosen") return rows.filter((r) => r.chosen.length > 0);
    if (filter === "automatic")
        return rows.filter((r) => r.automatic.length > 0);
    if (filter === "concluded")
        return rows.filter((r) => r.concluded.length > 0);
    return rows;
}
/** What a universal-search run amounts to, in the numbers the table shows. */
export interface LookupSummary {
    /** Rows a request was made for (or would have been: not the skipped ones). */
    asked: number;
    /** Values written from a web search. */
    found: number;
    /** Of those, the values the model concluded instead of a page stating them. */
    concluded: number;
}
export function lookupSummary(rows: ResultRow[]): LookupSummary {
    const asked = rows.filter((row) => !row.skipped);
    const values = asked.flatMap((row) => Object.values(row.found));
    return {
        asked: asked.length,
        found: values.length,
        concluded: values.filter((item) => item.basis === "concluded").length,
    };
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
    /** Rows the run looked at; rows that were not looked up are not among them. */
    total: number;
    /** Rows not looked up because the cell already had a value. */
    skipped: number;
    min?: number;
    max?: number;
    mean?: number;
}
/**
 * Numeric summary only when every filled value of the column is a number.
 * Rows that were not looked up are counted apart: they are neither filled
 * nor failed, and their earlier values are not part of the summary.
 */
export function columnStats(rows: ResultRow[], column: string): ColumnStats {
    const looked = rows.filter((r) => !r.skipped);
    const values = looked
        .map((r) => r.outputs[column])
        .filter((v) => v !== undefined);
    const numbers = values.filter((v) => typeof v === "number");
    const stats: ColumnStats = {
        filled: values.length,
        total: looked.length,
        skipped: rows.length - looked.length,
    };
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
    digits = 2,
) {
    if (value === undefined || value === null) return "—";
    if (typeof value !== "number") return String(value);
    const text = new Intl.NumberFormat(locale, {
        maximumFractionDigits: digits,
    }).format(value);
    return unit ? `${text} ${unit}` : text;
}
/** What a universal-search step returned: one entry per requested column. */
export interface FoundSlot {
    value: Scalar | null;
    basis: "stated" | "concluded" | null;
    sources: string[];
    quote: string | null;
}
/** The answer of a universal-search step, or null for any other value. */
export function foundSlots(value: unknown): Record<string, FoundSlot> | null {
    const slots = (value as { slots?: unknown } | null)?.slots;
    if (!slots || typeof slots !== "object" || Array.isArray(slots))
        return null;
    return slots as Record<string, FoundSlot>;
}

/** Compact, readable form of a step value for the detail layer. */
export function describeValue(value: unknown, locale: string): string {
    if (value === null || value === undefined) return "—";
    if (scalar(value)) return formatValue(value, locale);
    const slots = foundSlots(value);
    if (slots)
        return Object.entries(slots)
            .map(([name, slot]) =>
                slot.value === null || slot.value === undefined
                    ? `${name}: —`
                    : `${name}: ${formatValue(slot.value, locale)}${slot.basis ? ` (${slot.basis})` : ""}`,
            )
            .join(" · ");
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
