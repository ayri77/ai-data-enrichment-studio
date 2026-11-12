import {
    act,
    fireEvent,
    render,
    renderHook,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { useWorkflow } from "./useWorkflow";
import {
    api,
    WorkspaceApiError,
    type AuditReport,
    type Job,
    type PlanDraft,
    type Preview,
    type Review,
    type ReviewCandidate,
} from "./api";
import i18n, { messages } from "./i18n";

vi.mock("./api", async (original) => ({
    ...(await original<typeof import("./api")>()),
    api: {
        limits: vi.fn(),
        access: vi.fn(),
        samples: vi.fn(),
        sampleWorkbook: vi.fn(),
        samplePlan: vi.fn(),
        providerSettings: vi.fn(),
        configureProvider: vi.fn(),
        removeProviderKey: vi.fn(),
        upload: vi.fn(),
        preview: vi.fn(),
        remove: vi.fn(),
        createPlan: vi.fn(),
        getPlan: vi.fn(),
        validatePlan: vi.fn(),
        approvePlan: vi.fn(),
        startRun: vi.fn(),
        getJob: vi.fn(),
        getReport: vi.fn(),
        cancelJob: vi.fn(),
        getReview: vi.fn(),
        resumeJob: vi.fn(),
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
        columns: ["country", "city", "country2", "city2", "distance"].map(
            (name) => ({
                name,
                inferred_type: "Text",
                null_count: name === "distance" ? 10 : 0,
                warnings: [],
            }),
        ),
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
const place = (
    entity_id: string,
    extra: Partial<ReviewCandidate> = {},
): ReviewCandidate => ({
    source: "open-meteo",
    entity_id,
    label: "Berlin",
    entity_type: "city",
    verification: "verified",
    attributes: { country_code: "DE", latitude: 52.52, longitude: 13.4 },
    evidence: [{ source: "open-meteo", reference: `geonames:${entity_id}` }],
    selectable: true,
    blocker: null,
    recommended: false,
    recommendation_reasons: [],
    reason_codes: ["verified_context_match"],
    ...extra,
});
const review: Review = {
    job_id: "job",
    previous_job_id: null,
    affected_rows: 3,
    items: [
        {
            operation_id: "op-berlin",
            capability: "geocode",
            provider_id: "open-meteo",
            query: {
                name: "Berlin",
                entity_type: "city",
                context: { country_code: "DE" },
            },
            status: "needs_review",
            reason_codes: ["ambiguous_or_unverified"],
            search_complete: true,
            affected: [
                { record_id: "Sheet1!4", row: 4, step_id: "to_city" },
                { record_id: "Sheet1!5", row: 5, step_id: "from_city" },
            ],
            candidates: [
                place("2950159", {
                    recommended: true,
                    recommendation_reasons: ["country_capital_role"],
                    attributes: {
                        country_code: "DE",
                        latitude: 52.52,
                        longitude: 13.4,
                        administrative_role: "country_capital",
                        population: 3426354,
                    },
                }),
                place("2950157"),
                place("2950200", {
                    entity_type: "district",
                    selectable: false,
                    blocker: "excluded",
                }),
            ],
        },
        {
            operation_id: "op-kyiv",
            capability: "geocode",
            provider_id: "open-meteo",
            query: {
                name: "Kyiv",
                entity_type: "city",
                context: { country_code: "UA" },
            },
            status: "needs_review",
            reason_codes: ["ambiguous_or_unverified"],
            search_complete: true,
            affected: [{ record_id: "Sheet1!2", row: 2, step_id: "from_city" }],
            candidates: [
                place("703448", { label: "Kyiv" }),
                place("704213", { label: "Kyiv" }),
            ],
        },
    ],
    informational: [
        {
            operation_id: "op-oslo",
            capability: "geocode",
            provider_id: "open-meteo",
            query: {
                name: "Oslo",
                entity_type: "city",
                context: { country_code: "NO" },
            },
            status: "unresolved",
            reason_codes: ["no_eligible_candidate"],
            search_complete: true,
            affected: [{ record_id: "Sheet1!10", row: 10, step_id: "to_city" }],
            candidates: [],
        },
    ],
};
const radio = (id: string) =>
    within(screen.getByTestId(`candidate-open-meteo:${id}`)).getByRole("radio");

beforeEach(async () => {
    vi.resetAllMocks();
    vi.mocked(api.providerSettings).mockResolvedValue({
        provider: "openai",
        supported_models: [{ id: "gpt-4.1-mini", label: "gpt-4.1-mini" }],
        model: "gpt-4.1-mini",
        key_present: true,
        server_network_allowed: true,
        live_ready: true,
        planning_mode: "live",
        configuration_enabled: true,
        storage: "process_memory",
    });
    localStorage.clear();
    // These suites exercise the complete interface; Basic has its own tests.
    localStorage.setItem("enrichment.mode", "advanced");
    await i18n.changeLanguage("en");
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "capitals.xlsx",
        worksheets: [
            {
                name: "Sheet1",
                columns: preview.profile.columns.map((x) => x.name),
                row_count: 10,
            },
        ],
    });
    vi.mocked(api.preview).mockResolvedValue(preview);
    vi.mocked(api.createPlan).mockResolvedValue(draft);
    vi.mocked(api.approvePlan).mockResolvedValue({
        ...draft,
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    });
    vi.mocked(api.startRun).mockResolvedValue(queued);
    vi.mocked(api.getJob).mockResolvedValue(finished);
    vi.mocked(api.getReport).mockResolvedValue(report);
    vi.mocked(api.getReview).mockResolvedValue(review);
});
const button = (key: string) =>
    screen.getByRole("button", { name: i18n.t(key) });
const navigate = async (key: string) =>
    userEvent.click(
        screen.getByRole("navigation").querySelectorAll("button")[
            ["dataset", "plan", "execution", "review", "export"].indexOf(key)
        ],
    );
async function prepare() {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    fireEvent.change(await screen.findByLabelText("Enrichment instruction"), {
        target: { value: "Literal cities" },
    });
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 1"),
        "E",
    );
    await userEvent.click(screen.getByLabelText(i18n.t("workflow.network")));
}
async function generate() {
    await prepare();
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
}
async function start() {
    await generate();
    await userEvent.click(button("ws.approveContinue"));
    await userEvent.click(button("workflow.run"));
}

it("uses exact source/mapping, validates and explicitly approves before a separate run", async () => {
    render(<App />);
    await generate();
    expect(api.createPlan).toHaveBeenCalledWith({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:E11",
        instruction: "Literal cities",
        output_columns: { distance: "E" },
        allow_network: true,
        // Default: accept the single confirmed match; "strict" asks instead.
        mountain_resolution_policy: "verified_only",
    });
    expect(api.approvePlan).not.toHaveBeenCalled();
    expect(api.startRun).not.toHaveBeenCalled();
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
    await navigate("plan");
    vi.mocked(api.validatePlan).mockResolvedValue(draft.validation);
    vi.mocked(api.getPlan).mockResolvedValue(draft);
    await userEvent.click(button("workflow.validate"));
    expect(api.validatePlan).toHaveBeenCalledWith("plan");
    expect(api.getPlan).toHaveBeenCalledWith("plan");
    await userEvent.click(button("ws.approveContinue"));
    expect(api.approvePlan).toHaveBeenCalledWith("plan", draft.fingerprint);
    expect(button("workflow.run")).toBeEnabled();
    expect(api.startRun).not.toHaveBeenCalled();
    // The limit is set in one place, with the task; it does not invalidate the plan.
    await navigate("dataset");
    await userEvent.click(screen.getByText("Advanced policy and limits"));
    fireEvent.change(screen.getByLabelText(i18n.t("workflow.timeout")), {
        target: { value: "301" },
    });
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
});

it("keeps polling and locks source edits across workspace changes, without cancellation", async () => {
    vi.mocked(api.getJob).mockResolvedValueOnce({
        ...queued,
        status: "running",
    });
    const view = render(<App />);
    await start();
    await waitFor(() =>
        expect(screen.getByTestId("job-status")).toHaveTextContent("Running"),
    );
    await navigate("dataset");
    expect(screen.getByLabelText("Upload Excel workbook")).toBeDisabled();
    expect(screen.getByLabelText("Enrichment instruction")).toBeDisabled();
    await navigate("review");
    await waitFor(() => expect(api.getReport).toHaveBeenCalled(), {
        timeout: 3000,
    });
    expect(api.cancelJob).not.toHaveBeenCalled();
    expect(api.startRun).toHaveBeenCalledTimes(1);
    await navigate("execution");
    expect(screen.getByTestId("job-status")).toHaveTextContent("Needs review");
    const signal = vi.mocked(api.getJob).mock.calls.at(-1)![1]!;
    view.unmount();
    expect(signal.aborted).toBe(true);
});

it.each(["instruction", "mapping", "network", "policy", "saved", "source"])(
    "invalidates current approval/results on %s edits, retaining prior runs in Diagnostics",
    async (change) => {
        render(<App />);
        await start();
        await waitFor(() => expect(api.getReport).toHaveBeenCalled());
        await navigate("dataset");
        expect(
            screen.getAllByText(i18n.t("ws.invalidateWarning"))[0],
        ).toBeInTheDocument();
        if (change === "instruction")
            fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
                target: { value: "New task" },
            });
        if (change === "mapping")
            await userEvent.selectOptions(
                screen.getByLabelText("Output column 1"),
                "A",
            );
        if (change === "network")
            await userEvent.click(
                screen.getByLabelText(i18n.t("workflow.network")),
            );
        if (change === "policy")
            fireEvent.change(screen.getByLabelText(i18n.t("workflow.policy")), {
                target: { value: "strict" },
            });
        if (change === "saved") {
            await userEvent.click(
                screen.getByText(i18n.t("workflow.savedPlan")),
            );
            fireEvent.change(screen.getByLabelText("EnrichmentPlan JSON"), {
                target: { value: "{}" },
            });
        }
        if (change === "source")
            fireEvent.change(screen.getByLabelText("Last row"), {
                target: { value: "10" },
            });
        await navigate("execution");
        expect(button("workflow.run")).toBeDisabled();
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            i18n.t("ws.readyToRun"),
        );
        await userEvent.click(button("ws.diagnostics"));
        expect(screen.queryByTestId("fingerprint")).not.toBeInTheDocument();
        expect(screen.getByText(/job · Needs review/)).toBeInTheDocument();
    },
);

