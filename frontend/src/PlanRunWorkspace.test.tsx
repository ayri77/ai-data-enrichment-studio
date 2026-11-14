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
    type ReviewItem,
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
        searchOtherName: vi.fn(),
        suggestNames: vi.fn(),
        suggestTasks: vi.fn(),
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
// A card with a choice is folded: it shows the choice and a way back to the list.
const chosen = (name: string) =>
    within(screen.getByTestId(`review-${name}`)).getByTestId("review-chosen");
const reopen = (name: string) =>
    userEvent.click(
        within(chosen(name)).getByRole("button", {
            name: i18n.t("review.change"),
        }),
    );

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
// Basic: the table is previewed on upload, the only empty column is proposed,
// and pressing the button is the consent.
async function prepareBasic() {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    await waitFor(() =>
        expect(screen.getByLabelText(i18n.t("ws.fillColumn"))).toHaveValue("E"),
    );
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: "Literal cities" },
    });
}
async function generateBasic() {
    await prepareBasic();
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
}
// Basic approves and starts with one press.
async function startBasic() {
    await generateBasic();
    await userEvent.click(button("ws.approveRun"));
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
        // Default: a cell that already holds a value is kept.
        existing_outputs: "keep",
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

it("fills the first rows of a table longer than one run accepts, as many as asked for", async () => {
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:E1001",
        profile: { ...preview.profile, row_count: 1000 },
        rows: {
            row_numbers: Array.from({ length: 1000 }, (_, index) => index + 2),
            values: Array.from({ length: 1000 }, () => [
                "UA",
                "Kyiv",
                "PL",
                "Warsaw",
                null,
            ]),
        },
    });
    render(<App />);
    await prepare();
    // The length of the table is not a reason to refuse: a run takes its first rows.
    const rows = within(screen.getByTestId("run-rows"));
    const count = rows.getByLabelText(i18n.t("run.rows"));
    expect(count).toHaveValue(20);
    expect(screen.getByTestId("run-rows")).toHaveTextContent(
        i18n.t("run.rowsFrom", { count: 20, total: 1000, first: 2, last: 21 }),
    );
    expect(screen.getByTestId("run-rows")).toHaveTextContent(
        i18n.t("run.rowsLimit", { max: 20 }),
    );
    expect(document.querySelector("[data-requirement]")).toBeNull();
    // More than a run accepts is said at the field, and nothing is sent.
    fireEvent.change(count, { target: { value: "21" } });
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(
        document.querySelector('[data-requirement="selection"]'),
    ).toHaveTextContent("20");
    expect(screen.getByTestId("run-rows")).toHaveTextContent(
        i18n.t("run.rowsInvalid", { max: 20 }),
    );
    fireEvent.change(count, { target: { value: "5" } });
    await userEvent.click(button("ws.buildPlan"));
    // The plan is asked for the run's rows; the table shown stays whole.
    expect(api.createPlan).toHaveBeenCalledWith(
        expect.objectContaining({ range_ref: "A1:E6" }),
    );
});

