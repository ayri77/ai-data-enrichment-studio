import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { PlanRunWorkspace } from "./components/PlanRunWorkspace";
import {
    api,
    WorkspaceApiError,
    type AuditReport,
    type Job,
    type PlanDraft,
    type Preview,
} from "./api";
import i18n, { messages } from "./i18n";

vi.mock("./api", async (original) => ({
    ...(await original<typeof import("./api")>()),
    api: {
        createPlan: vi.fn(),
        getPlan: vi.fn(),
        validatePlan: vi.fn(),
        approvePlan: vi.fn(),
        startRun: vi.fn(),
        getJob: vi.fn(),
        getReport: vi.fn(),
        cancelJob: vi.fn(),
        artifact: vi.fn(),
    },
}));
const preview: Preview = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:E11",
    sample_limit: 5,
    profile: {
        row_count: 10,
        columns: [],
        sample_records: [],
        schema_warnings: [],
    },
};
const draft: PlanDraft = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:E11",
    instruction: "Literal cities",
    output_columns: { distance: "E" },
    mountain_resolution_policy: "strict",
    plan_id: "plan",
    fingerprint: "a".repeat(64),
    source_sha256: "b".repeat(64),
    status: "draft",
    approved_at: null,
    planning_status: "ready",
    reason: null,
    validation: { valid: true, errors: [] },
    plan: {
        version: "1",
        steps: [
            {
                id: "distance",
                capability: "calculate_distance",
                inputs: {},
                parameters: { unit: "km" },
            },
        ],
        outputs: [
            {
                step_id: "distance",
                value_field: "value",
                target_column: "distance",
                expected_unit: "km",
            },
        ],
        assumptions: ["Literal city names"],
    },
};
const queued: Job = {
    job_id: "job",
    plan_id: "plan",
    fingerprint: draft.fingerprint,
    status: "queued",
    created_at: "2026-11-10T09:00:00Z",
    started_at: null,
    finished_at: null,
    error: null,
    metrics: null,
    artifacts: {},
    execution_mode: "live",
    timeout_seconds: 180,
};
const finished: Job = {
    ...queued,
    status: "needs_review",
    finished_at: "2026-11-10T09:00:10Z",
    metrics: {
        total_rows: 10,
        successful_rows: 1,
        partial_rows: 3,
        failed_rows: 6,
    },
    artifacts: {
        result: "/api/jobs/job/artifacts/result",
        report: "/api/jobs/job/artifacts/report",
    },
};
const report: AuditReport = {
    ...finished,
    source_sha256: draft.source_sha256,
    rows: [{ record_id: "excel-row-2", status: "partial" }],
    execution: {
        provider_requests: { provider: { transport_attempts: 2 } },
        records: [
            {
                record_id: "excel-row-2",
                row_result: {
                    steps: [
                        {
                            step_id: "city",
                            result: {
                                status: "failure",
                                value: null,
                                error_code: "ambiguous",
                                message: "No verified value",
                                evidence: [],
                            },
                        },
                    ],
                },
            },
        ],
    },
};

beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
    vi.mocked(api.createPlan).mockResolvedValue(draft);
    vi.mocked(api.approvePlan).mockResolvedValue({
        ...draft,
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    });
    vi.mocked(api.startRun).mockResolvedValue(queued);
    vi.mocked(api.getJob).mockResolvedValue(finished);
    vi.mocked(api.getReport).mockResolvedValue(report);
});
const button = (key: string) =>
    screen.getByRole("button", { name: i18n.t(key) });
async function generate(expectPlan = true) {
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: "Literal cities" },
    });
    fireEvent.change(screen.getByLabelText("Output name 1"), {
        target: { value: "distance" },
    });
    fireEvent.change(screen.getByLabelText("Excel column 1"), {
        target: { value: "E" },
    });
    await userEvent.click(
        screen.getByLabelText(
            "Allow external network requests for planning and execution",
        ),
    );
    await userEvent.click(button("workflow.generate"));
    if (expectPlan) await screen.findByTestId("fingerprint");
}
async function start() {
    await generate();
    await userEvent.click(button("workflow.approve"));
    await userEvent.click(button("workflow.run"));
}