it.each(["invalid", "missing"])("blocks a %s plan", async (kind) => {
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
    render(<App />);
    await generate();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        "Validation failed",
    );
    expect(button("ws.approveContinue")).toBeDisabled();
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
    expect(api.approvePlan).not.toHaveBeenCalled();
});

it("shows only compact actual metrics, groups issues honestly and keeps technical details on demand", async () => {
    render(<App />);
    await start();
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    expect(screen.getByTestId("metric-partial_rows")).toHaveTextContent("3");
    expect(
        screen.queryByText("Actual provider request counters"),
    ).not.toBeInTheDocument();
    await navigate("review");
    // The per-code summary stays available as a second layer for audit.
    expect(screen.getByText("ambiguous").closest("details")).toHaveAttribute(
        "data-advanced",
        "true",
    );
    expect(button("review.resume")).toBeDisabled();
    await navigate("export");
    expect(button("workflow.downloadXlsx")).toBeEnabled();
    expect(screen.getByLabelText("Workbook filename")).toHaveValue(
        "capitals-enriched.xlsx",
    );
    await userEvent.click(button("ws.diagnostics"));
    expect(screen.getByTestId("fingerprint")).toHaveTextContent(
        draft.fingerprint,
    );
    expect(
        screen.getByText("Actual provider request counters"),
    ).toBeInTheDocument();
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(screen.getByTestId("fingerprint")).toHaveTextContent(
            draft.fingerprint,
        );
        expect(screen.getByRole("dialog")).toHaveAccessibleName(
            messages[locale]["ws.diagnostics"],
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
            artifacts: { report: "/report" },
        });
        vi.mocked(api.getReport).mockResolvedValue({
            ...queued,
            status,
            source_sha256: draft.source_sha256,
        });
        render(<App />);
        await start();
        await waitFor(() => expect(api.getReport).toHaveBeenCalled());
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            i18n.t(`workflow.status.${status}`),
        );
        await navigate("export");
        expect(button("workflow.downloadXlsx")).toBeDisabled();
        expect(button("workflow.downloadReport")).toBeEnabled();
    },
);