it("rejects malformed supplied JSON before API submission", async () => {
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
    // The map opens over the page, on the option it was asked for.
    await userEvent.click(
        berlin.getAllByRole("button", { name: i18n.t("review.link.map") })[1],
    );
    const map = () => screen.getByTestId("candidate-map");
    const frame = () => map().querySelector("iframe")!;
    expect(within(map()).getByTestId("map-position")).toHaveTextContent(
        // The two options that can be chosen; the ruled-out one is listed apart.
        i18n.t("review.map.position", { index: 2, total: 2 }),
    );
    expect(frame().src).toContain("openstreetmap.org/export/embed.html");
    expect(frame().src).toContain("marker=52.52000,13.40000");
    // Stepping to a neighbour and changing the zoom stay in the same window.
    await userEvent.click(
        within(map()).getByRole("button", {
            name: i18n.t("review.map.previous"),
        }),
    );
    expect(within(map()).getByTestId("map-position")).toHaveTextContent(
        i18n.t("review.map.position", { index: 1, total: 2 }),
    );
    const regional = frame().src;
    await userEvent.click(
        within(map()).getByRole("button", {
            name: i18n.t("review.map.closer"),
        }),
    );
    expect(frame().src).not.toBe(regional);
    await userEvent.click(
        within(map()).getByRole("button", { name: i18n.t("ws.close") }),
    );
    expect(screen.queryByTestId("candidate-map")).toBeNull();
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
    // The decided card folds to its choice; the long list is out of the way.
    expect(chosen("Kyiv")).toHaveTextContent(i18n.t("review.leaveEmpty"));
    expect(
        within(screen.getByTestId("review-Kyiv")).queryByRole("radio"),
    ).toBeNull();
    expect(screen.getByTestId("review-Berlin")).toHaveAttribute(
        "data-folded",
        "false",
    );

    // A recommendation is applied only when the person accepts it.
    expect(radio("2950159")).not.toBeChecked();
    await userEvent.click(button("review.accept"));
    expect(chosen("Berlin")).toHaveTextContent("Berlin");
    expect(button("review.accept")).toBeDisabled();
    // "Change" brings the list back with the choice still marked.
    await reopen("Berlin");
    expect(radio("2950159")).toBeChecked();
    // An open card that has a choice can be folded by hand, and from the keyboard
    // a choice does not fold it: arrow keys select while moving.
    await userEvent.click(
        within(screen.getByTestId("review-Berlin")).getByRole("button", {
            name: i18n.t("review.fold"),
        }),
    );
    // The keyboard stays on the card: focus moves to "Change".
    expect(
        within(chosen("Berlin")).getByRole("button", {
            name: i18n.t("review.change"),
        }),
    ).toHaveFocus();
    await reopen("Berlin");
    radio("2950159").focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByTestId("review-Berlin")).toHaveAttribute(
        "data-folded",
        "false",
    );
    await userEvent.click(radio("2950157"));
    expect(
        within(screen.getByTestId("review-Berlin")).queryByRole("radio"),
    ).toBeNull();
    await reopen("Berlin");
    expect(radio("2950159")).not.toBeChecked();
    expect(radio("2950157")).toBeChecked();

    // Choices survive navigation and language changes; decided cards come back folded.
    await navigate("execution");
    await navigate("review");
    expect(screen.getByTestId("review-Berlin")).toHaveAttribute(
        "data-folded",
        "true",
    );
    await reopen("Berlin");
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

it("says before the run that the chosen column already has values, and asks what to do with them", async () => {
    // Rows 4 and 9 of the worksheet already have a distance.
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        rows: {
            row_numbers: Array.from({ length: 10 }, (_, index) => index + 2),
            values: Array.from({ length: 10 }, (_, index) => [
                "UA",
                "Kyiv",
                "PL",
                "Warsaw",
                index === 2 ? 99999 : index === 7 ? "=1+1" : null,
            ]),
        },
    });
    render(<App />);
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
    const notice = within(screen.getByTestId("existing-outputs"));
    expect(
        notice.getByText(
            i18n.t("existing.found", {
                column: "distance",
                count: 2,
                total: 10,
                rows: "4, 9",
            }),
        ),
    ).toBeInTheDocument();
    // Keeping is the default; replacing has to be asked for.
    expect(notice.getByLabelText(i18n.t("existing.keep"))).toBeChecked();
    await userEvent.click(notice.getByLabelText(i18n.t("existing.replace")));
    await userEvent.click(screen.getByLabelText(i18n.t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    expect(api.createPlan).toHaveBeenCalledWith(
        expect.objectContaining({ existing_outputs: "replace" }),
    );
});

it("makes applying the choices the next step once something is chosen", async () => {
    render(<App />);
    await start();
    await navigate("review");
    await screen.findByTestId("review-Kyiv");
    // Nothing chosen: the way on is to the results, and there is nothing to apply.
    expect(screen.queryByTestId("review-unapplied")).toBeNull();
    expect(button("ws.toExport")).toBeEnabled();
    expect(button("review.resume")).toBeDisabled();
    await userEvent.click(radio("703448"));
    expect(screen.getByTestId("review-unapplied")).toHaveTextContent(
        i18n.t("review.unapplied"),
    );
    expect(button("review.resume")).toBeEnabled();
    // Leaving for the results is still possible, and says what it means.
    expect(
        screen.queryByRole("button", { name: i18n.t("ws.toExport") }),
    ).toBeNull();
    await userEvent.click(button("review.toResultsUnfilled"));
    expect(button("workflow.downloadReport")).toBeInTheDocument();
});

it("makes the choice from the map, without going back to find the option in the list", async () => {
    render(<App />);
    await start();
    await navigate("review");
    const kyiv = within(await screen.findByTestId("review-Kyiv"));
    await userEvent.click(
        kyiv.getAllByRole("button", { name: i18n.t("review.link.map") })[0],
    );
    const map = within(screen.getByTestId("candidate-map"));
    await userEvent.click(
        map.getByRole("button", { name: i18n.t("review.map.next") }),
    );
    await userEvent.click(
        map.getByRole("button", { name: i18n.t("review.map.choose") }),
    );
    // The window closes and the card shows what was chosen there.
    expect(screen.queryByTestId("candidate-map")).toBeNull();
    expect(chosen("Kyiv")).toBeInTheDocument();
    expect(button("review.resume")).toBeEnabled();
    expect(screen.getByTestId("review-progress")).toHaveTextContent(
        i18n.t("review.progress", { decided: 1, total: 2 }),
    );
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
    await reopen("Kyiv");
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

it("searches a name under another one at once and offers what the source confirmed", async () => {
    const [berlin, kyiv] = review.items;
    const [oslo] = review.informational;
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [berlin, { ...kyiv, other_name: true }],
        informational: [
            {
                ...oslo,
                other_name: true,
                query: { ...oslo.query, searched_as: "Christiania" },
            },
        ],
    });
    const found = (item: ReviewItem, name: string, ...ids: string[]) => ({
        ...item,
        status: ids.length ? "needs_review" : "unresolved",
        query: { ...item.query, searched_as: name },
        candidates: ids.map((id) => place(id, { label: name })),
    });
    vi.mocked(api.searchOtherName).mockImplementation(
        async (_job, operationId, name) =>
            operationId === "op-oslo"
                ? found(
                      oslo,
                      name,
                      ...(name === "Kristiania" ? ["3143244"] : []),
                  )
                : found(kyiv, name, "703448", "9001"),
    );
    render(<App />);
    await start();
    await navigate("review");
    const search = { name: i18n.t("review.otherName.search") };
    const unmatched = within(await screen.findByTestId("unresolved-Oslo"));
    // The name tried by the earlier run is shown; the row's own value cannot be searched again.
    expect(unmatched.getByTestId("searched-as")).toHaveTextContent(
        i18n.t("review.searchedAs", { name: "Christiania" }),
    );
    const field = unmatched.getByLabelText(
        i18n.t("review.otherName.label", { name: "Oslo" }),
    );
    await userEvent.type(field, " oslo ");
    expect(unmatched.getByRole("button", search)).toBeDisabled();

    // Nothing confirmed under one name: it says so and another can be tried.
    await userEvent.clear(field);
    await userEvent.type(field, "Kristiana{Enter}");
    expect(api.searchOtherName).toHaveBeenLastCalledWith(
        "job",
        "op-oslo",
        "Kristiana",
        true,
    );
    expect(
        await unmatched.findByText(
            i18n.t("review.otherName.none", { name: "Kristiana" }),
        ),
    ).toBeVisible();
    expect(button("review.resume")).toBeDisabled();

    await userEvent.clear(field);
    await userEvent.type(field, " Kristiania ");
    await userEvent.click(unmatched.getByRole("button", search));
    const match = await unmatched.findByTestId("found-open-meteo:3143244");
    // A search writes nothing: the match still has to be chosen.
    expect(button("review.resume")).toBeDisabled();
    await userEvent.click(within(match).getByRole("radio"));
    const chosenOslo = unmatched.getByTestId("review-chosen");
    expect(chosenOslo).toHaveTextContent("Kristiania");
    expect(chosenOslo).toHaveTextContent(
        i18n.t("review.otherName.foundAs", { name: "Kristiania" }),
    );
    expect(button("review.resume")).toBeEnabled();
    // The choice can be taken back; the found match stays on offer.
    await userEvent.click(
        within(chosenOslo).getByRole("button", {
            name: i18n.t("review.otherName.clear"),
        }),
    );
    expect(button("review.resume")).toBeDisabled();
    await userEvent.click(
        within(unmatched.getByTestId("found-open-meteo:3143244")).getByRole(
            "radio",
        ),
    );

    // A name that has candidates can be searched under another one as well;
    // the same place found by the search is a separate option.
    const card = within(screen.getByTestId("review-Kyiv"));
    await userEvent.type(
        card.getByLabelText(i18n.t("review.otherName.label", { name: "Kyiv" })),
        "Kiev{Enter}",
    );
    const again = await card.findByTestId("found-open-meteo:703448");
    expect(radio("703448")).not.toBeChecked();
    await userEvent.click(within(again).getByRole("radio"));
    expect(chosen("Kyiv")).toHaveTextContent(
        i18n.t("review.otherName.foundAs", { name: "Kiev" }),
    );
    expect(screen.getByTestId("review-progress")).toHaveTextContent(
        i18n.t("review.progress", { decided: 1, total: 2 }),
    );
    // Berlin was not offered a search.
    expect(
        within(screen.getByTestId("review-Berlin")).queryByText(
            i18n.t("review.otherName.toggle"),
        ),
    ).toBeNull();

    vi.mocked(api.resumeJob).mockResolvedValue({
        ...queued,
        job_id: "job-2",
        previous_job_id: "job",
    });
    await userEvent.click(button("review.resume"));
    expect(api.resumeJob).toHaveBeenCalledWith(
        "job",
        [
            {
                operation_id: "op-kyiv",
                source: "open-meteo",
                entity_id: "703448",
                name: "Kiev",
            },
            {
                operation_id: "op-oslo",
                source: "open-meteo",
                entity_id: "3143244",
                name: "Kristiania",
            },
        ],
        true,
        180,
    );
});

it("offers the fill for a run with nothing to choose once a search has a chosen match", async () => {
    const [oslo] = review.informational;
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [],
        affected_rows: 0,
        informational: [{ ...oslo, other_name: true }],
    });
    vi.mocked(api.searchOtherName)
        .mockRejectedValueOnce(
            new WorkspaceApiError("search_failed", "sanitized"),
        )
        .mockResolvedValue({
            ...oslo,
            status: "provider_failure",
            query: { ...oslo.query, searched_as: "Kristiania" },
        });
    render(<App />);
    await start();
    await navigate("review");
    await screen.findByTestId("unresolved-Oslo");
    expect(screen.queryByTestId("review-progress")).toBeNull();
    expect(
        screen.queryByRole("button", { name: i18n.t("review.accept") }),
    ).toBeNull();
    expect(button("review.resume")).toBeDisabled();
    const field = screen.getByLabelText(
        i18n.t("review.otherName.label", { name: "Oslo" }),
    );
    // A refused or broken search is reported like any other request.
    await userEvent.type(field, "Kristiania{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
        i18n.t("error.search_failed"),
    );
    // A source that did not answer is not "no match".
    await userEvent.type(field, "{Enter}");
    expect(
        await screen.findByText(
            i18n.t("review.otherName.failed", { name: "Kristiania" }),
        ),
    ).toBeVisible();
    expect(button("review.resume")).toBeDisabled();
});

