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
        fetch(path, {
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
}
export interface AuditReport extends Job {
    source_sha256: string;
    result_sha256?: string;
    source_unchanged?: boolean;
    preserved_cells?: string[];
    rows?: { record_id: string; status: "success" | "partial" | "failure" }[];
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
export const api = {
    createPlan: (body: PlanRequest) => post<PlanDraft>("/api/plans", body),
    getPlan: (id: string) => response<PlanDraft>(fetch(`/api/plans/${id}`)),
    validatePlan: (id: string) => post<Validation>(`/api/plans/${id}/validate`),
    approvePlan: (id: string, fingerprint: string) =>
        post<PlanDraft>(`/api/plans/${id}/approve`, {
            approved: true,
            fingerprint,
        }),
    startRun: (id: string, allow_network: boolean, timeout_seconds: number) =>
        post<Job>(`/api/plans/${id}/runs`, { allow_network, timeout_seconds }),
    getJob: (id: string, signal?: AbortSignal) =>
        response<Job>(fetch(`/api/jobs/${id}`, { signal })),
    cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
    getReport: (id: string, signal?: AbortSignal) =>
        response<AuditReport>(
            fetch(`/api/jobs/${id}/artifacts/report`, { signal }),
        ),
    artifact: async (id: string, kind: "result" | "report") =>
        (await checked(fetch(`/api/jobs/${id}/artifacts/${kind}`))).blob(),
    upload: (file: File) =>
        response<Dataset>(
            fetch(`/api/datasets?filename=${encodeURIComponent(file.name)}`, {
                method: "POST",
                body: file,
                headers: { "Content-Type": "application/octet-stream" },
            }),
        ),
    preview: (id: string, sheet_name: string, range_ref: string) =>
        response<Preview>(
            fetch(`/api/datasets/${id}/preview`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sheet_name, range_ref }),
            }),
        ),
    remove: (id: string) =>
        fetch(`/api/datasets/${id}`, { method: "DELETE" }).catch(
            () => undefined,
        ),
};