it.each([
    "network_disabled",
    "llm_not_configured",
    "missing_api_key",
    "job_busy",
])("preserves distinct localized %s errors", async (code) => {
    vi.mocked(api.createPlan).mockRejectedValue(
        new WorkspaceApiError(code, "sanitized"),
    );
    render(<App />);
    await prepare();
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByRole("alert");
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(screen.getByRole("alert")).toHaveTextContent(
            i18n.t(`error.${code}`),
        );
    }
    expect(api.approvePlan).not.toHaveBeenCalled();
});

it("retries status/report without starting another job; retrying the approved plan is separate", async () => {
    vi.mocked(api.getJob).mockRejectedValueOnce(
        new WorkspaceApiError("network_error", "unreachable"),
    );
    render(<App />);
    await start();
    await screen.findByRole("alert");
    expect(button("workflow.cancel")).toBeEnabled();
    await userEvent.click(button("workflow.retry"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    expect(api.startRun).toHaveBeenCalledTimes(1);
    await userEvent.click(button("ws.retryPlan"));
    expect(api.startRun).toHaveBeenCalledTimes(2);
});

it("requests cancellation and keeps polling until a terminal backend status", async () => {
    vi.mocked(api.getJob).mockResolvedValue({ ...queued, status: "running" });
    render(<App />);
    await start();
    await waitFor(() =>
        expect(screen.getByTestId("job-status")).toHaveTextContent("Running"),
    );
    await userEvent.click(button("workflow.cancel"));
    expect(api.cancelJob).toHaveBeenCalledWith("job");
    expect(screen.getByTestId("job-status")).toHaveTextContent("Running");
});

it("discards a late plan response after its source binding changes", async () => {
    let resolve!: (value: PlanDraft) => void;
    vi.mocked(api.createPlan).mockReturnValue(
        new Promise((done) => {
            resolve = done;
        }),
    );
    const view = renderHook(
        ({ key }) =>
            useWorkflow(
                preview,
                key,
                "capitals.xlsx",
                preview.profile.columns.map((c) => c.name),
            ),
        { initialProps: { key: "old" } },
    );
    act(() => {
        view.result.current.setInstruction("Literal cities");
        view.result.current.setMapping([
            { mode: "existing", name: "distance", column: "E" },
        ]);
        view.result.current.setNetwork(true);
    });
    let request!: Promise<void>;
    act(() => {
        request = view.result.current.action(
            view.result.current.create,
            "plan",
        );
    });
    view.rerender({ key: "new" });
    await act(async () => {
        resolve(draft);
        await request;
    });
    expect(view.result.current.plan).toBeNull();
    expect(view.result.current.instruction).toBe("");
    expect(view.result.current.workspace).toBe("dataset");
});

it("blocks the backend row envelope and rejects malformed supplied JSON before API submission", async () => {
    vi.mocked(api.preview).mockResolvedValueOnce({
        ...preview,
        range_ref: "A1:E22",
    });
    const view = render(<App />);
    await prepare();
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(
        document.querySelector('[data-requirement="selection"]'),
    ).toHaveTextContent("1–20");
    view.unmount();
    render(<App />);
    await prepare();
    await userEvent.click(screen.getByText(i18n.t("workflow.savedPlan")));
    fireEvent.change(screen.getByLabelText("EnrichmentPlan JSON"), {
        target: { value: "{" },
    });
    await userEvent.click(button("workflow.submitPlan"));
    expect(await screen.findByRole("alert")).toHaveAttribute(
        "data-error-code",
        "plan_json",
    );
    expect(api.createPlan).not.toHaveBeenCalled();
});

it("holds the user on Dataset while a source replacement is pending; the approved plan cannot be started", async () => {
    render(<App />);
    await generate();
    await userEvent.click(button("ws.approveContinue"));
    await navigate("dataset");
    let resolve!: (value: Awaited<ReturnType<typeof api.upload>>) => void;
    vi.mocked(api.upload).mockReturnValueOnce(
        new Promise((done) => {
            resolve = done;
        }),
    );
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["new"], "new.xlsx"),
    );
    // The wait is named, and the other steps cannot be opened meanwhile.
    expect(screen.getByText(i18n.t("activity.upload"))).toBeVisible();
    const execution = within(screen.getByRole("navigation")).getAllByRole(
        "button",
    )[2];
    expect(execution).toBeDisabled();
    expect(execution).toHaveAttribute("title", i18n.t("activity.wait"));
    await act(async () =>
        resolve({
            dataset_id: "replacement",
            filename: "new.xlsx",
            worksheets: [{ name: "Sheet1", columns: ["city"], row_count: 1 }],
        }),
    );
    expect(
        screen.queryByText(i18n.t("activity.upload")),
    ).not.toBeInTheDocument();
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
    expect(api.startRun).not.toHaveBeenCalled();
});

it("preserves the exact header when selecting an existing output column", async () => {
    const source = {
        ...preview,
        profile: {
            ...preview.profile,
            columns: preview.profile.columns.map((column) =>
                column.name === "distance"
                    ? { ...column, name: " distance " }
                    : column,
            ),
        },
    };
    const { result } = renderHook(() =>
        useWorkflow(
            source,
            "source",
            "book.xlsx",
            source.profile.columns.map((column) => column.name),
        ),
    );
    act(() => {
        result.current.setInstruction("Write to the existing output header.");
        result.current.setNetwork(true);
        result.current.setMapping([
            { mode: "existing", name: " distance ", column: "E" },
        ]);
    });
    await waitFor(() => expect(result.current.canBuild).toBe(true));
    await act(() => result.current.action(result.current.create, "plan"));
    expect(api.createPlan).toHaveBeenCalledWith(
        expect.objectContaining({ output_columns: { " distance ": "E" } }),
    );
});