it("Basic fills several columns: each is asked for plainly and new ones stand side by side", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    await prepareBasic();
    const another = () => screen.getAllByLabelText(i18n.t("ws.fillAnother"));
    const names = () => screen.getAllByLabelText(i18n.t("ws.newColumnName"));
    // One column needs no button to take it away.
    expect(
        screen.queryByLabelText(i18n.t("ws.removeFill", { n: 1 })),
    ).toBeNull();
    await userEvent.click(button("ws.addFill"));
    // A column is filled once: the one already chosen cannot be chosen again.
    expect(
        within(another()[0]).getByRole("option", { name: "distance (E)" }),
    ).toBeDisabled();
    await userEvent.selectOptions(another()[0], i18n.t("ws.newColumnOption"));
    await userEvent.type(names()[0], "road");
    await userEvent.click(button("ws.addFill"));
    await userEvent.selectOptions(another()[1], i18n.t("ws.newColumnOption"));
    await userEvent.type(names()[1], "detour");
    await userEvent.click(button("ws.buildPlan"));
    await waitFor(() =>
        expect(api.createPlan).toHaveBeenLastCalledWith(
            expect.objectContaining({
                // New columns follow the sheet's last one, in the order asked.
                output_columns: { distance: "E", road: "F", detour: "G" },
            }),
        ),
    );
});

it("Basic closes the gap when a new column is taken away again", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    await prepareBasic();
    for (const name of ["road", "detour"]) {
        await userEvent.click(button("ws.addFill"));
        const selects = screen.getAllByLabelText(i18n.t("ws.fillAnother"));
        await userEvent.selectOptions(
            selects[selects.length - 1],
            i18n.t("ws.newColumnOption"),
        );
        const fields = screen.getAllByLabelText(i18n.t("ws.newColumnName"));
        await userEvent.type(fields[fields.length - 1], name);
    }
    await userEvent.click(
        screen.getByLabelText(i18n.t("ws.removeFill", { n: 2 })),
    );
    await userEvent.click(button("ws.buildPlan"));
    await waitFor(() =>
        expect(api.createPlan).toHaveBeenLastCalledWith(
            expect.objectContaining({
                output_columns: { distance: "E", detour: "F" },
            }),
        ),
    );
});