it("sends exact source and explicit mapping; never approves or starts automatically", async () => {
    render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
    expect(button("workflow.generate")).toBeDisabled();
    await generate();
    expect(api.createPlan).toHaveBeenCalledWith({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:E11",
        instruction: "Literal cities",
        output_columns: { distance: "E" },
        allow_network: true,
        mountain_resolution_policy: "strict",
    });
    expect(api.approvePlan).not.toHaveBeenCalled();
    expect(api.startRun).not.toHaveBeenCalled();
    expect(button("workflow.run")).toBeDisabled();
    await userEvent.click(button("workflow.approve"));
    expect(api.approvePlan).toHaveBeenCalledWith("plan", "a".repeat(64));
    expect(button("workflow.run")).toBeEnabled();
    expect(api.startRun).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Run timeout (seconds, 1–300)"), {
        target: { value: "301" },
    });
    expect(button("workflow.run")).toBeDisabled();
});

it.each(["instruction", "mapping", "network", "policy", "saved"])(
    "clears approval and job state after changing %s",
    async (change) => {
        render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
        await start();
        await screen.findByText("Unresolved and operation errors");
        if (change === "instruction")
            fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
                target: { value: "New instruction" },
            });
        if (change === "mapping")
            fireEvent.change(screen.getByLabelText("Excel column 1"), {
                target: { value: "F" },
            });
        if (change === "network")
            await userEvent.click(
                screen.getByLabelText(
                    "Allow external network requests for planning and execution",
                ),
            );
        if (change === "policy")
            fireEvent.change(
                screen.getByLabelText("Mountain resolution policy"),
                { target: { value: "verified_only" } },
            );
        if (change === "saved")
            fireEvent.change(screen.getByLabelText("EnrichmentPlan JSON"), {
                target: { value: "{}" },
            });
        expect(screen.queryByTestId("fingerprint")).not.toBeInTheDocument();
        expect(screen.queryByTestId("job-status")).not.toBeInTheDocument();
    },
);

it.each(["invalid", "missing"])(
    "blocks approval and execution for a %s plan",
    async (kind) => {
        vi.mocked(api.createPlan).mockResolvedValue({
            ...draft,
            plan: kind === "missing" ? null : draft.plan,
            validation: {
                valid: false,
                errors: [
                    {
                        code: "invalid_output_mapping",
                        message: "Output mapping is invalid",
                    },
                ],
            },
        });
        render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
        await generate();
        expect(
            screen.getAllByText("Output mapping is invalid", {
                exact: false,
            })[0],
        ).toBeInTheDocument();
        expect(button("workflow.approve")).toBeDisabled();
        expect(button("workflow.run")).toBeDisabled();
        expect(api.approvePlan).not.toHaveBeenCalled();
    },
);

it("revalidates through the server and reads the current draft", async () => {
    vi.mocked(api.validatePlan).mockResolvedValue(draft.validation);
    vi.mocked(api.getPlan).mockResolvedValue(draft);
    render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
    await generate();
    await userEvent.click(button("workflow.validate"));
    expect(api.validatePlan).toHaveBeenCalledWith("plan");
    expect(api.getPlan).toHaveBeenCalledWith("plan");
});

it("displays real partial metrics, report errors, stops polling and preserves state across locales", async () => {
    render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
    await start();
    await screen.findByText("Unresolved and operation errors");
    expect(api.startRun).toHaveBeenCalledWith("plan", true, 180);
    expect(screen.getByTestId("job-status")).toHaveTextContent("Needs review");
    expect(screen.getAllByText("ambiguous")[0]).toBeInTheDocument();
    expect(button("workflow.downloadXlsx")).toBeEnabled();
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            messages[locale]["workflow.status.needs_review"],
        );
        expect(screen.getByTestId("fingerprint")).toHaveTextContent(
            draft.fingerprint,
        );
    }
    expect(api.getJob).toHaveBeenCalledTimes(1);
});