it.each(["en", "uk", "de"] as const)(
    "configures masked ephemeral settings without losing the %s workflow or preferences",
    async (locale) => {
        render(<App />);
        await generate();
        await navigate("dataset");
        await userEvent.selectOptions(
            screen.getByLabelText(i18n.t("language")),
            locale,
        );
        await userEvent.selectOptions(
            screen.getByLabelText(i18n.t("theme")),
            "light",
        );
        const secret = ["synthetic", "key", String(Math.random())].join("-");
        const storage = JSON.stringify(localStorage);
        const session = JSON.stringify(sessionStorage);
        const log = vi.spyOn(console, "log");
        const errorLog = vi.spyOn(console, "error");
        const current = await api.providerSettings();
        vi.mocked(api.configureProvider).mockResolvedValue({
            ...current,
            model: "gpt-4.1-mini",
        });
        vi.mocked(api.removeProviderKey).mockResolvedValue({
            ...current,
            key_present: false,
            live_ready: false,
        });
        await userEvent.click(button("ws.providerSettings"));
        const input = screen.getByLabelText(i18n.t("provider.keyInput"));
        expect(input).toHaveAttribute("type", "password");
        expect(input).toHaveValue("");
        await userEvent.type(input, secret);
        await userEvent.click(button("provider.apply"));
        await screen.findByText(i18n.t("provider.saved"));
        expect(input).toHaveValue("");
        expect(api.configureProvider).toHaveBeenCalledWith({
            model: "gpt-4.1-mini",
            api_key: secret,
        });
        // Empty entry changes/keeps the model without returning or resending an existing key.
        await userEvent.click(button("provider.apply"));
        expect(api.configureProvider).toHaveBeenLastCalledWith({
            model: "gpt-4.1-mini",
        });
        await userEvent.click(button("provider.remove"));
        await screen.findByText(i18n.t("provider.removed"));
        expect(button("provider.remove")).toBeDisabled();
        await userEvent.type(input, secret);
        await userEvent.click(button("ws.close"));
        expect(input).toHaveValue("");
        expect(JSON.stringify(localStorage)).toBe(storage);
        expect(JSON.stringify(sessionStorage)).toBe(session);
        expect(document.body.textContent).not.toContain(secret);
        expect(
            JSON.stringify(log.mock.calls) +
                JSON.stringify(errorLog.mock.calls),
        ).not.toContain(secret);
        expect(
            screen.getByLabelText(i18n.t("workflow.instruction")),
        ).toHaveValue("Literal cities");
        expect(
            screen.getByLabelText(`${i18n.t("ws.targetColumn")} 1`),
        ).toHaveValue("E");
        expect(screen.getByLabelText(i18n.t("workflow.network"))).toBeChecked();
        expect(document.querySelector(".studio")).toHaveAttribute(
            "data-theme",
            "light",
        );
        expect(document.documentElement.lang).toBe(locale);
        await navigate("plan");
        expect(screen.getByTestId("validation")).toBeInTheDocument();
        expect(api.createPlan).toHaveBeenCalledTimes(1);
        expect(api.approvePlan).not.toHaveBeenCalled();
        expect(api.startRun).not.toHaveBeenCalled();
        log.mockRestore();
        errorLog.mockRestore();
    },
);

it("clears rejected key input and presents a stable error without displaying a backend secret", async () => {
    const secret = ["synthetic", String(Math.random())].join("-");
    vi.mocked(api.configureProvider).mockRejectedValue(
        new WorkspaceApiError("configuration_forbidden", secret),
    );
    render(<App />);
    await userEvent.click(button("ws.providerSettings"));
    const input = screen.getByLabelText(i18n.t("provider.keyInput"));
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.type(input, secret);
    await userEvent.click(button("provider.apply"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        i18n.t("error.configuration_forbidden"),
    );
    expect(input).toHaveValue("");
    expect(document.body.textContent).not.toContain(secret);
    expect(api.createPlan).not.toHaveBeenCalled();
});

it.each([
    ["modelMissing", { model: null, live_ready: false }],
    ["keyMissing", { key_present: false, live_ready: false }],
    ["networkDisabled", { server_network_allowed: false, live_ready: false }],
] as const)(
    "blocks live generation with a clear %s reason while allowing submitted plans offline",
    async (reason, overrides) => {
        const current = await api.providerSettings();
        vi.mocked(api.providerSettings).mockResolvedValue({
            ...current,
            ...overrides,
        });
        render(<App />);
        await prepare();
        expect(
            screen.getByText(i18n.t(`provider.${reason}`)),
        ).toBeInTheDocument();
        expect(button("ws.buildPlan")).toBeDisabled();
        await userEvent.click(
            screen.getByLabelText(i18n.t("workflow.network")),
        );
        fireEvent.change(screen.getByLabelText(i18n.t("workflow.planJson")), {
            target: { value: JSON.stringify(draft.plan) },
        });
        expect(button("workflow.submitPlan")).toBeEnabled();
        await userEvent.click(button("workflow.submitPlan"));
        await screen.findByTestId("validation");
        expect(api.createPlan).toHaveBeenCalledWith(
            expect.objectContaining({ plan: draft.plan, allow_network: false }),
        );
    },
);

it("distinguishes injected offline mode and still requires explicit consent for generated planning", async () => {
    const current = await api.providerSettings();
    vi.mocked(api.providerSettings).mockResolvedValue({
        ...current,
        planning_mode: "offline_injected",
        live_ready: false,
    });
    render(<App />);
    await prepare();
    expect(screen.getByText(i18n.t("provider.offline"))).toBeInTheDocument();
    expect(button("ws.buildPlan")).toBeEnabled();
    await userEvent.click(screen.getByLabelText(i18n.t("workflow.network")));
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(
        screen.getByText(i18n.t("provider.consentMissing")),
    ).toBeInTheDocument();
});

it("fails closed when readiness cannot be read and disables settings outside local mode", async () => {
    vi.mocked(api.providerSettings).mockRejectedValue(new Error("offline"));
    render(<App />);
    await prepare();
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(
        screen.getByText(i18n.t("provider.unavailable")),
    ).toBeInTheDocument();
    await userEvent.click(button("ws.providerSettings"));
    const current = {
        provider: "openai",
        supported_models: [],
        model: null,
        key_present: false,
        server_network_allowed: false,
        live_ready: false,
        planning_mode: "live",
        configuration_enabled: false,
        storage: "process_memory",
    } as const;
    vi.mocked(api.providerSettings).mockResolvedValue({
        ...current,
        supported_models: [],
    });
    await userEvent.click(button("provider.refresh"));
    await screen.findByText(i18n.t("provider.localOnly"));
    expect(button("provider.apply")).toBeDisabled();
    expect(screen.getByLabelText(i18n.t("provider.keyInput"))).toBeDisabled();
});

it("does not let an older readiness response overwrite newly applied settings", async () => {
    let resolve!: (
        value: Awaited<ReturnType<typeof api.providerSettings>>,
    ) => void;
    vi.mocked(api.providerSettings).mockReturnValueOnce(
        new Promise((done) => {
            resolve = done;
        }),
    );
    const { result } = renderHook(() => useWorkflow(null, "source"));
    const current = await api.providerSettings();
    act(() =>
        result.current.setProvider({ ...current, model: "gpt-4.1-mini" }),
    );
    await act(async () =>
        resolve({
            ...current,
            model: null,
            key_present: false,
            live_ready: false,
        }),
    );
    expect(result.current.planningReadiness).toBe("ready");
});

it("distinguishes passing contracts from intent conflicts and blocks approval", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...draft,
        validation: {
            valid: false,
            contract_valid: true,
            errors: [
                {
                    code: "INTENT_PARAMETER_CONFLICT",
                    message:
                        "Explicit user intent prohibits the capital-role restriction.",
                },
            ],
        },
    });
    render(<App />);
    await generate();
    expect(screen.getByText(i18n.t("workflow.intentConflict"))).toBeVisible();
    expect(screen.getByText(i18n.t("workflow.assumptions"))).toBeVisible();
    expect(button("ws.approveContinue")).toBeDisabled();
    expect(api.approvePlan).not.toHaveBeenCalled();
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
});

