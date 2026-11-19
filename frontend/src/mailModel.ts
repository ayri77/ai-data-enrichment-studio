import {
    WorkspaceApiError,
    type AuditReport,
    type EnrichmentPlan,
    type ExtractionKind,
    type ExtractionPlanRequest,
    type Job,
    type PlanDraft,
    type Preview,
} from "./api";
import { storedZip, storedZipSize } from "./zip";

// Tables built from e-mails and values read out of their texts. Plain data in
// and out: what the server accepts is its own business and its message is
// shown as it comes; the bounds that are known here are checked before an
// upload or a request, with the wording of the server's own refusals.

/** The capability id of the extraction; the server's list never names it. */
export const EXTRACTION_CAPABILITY = "text_extraction";
/** The column of a table of e-mails that holds the text of each mail. */
export const MAIL_TEXT_COLUMN = "Text";
/** The columns the server gives a table of e-mails, in its order. */
export const MAIL_COLUMNS = ["Received", "From", "Subject", MAIL_TEXT_COLUMN];

// The server's bounds for an upload of e-mails (mail/upload.py).
export const MAX_MAILS = 500;
export const MAX_MAIL_BYTES = 10 * 1024 * 1024;
export const MAX_MAIL_UPLOAD_BYTES = 32 * 1024 * 1024;
// The server's bounds for an extraction (providers/text_extraction.py).
export const MAX_EXTRACTION_COLUMNS = 20;
export const MAX_EXTRACTION_QUESTION = 1000;
export const MAX_EXTRACTION_HEADER = 100;
export const MAX_EXTRACTION_OPTIONS = 20;
export const MAX_EXTRACTION_OPTION = 60;

export const EXTRACTION_KINDS: ExtractionKind[] = [
    "text",
    "number",
    "date",
    "yes_no",
    "one_of",
];

/** A table the application built from e-mails: it has no planner and no web search. */
export const isMailTable = (
    preview: Pick<Preview, "mail"> | null | undefined,
) => preview?.mail === true;

export const isExtractionDraft = (
    draft: Pick<PlanDraft, "kind"> | null | undefined,
) => draft?.kind === "extraction";

/** A run that reads values out of texts: its job says so, and so does the plan it ran. */
export const isExtractionRun = (
    job: Pick<Job, "kind"> | null | undefined,
    plan: Pick<PlanDraft, "kind"> | null | undefined,
) => job?.kind === "extraction" || plan?.kind === "extraction";

// --- Choosing the e-mails ---------------------------------------------------------

const extensionOf = (file: Pick<File, "name">) =>
    file.name.toLowerCase().slice(file.name.lastIndexOf(".") + 1);

/**
 * What is sent for the chosen files, or a refusal with the code the server
 * would give. Several `.eml` files (or one) are packed into one stored zip in
 * memory under neutral entry names (`0001.eml`, ...): the names of the
 * person's files are not in what is sent. One `.zip` is sent as it is.
 */
export async function packMails(files: File[]): Promise<Blob> {
    const refuse = (code: string, message: string, status?: number) =>
        new WorkspaceApiError(code, message, status);
    const zips = files.filter((file) => extensionOf(file) === "zip");
    const mails = files.filter((file) => extensionOf(file) === "eml");
    const archive = files.length === 1 && zips.length === 1;
    if (!files.length || (!archive && mails.length !== files.length))
        throw refuse(
            "invalid_mail_file_type",
            "Upload one .zip of .eml files, or one .eml file.",
        );
    if (archive) {
        if (files[0].size > MAX_MAIL_UPLOAD_BYTES)
            throw refuse("mail_upload_too_large", "Upload up to 32 MiB.", 413);
        return files[0];
    }
    if (mails.length > MAX_MAILS)
        throw refuse(
            "mail_too_many",
            "Upload at most 500 .eml files in one .zip.",
            422,
        );
    if (mails.some((file) => file.size > MAX_MAIL_BYTES))
        throw refuse(
            "mail_entry_too_large",
            "A mail in the upload is larger than 10 MiB.",
            413,
        );
    // The size of the archive is known from the sizes of the files, before any
    // of them is read into memory.
    const names = mails.map((_, i) => entryName(i));
    if (
        storedZipSize(
            mails.map((file, i) => ({
                size: file.size,
                nameLength: names[i].length,
            })),
        ) > MAX_MAIL_UPLOAD_BYTES
    )
        throw refuse("mail_upload_too_large", "Upload up to 32 MiB.", 413);
    const entries = await Promise.all(
        mails.map(async (file, i) => ({
            name: names[i],
            data: await readBytes(file),
        })),
    );
    return new Blob([storedZip(entries) as BlobPart], {
        type: "application/zip",
    });
}