it.each(["failed", "cancelled", "timed_out"] as const)(
    "shows %s with an audit and no workbook",
    async (status) => {
        vi.mocked(api.getJob).mockResolvedValue({
            ...queued,
            status,
            artifacts: { report: "/api/jobs/job/artifacts/report" },
        });
        vi.mocked(api.getReport).mockResolvedValue({
            ...queued,
            status,
            source_sha256: draft.source_sha256,
        });
        render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
        await start();
        await waitFor(() => expect(api.getReport).toHaveBeenCalled());
        expect(button("workflow.downloadXlsx")).toBeDisabled();
        expect(button("workflow.downloadReport")).toBeEnabled();
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            i18n.t(`workflow.status.${status}`),
        );
    },
);

it.each([
    "network_disabled",
    "llm_not_configured",
    "missing_api_key",
    "job_busy",
])("keeps %s distinct in all locales", async (code) => {
    vi.mocked(api.createPlan).mockRejectedValue(
        new WorkspaceApiError(code, "sanitized"),
    );
    render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
    await generate(false);
    await screen.findByRole("alert");
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(screen.getByRole("alert")).toHaveTextContent(
            i18n.t(`error.${code}`),
        );
        expect(screen.getByRole("alert")).toHaveAttribute(
            "data-error-code",
            code,
        );
    }
    expect(api.approvePlan).not.toHaveBeenCalled();
});

it("recovers polling errors without starting another job", async () => {
    vi.mocked(api.getJob).mockRejectedValueOnce(
        new WorkspaceApiError("network_error", "unreachable"),
    );
    render(<PlanRunWorkspace preview={preview} onLock={vi.fn()} />);
    await start();
    await screen.findByRole("alert");
    expect(button("workflow.run")).toBeDisabled();
    await userEvent.click(button("workflow.retry"));
    await screen.findByText("Unresolved and operation errors");
    expect(api.startRun).toHaveBeenCalledTimes(1);
});

it("locks edits while active, requests cancellation and aborts polling on unmount", async () => {
    vi.mocked(api.getJob).mockResolvedValue({ ...queued, status: "running" });
    const lock = vi.fn();
    const view = render(<PlanRunWorkspace preview={preview} onLock={lock} />);
    await start();
    await screen.findByText("Running");
    expect(screen.getByLabelText("Enrichment instruction")).toBeDisabled();
    expect(lock).toHaveBeenLastCalledWith(true);
    await userEvent.click(button("workflow.cancel"));
    expect(api.cancelJob).toHaveBeenCalledWith("job");
    const calls = vi.mocked(api.getJob).mock.calls;
    const signal = calls[calls.length - 1][1]!;
    view.unmount();
    expect(signal.aborted).toBe(true);
    expect(lock).toHaveBeenLastCalledWith(false);
});

it("discards late plan responses after changing the source selection", async () => {
    let resolve!: (plan: PlanDraft) => void;
    vi.mocked(api.createPlan).mockReturnValue(
        new Promise((done) => {
            resolve = done;
        }),
    );
    const view = render(
        <PlanRunWorkspace key="old" preview={preview} onLock={vi.fn()} />,
    );
    await generate(false);
    view.rerender(
        <PlanRunWorkspace
            key="new"
            preview={{ ...preview, range_ref: "A1:C4" }}
            onLock={vi.fn()}
        />,
    );
    await act(async () => resolve(draft));
    expect(screen.queryByTestId("fingerprint")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Enrichment instruction")).toHaveValue("");
});

it("blocks selections beyond the backend row envelope and duplicate mappings", async () => {
    render(
        <PlanRunWorkspace
            preview={{ ...preview, range_ref: "A1:E22" }}
            onLock={vi.fn()}
        />,
    );
    await generate(false);
    expect(button("workflow.generate")).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("1–20");
    expect(api.createPlan).not.toHaveBeenCalled();
});
