export interface Worksheet {
    name: string;
    columns: string[];
    row_count: number;
    /** Where the table most likely is; absent when nothing looks like one. */
    table?: {
        first_row: number;
        first_column: number;
        last_row: number;
        last_column: number;
    } | null;
}
export interface Dataset {
    dataset_id: string;
    filename: string;
    worksheets: Worksheet[];
}
export interface Preview {
    dataset_id: string;
    sheet_name: string;
    range_ref: string;
    sample_limit: number;
    profile: {
        row_count: number;
        columns: {
            name: string;
            inferred_type: string;
            null_count: number;
            warnings: string[];
        }[];
        sample_records: Record<string, string | number | boolean | null>[];
        schema_warnings: string[];
    };
    /** Every non-empty data row of the selection, with its worksheet row. */
    rows?: {
        row_numbers: number[];
        values: (string | number | boolean | null)[][];
    };
}
/** Retain the backend's stable code; presentation decides how to localize it. */
export class WorkspaceApiError extends Error {
    constructor(
        public readonly code: string,
        message: string,
        public readonly status?: number,
    ) {
        super(message);
        this.name = "WorkspaceApiError";
    }
}
// A hosted demo protects its API with a shared access code. It is held in
// memory for the page and sent with every request; locally it stays empty.
let accessCode = "";
export function setAccessCode(code: string) {
    accessCode = code;
}
/** A bundled example: a public workbook, its measured instruction and output. */
export interface Sample {
    sample_id: string;
    filename: string;
    instruction: string;
    /** Every column the example fills, in the order of its instruction. */
    outputs: { name: string; column: string }[];
    /** A prepared plan exists, so the example also works without the AI provider. */
    has_plan: boolean;
}
/** One value a capability takes or is set up with, as its contract states it. */
export interface CapabilityField {
    type: string;
    required: boolean;
    description: string;
    /** The only values a parameter accepts, where it is restricted to some. */
    allowed_values?: string[] | null;
}
/** One kind of step a plan may be built from on this server. */
export interface Capability {
    id: string;
    version: string;
    /** The description the planning model is shown, in English. */
    description: string;
    /** Where the values come from: a source id, or "local" for a calculation. */
    source: string;
    /**
     * What stands behind the values: "computed", "record", "resolved_name",
     * "service" or "ai_found". A later server may say more; absent from an older one.
     */
    trust?: string;
    inputs: Record<string, CapabilityField>;
    parameters: Record<string, CapabilityField>;
    output: {
        type: string;
        description: string;
        fields: Record<string, string>;
    };
}
export interface AccessState {
    /** True on a hosted deployment that asks for a code. */
    required: boolean;
    /** True when no code is needed or the one sent with the request is right. */
    granted: boolean;
}
/** What one visitor of a hosted demo used, as the server counted it. */
export interface VisitorUsage {
    /** The label of the visitor's access code. */
    who: string;
    first_seen: string | null;
    last_seen: string | null;
    visits: number;
    uploads: number;
    examples: number;
    plans: number;
    plans_failed: number;
    runs: number;
    rows: number;
    rows_filled: number;
    downloads: number;
    model_requests: number;
    input_tokens: number;
    output_tokens: number;
    /** Requests that left for each source during the visitor's runs. */
    source_requests: Record<string, number>;
}
/** Counted events: numbers and fixed words only. */
export interface UsageSummary {
    /** When this server process started (also `process_started_at`). */
    started_at: string;
    process_started_at: string;
    now: string;
    /** The first counted event; after a restart, older than the process. */
    since: string | null;
    /** True when the counts are kept in a file across restarts. */
    persistent: boolean;
    /** Why they are not kept: switched off, or the file could not be written. */
    reason?: "off" | "write_failed";
    /** Lines of the usage file that could not be read and were skipped. */
    skipped_lines: number;
    events: Record<string, number>;
    visitors: VisitorUsage[];
    recent: ({ at: string; who: string; kind: string } & Record<
        string,
        unknown
    >)[];
    /** Every label a code exists for, used or not. */
    labels: string[];
}
function call(input: string, init?: RequestInit) {
    if (!accessCode)
        return init === undefined ? fetch(input) : fetch(input, init);
    return fetch(input, {
        ...init,
        headers: {
            ...(init?.headers as Record<string, string> | undefined),
            "X-Access-Code": accessCode,
        },
    });
}
async function checked(request: Promise<Response>): Promise<Response> {
    let result: Response;
    try {
        result = await request;
    } catch {
        throw new WorkspaceApiError(
            "network_error",
            "Cannot reach the local API. Start the backend and try again.",
        );
    }
    if (!result.ok) {
        const body = await result.json().catch(() => null);
        throw new WorkspaceApiError(
            typeof body?.error?.code === "string"
                ? body.error.code
                : "request_failed",
            body?.error?.message ??
                "The request could not be completed. Please try again.",
            result.status,
        );
    }
    return result;
}
async function response<T>(request: Promise<Response>): Promise<T> {
    return (await checked(request)).json() as Promise<T>;
}
function post<T>(path: string, body?: unknown) {
    return response<T>(
        call(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        }),
    );
}