/** The bytes of a file; `FileReader` where `Blob.arrayBuffer` is missing (older engines, jsdom). */
export const readBytes = (file: Blob): Promise<Uint8Array> =>
    typeof file.arrayBuffer === "function"
        ? file.arrayBuffer().then((buffer) => new Uint8Array(buffer))
        : new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () =>
                  resolve(new Uint8Array(reader.result as ArrayBuffer));
              reader.onerror = () => reject(reader.error);
              reader.readAsArrayBuffer(file);
          });

/** `0001.eml`, `0002.eml`, ...: the position, never anything about the file. */
export const entryName = (index: number) =>
    `${String(index + 1).padStart(4, "0")}.eml`;

// --- The form: what to read out of each mail -------------------------------------

/** One column to fill, as the person is typing it. */
export interface ExtractionColumnDraft {
    /** Only to keep the rows apart while they are added and removed. */
    id: number;
    name: string;
    kind: ExtractionKind;
    /** For a choice: the options with commas between them, as typed. */
    options: string;
}

export const blankColumn = (id: number): ExtractionColumnDraft => ({
    id,
    name: "",
    kind: "text",
    options: "",
});

/** Line breaks (and runs of blanks around them) become one space; the server refuses control characters. */
export const oneLine = (text: string) =>
    text.replace(/\s*[\r\n\u2028\u2029]+\s*/g, " ");