it("an example that fills three columns arrives with all three chosen", async () => {
    localStorage.removeItem("enrichment.mode");
    const columns = [
        "country",
        "city",
        "country2",
        "city2",
        "distance",
        "road",
        "detour",
    ];
    vi.mocked(api.samples).mockResolvedValue({
        samples: [
            {
                sample_id: "capitals_roads",
                filename: "capitals_roads.xlsx",
                instruction: "Three values for each pair",
                outputs: [
                    { name: "distance", column: "E" },
                    { name: "road", column: "F" },
                    { name: "detour", column: "G" },
                ],
                has_plan: true,
            },
        ],
    });
    vi.mocked(api.sampleWorkbook).mockResolvedValue(new Blob(["x"]));
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "capitals_roads.xlsx",
        worksheets: [{ name: "Sheet1", columns, row_count: 11 }],
    });
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:G11",
        profile: {
            ...preview.profile,
            columns: columns.map((name, index) => ({
                name,
                inferred_type: "Text",
                null_count: index > 3 ? 10 : 0,
                warnings: [],
            })),
        },
    });
    render(<App />);
    await userEvent.click(
        await screen.findByRole("button", {
            name: i18n.t("sample.capitals_roads"),
        }),
    );
    await waitFor(() =>
        expect(screen.getByLabelText(i18n.t("ws.fillColumn"))).toHaveValue("E"),
    );
    expect(
        screen
            .getAllByLabelText(i18n.t("ws.fillAnother"))
            .map((select) => (select as HTMLSelectElement).value),
    ).toEqual(["F", "G"]);
    expect(screen.getByLabelText("Enrichment instruction")).toHaveValue(
        "Three values for each pair",
    );
    // Asked for a task now, the AI is told all three columns.
    vi.mocked(api.suggestTasks).mockResolvedValue({ tasks: ["One task"] });
    await userEvent.click(button("task.suggest"));
    expect(api.suggestTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({
            fill_columns: ["distance", "road", "detour"],
        }),
    );
    await userEvent.click(button("ws.buildPlan"));
    await waitFor(() =>
        expect(api.createPlan).toHaveBeenLastCalledWith(
            expect.objectContaining({
                range_ref: "A1:G11",
                output_columns: { distance: "E", road: "F", detour: "G" },
            }),
        ),
    );
});

it("asks the AI for one task that fills all chosen columns, and again when they change", async () => {
    vi.mocked(api.suggestTasks).mockResolvedValue({
        tasks: ["Fill distance and city2."],
    });
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    await screen.findByLabelText(i18n.t("workflow.instruction"));
    await userEvent.click(screen.getByLabelText(i18n.t("workflow.network")));
    // One chosen column is the open question: no columns are stated.
    expect(screen.queryByText(i18n.t("task.forColumns", { n: 2 }))).toBeNull();
    await userEvent.click(button("workflow.addMapping"));
    await userEvent.selectOptions(
        screen.getByLabelText(`${i18n.t("ws.targetColumn")} 2`),
        "D",
    );
    expect(screen.getByText(i18n.t("task.forColumns", { n: 2 }))).toBeVisible();
    await userEvent.click(button("task.suggest"));
    expect(api.suggestTasks).toHaveBeenLastCalledWith({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:E11",
        language: "en",
        allow_network: true,
        fill_columns: ["distance", "city2"],
    });
    const ideas = within(await screen.findByTestId("task-ideas"));
    expect(ideas.getByText(i18n.t("task.proposedOne"))).toBeVisible();
    expect(ideas.getAllByRole("button")).toHaveLength(1);
    // The proposal belonged to those two columns; with others it is asked anew.
    await userEvent.selectOptions(
        screen.getByLabelText(`${i18n.t("ws.targetColumn")} 2`),
        "B",
    );
    expect(screen.queryByTestId("task-ideas")).toBeNull();
    expect(button("task.suggest")).toBeEnabled();
    // No task that fills them all is said as such, not as "nothing for this table".
    vi.mocked(api.suggestTasks).mockResolvedValue({ tasks: [] });
    await userEvent.click(button("task.suggest"));
    expect(await screen.findByTestId("task-ideas")).toHaveTextContent(
        i18n.t("task.noneForColumns"),
    );
});

it("asks the AI for a task in the interface language and puts the chosen one into the field", async () => {
    const tasks = [
        "Знайди відстань між столицями й запиши у «distance».",
        "Знайди країну кожної столиці.",
    ];
    vi.mocked(api.suggestTasks)
        .mockRejectedValueOnce(
            new WorkspaceApiError("task_ideas_failed", "sanitized"),
        )
        .mockImplementation(async ({ language }) => ({
            tasks: language === "uk" ? tasks : [],
        }));
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    const field = await screen.findByLabelText(i18n.t("workflow.instruction"));
    // Advanced: nothing is sent before the person allows it.
    expect(button("task.suggest")).toBeDisabled();
    expect(screen.getByText(i18n.t("task.needsConsent"))).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(i18n.t("workflow.network")));
    expect(screen.getByText(i18n.t("task.sends"))).toBeInTheDocument();
    await userEvent.click(button("task.suggest"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        i18n.t("error.task_ideas_failed"),
    );
    await act(() => i18n.changeLanguage("uk"));
    await userEvent.click(button("task.suggest"));
    expect(api.suggestTasks).toHaveBeenLastCalledWith({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:E11",
        language: "uk",
        allow_network: true,
    });
    const ideas = within(await screen.findByTestId("task-ideas"));
    // A proposal is only text: the field is the person's until one is chosen.
    expect(field).toHaveValue("");
    expect(api.createPlan).not.toHaveBeenCalled();
    await userEvent.click(ideas.getByRole("button", { name: tasks[0] }));
    expect(field).toHaveValue(tasks[0]);
    expect(ideas.getByRole("button", { name: tasks[0] })).toHaveAttribute(
        "aria-pressed",
        "true",
    );
    // The text can be edited like any typed instruction.
    await userEvent.type(field, " Км.");
    expect(field).toHaveValue(`${tasks[0]} Км.`);
    // Another language is another question; an empty answer says so.
    await act(() => i18n.changeLanguage("de"));
    await userEvent.click(button("task.suggest"));
    expect(await screen.findByTestId("task-ideas")).toHaveTextContent(
        i18n.t("task.none"),
    );
    expect(field).toHaveValue(`${tasks[0]} Км.`);
});

