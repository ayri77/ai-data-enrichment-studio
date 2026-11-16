import type {
    EnrichmentPlan,
    FallbackPlanRequest,
    Job,
    LookupKind,
    PlanDraft,
    Preview,
    SourcesInWorkbook,
} from "./api";
import type { OutputTarget } from "./workflowModel";

// The universal search: a plan the server builds from a question, the columns
// that identify a row and the columns to fill. Everything here is plain data
// in and out; what the server accepts is its own business and its message is
// shown as it comes.

/** The capability id of the universal search; the server's list never names it. */
export const LOOKUP_CAPABILITY = "web_lookup";
/** The longest unit the server takes. */
export const MAX_UNIT = 30;
/** The longest question a plan request takes. */
export const MAX_QUESTION = 4000;

export const isFallbackDraft = (
    draft: Pick<PlanDraft, "kind"> | null | undefined,
) => draft?.kind === "fallback";

/** A run of the universal search: its job says so, and so does the plan it ran. */
export const isFallbackRun = (
    job: Pick<Job, "kind"> | null | undefined,
    plan: Pick<PlanDraft, "kind"> | null | undefined,
) => job?.kind === "fallback" || plan?.kind === "fallback";

/** The two situations in which the page offers the search. */
export type FallbackOffer = "refused" | "invalid";

/**
 * Whether, and why, the page offers the search. Two cases, both only for a
 * planner draft (never for a draft of the search itself) and only on a server
 * that has it switched on:
 * - "refused": the planner built no plan and said "unsupported" or "ambiguous";
 * - "invalid": the planner did hand over a plan, but the validator rejected it
 *   (for instance one with no steps), so nothing can be run.
 * A plan that passed the check is never replaced by an offer.
 */
export function fallbackOffer(
    draft: PlanDraft | null,
    webLookup: boolean,
): FallbackOffer | null {
    if (!webLookup || !draft || draft.kind) return null;
    if (!draft.plan) {
        return draft.planning_status === "unsupported" ||
            draft.planning_status === "ambiguous"
            ? "refused"
            : null;
    }
    return draft.validation.valid ? null : "invalid";
}

/** Whether the page offers the search, in either of the cases above. */
export function offersFallback(
    draft: PlanDraft | null,
    webLookup: boolean,
): boolean {
    return fallbackOffer(draft, webLookup) !== null;
}

/**
 * Whether the draft is one where the offer may appear once the server says so:
 * the same two cases, without the state of the limits. Used to decide when the
 * limits are read again.
 */
export function couldOfferFallback(draft: PlanDraft | null): boolean {
    return fallbackOffer(draft, true) !== null;
}

/** Why a column cannot identify a row; null when it can. */
export type ContextBlocker = "date" | "boolean" | "mixed" | "empty" | "other";
export function contextBlocker(inferredType: string): ContextBlocker | null {
    switch (inferredType) {
        case "Text":
        case "Integer":
        case "Number":
            return null;
        case "Date":
        case "DateTime":
            return "date";
        case "Boolean":
            return "boolean";
        case "Mixed":
            return "mixed";
        case "Unknown":
            return "empty";
        default:
            return "other";
    }
}

/** Number where the existing column holds numbers, Text otherwise. */
export function defaultKind(inferredType: string | undefined): LookupKind {
    return inferredType === "Integer" || inferredType === "Number"
        ? "number"
        : "text";
}

export interface TargetSetting {
    kind: LookupKind;
    unit: string;
}
export interface FallbackForm {
    question: string;
    /** Headers of the columns that identify a row, as chosen. */
    context: string[];
    /** By the header of the column to fill. A missing entry means the default. */
    settings: Record<string, TargetSetting>;
    sources: SourcesInWorkbook;
}

const nameOf = (entry: OutputTarget) =>
    entry.mode === "existing" ? entry.name : entry.name.trim();

/** The columns to fill, as the failed plan request named them. */
export function targetNames(mapping: OutputTarget[]): string[] {
    return mapping.map(nameOf).filter((name) => name);
}

/** What kind of values the existing column holds; undefined for a new column. */
function typeOf(name: string, mapping: OutputTarget[], preview: Preview) {
    const entry = mapping.find((item) => nameOf(item) === name);
    if (!entry || entry.mode !== "existing") return undefined;
    return preview.profile.columns.find((column) => column.name === name)
        ?.inferred_type;
}

export function settingFor(
    form: Pick<FallbackForm, "settings">,
    name: string,
    mapping: OutputTarget[],
    preview: Preview,
): TargetSetting {
    return (
        form.settings[name] ?? {
            kind: defaultKind(typeOf(name, mapping, preview)),
            unit: "",
        }
    );
}

