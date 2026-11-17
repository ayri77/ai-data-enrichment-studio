import type {
    EnrichmentPlan,
    FallbackPlanRequest,
    Job,
    LookupKind,
    MixedPlanRequest,
    PlanDraft,
    Preview,
    SourcesInWorkbook,
} from "./api";
import { readPreference, savePreference } from "./preferences";
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
/** The most columns that may identify a row in one request. */
export const MAX_CONTEXT = 30;

const SWITCH_KEY = "fallbackSearch";
/**
 * The person's choice to search the web with AI when the system has no source
 * for a task. Off unless it was switched on in this browser; a storage that
 * cannot be read means off.
 */
export function readFallbackSwitch(): boolean {
    return readPreference(SWITCH_KEY) === "on";
}
export function saveFallbackSwitch(on: boolean) {
    savePreference(SWITCH_KEY, on ? "on" : "off");
}

export const isFallbackDraft = (
    draft: Pick<PlanDraft, "kind"> | null | undefined,
) => draft?.kind === "fallback";

/** A run of the universal search: its job says so, and so does the plan it ran. */
export const isFallbackRun = (
    job: Pick<Job, "kind"> | null | undefined,
    plan: Pick<PlanDraft, "kind"> | null | undefined,
) => job?.kind === "fallback" || plan?.kind === "fallback";

export const isMixedDraft = (
    draft: Pick<PlanDraft, "kind"> | null | undefined,
) => draft?.kind === "mixed";

/** A run of a mixed plan: the planner's steps and one web search. */
export const isMixedRun = (
    job: Pick<Job, "kind"> | null | undefined,
    plan: Pick<PlanDraft, "kind"> | null | undefined,
) => job?.kind === "mixed" || plan?.kind === "mixed";

/** The kind of plan a draft is: the wording of banners follows it. */
export function searchKind(
    draft: Pick<PlanDraft, "kind"> | null | undefined,
): "planner" | "fallback" | "mixed" {
    return draft?.kind === "fallback" || draft?.kind === "mixed"
        ? draft.kind
        : "planner";
}

/**
 * The columns a planner draft leaves unfilled although it can be run: the
 * case in which the page offers a web search for them (or does it at once).
 * Empty for every other draft: one with no plan or a failed check has nothing
 * to build on, and a search or mixed draft has no such columns left.
 */
export function partialColumns(draft: PlanDraft | null | undefined): string[] {
    if (!draft || draft.kind || !draft.plan || !draft.validation.valid)
        return [];
    return draft.unfilled_targets ?? [];
}

/**
 * How the notice for the columns without a source reads. "plain": it says only
 * that they stay as they are (the server cannot search, or the plan cannot be
 * run). "offer": it also has the button and the checkbox for the search.
 * Null: there is nothing to say.
 */
export function unfilledWording(
    draft: PlanDraft | null | undefined,
    webLookup: boolean,
): "plain" | "offer" | null {
    if (!draft || (draft.unfilled_targets ?? []).length === 0) return null;
    return webLookup && partialColumns(draft).length > 0 ? "offer" : "plain";
}

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
 * the same two cases and a plan with columns left unfilled, without the state
 * of the limits. Used to decide when the
 * limits are read again.
 */