it("lets a person settle each ambiguous name once and resumes as a new job", async () => {
    render(<App />);
    await start();
    await waitFor(() =>
        expect(api.getReview).toHaveBeenCalledWith("job", expect.anything()),
    );
    await navigate("review");
    const berlin = within(await screen.findByTestId("review-Berlin"));
    expect(
        berlin.getByText(i18n.t("review.rows", { rows: "4, 5" })),
    ).toBeVisible();
    expect(berlin.getByText(i18n.t("review.recommended"))).toBeVisible();
    expect(berlin.getByText(/capital of the country/)).toBeVisible();
    // Matches that cannot be chosen stay out of the choice, in a closed second layer.
    const blocked = radio("2950200").closest("details")!;
    expect(blocked).toHaveAttribute("data-advanced", "true");
    expect(blocked).not.toHaveAttribute("open");
    // The choice itself offers two candidates and "leave empty".
    expect(
        within(
            berlin.getByRole("group", {
                name: i18n.t("review.candidates", { name: "Berlin" }),
            }),
        ).getAllByRole("radio"),
    ).toHaveLength(3);
    await userEvent.click(
        berlin.getByText(i18n.t("review.blockedCount", { count: 1 })),
    );
    expect(radio("2950200")).toBeDisabled();
    expect(berlin.getByText(/the source ruled it out/)).toBeVisible();
    expect(
        berlin.getAllByRole("link", { name: /Map/ })[0].getAttribute("href"),
    ).toContain("openstreetmap.org");
    // Names without any match are listed, with nothing to choose.
    expect(
        within(screen.getByTestId("unresolved-Oslo")).queryByRole("radio"),
    ).toBeNull();
    expect(screen.getByTestId("review-progress")).toHaveTextContent("0 of 2");
    expect(button("review.resume")).toBeDisabled();

    // "Leave empty" alone changes nothing, so it does not enable a resume.
    await userEvent.click(
        within(screen.getByTestId("review-Kyiv")).getByRole("radio", {
            name: new RegExp(i18n.t("review.leaveEmpty")),
        }),
    );
    expect(screen.getByTestId("review-progress")).toHaveTextContent("1 of 2");
    expect(button("review.resume")).toBeDisabled();

    // A recommendation is applied only when the person accepts it.
    expect(radio("2950159")).not.toBeChecked();
    await userEvent.click(button("review.accept"));
    expect(radio("2950159")).toBeChecked();
    expect(button("review.accept")).toBeDisabled();
    await userEvent.click(radio("2950157"));
    expect(radio("2950159")).not.toBeChecked();

    // Choices survive navigation and language changes.
    await navigate("execution");
    await navigate("review");
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(radio("2950157")).toBeChecked();
        expect(
            screen.getByRole("button", {
                name: messages[locale]["review.resume"],
            }),
        ).toBeEnabled();
        expect(
            within(screen.getByTestId("review-Berlin")).getByText(
                new RegExp(`^${messages[locale]["review.type.city"]} · `),
            ),
        ).toBeInTheDocument();
    }

    const resumed: Job = { ...queued, job_id: "job-2", previous_job_id: "job" };
    vi.mocked(api.resumeJob).mockResolvedValue(resumed);
    vi.mocked(api.getJob).mockResolvedValue({
        ...finished,
        job_id: "job-2",
        previous_job_id: "job",
        status: "completed",
        metrics: { ...finished.metrics!, successful_rows: 3 },
    });
    await userEvent.click(button("review.resume"));
    expect(api.resumeJob).toHaveBeenCalledWith(
        "job",
        [
            {
                operation_id: "op-berlin",
                source: "open-meteo",
                entity_id: "2950157",
            },
            { operation_id: "op-kyiv" },
        ],
        true,
        180,
    );
    expect(api.startRun).toHaveBeenCalledTimes(1);
    await waitFor(() =>
        expect(screen.getByTestId("metric-successful_rows")).toHaveTextContent(
            "3",
        ),
    );
    expect(screen.getByText(i18n.t("review.continued"))).toBeVisible();
    // The earlier run is kept for inspection, not overwritten.
    await userEvent.click(button("ws.diagnostics"));
    expect(screen.getByText(/job · Needs review/)).toBeInTheDocument();
});

it("reports a rejected resume with a localized message and keeps the choices", async () => {
    vi.mocked(api.resumeJob).mockRejectedValue(
        new WorkspaceApiError("invalid_decision", "sanitized"),
    );
    render(<App />);
    await start();
    await navigate("review");
    await screen.findByTestId("review-Kyiv");
    await userEvent.click(radio("703448"));
    await userEvent.click(button("review.resume"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        i18n.t("error.invalid_decision"),
    );
    // Still on Review, with the choice intact, and no second job was started.
    expect(radio("703448")).toBeChecked();
    expect(api.getJob).toHaveBeenCalledTimes(1);
});

it("offers nothing to decide when a run only has names without a match", async () => {
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [],
        affected_rows: 0,
    });
    render(<App />);
    await start();
    await navigate("review");
    expect(
        await screen.findByText(i18n.t("review.nothingToChoose")),
    ).toBeVisible();
    expect(screen.getByTestId("unresolved-Oslo")).toBeInTheDocument();
    expect(
        screen.queryByRole("button", { name: i18n.t("review.resume") }),
    ).toBeNull();
    expect(button("ws.toExport")).toBeEnabled();
});