it("asks the AI for other names and searches the one that is chosen", async () => {
    const [oslo] = review.informational;
    const [berlin, kyiv] = review.items;
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [berlin, { ...kyiv, other_name: true }],
        informational: [{ ...oslo, other_name: true }],
    });
    vi.mocked(api.suggestNames)
        .mockRejectedValueOnce(
            new WorkspaceApiError("suggestions_failed", "sanitized"),
        )
        .mockImplementation(async (_job, operationId) => ({
            operation_id: operationId,
            names:
                operationId === "op-oslo" ? ["Kristiania", "Christiania"] : [],
        }));
    vi.mocked(api.searchOtherName).mockResolvedValue({
        ...oslo,
        status: "resolved",
        query: { ...oslo.query, searched_as: "Kristiania" },
        candidates: [place("3143244", { label: "Kristiania" })],
    });
    render(<App />);
    await start();
    await navigate("review");
    const ask = { name: i18n.t("review.ai.ask") };
    const unmatched = within(await screen.findByTestId("unresolved-Oslo"));
    // What leaves the workspace is said before anything is sent.
    expect(unmatched.getByText(i18n.t("review.ai.sends"))).toBeInTheDocument();
    await userEvent.click(unmatched.getByRole("button", ask));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        i18n.t("error.suggestions_failed"),
    );
    await userEvent.click(unmatched.getByRole("button", ask));
    expect(api.suggestNames).toHaveBeenLastCalledWith("job", "op-oslo", true);
    const names = within(await unmatched.findByTestId("ai-names"));
    expect(names.getByText(i18n.t("review.ai.proposed"))).toBeInTheDocument();
    // A proposal is not a result: nothing is searched or chosen yet.
    expect(api.searchOtherName).not.toHaveBeenCalled();
    expect(button("review.resume")).toBeDisabled();
    await userEvent.click(names.getByRole("button", { name: "Kristiania" }));
    expect(api.searchOtherName).toHaveBeenCalledWith(
        "job",
        "op-oslo",
        "Kristiania",
        true,
    );
    expect(
        unmatched.getByLabelText(
            i18n.t("review.otherName.label", { name: "Oslo" }),
        ),
    ).toHaveValue("Kristiania");
    // The source's answer still has to be chosen.
    await unmatched.findByTestId("found-open-meteo:3143244");
    expect(button("review.resume")).toBeDisabled();

    // No proposals is said as such.
    const card = within(screen.getByTestId("review-Kyiv"));
    await userEvent.click(card.getByRole("button", ask));
    expect(await card.findByTestId("ai-names")).toHaveTextContent(
        i18n.t("review.ai.none", { name: "Kyiv" }),
    );
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
        supported_models: [
            { id: "gpt-4.1-mini", label: "gpt-4.1-mini", measured: true },
            { id: "gpt-6-luna", label: "gpt-6-luna", measured: false },
        ],
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
    const select = screen.getByLabelText(i18n.t("provider.model"));
    // The list is the server's catalog; an unmeasured model says so, in the
    // list and once it is selected.
    expect(
        within(select)
            .getAllByRole("option")
            .map((option) => option.textContent),
    ).toEqual([
        i18n.t("provider.chooseModel"),
        "gpt-4.1-mini",
        i18n.t("provider.notMeasured", { model: "gpt-6-luna" }),
    ]);
    await userEvent.selectOptions(select, "gpt-4.1-mini");
    expect(screen.queryByTestId("model-not-measured")).toBeNull();
    await userEvent.selectOptions(select, "gpt-6-luna");
    expect(screen.getByTestId("model-not-measured")).toHaveTextContent(
        i18n.t("provider.notMeasuredHint"),
    );
});

it("keeps the task when the boundaries of the table are adjusted", async () => {
    render(<App />);
    await prepare();
    await userEvent.click(button("ws.buildPlan"));
    await waitFor(() => expect(api.createPlan).toHaveBeenCalledTimes(1));
    await navigate("dataset");
    // Another last row: the plan made for the old selection is gone …
    fireEvent.change(screen.getByLabelText("Last row"), {
        target: { value: "8" },
    });
    await userEvent.click(button("previewAction"));
    await waitFor(() =>
        expect(api.preview).toHaveBeenLastCalledWith(
            "source",
            "Sheet1",
            "A1:E8",
        ),
    );
    // … but what was asked for and where it goes are still there.
    expect(screen.getByLabelText("Enrichment instruction")).toHaveValue(
        "Literal cities",
    );
    expect(screen.getByLabelText("Output column 1")).toHaveValue("E");
    await userEvent.click(button("ws.buildPlan"));
    await waitFor(() => expect(api.createPlan).toHaveBeenCalledTimes(2));
    expect(api.createPlan).toHaveBeenLastCalledWith(
        expect.objectContaining({
            instruction: "Literal cities",
            output_columns: { distance: "E" },
        }),
    );
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
        outputs: [{ name: "distance", column: "E" }],
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
    await generateBasic();
    // Column types and the column inspector belong to Advanced.
    await navigate("dataset");
    expect(
        screen.queryByRole("complementary", { name: i18n.t("inspector") }),
    ).not.toBeInTheDocument();
    await navigate("plan");
    absent("workflow.validate");
    expect(screen.queryByText("Sheet1 · A1:E11")).not.toBeInTheDocument();
    await userEvent.click(button("ws.approveRun"));
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
    await startBasic();
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
                outputs: [{ name: "distance", column: "E" }],
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

it("names a road distance among what it can do when the server offers it", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.limits).mockResolvedValue({
        max_run_rows: 20,
        max_timeout_seconds: 300,
        default_timeout_seconds: 180,
        capabilities: ["geocode", "geodesic_distance", "road_distance"],
    });
    render(<App />);
    expect(await screen.findByText(i18n.t("can.roadDistance"))).toBeVisible();
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

it("Basic previews the table on upload, proposes the empty column and takes the button as consent", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    // Before a workbook there is a plain statement of what can be asked for.
    expect(screen.getByText(i18n.t("can.cityDistance"))).toBeVisible();
    // A road distance is named only where the server offers it.
    expect(screen.queryByText(i18n.t("can.roadDistance"))).toBeNull();
    expect(screen.getByText(i18n.t("can.limit", { max: 20 }))).toBeVisible();
    await prepareBasic();
    expect(api.preview).toHaveBeenCalledWith("source", "Sheet1", "A1:E11");
    absent("previewAction");
    expect(
        screen.queryByLabelText(i18n.t("workflow.network")),
    ).not.toBeInTheDocument();
    // What leaves the browser is said next to the button instead.
    expect(screen.getByTestId("sends")).toHaveTextContent("OpenAI");
    expect(screen.getByTestId("sends")).toHaveTextContent("Wikidata");
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
    expect(api.createPlan).toHaveBeenCalledWith(
        expect.objectContaining({
            range_ref: "A1:E11",
            output_columns: { distance: "E" },
            allow_network: true,
        }),
    );
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() =>
        expect(api.startRun).toHaveBeenCalledWith("plan", true, 180),
    );
    // The consent given by the button is a ticked checkbox in Advanced.
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "advanced",
    );
    await navigate("dataset");
    expect(screen.getByLabelText(i18n.t("workflow.network"))).toBeChecked();
});

