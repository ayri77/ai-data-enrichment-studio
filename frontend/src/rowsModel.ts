import type {
    Preview,
    RowCandidates,
    RowSource,
    RowSourceField,
    RowsCondition,
    RowsCount,
    RowsDuplicate,
    RowsPreview,
    RowsRequest,
} from "./api";
import { columnLetter, parseRange } from "./range";

// The second kind of task: adding new rows to the table from a source. Plain
// data in and out. What the server accepts is its own business and its codes
// are shown with the locale text of the code; what is known here (which fields
// take which operators, what a value looks like) is checked before a request.

/** The operators of a field by its kind, in the order they are offered. */
export const OPERATORS = {
    moment: ["within_last_days", "on_or_after", "on_or_before"],
    text: ["contains"],
} as const;
export type Operator = (typeof OPERATORS)[keyof typeof OPERATORS][number];
export const operatorsFor = (kind: RowSourceField["kind"]): readonly string[] =>
    OPERATORS[kind];

/** What a value of an operator is: a whole number of days, a date, or text. */
export type ValueKind = "days" | "date" | "text";
export const valueKind = (op: string): ValueKind =>
    op === "within_last_days"
        ? "days"
        : op === "on_or_after" || op === "on_or_before"
          ? "date"
          : "text";

export const MAX_DAYS = 36500;
export const MAX_CONDITIONS = 16;
export const MAX_TEXT = 200;

/** One condition as it is being typed; the value is always the text of the field. */
export interface ConditionDraft {
    /** Only to keep the rows apart while they are added and removed. */
    id: number;
    field_id: string;
    op: string;
    value: string;
}

const defaultValue = (op: string) => (op === "within_last_days" ? "30" : "");

/** A new condition on the first field of the source. */
export function newCondition(source: RowSource, id: number): ConditionDraft {
    const field = source.fields[0];
    const op = operatorsFor(field.kind)[0];
    return { id, field_id: field.field_id, op, value: defaultValue(op) };
}

/** Another field: its first operator, and the value of that operator. */
export function withField(
    draft: ConditionDraft,
    source: RowSource,
    fieldId: string,
): ConditionDraft {
    const field = source.fields.find((f) => f.field_id === fieldId);
    if (!field || field.field_id === draft.field_id) return draft;
    const op = operatorsFor(field.kind)[0];
    return { ...draft, field_id: field.field_id, op, value: defaultValue(op) };
}

/** Another operator; the value stays where the kind of value is the same. */
export function withOperator(
    draft: ConditionDraft,
    op: string,
): ConditionDraft {
    return valueKind(op) === valueKind(draft.op)
        ? { ...draft, op }
        : { ...draft, op, value: defaultValue(op) };
}

const isDate = (text: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const date = new Date(`${text}T00:00:00Z`);
    return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(text);
};

/** The condition the server takes, or null while the value is not a good one. */
export function conditionOf(draft: ConditionDraft): RowsCondition | null {
    const kind = valueKind(draft.op);
    if (kind === "days") {
        if (!/^\d+$/.test(draft.value)) return null;
        const days = Number(draft.value);
        return days >= 1 && days <= MAX_DAYS
            ? { field_id: draft.field_id, op: draft.op, value: days }
            : null;
    }
    if (kind === "date")
        return isDate(draft.value)
            ? { field_id: draft.field_id, op: draft.op, value: draft.value }
            : null;
    const text = draft.value.trim();
    return text && text.length <= MAX_TEXT
        ? { field_id: draft.field_id, op: draft.op, value: text }
        : null;
}

/** Every condition of the task, or null when one is not complete. */
export function conditionsOf(drafts: ConditionDraft[]): RowsCondition[] | null {
    const found: RowsCondition[] = [];
    for (const draft of drafts) {
        const condition = conditionOf(draft);
        if (!condition) return null;
        found.push(condition);
    }
    return found;
}

// --- The task: which parts are on ------------------------------------------------

/** What the person asked for on the first step. */
export interface RowsTask {
    /** "Fill the rows that are in the table": the columns part. */
    fill: boolean;
    /** "Add new rows from a source": the rows part. */
    add: boolean;
    sourceId: string;
    /** The rows the chosen files gave, held by the server. */
    candidates: RowCandidates | null;
    conditions: ConditionDraft[];
    /** The id the next condition gets. */
    next: number;
}

export const initialTask = (): RowsTask => ({
    fill: true,
    add: false,
    sourceId: "",
    candidates: null,
    conditions: [],
    next: 1,
});

/**
 * "columns": the task as it always was. "rows": only rows are added.
 * "both": not possible in one run yet. "none": nothing is asked for.
 */