export function couldOfferFallback(draft: PlanDraft | null): boolean {
    return (
        fallbackOffer(draft, true) !== null || partialColumns(draft).length > 0
    );
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

/**
 * The form of a search plan that is built without asking: the task as the
 * question, the columns to fill as the request names them (a number where the
 * existing column holds numbers, text otherwise, no unit), and as identifying
 * columns every column of the table that can identify a row and is not
 * filled, in the table's order, up to what one request takes. The links are
 * written as notes on the cells.
 */
export function defaultFallbackForm(
    task: string,
    mapping: OutputTarget[],
    preview: Preview,
): FallbackForm {
    const targets = targetNames(mapping);
    return {
        question: task,
        context: preview.profile.columns
            .filter(
                (column) =>
                    !targets.includes(column.name) &&
                    contextBlocker(column.inferred_type) === null,
            )
            .map((column) => column.name)
            .slice(0, MAX_CONTEXT),
        settings: {},
        sources: "notes",
    };
}

/**
 * The choices a search plan was built from, read back from the plan, to fill
 * the form again. Null when the plan does not say (it is not a one-step search
 * plan), and the caller then starts from the defaults.
 */
export function formFromDraft(draft: PlanDraft): FallbackForm | null {
    const lookup = readLookupTask(draft.plan, draft.output_columns);
    if (!lookup) return null;
    return {
        question: lookup.question,
        context: lookup.context,
        settings: Object.fromEntries(
            lookup.columns.map((column) => [
                column.header,
                { kind: column.kind, unit: column.unit ?? "" },
            ]),
        ),
        sources: draft.sources_in_workbook ?? "none",
    };
}

/** A column as the page names it: its header and its letter in the worksheet. */
export interface ColumnRef {
    name: string;
    letter: string;
}

/**
 * The columns of a mixed plan in its two groups: those the planner's steps
 * fill from the system's sources, and those the web search fills.
 */
export function columnGroups(
    draft: Pick<PlanDraft, "plan" | "output_columns" | "searched_targets">,
): { filled: ColumnRef[]; searched: ColumnRef[] } {
    const searched = draft.searched_targets ?? [];
    const ref = (name: string): ColumnRef => ({
        name,
        letter: draft.output_columns[name] ?? "",
    });
    const filled: string[] = [];
    for (const output of draft.plan?.outputs ?? [])
        if (
            !searched.includes(output.target_column) &&
            !filled.includes(output.target_column)
        )
            filled.push(output.target_column);
    return { filled: filled.map(ref), searched: searched.map(ref) };
}

/**
 * The form of a mixed plan that is built without asking: the task as the
 * question; the columns to search are exactly the ones the planner left
 * unfilled (a number where the existing column holds numbers, text otherwise,
 * no unit); as identifying columns every text or number column of the table
 * that no output of the plan writes, neither searched nor filled by the
 * planner's steps, and that is not empty, in the table's order, up to what one
 * request takes. The links are written as notes on the cells.
 */
export function defaultMixedForm(
    draft: Pick<
        PlanDraft,
        | "instruction"
        | "output_columns"
        | "unfilled_targets"
        | "searched_targets"
    >,
    mapping: OutputTarget[],
    preview: Preview,
): FallbackForm {
    const targets = mixedTargets(draft);
    const written = new Set([...Object.keys(draft.output_columns), ...targets]);
    return {
        question: draft.instruction,
        context: preview.profile.columns
            .filter(
                (column) =>
                    !written.has(column.name) &&
                    contextBlocker(column.inferred_type) === null &&
                    column.null_count < preview.profile.row_count,
            )
            .map((column) => column.name)
            .slice(0, MAX_CONTEXT),
        settings: Object.fromEntries(
            targets.map((name) => [
                name,
                { kind: defaultKind(typeOf(name, mapping, preview)), unit: "" },
            ]),
        ),
        sources: "notes",
    };
}

/** The columns the web search part of a draft is about. */
export function mixedTargets(
    draft: Pick<PlanDraft, "unfilled_targets" | "searched_targets">,
): string[] {
    return (draft.unfilled_targets ?? []).length > 0
        ? draft.unfilled_targets!
        : (draft.searched_targets ?? []);
}

/**
 * The body of the request for a mixed plan: the planner draft it is built
 * from, the form's choices and the columns to search, fixed. Nothing of the
 * table is repeated; the server takes it from the planner draft.
 */
export function buildMixedRequest(input: {
    planId: string;
    targets: string[];
    preview: Preview;
    mapping: OutputTarget[];
    form: FallbackForm;
}): MixedPlanRequest {
    const { form, targets, preview, mapping } = input;
    return {
        plan_id: input.planId,
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
        sources_in_workbook: form.sources,
    };
}

/**
 * The page's choice of output columns reduced to the ones the web search
 * fills, in the order of `targets`. A column the page no longer has an entry
 * for is read from the draft (and counts as new, so its kind starts as text).
 */
export function searchedMapping(
    targets: string[],
    mapping: OutputTarget[],
    outputColumns: Record<string, string>,
): OutputTarget[] {
    return targets.map(
        (name) =>
            mapping.find((entry) => nameOf(entry) === name) ?? {
                mode: "new" as const,
                name,
                column: outputColumns[name] ?? "",
            },
    );
}
