export interface Worksheet {
    name: string;
    columns: string[];
    row_count: number;
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
    output: { name: string; column: string };
    /** A prepared plan exists, so the example also works without the AI provider. */
    has_plan: boolean;
}
export interface AccessState {
    /** True on a hosted deployment that asks for a code. */
    required: boolean;
    /** True when no code is needed or the one sent with the request is right. */
    granted: boolean;
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
export interface PlanRequest {
    dataset_id: string;
    sheet_name: string;
    range_ref: string;
    instruction: string;
    output_columns: Record<string, string>;
    allow_network: boolean;
    mountain_resolution_policy: "strict" | "verified_only";
    plan?: EnrichmentPlan;
}
export interface PlanDraft extends Omit<PlanRequest, "allow_network" | "plan"> {
    plan_id: string;
    fingerprint: string;
    source_sha256: string;
    status: "draft" | "approved";
    approved_at: string | null;
    planning_status: string;
    reason: string | null;
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
    fingerprint: string;
    status: JobStatus;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    error: { code: string; message: string } | null;
    metrics: Record<string, number> | null;
    artifacts: { result?: string; report?: string };
    execution_mode: "live" | "offline_fixture";
    timeout_seconds: number;
    /** Set when this job continues an earlier one after review. */
    previous_job_id?: string | null;
}
export interface AuditReport extends Job {
    source_sha256: string;
    result_sha256?: string;
    source_unchanged?: boolean;
    preserved_cells?: string[];
    rows?: {
        record_id: string;
        status: "success" | "partial" | "failure";
        /** Excel row number and the source values the plan used. */
        row?: number | null;
        inputs?: Record<string, ReviewValue>;
        /** Steps whose entity the user chose during review. */
        user_chosen_steps?: string[];
        /** Steps accepted automatically while other matches stayed unconfirmed. */
        auto_accepted_steps?: string[];
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
    };
    status: string;
    reason_codes: string[];
    search_complete: boolean;
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
/** Without `source` and `entity_id` the query is left empty on purpose. */
export interface ReviewDecision {
    operation_id: string;
    source?: string;
    entity_id?: string;
}
export interface ProviderReadiness {
    provider: "openai";
    supported_models: { id: string; label: string }[];
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
export const api = {
    access: () =>
        response<AccessState>(call("/api/access", { cache: "no-store" })),
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