export interface EnrichmentPlan {
    version: string;
    steps: {
        id: string;
        capability: string;
        inputs: Record<string, unknown>;
        parameters: Record<string, unknown>;
    }[];
    outputs: {
        step_id: string;
        value_field: string;
        target_column: string;
        expected_unit?: string;
    }[];
    assumptions: string[];
}
export interface Validation {
    valid: boolean;
    contract_valid?: boolean;
    errors: { code: string; message: string; step_id?: string | null }[];
}
/** Nothing, a note on each written cell, or an added worksheet. */
export type SourcesInWorkbook = "none" | "notes" | "sheet";
export interface PlanRequest {
    dataset_id: string;
    sheet_name: string;
    range_ref: string;
    instruction: string;
    output_columns: Record<string, string>;
    allow_network: boolean;
    mountain_resolution_policy: "strict" | "verified_only";
    /** What a run does with an output cell that already holds a value. */
    existing_outputs?: "keep" | "replace";
    /** Whether the result workbook says where each written value came from. */
    sources_in_workbook?: SourcesInWorkbook;
    plan?: EnrichmentPlan;
}
/** One column the universal search fills: what kind of value it holds. */
export type LookupKind = "text" | "number" | "yes_no";
/**
 * The universal search asks for its own draft: no instruction for the planner
 * but a question, the columns that identify a row and the columns to fill.
 */
export interface FallbackPlanRequest {
    dataset_id: string;
    sheet_name: string;
    range_ref: string;
    question: string;
    /** Headers of the columns whose values are sent to the model for each row. */
    context_columns: string[];
    targets: { column: string; kind: LookupKind; unit?: string }[];
    output_columns: Record<string, string>;
    existing_outputs: "keep" | "replace";
    sources_in_workbook: SourcesInWorkbook;
}
/**
 * The same route builds a mixed plan from a planner draft that left columns
 * unfilled: the draft's own dataset, range and output columns are used, and the
 * body names only the search part. `targets` are exactly the unfilled columns.
 */
export interface MixedPlanRequest {
    plan_id: string;
    question: string;
    context_columns: string[];
    targets: { column: string; kind: LookupKind; unit?: string }[];
    sources_in_workbook: SourcesInWorkbook;
}
export interface PlanDraft extends Omit<PlanRequest, "allow_network" | "plan"> {
    /**
     * Only a draft the server built says so: "fallback" for a plan that is one
     * web search, "mixed" for the planner's steps and one web search.
     */
    kind?: "fallback" | "mixed";
    /** A mixed draft: the columns the web search fills, in the order of the request. */
    searched_targets?: string[];
    /** A mixed draft: the planner draft it was built from, still stored. */
    planner_plan_id?: string;
    plan_id: string;
    fingerprint: string;
    source_sha256: string;
    status: "draft" | "approved";
    approved_at: string | null;
    planning_status: string;
    reason: string | null;
    /**
     * Chosen columns the planner found no source for. The plan does not write
     * them. Absent in a draft the server made before the field existed.
     */
    unfilled_targets?: string[];
    validation: Validation;
    plan: EnrichmentPlan | null;
}
export type JobStatus =
    | "queued"
    | "running"
    | "completed"
    | "needs_review"
    | "failed"
    | "cancelled"
    | "timed_out";