it("Basic reads the whole table, however many rows one run accepts", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "long.xlsx",
        worksheets: [
            {
                name: "Sheet1",
                columns: preview.profile.columns.map((x) => x.name),
                row_count: 100,
            },
        ],
    });
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:E101",
        profile: { ...preview.profile, row_count: 100 },
    });
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "long.xlsx"),
    );
    await waitFor(() =>
        expect(api.preview).toHaveBeenCalledWith("source", "Sheet1", "A1:E101"),
    );
    // Nothing was left out, so there is nothing to say about it; the run's
    // rows are stated where the task is written.
    expect(await screen.findByTestId("run-rows")).toHaveTextContent(
        i18n.t("run.rowsOf", { count: 20, total: 100 }),
    );
    expect(screen.queryByTestId("rows-taken")).toBeNull();
});

it("says so when a table is longer than can be read", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "huge.xlsx",
        worksheets: [
            {
                name: "Sheet1",
                columns: preview.profile.columns.map((x) => x.name),
                row_count: 9000,
            },
        ],
    });
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:E5001",
        profile: { ...preview.profile, row_count: 5000 },
    });
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "huge.xlsx"),
    );
    // Five columns: the row bound is reached before the cell bound.
    await waitFor(() =>
        expect(api.preview).toHaveBeenCalledWith(
            "source",
            "Sheet1",
            "A1:E5001",
        ),
    );
    expect(await screen.findByTestId("rows-taken")).toHaveTextContent(
        i18n.t("rowsTaken", { taken: 5000, total: 9000 }),
    );
});

it("Basic reads a table that does not start in A1 from where the server found it", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "report.xlsx",
        worksheets: [
            {
                name: "Regional_Data",
                columns: preview.profile.columns.map((x) => x.name),
                row_count: 40,
                // A title and a note above, two empty columns to the left.
                table: {
                    first_row: 4,
                    first_column: 3,
                    last_row: 44,
                    last_column: 7,
                },
            },
        ],
    });
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        sheet_name: "Regional_Data",
        range_ref: "C4:G44",
        profile: { ...preview.profile, row_count: 40 },
    });
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "report.xlsx"),
    );
    // The header row of the table and every row under it.
    await waitFor(() =>
        expect(api.preview).toHaveBeenCalledWith(
            "source",
            "Regional_Data",
            "C4:G44",
        ),
    );
    expect(await screen.findByTestId("run-rows")).toHaveTextContent(
        i18n.t("run.rowsOf", { count: 20, total: 40 }),
    );
});

it("Basic shows the table boundaries when the table cannot be read, or on request", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.preview).mockRejectedValueOnce(
        new WorkspaceApiError("invalid_headers", "Headers"),
    );
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    // The reason is shown and the boundaries can be corrected by hand.
    expect(await screen.findByRole("alert")).toBeVisible();
    await userEvent.click(button("previewAction"));
    await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(2));
    // Once the table is read, the boundaries step back behind a link.
    await userEvent.click(
        await screen.findByRole("button", { name: i18n.t("adjustTable") }),
    );
    expect(button("previewAction")).toBeVisible();
});

it("Basic can fill a new column placed after the table", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    await prepareBasic();
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("ws.fillColumn")),
        i18n.t("ws.newColumnOption"),
    );
    expect(button("ws.buildPlan")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(i18n.t("ws.newColumnName")), {
        target: { value: "km" },
    });
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
    expect(api.createPlan).toHaveBeenCalledWith(
        expect.objectContaining({ output_columns: { km: "F" } }),
    );
});

it("proposes no column when the table has none or several empty ones", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        profile: {
            ...preview.profile,
            columns: preview.profile.columns
                .map((column) => ({
                    ...column,
                    null_count: column.name.startsWith("c") ? 0 : 10,
                }))
                .map((column, index) =>
                    index === 3 ? { ...column, null_count: 10 } : column,
                ),
        },
    });
    render(<App />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "capitals.xlsx"),
    );
    const fill = await screen.findByLabelText(i18n.t("ws.fillColumn"));
    await waitFor(() => expect(api.preview).toHaveBeenCalled());
    expect(fill).toHaveValue("");
    expect(
        within(
            document.querySelector<HTMLElement>(".build-checklist")!,
        ).getByText(i18n.t("ws.need.mapping")),
    ).toBeVisible();
});

const capitals: PlanDraft = {
    ...draft,
    plan: {
        version: "1.0",
        steps: [
            {
                id: "from_city",
                capability: "geocode",
                inputs: {
                    city: { kind: "column", name: "city" },
                    country: { kind: "column", name: "country" },
                },
                parameters: {},
            },
            {
                id: "to_city",
                capability: "geocode",
                inputs: {
                    city: { kind: "column", name: "city2" },
                    country: { kind: "column", name: "country2" },
                },
                parameters: {},
            },
            {
                id: "distance",
                capability: "geodesic_distance",
                inputs: {
                    point_a: { kind: "step_output", step_id: "from_city" },
                    point_b: { kind: "step_output", step_id: "to_city" },
                },
                parameters: { unit: "km" },
            },
        ],
        outputs: draft.plan!.outputs,
        assumptions: [],
    },
};