const hasControl = (text: string) =>
    /[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/.test(text);

/** The options of a choice: split at commas, trimmed, empty ones dropped. */
export const parseOptions = (typed: string) =>
    typed
        .split(",")
        .map((option) => option.trim())
        .filter((option) => option);

export type ColumnProblem =
    | "blank"
    | "long"
    | "control"
    | "taken"
    | "existing"
    | "optionCount"
    | "optionsSame"
    | "optionLong"
    | "optionControl";

/** What is wrong with one column; null when nothing is. */
export function columnProblem(
    column: ExtractionColumnDraft,
    all: ExtractionColumnDraft[],
    tableColumns: readonly string[] = MAIL_COLUMNS,
): ColumnProblem | null {
    const name = column.name.trim();
    if (!name) return "blank";
    if (name.length > MAX_EXTRACTION_HEADER) return "long";
    if (hasControl(name)) return "control";
    const key = name.toLowerCase();
    if (tableColumns.some((existing) => existing.toLowerCase() === key))
        return "existing";
    if (
        all.some(
            (other) =>
                other.id !== column.id &&
                other.name.trim().toLowerCase() === key,
        )
    )
        return "taken";
    if (column.kind === "one_of") {
        const options = parseOptions(column.options);
        if (options.some(hasControl)) return "optionControl";
        if (options.some((option) => option.length > MAX_EXTRACTION_OPTION))
            return "optionLong";
        if (
            new Set(options.map((option) => option.toLowerCase())).size !==
            options.length
        )
            return "optionsSame";
        if (options.length < 2 || options.length > MAX_EXTRACTION_OPTIONS)
            return "optionCount";
    }
    return null;
}

export type FormProblem = "question" | "columns";
/** What still stops the form from being sent; empty when nothing does. */
export function extractionProblems(
    question: string,
    columns: ExtractionColumnDraft[],
    tableColumns: readonly string[] = MAIL_COLUMNS,
): FormProblem[] {
    const problems: FormProblem[] = [];
    const asked = oneLine(question).trim();
    if (!asked || asked.length > MAX_EXTRACTION_QUESTION || hasControl(asked))
        problems.push("question");
    if (
        columns.length < 1 ||
        columns.length > MAX_EXTRACTION_COLUMNS ||
        columns.some((column) => columnProblem(column, columns, tableColumns))
    )
        problems.push("columns");
    return problems;
}

/**
 * The body of the request, from the page's state. The text column is the
 * one a table of e-mails has; a choice carries its options, no other kind
 * does. The quotes are written as notes on the cells of the result.
 */
export function buildExtractionRequest(input: {
    preview: Preview;
    /** The rows this run takes; the whole selection when absent. */
    range?: string;
    question: string;
    columns: ExtractionColumnDraft[];
}): ExtractionPlanRequest {
    const { preview, columns } = input;
    return {
        dataset_id: preview.dataset_id,
        question: oneLine(input.question).trim(),
        text_column: MAIL_TEXT_COLUMN,
        targets: columns.map((column) => ({
            column: column.name.trim(),
            kind: column.kind,
            ...(column.kind === "one_of"
                ? { options: parseOptions(column.options) }
                : {}),
        })),
        sheet_name: preview.sheet_name,
        range_ref: input.range ?? preview.range_ref,
        sources_in_workbook: "notes",
    };
}

// --- The plan, read back ---------------------------------------------------------

/** What an extraction plan asks, read back from the plan itself. */
export interface ExtractionView {
    question: string;
    columns: {
        header: string;
        kind: ExtractionKind;
        options: string[];
        /** The worksheet column it is written to, when the draft says. */
        letter: string | null;
    }[];
}

const isKind = (value: unknown): value is ExtractionKind =>
    EXTRACTION_KINDS.includes(value as ExtractionKind);

/**
 * The task of a one-step extraction plan: the plan carries it as a literal
 * text, so the approved plan alone says what is asked. Anything that is not
 * exactly that returns null and the plan is described like any other.
 */
export function readExtractionTask(
    plan: EnrichmentPlan | null,
    outputColumns: Record<string, string> = {},
): ExtractionView | null {
    const step = plan?.steps.find(
        (s) => s.capability === EXTRACTION_CAPABILITY,
    );
    const literal = step?.inputs?.task as { value?: unknown } | undefined;
    if (!literal || typeof literal.value !== "string") return null;
    let task: unknown;
    try {
        task = JSON.parse(literal.value);
    } catch {
        return null;
    }
    if (!task || typeof task !== "object") return null;
    const { question, columns } = task as Record<string, unknown>;
    if (typeof question !== "string" || !Array.isArray(columns)) return null;
    const read: ExtractionView["columns"] = [];
    for (const column of columns) {
        const item = column as Record<string, unknown> | null;
        if (
            !item ||
            typeof item.header !== "string" ||
            !isKind(item.kind) ||
            (item.options !== undefined &&
                (!Array.isArray(item.options) ||
                    item.options.some((option) => typeof option !== "string")))
        )
            return null;
        read.push({
            header: item.header,
            kind: item.kind,
            options: (item.options as string[] | undefined) ?? [],
            letter: outputColumns[item.header] ?? null,
        });
    }
    return { question, columns: read };
}

// --- What a run of it amounts to -------------------------------------------------

/** The rows of a finished extraction run by what became of their text. */
export interface ExtractionCounts {
    total: number;
    /** At least one value was read and kept. */
    found: number;
    /** The text is not about the task: settled, nothing is written. */
    notApplicable: number;
    /**
     * About the task, but no value could be read in the text, or the request
     * failed. The two are told apart once the report has arrived.
     */
    unread: number;
    nothingFound: number | null;
    failed: number | null;
}

/**
 * The numbers of a finished run, from the report when it has arrived (it
 * tells the rows apart) and from the job's own counters until then. The job
 * counts a row that is not about the task as not successful; here it is its
 * own number.
 */
export function extractionCounts(
    job: Job,
    report: AuditReport | null,
): ExtractionCounts {
    const rows =
        report?.job_id === job.job_id
            ? report.text_extraction?.rows
            : undefined;
    if (rows)
        return {
            total:
                rows.found +
                rows.nothing_found +
                rows.not_applicable +
                rows.failed,
            found: rows.found,
            notApplicable: rows.not_applicable,
            unread: rows.nothing_found + rows.failed,
            nothingFound: rows.nothing_found,
            failed: rows.failed,
        };
    const total = job.metrics?.total_rows ?? 0;
    const found = job.metrics?.successful_rows ?? 0;
    const notApplicable = job.rows_not_applicable ?? 0;
    return {
        total,
        found,
        notApplicable,
        unread: Math.max(0, total - found - notApplicable),
        nothingFound: null,
        failed: null,
    };
}

// --- Which mail a row of the results is -------------------------------------------

/** What tells one mail from another in the results: never its text. */
export const MAIL_IDENTITY = ["Received", "From", "Subject"] as const;

/**
 * Received (the date only), From and Subject of each mail the browser holds,
 * by worksheet row, taken from the preview of the table (it holds every row of
 * the selection). A row the preview does not hold is simply absent.
 */
export function mailIdentities(
    preview: Preview | null | undefined,
): Map<number, Record<string, string>> {
    const found = new Map<number, Record<string, string>>();
    if (!isMailTable(preview) || !preview?.rows) return found;
    const at = MAIL_IDENTITY.map((name) =>
        preview.profile.columns.findIndex((column) => column.name === name),
    );
    preview.rows.row_numbers.forEach((row, i) => {
        const cells = preview.rows!.values[i] ?? [];
        const identity: Record<string, string> = {};
        MAIL_IDENTITY.forEach((name, k) => {
            const value = at[k] < 0 ? null : cells[at[k]];
            if (value === null || value === undefined || value === "") return;
            const text = String(value);
            identity[name] = name === "Received" ? text.slice(0, 10) : text;
        });
        found.set(row, identity);
    });
    return found;
}
