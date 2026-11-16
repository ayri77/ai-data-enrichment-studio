// A universal-search draft, run and report as the server writes them, for the
// tests of the model and of the interface. Test data only.
import type { AuditReport, EnrichmentPlan, Job, PlanDraft } from "./api";

export const task = {
    question: "Find when each mountain was first climbed",
    context: [
        ["ctx_1", "Mountain"],
        ["ctx_2", "Country"],
    ],
    columns: [
        { slot: "value_1", header: "Height", kind: "number", unit: "m" },
        { slot: "value_2", header: "First ascent", kind: "text" },
        { slot: "value_3", header: "Climbed in winter", kind: "yes_no" },
    ],
};

export const lookupPlan: EnrichmentPlan = {
    version: "1.0",
    steps: [
        {
            id: "lookup",
            capability: "web_lookup",
            inputs: {
                task: { kind: "literal", value: JSON.stringify(task) },
                ctx_1: { kind: "column", name: "Mountain" },
                ctx_2: { kind: "column", name: "Country" },
            },
            parameters: {},
        },
    ],
    outputs: [
        { step_id: "lookup", value_field: "value_1", target_column: "Height" },
        {
            step_id: "lookup",
            value_field: "value_2",
            target_column: "First ascent",
        },
        {
            step_id: "lookup",
            value_field: "value_3",
            target_column: "Climbed in winter",
        },
    ],
    assumptions: [
        "Each value is found by an AI model with web search, one request per row. The values are not verified; each cell says which pages it rests on.",
    ],
};

export const fallbackDraft: PlanDraft = {
    kind: "fallback",
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:F11",
    instruction: task.question,
    output_columns: {
        Height: "E",
        "First ascent": "F",
        "Climbed in winter": "G",
    },
    mountain_resolution_policy: "strict",
    existing_outputs: "keep",
    sources_in_workbook: "notes",
    plan_id: "plan-fallback",
    fingerprint: "c".repeat(64),
    source_sha256: "b".repeat(64),
    status: "draft",
    approved_at: null,
    planning_status: "ready",
    reason: null,
    validation: { valid: true, errors: [] },
    plan: lookupPlan,
};

export const lookupJob: Job = {
    kind: "fallback",
    job_id: "job-fallback",
    plan_id: "plan-fallback",
    fingerprint: fallbackDraft.fingerprint,
    status: "completed",
    created_at: "2026-11-10T09:00:00Z",
    started_at: "2026-11-10T09:00:01Z",
    finished_at: "2026-11-10T09:00:10Z",
    error: null,
    metrics: {
        total_rows: 3,
        successful_rows: 2,
        partial_rows: 0,
        failed_rows: 1,
    },
    progress: { rows_done: 3, rows_total: 3 },
    artifacts: {
        result: "/api/jobs/job-fallback/artifacts/result",
        report: "/api/jobs/job-fallback/artifacts/report",
    },
    execution_mode: "live",
    timeout_seconds: 180,
};

const found = (
    value: string | number | null,
    basis: "stated" | "concluded" | null,
    sources: string[] = ["https://example.org/page"],
) => ({ value, basis, sources: value === null ? [] : sources, quote: null });
const success = (slots: Record<string, unknown>) => ({
    status: "success" as const,
    value: { slots },
    error_code: null,
    message: null,
    evidence: [
        { source: "openai_web_search", reference: "https://example.org/page" },
    ],
});
const failure = (error_code: string) => ({
    status: "failure" as const,
    value: null,
    error_code,
    message: "Operation could not produce a verified value.",
    evidence: [],
});
const record = (row: number, name: string) => ({
    record_id: `Sheet1!${row}`,
    row,
    inputs: { Mountain: name, Country: "Nepal" },
});

/** Row 2: a stated height and a concluded year; row 3: a height only; row 4: nothing found. */
export const lookupReport: AuditReport = {
    ...lookupJob,
    source_sha256: fallbackDraft.source_sha256,
    web_lookup: {
        configuration_id: "openai-web-lookup-p1-gpt-6-luna-low",
        requests: 3,
        input_tokens: 3000,
        output_tokens: 600,
        web_searches: 6,
    },
    step_provenance: [
        {
            step_id: "lookup",
            capability: "web_lookup",
            source: "openai_web_search",
            trust: "ai_found",
        },
    ],
    rows: [
        {
            ...record(2, "Everest"),
            status: "success",
            ai_found: [
                { column: "Height", basis: "stated", links: 1 },
                { column: "First ascent", basis: "concluded", links: 2 },
                { column: "Climbed in winter", basis: "stated", links: 1 },
            ],
        },
        {
            ...record(3, "K2"),
            status: "partial",
            ai_found: [
                { column: "First ascent", basis: "stated", links: 1 },
                { column: "Climbed in winter", basis: "stated", links: 1 },
            ],
            empty_outputs: {
                Height: { field: "value_1", code: "VALUE_NOT_GIVEN" },
            },
        },
        {
            ...record(4, "Nowhere"),
            status: "failure",
            ai_found: [],
        },
    ],
    execution: {
        provider_requests: {},
        records: [
            {
                record_id: "Sheet1!2",
                row_result: {
                    steps: [
                        {
                            step_id: "lookup",
                            result: success({
                                value_1: found(8849, "stated"),
                                value_2: found("1953", "concluded", [
                                    "https://example.org/page",
                                    "https://example.org/other",
                                ]),
                                value_3: found("No", "stated"),
                            }),
                        },
                    ],
                },
            },
            {
                record_id: "Sheet1!3",
                row_result: {
                    steps: [
                        {
                            step_id: "lookup",
                            result: success({
                                value_1: found(null, null),
                                value_2: found("1954", "stated"),
                                value_3: found("Yes", "stated"),
                            }),
                        },
                    ],
                },
            },
            {
                record_id: "Sheet1!4",
                row_result: {
                    steps: [
                        { step_id: "lookup", result: failure("NOTHING_FOUND") },
                    ],
                },
            },
        ],
    },
};