it("Basic shows the plan in words instead of the graph, and one press approves and starts it", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.createPlan).mockResolvedValue(capitals);
    vi.mocked(api.approvePlan).mockResolvedValue({
        ...capitals,
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    });
    render(<App />);
    await generateBasic();
    expect(
        screen.getByRole("heading", { name: i18n.t("plan.title") }),
    ).toBeVisible();
    const steps = document.querySelectorAll(".plan-steps li");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveTextContent(i18n.t("plan.do.geocode"));
    expect(steps[0]).toHaveTextContent(
        "City: column “city” · Country: column “country”",
    );
    expect(steps[2]).toHaveTextContent(
        "From: result of step 1 · To: result of step 2",
    );
    expect(steps[2]).toHaveTextContent("Written to column “distance” (E), km");
    // The graph, its inspector and the step ids belong to Advanced.
    expect(
        screen.queryByRole("region", { name: i18n.t("ws.graph") }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("from_city")).not.toBeInTheDocument();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        i18n.t("plan.checked"),
    );
    expect(screen.getByText(i18n.t("plan.runNote"))).toBeVisible();
    absent("ws.approveContinue");
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalledTimes(1));
    // Approval is still a separate request, made first, for this exact plan.
    expect(api.approvePlan).toHaveBeenCalledWith("plan", draft.fingerprint);
    expect(vi.mocked(api.approvePlan).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(api.startRun).mock.invocationCallOrder[0],
    );
    expect(await screen.findByTestId("job-status")).toBeVisible();
    // With a run in place the Plan screen leads to it and cannot start another.
    await navigate("plan");
    absent("ws.approveRun");
    expect(button("ws.toExecution")).toBeEnabled();
    // The same plan in Advanced is the graph.
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "advanced",
    );
    expect(
        screen.getByRole("region", { name: i18n.t("ws.graph") }),
    ).toBeVisible();
    expect(document.querySelector(".plan-steps")).not.toBeInTheDocument();
});

it("Basic keeps the plan approved when the start is refused and offers the start again", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.startRun).mockRejectedValueOnce(
        new WorkspaceApiError("job_active", "sanitized"),
    );
    render(<App />);
    await generateBasic();
    await userEvent.click(button("ws.approveRun"));
    expect(await screen.findByRole("alert")).toHaveAttribute(
        "data-error-code",
        "job_active",
    );
    expect(api.approvePlan).toHaveBeenCalledTimes(1);
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalledTimes(2));
    // The approval is not repeated.
    expect(api.approvePlan).toHaveBeenCalledTimes(1);
});

it("says plainly why there is no plan and what can be asked for instead", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.createPlan).mockResolvedValue({
        ...draft,
        plan: null,
        planning_status: "unsupported",
        reason: "Population is not available.",
        validation: {
            valid: false,
            errors: [{ code: "no_plan", message: "No ready plan." }],
        },
    });
    render(<App />);
    await generateBasic();
    const refusal = screen.getByTestId("plan-refusal");
    expect(refusal).toHaveTextContent(i18n.t("plan.refused.unsupported"));
    // The model's own explanation is shown once, marked as the model's.
    expect(refusal).toHaveTextContent(
        `${i18n.t("plan.modelSays")} Population is not available.`,
    );
    expect(screen.getAllByText("Population is not available.")).toHaveLength(1);
    expect(
        within(refusal).getByText(i18n.t("can.mountainElevation")),
    ).toBeVisible();
    expect(within(refusal).getByText(i18n.t("plan.rephrase"))).toBeVisible();
    expect(screen.queryByText("no_plan")).not.toBeInTheDocument();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        i18n.t("plan.none"),
    );
    expect(button("ws.approveRun")).toBeDisabled();
    expect(screen.queryByText(i18n.t("plan.runNote"))).not.toBeInTheDocument();
    // Advanced sees the same explanation, with the code.
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "advanced",
    );
    expect(screen.getByTestId("plan-refusal")).toBeVisible();
    expect(screen.getByText("no_plan")).toBeVisible();
    expect(button("ws.approveContinue")).toBeDisabled();
});

it("Basic explains a plan that failed the check without codes and does not offer to run it", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.createPlan).mockResolvedValue({
        ...capitals,
        validation: {
            valid: false,
            contract_valid: false,
            errors: [
                {
                    code: "UNRESOLVED_INPUT_TYPE",
                    message: "Column city has mixed types.",
                    step_id: "from_city",
                },
            ],
        },
    });
    render(<App />);
    await generateBasic();
    const invalid = screen.getByTestId("plan-invalid");
    expect(invalid).toHaveTextContent(i18n.t("plan.invalid.text"));
    expect(invalid).toHaveTextContent("Column city has mixed types.");
    expect(screen.queryByText("UNRESOLVED_INPUT_TYPE")).not.toBeInTheDocument();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        i18n.t("plan.blocked"),
    );
    // The steps stay visible: they show what was understood.
    expect(document.querySelectorAll(".plan-steps li")).toHaveLength(3);
    expect(button("ws.approveRun")).toBeDisabled();
    expect(api.approvePlan).not.toHaveBeenCalled();
});

it("shows how many rows a running job has finished, in the bar and on Execution", async () => {
    localStorage.removeItem("enrichment.mode");
    const running: Job = {
        ...queued,
        status: "running",
        started_at: "2026-11-10T09:00:01Z",
    };
    vi.mocked(api.startRun).mockResolvedValue(running);
    vi.mocked(api.getJob)
        .mockResolvedValueOnce({ ...running, progress: null })
        .mockResolvedValue({
            ...running,
            progress: { rows_done: 4, rows_total: 10 },
        });
    render(<App />);
    await startBasic();
    // Until the worker has read the table there is nothing to count.
    expect(await screen.findByTestId("run-progress")).toHaveTextContent(
        i18n.t("exec.preparing"),
    );
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("value");
    const rows = i18n.t("activity.rows", { done: 4, total: 10 });
    await waitFor(
        () =>
            expect(screen.getByTestId("run-progress")).toHaveTextContent(rows),
        { timeout: 3000 },
    );
    expect(screen.getByRole("progressbar")).toHaveValue(4);
    expect(screen.getByRole("progressbar")).toHaveAttribute("max", "10");
    expect(screen.getByTestId("job-status")).toHaveTextContent(
        i18n.t("activity.running"),
    );
    // The bar carries the same count to every other step.
    await navigate("dataset");
    expect(screen.getByTestId("activity-rows")).toHaveTextContent(rows);
    expect(
        document.querySelector<HTMLElement>(".activity-track.measured")!.style
            .width,
    ).toBe("40%");
    // A count that changes with every row is not part of the announced status.
    expect(
        within(document.querySelector<HTMLElement>(".activity-bar")!).getByRole(
            "status",
        ),
    ).not.toHaveTextContent(rows);
});