export type FormProblem = "question" | "context" | "targets" | "unit";
/** What still stops the form from being sent; empty when nothing does. */
export function formProblems(
    form: FallbackForm,
    mapping: OutputTarget[],
    preview: Preview,
): FormProblem[] {
    const problems: FormProblem[] = [];
    if (!form.question.trim() || form.question.length > MAX_QUESTION)
        problems.push("question");
    const targets = targetNames(mapping);
    if (!targets.length) problems.push("targets");
    // The columns to fill never identify a row, and the form does not offer them.
    const chosen = form.context.filter((name) => !targets.includes(name));
    if (!chosen.length) problems.push("context");
    if (
        targets.some((name) => {
            const setting = settingFor(form, name, mapping, preview);
            return (
                setting.kind === "number" &&
                setting.unit.trim().length > MAX_UNIT
            );
        })
    )
        problems.push("unit");
    return problems;
}

/**
 * The body of the request, from the page's state. The table's columns keep
 * their own order whatever order they were ticked in; a unit is sent only for
 * a number and only when it is not blank.
 */
export function buildFallbackRequest(input: {
    preview: Preview;
    /** The rows this run takes; the whole selection when absent. */
    range?: string;
    mapping: OutputTarget[];
    existingOutputs: "keep" | "replace";
    form: FallbackForm;
}): FallbackPlanRequest {
    const { preview, mapping, form } = input;
    const targets = targetNames(mapping);
    return {
        dataset_id: preview.dataset_id,
        sheet_name: preview.sheet_name,
        range_ref: input.range ?? preview.range_ref,
        question: form.question.trim(),
        context_columns: preview.profile.columns
            .map((column) => column.name)
            .filter(
                (name) =>
                    form.context.includes(name) && !targets.includes(name),
            ),
        targets: targets.map((name) => {
            const setting = settingFor(form, name, mapping, preview);
            const unit = setting.unit.trim();
            return {
                column: name,
                kind: setting.kind,
                ...(setting.kind === "number" && unit ? { unit } : {}),
            };
        }),
        output_columns: Object.fromEntries(
            mapping.map((entry) => [nameOf(entry), entry.column]),
        ),
        existing_outputs: input.existingOutputs,
        sources_in_workbook: form.sources,
    };
}

/** What a fallback plan asks, read back from the plan itself. */
export interface LookupView {
    question: string;
    /** Headers of the columns that identify a row. */
    context: string[];
    columns: {
        header: string;
        kind: LookupKind;
        unit: string | null;
        /** The worksheet column it is written to, when the draft says. */
        letter: string | null;
    }[];
}

const isKind = (value: unknown): value is LookupKind =>
    value === "text" || value === "number" || value === "yes_no";

/**
 * The task of a one-step fallback plan: the plan carries it as a literal text,
 * so the approved plan alone says what is asked. Anything that is not exactly
 * that returns null and the plan is described like any other.
 */
export function readLookupTask(
    plan: EnrichmentPlan | null,
    outputColumns: Record<string, string> = {},
): LookupView | null {
    const step = plan?.steps.find((s) => s.capability === LOOKUP_CAPABILITY);
    const literal = step?.inputs?.task;
    if (
        !literal ||
        typeof literal !== "object" ||
        typeof (literal as { value?: unknown }).value !== "string"
    )
        return null;
    let task: unknown;
    try {
        task = JSON.parse((literal as { value: string }).value);
    } catch {
        return null;
    }
    if (!task || typeof task !== "object") return null;
    const { question, context, columns } = task as Record<string, unknown>;
    if (
        typeof question !== "string" ||
        !Array.isArray(context) ||
        !Array.isArray(columns)
    )
        return null;
    const headers: string[] = [];
    for (const entry of context) {
        if (!Array.isArray(entry) || typeof entry[1] !== "string") return null;
        headers.push(entry[1]);
    }
    const read: LookupView["columns"] = [];
    for (const column of columns) {
        const item = column as Record<string, unknown> | null;
        if (
            !item ||
            typeof item.header !== "string" ||
            !isKind(item.kind) ||
            (item.unit !== undefined && typeof item.unit !== "string")
        )
            return null;
        read.push({
            header: item.header,
            kind: item.kind,
            unit: typeof item.unit === "string" ? item.unit : null,
            letter: outputColumns[item.header] ?? null,
        });
    }
    return { question, context: headers, columns: read };
}