it("shows results with source values, readable numbers, reasons, filters and details", async () => {
    const step = (step_id: string, value: unknown, error_code?: string) => ({
        step_id,
        result: {
            status: error_code ? ("failure" as const) : ("success" as const),
            value,
            error_code: error_code ?? null,
            message: null,
            evidence: error_code
                ? []
                : [{ source: "open-meteo", reference: `geonames:${step_id}` }],
        },
    });
    const line = (row: number, city: string, chosen: string[] = []) => ({
        record_id: `Sheet1!${row}`,
        status: "success" as const,
        row,
        inputs: { city, country: "Ukraine" },
        user_chosen_steps: chosen,
    });
    vi.mocked(api.createPlan).mockResolvedValue({
        ...draft,
        plan: {
            ...draft.plan!,
            steps: [
                {
                    id: "place",
                    capability: "geocode",
                    inputs: { city: { kind: "column", name: "city" } },
                    parameters: {},
                },
                ...draft.plan!.steps,
            ],
        },
    });
    vi.mocked(api.approvePlan).mockImplementation(async () => ({
        ...(await api.createPlan({} as never)),
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    }));
    vi.mocked(api.getReport).mockResolvedValue({
        ...report,
        rows: [
            line(2, "Kyiv", ["place"]),
            { ...line(3, "Lviv"), auto_accepted_steps: ["place"] },
            { ...line(4, "Odesa"), status: "failure" },
            { ...line(5, "Xyz"), status: "failure" },
        ],
        execution: {
            provider_requests: {},
            records: [
                [2, 2023.7941796667708],
                [3, 470.5],
            ]
                .map(([row, value]) => ({
                    record_id: `Sheet1!${row}`,
                    row_result: {
                        steps: [
                            step("place", {
                                latitude: 50.45,
                                longitude: 30.52,
                            }),
                            step("distance", { value, unit: "km" }),
                        ],
                    },
                }))
                .concat(
                    [
                        [4, "AMBIGUOUS"],
                        [5, "NOT_FOUND"],
                    ].map(([row, code]) => ({
                        record_id: `Sheet1!${row}`,
                        row_result: {
                            steps: [
                                step("place", null, String(code)),
                                step("distance", null, "DEPENDENCY_FAILED"),
                            ],
                        },
                    })),
                ),
        },
    });
    render(<App />);
    await start();
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await navigate("export");
    const cells = () =>
        screen
            .getAllByTestId("row-outcome")
            .map((row) => within(row).getAllByRole("cell")[1].textContent);
    expect(cells()).toEqual(["Kyiv", "Lviv", "Odesa", "Xyz"]);
    const kyiv = screen.getAllByTestId("row-outcome")[0];
    // Rounded for reading; the exact value stays available.
    expect(within(kyiv).getByText("2,023.79 km")).toHaveAttribute(
        "title",
        "2023.7941796667708",
    );
    expect(kyiv).toHaveAttribute("data-chosen", "true");
    expect(
        within(kyiv).getByText(i18n.t("results.chosen", { names: "Kyiv" })),
    ).toBeVisible();
    // An empty cell says why, and offers the way to settle an ambiguous name.
    const odesa = screen.getAllByTestId("row-outcome")[2];
    expect(
        within(odesa).getByText(/Several matches for “Odesa”/),
    ).toBeVisible();
    await userEvent.click(
        within(odesa).getByRole("button", { name: i18n.t("results.toReview") }),
    );
    expect(await screen.findByTestId("review-Berlin")).toBeInTheDocument();
    await navigate("export");
    expect(
        within(screen.getAllByTestId("row-outcome")[3]).getByText(
            /did not find “Xyz”/,
        ),
    ).toBeVisible();
    expect(screen.getByTestId("result-stats")).toHaveTextContent(
        /filled 2 of 4.*min 470\.5 km.*max 2,023\.79 km.*average 1,247\.15 km/,
    );

    // A value accepted without asking says so, and has its own filter.
    const lviv = screen.getAllByTestId("row-outcome")[1];
    expect(lviv).toHaveAttribute("data-automatic", "true");
    expect(
        within(lviv).getByText(i18n.t("results.automatic", { names: "Lviv" })),
    ).toBeVisible();
    await userEvent.click(
        screen.getByRole("button", { name: "Automatic (1)" }),
    );
    expect(cells()).toEqual(["Lviv"]);

    // Filters and sorting change only what is shown.
    await userEvent.click(screen.getByRole("button", { name: "Empty (2)" }));
    expect(cells()).toEqual(["Odesa", "Xyz"]);
    await userEvent.click(
        screen.getByRole("button", { name: "After your choice (1)" }),
    );
    expect(cells()).toEqual(["Kyiv"]);
    await userEvent.click(screen.getByRole("button", { name: "All (4)" }));
    const distance = screen.getByRole("button", { name: "distance" });
    await userEvent.click(distance);
    expect(cells()).toEqual(["Lviv", "Kyiv", "Odesa", "Xyz"]);
    expect(distance.closest("th")).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(distance);
    expect(cells()).toEqual(["Kyiv", "Lviv", "Odesa", "Xyz"]);

    // Second layer: steps and source records, only on demand.
    expect(screen.queryByText(/geonames:place/)).toBeNull();
    await userEvent.click(
        screen.getByRole("button", {
            name: i18n.t("results.detailsFor", { row: 2 }),
        }),
    );
    expect(screen.getByText("open-meteo geonames:place")).toBeVisible();
    expect(screen.getByText("place · geocode")).toBeVisible();
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(
            screen.getByRole("columnheader", {
                name: new RegExp(messages[locale]["results.result"]),
            }),
        ).toBeInTheDocument();
        expect(cells()).toEqual(["Kyiv", "Lviv", "Odesa", "Xyz"]);
    }
});