export const isTerminal = (status: JobStatus) =>
    status !== "queued" && status !== "running";
export interface Job {
    job_id: string;
    plan_id: string;
    /** Only a run of the universal search says so ("mixed": with source steps too). */
    kind?: "fallback" | "mixed";
    fingerprint: string;
    status: JobStatus;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    error: { code: string; message: string } | null;
    metrics: Record<string, number> | null;
    /**
     * Finished and total rows, once the worker has read the table, and what
     * each step did over the finished rows (counts only; empty before the
     * first row is done).
     */
    progress?: {
        rows_done: number;
        rows_total: number;
        steps?: Record<
            string,
            { filled: number; failed: number; skipped: number }
        >;
    } | null;
    artifacts: { result?: string; report?: string };
    execution_mode: "live" | "offline_fixture";
    timeout_seconds: number;
    /** Set when this job continues an earlier one after review. */
    previous_job_id?: string | null;
    /**
     * Rows left out of the run because every chosen cell already had a value
     * (kept, not looked up). Present once a run has finished; `metrics` and
     * `progress` count the other rows only.
     */
    rows_skipped?: number;
}
/** What a universal-search run asked of the model: numbers only. */
export interface WebLookupSpend {
    configuration_id: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    web_searches: number;
    /** Rows a kept answer of an earlier job settled; absent from an older server. */
    answers_kept?: number;
}
/** One value written from a web search: its column, what it rests on, how many links. */
export interface AiFound {
    column: string;
    basis: "stated" | "concluded";
    links: number;
}
export interface AuditReport extends Job {
    source_sha256: string;
    /** A mixed run: the columns the web search filled. */
    searched_targets?: string[];
    /** A universal-search run: what it asked of the model. */
    web_lookup?: WebLookupSpend;
    /** What each step is, where its values come from and what stands behind them. */
    step_provenance?: {
        step_id: string;
        capability: string;
        source: string;
        trust: string;
    }[];
    result_sha256?: string;
    source_unchanged?: boolean;
    preserved_cells?: string[];
    /** Rows that were not looked up: every chosen cell already held a value. */
    skipped_rows?: {
        record_id: string;
        row: number;
        inputs?: Record<string, ReviewValue>;
        reason: "already_filled";
        /** Output columns whose cell holds a kept value. */
        kept_outputs: string[];
        /** The value each of those cells holds. */
        kept_values: Record<string, string | number | boolean>;
    }[];
    rows?: {
        record_id: string;
        status: "success" | "partial" | "failure";
        /** Excel row number and the source values the plan used. */
        row?: number | null;
        inputs?: Record<string, ReviewValue>;
        /** Steps whose entity the user chose during review. */
        user_chosen_steps?: string[];
        /** Steps whose value was found under another name, and who gave it. */
        other_names?: Record<
            string,
            { searched_as: string; origin?: "user" | "model" }
        >;
        /** Steps accepted automatically while other matches stayed unconfirmed. */
        auto_accepted_steps?: string[];
        /** Output columns whose cell already held a value and still holds it. */
        kept_outputs?: string[];
        /** Output columns whose earlier value was replaced, with that value. */
        replaced_outputs?: Record<string, string | number | boolean>;
        /**
         * Output columns left empty because the source holds no value for the
         * field, although the step succeeded: the field and a reason code.
         */
        empty_outputs?: Record<string, { field: string; code: string }>;
        /** A universal-search run: each value written from a web search. */
        ai_found?: AiFound[];
    }[];
    execution?: {
        provider_requests: Record<string, Record<string, number>>;
        records: {
            record_id: string;
            row_result: {
                steps: {
                    step_id: string;
                    result: {
                        status: "success" | "failure";
                        value: unknown;
                        error_code: string | null;
                        message: string | null;
                        evidence: { source: string; reference: string }[];
                    };
                }[];
            };
        }[];
    };
}
export type ReviewValue = string | number | boolean | null;
export interface ReviewCandidate {
    source: string;
    entity_id: string;
    label: string;
    entity_type: string | null;
    verification: "verified" | "rejected" | "unchecked";
    attributes: Record<string, ReviewValue>;
    evidence: { source: string; reference: string }[];
    /** False when the backend would reject this candidate; `blocker` says why. */
    selectable: boolean;
    blocker: "unverified" | "excluded" | "type" | null;
    /** Advisory only. Nothing is applied until the user sends a decision. */
    recommended: boolean;
    recommendation_reasons: string[];
    reason_codes: string[];
}
/** One source query. A decision covers every row and step listed in `affected`. */
export interface ReviewItem {
    operation_id: string;
    capability: string | null;
    provider_id: string;
    query: {
        name: string;
        entity_type: string | null;
        context: Record<string, ReviewValue>;
        /** The name the source was searched for, when the user gave another one. */
        searched_as?: string | null;
    };
    status: string;
    reason_codes: string[];
    search_complete: boolean;
    /** Whether searching under another name could change this outcome. */
    other_name?: boolean;
    affected: { record_id: string; row: number | null; step_id: string }[];
    candidates: ReviewCandidate[];
}
export interface Review {
    job_id: string;
    previous_job_id: string | null;
    /** Queries the user can decide. */
    items: ReviewItem[];
    /** Unresolved queries with nothing to choose from. */
    informational: ReviewItem[];
    affected_rows: number;
}
/**
 * A listed candidate (`source` and `entity_id`); a candidate found by a search
 * under another name (the same, with that `name`); or neither: the query is
 * left empty on purpose.
 */