it("Basic sums a finished run up in a sentence and leaves the counters to Advanced", async () => {
    localStorage.removeItem("enrichment.mode");
    render(<App />);
    await startBasic();
    await waitFor(() => expect(api.getReview).toHaveBeenCalled());
    // The fixture report lists one row, and it is empty; two names can be chosen.
    await waitFor(() =>
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            i18n.t("exec.outcome.none", { total: 1 }),
        ),
    );
    expect(
        await screen.findByText(i18n.t("exec.outcome.choices", { count: 2 })),
    ).toBeVisible();
    expect(screen.queryByTestId("metric-total_rows")).not.toBeInTheDocument();
    expect(
        screen.queryByText(i18n.t("workflow.unresolved")),
    ).not.toBeInTheDocument();
    absent("exec.runAgain");
    await userEvent.selectOptions(
        screen.getByLabelText(i18n.t("mode.label")),
        "advanced",
    );
    expect(screen.getByTestId("metric-total_rows")).toHaveTextContent("10");
    expect(screen.getByTestId("job-status")).toHaveTextContent(
        i18n.t("workflow.status.needs_review"),
    );
});

it("Basic says how a stopped run ended and offers to run it again", async () => {
    localStorage.removeItem("enrichment.mode");
    const timedOut: Job = {
        ...queued,
        status: "timed_out",
        finished_at: "2026-11-10T09:03:00Z",
        error: {
            code: "timed_out",
            message: "Run stopped; results discarded.",
        },
        progress: { rows_done: 3, rows_total: 10 },
        artifacts: { report: "/api/jobs/job/artifacts/report" },
    };
    vi.mocked(api.getJob).mockResolvedValue(timedOut);
    vi.mocked(api.getReport).mockResolvedValue({
        ...timedOut,
        source_sha256: draft.source_sha256,
    });
    render(<App />);
    await startBasic();
    await waitFor(() =>
        expect(screen.getByTestId("job-status")).toHaveTextContent(
            i18n.t("exec.outcome.timed_out", { limit: 180 }),
        ),
    );
    expect(
        screen.getByText(
            i18n.t("exec.outcome.reached", { done: 3, total: 10 }),
        ),
    ).toBeVisible();
    expect(screen.getByText(i18n.t("exec.outcome.fewer"))).toBeVisible();
    // The code is for Advanced; there is no table to look at.
    expect(screen.queryByText("timed_out")).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t("exec.retryHint"))).toBeVisible();
    expect(button("ws.viewOutput")).toBeDisabled();
    await userEvent.click(button("exec.runAgain"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalledTimes(2));
    // The same approved plan: no second approval.
    expect(api.approvePlan).toHaveBeenCalledTimes(1);
});

it("starting over asks first when a filled table would be lost", async () => {
    localStorage.removeItem("enrichment.mode");
    vi.mocked(api.remove).mockResolvedValue(undefined);
    render(<App />);
    await startBasic();
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await userEvent.click(
        await screen.findByRole("button", {
            name: i18n.t("startOver.action"),
        }),
    );
    expect(screen.getByText(i18n.t("startOver.warning"))).toBeVisible();
    expect(api.remove).not.toHaveBeenCalled();
    await userEvent.click(button("startOver.keep"));
    expect(
        screen.queryByText(i18n.t("startOver.warning")),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("job-status")).toBeVisible();
    // The same control closes the Results step.
    await navigate("export");
    await userEvent.click(button("startOver.action"));
    await userEvent.click(button("startOver.confirm"));
    expect(api.remove).toHaveBeenCalledWith("source");
    // Back at the first step with nothing loaded.
    expect(await screen.findByText(i18n.t("can.cityDistance"))).toBeVisible();
    expect(screen.getByLabelText("Enrichment instruction")).toHaveValue("");
    await navigate("execution");
    expect(screen.getByTestId("job-status")).toHaveTextContent(
        i18n.t("exec.idle"),
    );
});

it("tells mountain namesakes apart by what the source records about each", async () => {
    const [first] = review.items;
    const mountain = (
        entity_id: string,
        attributes: Record<string, string | number>,
    ) => ({
        ...first.candidates[0],
        source: "wikidata",
        entity_id,
        label: "Mont Blanc",
        selectable: true,
        blocker: null,
        recommended: false,
        recommendation_reasons: [],
        attributes: {
            match_kind: "label",
            country_codes: "FR/IT",
            ...attributes,
        },
    });
    vi.mocked(api.getReview).mockResolvedValue({
        ...review,
        items: [
            {
                ...first,
                provider_id: "wikidata",
                capability: "resolve_entity",
                query: {
                    name: "Mont Blanc",
                    entity_type: "mountain",
                    context: { country_codes: "FR/IT" },
                },
                candidates: [
                    mountain("Q583", {
                        description: "highest mountain in the Alps",
                        elevation_m: 4805.59,
                        latitude: 45.8327,
                        longitude: 6.8651,
                    }),
                    mountain("Q31605638", {
                        description:
                            "mountain in French Southern and Antarctic Lands",
                        latitude: -49.4,
                        longitude: 69.4,
                    }),
                ],
            },
        ],
    });
    render(<App />);
    await start();
    await navigate("review");
    const alps = within(await screen.findByTestId("candidate-wikidata:Q583"));
    expect(alps.getByText("highest mountain in the Alps")).toBeVisible();
    expect(
        alps.getByText(
            new RegExp(
                `${i18n.t("review.elevation", { value: "4,806" })} · 45\\.83, 6\\.87`,
            ),
        ),
    ).toBeVisible();
    const other = within(screen.getByTestId("candidate-wikidata:Q31605638"));
    expect(
        other.getByText("mountain in French Southern and Antarctic Lands"),
    ).toBeVisible();
    // No elevation is recorded for the namesake, and none is invented.
    expect(other.queryByText(/elevation/)).toBeNull();
    // The folded card keeps the description of what was chosen.
    await userEvent.click(alps.getByRole("radio"));
    expect(chosen("Mont Blanc")).toHaveTextContent(
        "highest mountain in the Alps",
    );
});