it("says next to the button what is still missing, and opens settings for a missing model", async () => {
    // A key from the environment with an unsupported model: ready except for the model.
    vi.mocked(api.providerSettings).mockResolvedValue({
        provider: "openai",
        supported_models: [{ id: "gpt-4.1-mini", label: "gpt-4.1-mini" }],
        model: null,
        key_present: true,
        server_network_allowed: true,
        live_ready: false,
        planning_mode: "live",
        configuration_enabled: true,
        storage: "process_memory",
    });
    render(<App />);
    const missing = () =>
        [...document.querySelectorAll("[data-requirement]")].map((item) =>
            item.getAttribute("data-requirement"),
        );
    await waitFor(() =>
        expect(missing()).toEqual([
            "preview",
            "instruction",
            "mapping",
            "consent",
            "provider",
        ]),
    );
    expect(button("ws.buildPlan")).toBeDisabled();
    // Each completed step leaves the list; the model is what remains.
    await prepare();
    expect(missing()).toEqual(["provider"]);
    expect(button("ws.buildPlan")).toBeDisabled();
    const model = document.querySelector('[data-requirement="provider"]')!;
    expect(model).toHaveTextContent(i18n.t("provider.modelMissing"));
    for (const locale of ["uk", "de", "en"] as const) {
        await act(() => i18n.changeLanguage(locale));
        expect(model).toHaveTextContent(
            messages[locale]["provider.modelMissing"],
        );
    }
    await userEvent.click(
        within(model as HTMLElement).getByRole("button", {
            name: i18n.t("ws.need.openSettings"),
        }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t("provider.model"))).toBeInTheDocument();
});

it("shows no list of missing items once a plan can be built", async () => {
    render(<App />);
    await prepare();
    expect(document.querySelector("[data-requirement]")).toBeNull();
    expect(button("ws.buildPlan")).toBeEnabled();
});

it("goes from an empty screen to a ready task with one click on an example", async () => {
    const example = {
        sample_id: "capitals",
        filename: "capitals.xlsx",
        instruction: "Calculate the distance between the two cities.",
        output: { name: "distance", column: "E" },
        has_plan: true,
    };
    vi.mocked(api.samples).mockResolvedValue({ samples: [example] });
    vi.mocked(api.sampleWorkbook).mockResolvedValue(new Blob(["x"]));
    vi.mocked(api.samplePlan).mockResolvedValue(draft.plan!);
    render(<App />);
    await userEvent.click(
        await screen.findByRole("button", { name: i18n.t("sample.capitals") }),
    );
    // The example travels through the ordinary upload and preview.
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    const uploaded = vi.mocked(api.upload).mock.calls[0][0];
    expect(uploaded.name).toBe("capitals.xlsx");
    expect(api.preview).toHaveBeenCalledWith("source", "Sheet1", "A1:E11");
    // Instruction and output column arrive filled in; consent stays the user's.
    expect(await screen.findByLabelText("Enrichment instruction")).toHaveValue(
        example.instruction,
    );
    expect(screen.getByLabelText("Output column 1")).toHaveValue("E");
    expect(screen.getByLabelText(i18n.t("workflow.network"))).not.toBeChecked();
    expect(
        [...document.querySelectorAll("[data-requirement]")].map((item) =>
            item.getAttribute("data-requirement"),
        ),
    ).toEqual(["consent"]);
    expect(button("ws.buildPlan")).toBeDisabled();

    // The prepared plan needs neither consent nor the AI provider.
    await userEvent.click(
        screen.getByRole("button", { name: i18n.t("sample.usePlan") }),
    );
    await waitFor(() => expect(button("workflow.submitPlan")).toBeEnabled());
    expect(api.samplePlan).toHaveBeenCalledWith("capitals");
    expect(document.querySelector("[data-requirement]")).toBeNull();
    await userEvent.click(button("workflow.submitPlan"));
    await waitFor(() => expect(api.createPlan).toHaveBeenCalled());
    expect(vi.mocked(api.createPlan).mock.calls[0][0]).toMatchObject({
        instruction: example.instruction,
        output_columns: { distance: "E" },
        plan: draft.plan,
    });
});

it("offers no examples when the server has none, and forgets one after a manual change", async () => {
    render(<App />);
    await waitFor(() => expect(api.samples).toHaveBeenCalled());
    expect(screen.queryByText(i18n.t("sample.try"))).toBeNull();
    // A workbook of the user's own starts with an empty task, as before.
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "own.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    expect(await screen.findByLabelText("Enrichment instruction")).toHaveValue(
        "",
    );
    expect(screen.queryByText(i18n.t("sample.planHint"))).toBeNull();
});

const absent = (key: string) =>
    expect(
        screen.queryByRole("button", { name: i18n.t(key) }),
    ).not.toBeInTheDocument();

it("starts in Basic without technical tools; the switch reveals them and is remembered", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    const mode = screen.getByLabelText(i18n.t("mode.label"));
    expect(mode).toHaveValue("basic");
    absent("ws.diagnostics");
    absent("ws.providerSettings");
    expect(screen.queryByText(i18n.t("localApi"))).not.toBeInTheDocument();
    expect(
        screen.queryByText(i18n.t("ws.advancedControls")),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByText(i18n.t("workflow.savedPlan")),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByLabelText(i18n.t("workflow.policy")),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(mode, "advanced");
    expect(button("ws.diagnostics")).toBeVisible();
    expect(screen.getByText(i18n.t("localApi"))).toBeVisible();
    expect(screen.getByLabelText(i18n.t("workflow.policy"))).toBeVisible();
    expect(localStorage.getItem("enrichment.mode")).toBe("advanced");
});

it("Basic runs the whole scenario with the second layer out of sight", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    await generate();
    // Column types and the column inspector belong to Advanced.
    await navigate("dataset");
    expect(
        screen.queryByRole("complementary", { name: i18n.t("inspector") }),
    ).not.toBeInTheDocument();
    await navigate("plan");
    absent("workflow.validate");
    expect(screen.queryByText("Sheet1 · A1:E11")).not.toBeInTheDocument();
    await userEvent.click(button("ws.approveContinue"));
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    // A finished run offers the way forward, not a repeat of the same run.
    absent("ws.retryPlan");
    expect(
        screen.queryByText(i18n.t("workflow.created")),
    ).not.toBeInTheDocument();
    await userEvent.click(button("ws.reviewIssues"));
    expect(screen.getByTestId("review-Berlin")).toBeVisible();
    expect(
        screen.queryByText(i18n.t("review.technical")),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByText(i18n.t("review.errorCodes")),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByText(i18n.t("review.blockedCount", { count: 1 })),
    ).not.toBeInTheDocument();
    await navigate("export");
    expect(button("workflow.downloadXlsx")).toBeEnabled();
    absent("workflow.downloadReport");
    expect(
        screen.queryByLabelText("Workbook filename"),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByRole("button", { name: /Details for row/ }),
    ).not.toBeInTheDocument();
});