export type TaskKind = "columns" | "rows" | "both" | "none";
export const taskKind = (task: Pick<RowsTask, "fill" | "add">): TaskKind =>
    task.add ? (task.fill ? "both" : "rows") : task.fill ? "columns" : "none";

/** The sources the server offers here, in its order. */
export const availableSources = (sources: RowSource[]) =>
    sources.filter((source) => source.available);

/** The source the task is for: the chosen one while it is offered, else the first offered. */
export function chosenSource(
    sources: RowSource[],
    sourceId: string,
): RowSource | null {
    const offered = availableSources(sources);
    return offered.find((s) => s.source_id === sourceId) ?? offered[0] ?? null;
}

export type RowsNeed = "preview" | "part" | "files" | "conditions";
/** What still stops a plan of new rows from being built; empty when nothing does. */
export function rowsNeeds(input: {
    task: RowsTask;
    source: RowSource | null;
    preview: Pick<Preview, "range_ref"> | null;
}): RowsNeed[] {
    if (taskKind(input.task) === "none") return ["part"];
    const needs: RowsNeed[] = [];
    if (!input.preview) needs.push("preview");
    if (!input.source || !input.task.candidates) needs.push("files");
    if (conditionsOf(input.task.conditions) === null) needs.push("conditions");
    return needs;
}

// --- The requests -----------------------------------------------------------------

/** The first request of a plan: nothing is mapped or keyed, the server proposes both. */
export function firstRequest(input: {
    candidatesId: string;
    preview: Pick<Preview, "sheet_name" | "range_ref">;
    conditions: RowsCondition[];
}): RowsRequest {
    return {
        candidates_id: input.candidatesId,
        sheet_name: input.preview.sheet_name,
        range_ref: input.preview.range_ref,
        mapping: null,
        key: null,
        on_duplicate: "skip",
        conditions: input.conditions,
    };
}

/** The plan as it is shown and as it runs: explicit mapping and key. */
export interface RowsPlanShown {
    candidates_id: string;
    sheet_name: string;
    range_ref: string;
    mapping: Record<string, string>;
    key: string[];
    on_duplicate: RowsDuplicate;
    conditions: RowsCondition[];
}

/** The body of a preview or a run for the plan shown: exactly what the person sees. */
export const requestOf = (plan: RowsPlanShown): RowsRequest => ({
    candidates_id: plan.candidates_id,
    sheet_name: plan.sheet_name,
    range_ref: plan.range_ref,
    mapping: plan.mapping,
    key: plan.key,
    on_duplicate: plan.on_duplicate,
    conditions: plan.conditions,
});

/** The plan the server answered, together with the rows it was asked for. */
export function shownPlan(
    candidatesId: string,
    answer: RowsPreview,
): RowsPlanShown {
    const { plan } = answer;
    return {
        candidates_id: candidatesId,
        sheet_name: plan.sheet_name,
        range_ref: plan.range_ref,
        mapping: plan.mapping,
        key: plan.key,
        on_duplicate: plan.on_duplicate,
        conditions: plan.conditions,
    };
}

/** One change of the plan the person can make on the second step. */
export type PlanEdit =
    | { type: "column"; field: string; column: string }
    | { type: "key"; field: string; on: boolean }
    | { type: "duplicate"; value: RowsDuplicate };

/**
 * The plan with one change made. A field without a column leaves the key too:
 * only a field that is written can tell one row from another.
 */
export function editPlan(plan: RowsPlanShown, edit: PlanEdit): RowsPlanShown {
    if (edit.type === "duplicate") return { ...plan, on_duplicate: edit.value };
    if (edit.type === "key") {
        const without = plan.key.filter((id) => id !== edit.field);
        return { ...plan, key: edit.on ? [...without, edit.field] : without };
    }
    const { [edit.field]: _, ...rest } = plan.mapping;
    return {
        ...plan,
        mapping: edit.column ? { ...rest, [edit.field]: edit.column } : rest,
        key: edit.column
            ? plan.key
            : plan.key.filter((id) => id !== edit.field),
    };
}

/** The fields whose column the server proposed (and the person has not changed). */
export const proposedFields = (answer: RowsPreview): string[] =>
    answer.proposed.mapping ? Object.keys(answer.plan.mapping) : [];

/** The columns of the table, each with the letter it has on the sheet: "A · Received". */
export function columnChoices(
    preview: Pick<Preview, "range_ref" | "profile"> | null,
): { letter: string; name: string }[] {
    if (!preview) return [];
    const first = parseRange(preview.range_ref).bounds?.firstColumn ?? 1;
    return preview.profile.columns.map((column, index) => ({
        letter: columnLetter(first + index),
        name: column.name,
    }));
}

