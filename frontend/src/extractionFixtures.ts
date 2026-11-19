// A table built from e-mails, its extraction plan, run and report as the server
// writes them, for the tests of the model and of the interface. Invented data
// only: example.com, and texts that no real mail contains.
import type {
    AuditReport,
    Dataset,
    EnrichmentPlan,
    Job,
    MailDataset,
    PlanDraft,
    Preview,
} from "./api";

const column = (name: string, inferred_type: string, null_count = 0) => ({
    name,
    inferred_type,
    null_count,
    warnings: [],
});

export const mailDataset: MailDataset = {
    dataset_id: "mail-table",
    filename: "mail.xlsx",
    worksheets: [
        {
            name: "Mail",
            columns: ["Received", "From", "Subject", "Text"],
            row_count: 5,
            table: {
                first_row: 1,
                first_column: 1,
                last_row: 6,
                last_column: 4,
            },
        },
    ],
    mail: {
        read: 5,
        skipped: 1,
        truncated: 2,
        masked: { card: 3, phone: 1, me: 4 },
    },
};

export const plainDataset: Dataset = {
    dataset_id: "source",
    filename: "mountains.xlsx",
    worksheets: [{ name: "Sheet1", columns: ["Mountain"], row_count: 3 }],
};

const subjects = [
    "Receipt one",
    "Receipt two",
    "Big sale",
    "Quiet note",
    "Order shipped",
];
export const mailPreview: Preview = {
    dataset_id: "mail-table",
    sheet_name: "Mail",
    range_ref: "A1:D6",
    sample_limit: 5,
    mail: true,
    profile: {
        row_count: 5,
        columns: [
            column("Received", "DateTime"),
            column("From", "Text"),
            column("Subject", "Text"),
            column("Text", "Text"),
        ],
        sample_records: subjects.map((subject, i) => ({
            Received: `2026-11-0${i + 1}T10:00:00`,
            From: "Shop Example",
            Subject: subject,
            Text: `Subject: ${subject}`,
        })),
        schema_warnings: [],
    },
    rows: {
        row_numbers: [2, 3, 4, 5, 6],
        values: subjects.map((subject, i) => [
            `2026-11-0${i + 1}T10:00:00`,
            "Shop Example",
            subject,
            `Subject: ${subject}`,
        ]),
    },
};

export const extractionTask = {
    question: "Payments: what was paid, when, and does it renew?",
    columns: [
        { header: "Amount", kind: "number" },
        { header: "Paid on", kind: "date" },
        { header: "Kind", kind: "one_of", options: ["payment", "renewal"] },
    ],
};

export const extractionPlan: EnrichmentPlan = {
    version: "1.0",
    steps: [
        {
            id: "extract",
            capability: "text_extraction",
            inputs: {
                task: {
                    kind: "literal",
                    value: JSON.stringify(extractionTask),
                },
                text: { kind: "column", name: "Text" },
            },
            parameters: {},
        },
    ],
    outputs: [
        { step_id: "extract", value_field: "value_1", target_column: "Amount" },
        {
            step_id: "extract",
            value_field: "value_2",
            target_column: "Paid on",
        },
        { step_id: "extract", value_field: "value_3", target_column: "Kind" },
    ],
    assumptions: [
        "Each value is read by an AI model from the text of its own row, one request per row, and kept only where the passage it quotes is in that text. The values are not verified against anything else.",
    ],
};

export const extractionDraft: PlanDraft = {
    kind: "extraction",
    text_column_emptied: true,
    dataset_id: "mail-table",
    sheet_name: "Mail",
    range_ref: "A1:D6",
    instruction: extractionTask.question,
    output_columns: { Amount: "E", "Paid on": "F", Kind: "G" },
    mountain_resolution_policy: "strict",
    existing_outputs: "keep",
    sources_in_workbook: "notes",
    plan_id: "plan-extraction",
    fingerprint: "d".repeat(64),
    source_sha256: "e".repeat(64),
    status: "draft",
    approved_at: null,
    planning_status: "ready",
    reason: null,
    unfilled_targets: [],
    validation: { valid: true, contract_valid: true, errors: [] },
    plan: extractionPlan,
};