it("Basic offers the review step only when there is something to settle", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.getJob).mockResolvedValue({
        ...finished,
        status: "completed",
    });
    render(<App />);
    const reviewTab = () =>
        screen.getByRole("navigation").querySelectorAll("button")[3];
    expect(reviewTab()).toBeDisabled();
    await start();
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    expect(reviewTab()).toBeDisabled();
    // The run leads straight to the results, and back from there to the run.
    await userEvent.click(button("ws.viewOutput"));
    expect(button("workflow.downloadXlsx")).toBeEnabled();
    await userEvent.click(
        within(
            document.querySelector<HTMLElement>(".workspace-actions")!,
        ).getByRole("button", { name: i18n.t("execution") }),
    );
    expect(screen.getByTestId("job-status")).toBeVisible();
    // Advanced keeps every step reachable.
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "advanced",
    );
    expect(reviewTab()).toBeEnabled();
    // Back in Basic, a step it does not offer gives way to the run.
    await navigate("review");
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "basic",
    );
    expect(await screen.findByTestId("job-status")).toBeVisible();
});

it("Basic says a prepared plan is in use and lets the user drop it", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.samples).mockResolvedValue({
        samples: [
            {
                sample_id: "capitals",
                filename: "capitals.xlsx",
                instruction: "Distance between the cities",
                output: { name: "distance", column: "E" },
                has_plan: true,
            },
        ],
    });
    vi.mocked(api.sampleWorkbook).mockResolvedValue(new Blob(["x"]));
    vi.mocked(api.samplePlan).mockResolvedValue(draft.plan!);
    render(<App />);
    await userEvent.click(
        await screen.findByRole("button", { name: i18n.t("sample.capitals") }),
    );
    await userEvent.click(
        await screen.findByRole("button", { name: i18n.t("sample.usePlan") }),
    );
    expect(await screen.findByText(i18n.t("sample.planLoaded"))).toBeVisible();
    expect(button("workflow.submitPlan")).toBeEnabled();
    await userEvent.click(button("sample.clearPlan"));
    expect(
        screen.queryByText(i18n.t("sample.planLoaded")),
    ).not.toBeInTheDocument();
    expect(button("ws.buildPlan")).toBeVisible();
});

it("names what it is waiting for, counts the time and says what is locked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
        let finish!: (value: PlanDraft) => void;
        vi.mocked(api.createPlan).mockReturnValueOnce(
            new Promise((done) => {
                finish = done;
            }),
        );
        vi.mocked(api.getJob).mockResolvedValue({
            ...queued,
            status: "running",
        });
        render(<App />);
        await prepare();
        await userEvent.click(button("ws.buildPlan"));
        // A short request: named, timed, and it keeps the user on its step.
        const bar = () => document.querySelector<HTMLElement>(".activity-bar")!;
        expect(within(bar()).getByRole("status")).toHaveTextContent(
            i18n.t("activity.plan"),
        );
        expect(within(bar()).getByRole("timer")).toHaveTextContent("0:00");
        await act(() => vi.advanceTimersByTimeAsync(7000));
        expect(within(bar()).getByRole("timer")).toHaveTextContent("0:07");
        const tabs = () =>
            within(screen.getByRole("navigation")).getAllByRole("button");
        expect(tabs()[1]).toBeDisabled();
        await act(async () => finish(draft));
        expect(document.querySelector(".activity-bar")).toBeNull();
        expect(tabs()[0]).toBeEnabled();
        // A running job: other steps stay reachable, and say why they are locked.
        await userEvent.click(button("ws.approveContinue"));
        await userEvent.click(button("workflow.run"));
        expect(
            await within(bar()).findByText(i18n.t("activity.running")),
        ).toBeVisible();
        expect(bar()).toHaveTextContent("180");
        expect(
            within(bar()).queryByText(i18n.t("activity.locked")),
        ).not.toBeInTheDocument();
        await navigate("dataset");
        expect(
            within(bar()).getByText(i18n.t("activity.locked")),
        ).toBeVisible();
        await userEvent.click(
            within(bar()).getByRole("button", {
                name: i18n.t("activity.open"),
            }),
        );
        expect(screen.getByTestId("job-status")).toBeVisible();
    } finally {
        vi.useRealTimers();
    }
});

it("takes the bounds of one run from the server", async () => {
    vi.mocked(api.limits).mockResolvedValue({
        max_run_rows: 500,
        max_timeout_seconds: 1800,
        default_timeout_seconds: 180,
    });
    // A table of 300 rows is beyond the demo's 20 and within this server's 500.
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:E301",
        profile: { ...preview.profile, row_count: 300 },
    });
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "source.xlsx",
        worksheets: [
            {
                name: "Sheet1",
                columns: preview.profile.columns.map((column) => column.name),
                row_count: 301,
            },
        ],
    });
    render(<App />);
    await generate();
    await userEvent.click(button("ws.approveContinue"));
    await navigate("dataset");
    await userEvent.click(screen.getByText("Advanced policy and limits"));
    const timeout = screen.getByLabelText(i18n.t("workflow.timeout"));
    await waitFor(() => expect(timeout).toHaveAttribute("max", "1800"));
    fireEvent.change(timeout, { target: { value: "900" } });
    await navigate("execution");
    expect(button("workflow.run")).toBeEnabled();
    await navigate("dataset");
    fireEvent.change(screen.getByLabelText(i18n.t("workflow.timeout")), {
        target: { value: "1801" },
    });
    await navigate("execution");
    expect(button("workflow.run")).toBeDisabled();
});

it("says in Review that a row's country was not recognized, as it was written", async () => {
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [],
        affected_rows: 0,
        informational: [
            ...review.informational,
            {
                ...review.informational[0],
                operation_id: "op-london",
                query: {
                    name: "London",
                    entity_type: "city",
                    context: { country: "UK" },
                },
                status: "incomplete_verification",
                reason_codes: ["country_not_recognized"],
                search_complete: false,
            },
        ],
    });
    render(<App />);
    await start();
    await navigate("review");
    const london = within(await screen.findByTestId("unresolved-London"));
    expect(london.getByText(/UK/)).toBeVisible();
    expect(
        london.getByText(i18n.t("review.input.country_not_recognized")),
    ).toBeVisible();
    // Another reason keeps the wording of its status.
    expect(
        within(screen.getByTestId("unresolved-Oslo")).getByText(
            i18n.t("review.status.unresolved"),
        ),
    ).toBeVisible();
});