// --- The numbers ------------------------------------------------------------------

export type TileId =
    "in_source" | "added" | "duplicates" | "left_out" | "without_key" | "twice";
export interface Tile {
    id: TileId;
    value: number;
}

/**
 * The tiles of the results: three always (in the source, added, duplicates),
 * the others only when their number is not zero.
 */
export function tiles(count: RowsCount): Tile[] {
    const extra: Tile[] = [
        { id: "left_out", value: count.left_out },
        { id: "without_key", value: count.without_key },
        { id: "twice", value: count.duplicates_in_source },
    ];
    return [
        { id: "in_source", value: count.in_source },
        { id: "added", value: count.added },
        { id: "duplicates", value: count.duplicates_in_table },
        ...extra.filter((tile) => tile.value !== 0),
    ];
}

/**
 * The check written out from the parts that are shown: they add up to the
 * rows of the source. `holds` is the server's own statement; the sum is shown
 * only from what is shown, and agrees with it when it is true.
 */
export function sumOf(count: RowsCount) {
    const parts = tiles(count).filter((tile) => tile.id !== "in_source");
    const sum = parts.reduce((total, tile) => total + tile.value, 0);
    return {
        parts,
        total: count.in_source,
        holds: count.holds && sum === count.in_source,
    };
}

/**
 * Whether to tell the person that no row of the table matched. It is a notice,
 * not an error: the table has rows, the source still has something after the
 * conditions, and not one of them is a duplicate in the table. Where matches
 * were expected, the dates of the table are the first thing to look at.
 */
export function noMatchNotice(
    count: RowsCount | null,
    tableRows: number,
): boolean {
    return (
        !!count &&
        tableRows > 0 &&
        count.in_source - count.left_out > 0 &&
        count.duplicates_in_table === 0
    );
}

/** Why the plan cannot run, or null when it can. */
export function runBlock(answer: RowsPreview): "refused" | "nothing" | null {
    if (answer.refused || !answer.count) return "refused";
    return answer.count.written > 0 ? null : "nothing";
}

/** What a second run on the same files would do, from the preview of the new table. */
export type SecondRun =
    | { kind: "none" }
    | { kind: "some"; rows: number }
    | { kind: "marked"; rows: number };
export function secondRun(
    count: RowsCount,
    onDuplicate: RowsDuplicate,
): SecondRun {
    // A duplicate that is added again by design is written again every time.
    if (onDuplicate === "add_marked" && count.written > 0)
        return { kind: "marked", rows: count.written };
    return count.added === 0
        ? { kind: "none" }
        : { kind: "some", rows: count.added };
}

// --- The end of the new table -----------------------------------------------------

export interface EndRow {
    row: number;
    cells: (string | number | boolean | null)[];
    /** Written by this run. */
    added: boolean;
}
/** The last rows of the new table, the written ones marked. */
/**
 * Whether every row the table had stands in the new table with the same values,
 * compared cell by cell as the two previews show them. Null when one of the
 * two is not at hand: then nothing is said about it.
 */
export function oldRowsKept(
    before: Preview | null,
    after: Preview | null,
): { rows: number; same: boolean } | null {
    const was = before?.rows;
    const now = after?.rows;
    if (!was || !now) return null;
    const byRow = new Map(
        now.row_numbers.map((row, index) => [row, now.values[index]]),
    );
    const same = was.row_numbers.every((row, index) => {
        const then = was.values[index];
        const cells = byRow.get(row);
        return (
            !!cells &&
            cells.length === then.length &&
            cells.every((cell, column) => cell === then[column])
        );
    });
    return { rows: was.row_numbers.length, same };
}

export function endOfTable(
    preview: Preview | null,
    written: { first: number | null; last: number | null },
    limit = 8,
): EndRow[] {
    const rows = preview?.rows;
    if (!rows) return [];
    return rows.row_numbers
        .map((row, index) => ({
            row,
            cells: rows.values[index] ?? [],
            added:
                written.first !== null &&
                written.last !== null &&
                row >= written.first &&
                row <= written.last,
        }))
        .slice(-limit);
}

// --- Names ------------------------------------------------------------------------

const SUFFIX = "-rows-added";
/** The file name of a table that rows were added to: the known name with a suffix. */
export function rowsFilename(filename: string | undefined): string {
    const base = (filename ?? "dataset")
        .replace(/\.xlsx$/i, "")
        .replace(new RegExp(`${SUFFIX}$`), "")
        .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-");
    return `${base || "dataset"}${SUFFIX}.xlsx`;
}