export interface ReviewDecision {
    operation_id: string;
    source?: string;
    entity_id?: string;
    name?: string;
}
export interface ProviderReadiness {
    provider: "openai";
    /** `measured` is false for a model whose planning reliability was not measured. */
    supported_models: { id: string; label: string; measured?: boolean }[];
    model: string | null;
    key_present: boolean;
    server_network_allowed: boolean;
    live_ready: boolean;
    planning_mode: "live" | "offline_injected";
    configuration_enabled: boolean;
    storage: "process_memory";
}
// Configuration errors retain only stable codes, never raw server messages or input.
async function configureProvider(
    method: "PUT" | "DELETE",
    body?: { model: string | null; api_key?: string },
): Promise<ProviderReadiness> {
    try {
        return await response<ProviderReadiness>(
            call(
                method === "DELETE"
                    ? "/api/provider-settings/key"
                    : "/api/provider-settings",
                {
                    method,
                    cache: "no-store",
                    credentials: "same-origin",
                    redirect: "error",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Enrichment-Config": "1",
                    },
                    ...(body ? { body: JSON.stringify(body) } : {}),
                },
            ),
        );
    } catch (error) {
        throw new WorkspaceApiError(
            error instanceof WorkspaceApiError ? error.code : "request_failed",
            "Provider configuration failed.",
        );
    }
}
/** What one run accepts; the server may be started with other bounds. */
export interface RunLimits {
    /** A table of up to this size can be read and looked through. */
    max_table_rows?: number;
    max_table_cells?: number;
    max_run_rows: number;
    max_timeout_seconds: number;
    default_timeout_seconds: number;
    /** Ids of the capabilities a plan may use on this server. */
    capabilities?: string[];
    /** The universal search can be asked for here (switched on, with a key). */
    web_lookup?: boolean;
}
export const api = {
    limits: () => response<RunLimits>(call("/api/limits")),
    capabilities: () =>
        response<{ capabilities: Capability[] }>(call("/api/capabilities")),
    access: () =>
        response<AccessState>(call("/api/access", { cache: "no-store" })),
    /** The usage summary; opened by the admin code, not by a visitor's. */
    usage: (adminCode: string) =>
        response<UsageSummary>(
            fetch("/api/usage", {
                cache: "no-store",
                headers: { "X-Admin-Code": adminCode },
            }),
        ),
    samples: () => response<{ samples: Sample[] }>(call("/api/samples")),
    sampleWorkbook: async (id: string) =>
        (await checked(call(`/api/samples/${id}/workbook`))).blob(),
    samplePlan: (id: string) =>
        response<EnrichmentPlan>(call(`/api/samples/${id}/plan`)),
    providerSettings: () =>
        response<ProviderReadiness>(
            call("/api/provider-settings", { cache: "no-store" }),
        ),
    configureProvider: (body: { model: string | null; api_key?: string }) =>
        configureProvider("PUT", body),
    removeProviderKey: () => configureProvider("DELETE"),
    createPlan: (body: PlanRequest) => post<PlanDraft>("/api/plans", body),
    createFallbackPlan: (body: FallbackPlanRequest | MixedPlanRequest) =>
        post<PlanDraft>("/api/fallback-plans", body),
    getPlan: (id: string) => response<PlanDraft>(call(`/api/plans/${id}`)),
    validatePlan: (id: string) => post<Validation>(`/api/plans/${id}/validate`),
    approvePlan: (id: string, fingerprint: string) =>
        post<PlanDraft>(`/api/plans/${id}/approve`, {
            approved: true,
            fingerprint,
        }),
    startRun: (id: string, allow_network: boolean, timeout_seconds: number) =>
        post<Job>(`/api/plans/${id}/runs`, { allow_network, timeout_seconds }),
    getJob: (id: string, signal?: AbortSignal) =>
        response<Job>(call(`/api/jobs/${id}`, { signal })),
    cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
    getReview: (id: string, signal?: AbortSignal) =>
        response<Review>(call(`/api/jobs/${id}/review`, { signal })),
    /** Ask the source once for another name of a reviewed query. */
    searchOtherName: (
        id: string,
        operationId: string,
        name: string,
        allow_network: boolean,
    ) =>
        post<ReviewItem>(
            `/api/jobs/${id}/review/${encodeURIComponent(operationId)}/search`,
            { name, allow_network },
        ),
    /** Ask the model what could be asked for with the selected table. */
    suggestTasks: (body: {
        dataset_id: string;
        sheet_name: string;
        range_ref: string;
        language: string;
        allow_network: boolean;
        /** Headers of the columns chosen to be filled, when there are several. */
        fill_columns?: string[];
        /** Ask the model again although an answer is kept; it replaces that one. */
        again?: boolean;
    }) => post<{ tasks: string[] }>("/api/task-ideas", body),
    /** Ask the model under which other names the source may know a query. */
    suggestNames: (id: string, operationId: string, allow_network: boolean) =>
        post<{ operation_id: string; names: string[] }>(
            `/api/jobs/${id}/review/${encodeURIComponent(operationId)}/suggestions`,
            { allow_network },
        ),
    resumeJob: (
        id: string,
        decisions: ReviewDecision[],
        allow_network: boolean,
        timeout_seconds: number,
    ) =>
        post<Job>(`/api/jobs/${id}/resume`, {
            decisions,
            allow_network,
            timeout_seconds,
        }),
    getReport: (id: string, signal?: AbortSignal) =>
        response<AuditReport>(
            call(`/api/jobs/${id}/artifacts/report`, { signal }),
        ),
    artifact: async (id: string, kind: "result" | "report") =>
        (await checked(call(`/api/jobs/${id}/artifacts/${kind}`))).blob(),
    upload: (file: File) =>
        response<Dataset>(
            call(`/api/datasets?filename=${encodeURIComponent(file.name)}`, {
                method: "POST",
                body: file,
                headers: { "Content-Type": "application/octet-stream" },
            }),
        ),
    preview: (id: string, sheet_name: string, range_ref: string) =>
        response<Preview>(
            call(`/api/datasets/${id}/preview`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sheet_name, range_ref }),
            }),
        ),
    remove: (id: string) =>
        call(`/api/datasets/${id}`, { method: "DELETE" }).catch(
            () => undefined,
        ),
};