/** Five mails: two found (one with a single value), one not about the task, two with nothing. */
export const extractionJob: Job = {
    kind: "extraction",
    job_id: "job-extraction",
    plan_id: "plan-extraction",
    fingerprint: extractionDraft.fingerprint,
    status: "needs_review",
    created_at: "2026-11-19T09:00:00Z",
    started_at: "2026-11-19T09:00:01Z",
    finished_at: "2026-11-19T09:00:20Z",
    error: null,
    // The server counts the mail that is not about the task among the failed rows.
    metrics: {
        total_rows: 5,
        successful_rows: 2,
        partial_rows: 0,
        failed_rows: 3,
    },
    progress: {
        rows_done: 5,
        rows_total: 5,
        steps: {
            extract: { filled: 2, failed: 2, skipped: 0, not_applicable: 1 },
        },
    },
    artifacts: {
        result: "/api/jobs/job-extraction/artifacts/result",
        report: "/api/jobs/job-extraction/artifacts/report",
    },
    execution_mode: "live",
    timeout_seconds: 300,
    rows_skipped: 0,
    rows_not_applicable: 1,
};

const kept = (
    value: string | number | null,
    basis: "literal" | "derived" | null,
    quote: string | null,
) => ({ value, basis, sources: [], quote, unit: null });
const none = kept(null, null, null);
const success = (slots: Record<string, unknown>) => ({
    status: "success" as const,
    value: { slots },
    error_code: null,
    message: null,
    evidence: [],
});
const failure = (error_code: string) => ({
    status: "failure" as const,
    value: null,
    error_code,
    message: "Operation could not produce a verified value.",
    evidence: [],
});
const record = (row: number) => ({
    record_id: `Mail!${row}`,
    row,
    inputs: { Text: "[text not kept: 175 characters]" },
});

export const extractionReport: AuditReport = {
    ...extractionJob,
    source_sha256: extractionDraft.source_sha256,
    text_extraction: {
        configuration_id: "openai-text-extraction-p1-gpt-6-luna-low",
        requests: 5,
        input_tokens: 3500,
        output_tokens: 450,
        answers_kept: 0,
        rows: { found: 2, nothing_found: 2, not_applicable: 1, failed: 0 },
        values_kept: { literal: 3, derived: 1 },
        values_dropped: {},
        text_column_emptied: true,
    },
    step_provenance: [
        {
            step_id: "extract",
            capability: "text_extraction",
            source: "openai_text_extraction",
            trust: "extracted",
        },
    ],
    rows: [
        {
            ...record(2),
            status: "success",
            extracted: [
                {
                    column: "Amount",
                    basis: "literal",
                    quote: "Total: 9,99 EUR",
                },
                {
                    column: "Paid on",
                    basis: "literal",
                    quote: "Paid on 3 Nov 2026",
                },
                {
                    column: "Kind",
                    basis: "derived",
                    quote: "Thank you for paying",
                },
            ],
        },
        {
            ...record(3),
            status: "success",
            extracted: [
                {
                    column: "Amount",
                    basis: "literal",
                    quote: "Total: 20,00 EUR",
                },
            ],
            empty_outputs: {
                "Paid on": { field: "value_2", code: "VALUE_NOT_GIVEN" },
                Kind: { field: "value_3", code: "VALUE_NOT_GIVEN" },
            },
        },
        { ...record(4), status: "failure", extracted: [] },
        { ...record(5), status: "failure", extracted: [] },
        { ...record(6), status: "failure", extracted: [] },
    ],
    execution: {
        provider_requests: {},
        records: [
            {
                record_id: "Mail!2",
                row_result: {
                    steps: [
                        {
                            step_id: "extract",
                            result: success({
                                value_1: kept(
                                    9.99,
                                    "literal",
                                    "Total: 9,99 EUR",
                                ),
                                value_2: kept(
                                    "2026-11-03",
                                    "literal",
                                    "Paid on 3 Nov 2026",
                                ),
                                value_3: kept(
                                    "payment",
                                    "derived",
                                    "Thank you for paying",
                                ),
                            }),
                        },
                    ],
                },
            },
            {
                record_id: "Mail!3",
                row_result: {
                    steps: [
                        {
                            step_id: "extract",
                            result: success({
                                value_1: kept(
                                    20,
                                    "literal",
                                    "Total: 20,00 EUR",
                                ),
                                value_2: none,
                                value_3: none,
                            }),
                        },
                    ],
                },
            },
            {
                record_id: "Mail!4",
                row_result: {
                    steps: [
                        {
                            step_id: "extract",
                            result: failure("NOT_APPLICABLE"),
                        },
                    ],
                },
            },
            {
                record_id: "Mail!5",
                row_result: {
                    steps: [
                        {
                            step_id: "extract",
                            result: failure("NOTHING_FOUND"),
                        },
                    ],
                },
            },
            {
                record_id: "Mail!6",
                row_result: {
                    steps: [
                        {
                            step_id: "extract",
                            result: failure("NOTHING_FOUND"),
                        },
                    ],
                },
            },
        ],
    },
};
