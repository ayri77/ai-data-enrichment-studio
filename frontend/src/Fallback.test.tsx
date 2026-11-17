import {
    cleanup,
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { api, WorkspaceApiError, type PlanDraft, type Preview } from "./api";
import i18n from "./i18n";
import { readFallbackSwitch } from "./fallbackModel";
import {
    fallbackDraft,
    lookupJob,
    lookupPlan,
    lookupReport,
} from "./fallbackFixtures";

vi.mock("./api", async (original) => ({
    ...(await original<typeof import("./api")>()),
    api: {
        limits: vi.fn(),
        capabilities: vi.fn(),
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
        createFallbackPlan: vi.fn(),
        getPlan: vi.fn(),
        validatePlan: vi.fn(),
        approvePlan: vi.fn(),
        startRun: vi.fn(),
        getJob: vi.fn(),
        getReport: vi.fn(),
        cancelJob: vi.fn(),
        getReview: vi.fn(),
        artifact: vi.fn(),
    },
}));

const column = (name: string, inferred_type: string, null_count = 0) => ({
    name,
    inferred_type,
    null_count,
    warnings: [],
});
// Two empty columns, so that nothing is proposed for the person.
const preview: Preview = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:G11",
    sample_limit: 5,
    profile: {
        row_count: 10,
        columns: [
            column("Mountain", "Text"),
            column("Country", "Text"),
            column("Rank", "Integer"),
            column("Born", "Date"),
            column("Height", "Number", 10),
            column("First ascent", "Unknown", 10),
            column("Climbed in winter", "Text", 10),
        ],
        sample_records: [],
        schema_warnings: [],
    },
};
const refused: PlanDraft = {
    ...fallbackDraft,
    kind: undefined,
    instruction: "Find when each mountain was first climbed",
    plan: null,
    planning_status: "unsupported",
    reason: "The first ascent is not in the sources.",
    validation: { valid: false, errors: [] },
};
// The planner said "ready" with a plan that has no steps; the check refused it.
const invalidPlan: PlanDraft = {
    ...fallbackDraft,
    kind: undefined,
    instruction: refused.instruction,
    planning_status: "ready",
    reason: null,
    plan: { ...lookupPlan, steps: [] },
    validation: {
        valid: false,
        contract_valid: false,
        errors: [
            {
                code: "plan_empty",
                message: "Enrichment plan must contain at least one step",
            },
        ],
    } as PlanDraft["validation"],
};
const limits = (web_lookup?: boolean) => ({
    max_run_rows: 20,
    max_timeout_seconds: 300,
    default_timeout_seconds: 180,
    ...(web_lookup === undefined ? {} : { web_lookup }),
});
const t = (key: string, values?: Record<string, unknown>) =>
    i18n.t(key, { ...values }) as string;

beforeEach(async () => {
    vi.resetAllMocks();
    localStorage.clear();
    await i18n.changeLanguage("en");
    vi.mocked(api.limits).mockResolvedValue(limits(true));
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
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "mountains.xlsx",
        worksheets: [
            {
                name: "Sheet1",
                columns: preview.profile.columns.map((x) => x.name),
                row_count: 10,
            },
        ],
    });
    vi.mocked(api.preview).mockResolvedValue(preview);
    vi.mocked(api.createPlan).mockResolvedValue(refused);
    vi.mocked(api.createFallbackPlan).mockResolvedValue(fallbackDraft);
    vi.mocked(api.approvePlan).mockResolvedValue({
        ...fallbackDraft,
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    });
    vi.mocked(api.startRun).mockResolvedValue({
        ...lookupJob,
        status: "queued",
        artifacts: {},
    });
    vi.mocked(api.getJob).mockResolvedValue(lookupJob);
    vi.mocked(api.getReport).mockResolvedValue(lookupReport);
    vi.mocked(api.getReview).mockResolvedValue({
        job_id: lookupJob.job_id,
        previous_job_id: null,
        items: [],
        informational: [],
        affected_rows: 0,
    });
});
const button = (key: string) => screen.getByRole("button", { name: t(key) });
const nav = (key: string) =>
    userEvent.click(
        screen.getByRole("navigation").querySelectorAll("button")[
            [
                "overview",
                "dataset",
                "plan",
                "execution",
                "review",
                "export",
            ].indexOf(key)
        ],
    );
// Basic: the planner refuses the task the person wrote.
async function refusedTask(view = "plan-refusal") {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: refused.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillColumn")),
        "E",
    );
    await userEvent.click(button("ws.addFill"));
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillAnother")),
        "F",
    );
    await userEvent.click(button("ws.addFill"));
    await userEvent.selectOptions(
        screen.getAllByLabelText(t("ws.fillAnother"))[1],
        "G",
    );
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId(view);
}
it("offers it for an ambiguous task too", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...refused,
        planning_status: "ambiguous",
    });
    render(<App opening="dataset" />);
    await refusedTask();
    expect(await screen.findByTestId("fallback-offer")).toBeVisible();
});
it("does not offer it where the server cannot do it", async () => {
    for (const stated of [limits(false), limits()]) {
        vi.mocked(api.limits).mockResolvedValue(stated);
        const { unmount } = render(<App opening="dataset" />);
        await refusedTask();
        expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
        // Nothing else about the explanation changes.
        expect(screen.getByTestId("plan-refusal")).toBeVisible();
        unmount();
    }
});
it("offers the search after a refusal when the key came after the page was loaded", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(limits(false));
    vi.mocked(api.limits).mockResolvedValue(limits(true));
    render(<App opening="dataset" />);
    await refusedTask();
    expect(await screen.findByTestId("fallback-offer")).toBeVisible();
    expect(api.limits).toHaveBeenCalledTimes(2);
});
it("reads the limits again after the provider settings are saved or cleared", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(limits(false));
    vi.mocked(api.limits).mockResolvedValue(limits(true));
    const current = await api.providerSettings();
    vi.mocked(api.configureProvider).mockResolvedValue(current);
    vi.mocked(api.removeProviderKey).mockResolvedValue({
        ...current,
        key_present: false,
    });
    localStorage.setItem("enrichment.mode", "advanced");
    render(<App opening="dataset" />);
    await waitFor(() => expect(api.limits).toHaveBeenCalledTimes(1));
    await userEvent.click(button("ws.providerSettings"));
    await userEvent.click(button("provider.apply"));
    await screen.findByText(t("provider.saved"));
    await waitFor(() => expect(api.limits).toHaveBeenCalledTimes(2));
    await userEvent.click(button("provider.remove"));
    await screen.findByText(t("provider.removed"));
    await waitFor(() => expect(api.limits).toHaveBeenCalledTimes(3));
});
it("does not read the limits again when the settings could not be saved", async () => {
    vi.mocked(api.configureProvider).mockRejectedValue(
        new WorkspaceApiError("request_failed", "failed"),
    );
    localStorage.setItem("enrichment.mode", "advanced");
    render(<App opening="dataset" />);
    await waitFor(() => expect(api.limits).toHaveBeenCalledTimes(1));
    await userEvent.click(button("ws.providerSettings"));
    await userEvent.click(button("provider.apply"));
    await waitFor(() => expect(api.configureProvider).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.limits).toHaveBeenCalledTimes(1);
});
it("keeps the previous limits when the refresh fails", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(limits(true));
    vi.mocked(api.limits).mockRejectedValue(new Error("offline"));
    render(<App opening="dataset" />);
    await refusedTask();
    expect(await screen.findByTestId("fallback-offer")).toBeVisible();
    expect(api.limits).toHaveBeenCalledTimes(2);
});
it("does not read the limits again after an ordinary plan", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...fallbackDraft,
        kind: undefined,
        plan: { ...lookupPlan, steps: [] },
    });
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: "Anything" },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillColumn")),
        "E",
    );
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
    expect(api.limits).toHaveBeenCalledTimes(1);
});
it("does not offer it for any other state of the plan", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...refused,
        planning_status: "other",
    });
    const { unmount } = render(<App opening="dataset" />);
    await refusedTask();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    unmount();
    // A plan that was built.
    vi.mocked(api.createPlan).mockResolvedValue({
        ...fallbackDraft,
        kind: undefined,
        plan: { ...lookupPlan, steps: [] },
    });
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: "Anything" },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillColumn")),
        "E",
    );
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("validation");
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
});

const SWITCH = "enrichment.fallbackSearch";
const switchOn = () => localStorage.setItem(SWITCH, "on");
const change = () => userEvent.click(button("fallback.change.open"));
const context = () => within(screen.getByTestId("fallback-context"));
const targets = () => within(screen.getByTestId("fallback-targets"));
const choose = (name: string) =>
    userEvent.click(
        context().getByRole("checkbox", { name: new RegExp(`^${name}`) }),
    );
const switchBox = () => screen.getByLabelText(t("fallback.switch.label"));
async function uploadTable() {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
}
// What the page asks of the server for the task of these tests, without a choice
// of the person: the task, the columns to fill in order, a number where the
// column holds numbers, every other text or number column to identify the row.
const defaultRequest = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: "A1:G11",
    question: refused.instruction,
    context_columns: ["Mountain", "Country", "Rank"],
    targets: [
        { column: "Height", kind: "number" },
        { column: "First ascent", kind: "text" },
        { column: "Climbed in winter", kind: "text" },
    ],
    output_columns: {
        Height: "E",
        "First ascent": "F",
        "Climbed in winter": "G",
    },
    existing_outputs: "keep",
    sources_in_workbook: "notes",
};
const searchPlan: PlanDraft = {
    ...fallbackDraft,
    plan_id: "plan-changed",
    fingerprint: "d".repeat(64),
};
// A plan the planner built and the check passed.
const ordinary: PlanDraft = {
    ...fallbackDraft,
    kind: undefined,
    plan: { ...lookupPlan, steps: [] },
};
// The search plan, built at once because the switch is on.
async function toPlan() {
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask("fallback-plan");
}

// --- The switch --------------------------------------------------------------------

it("offers one switch where the task is written, only where the server has the search", async () => {
    for (const stated of [limits(false), limits()]) {
        vi.mocked(api.limits).mockResolvedValue(stated);
        const { unmount } = render(<App opening="dataset" />);
        await uploadTable();
        await waitFor(() => expect(api.limits).toHaveBeenCalled());
        expect(
            screen.queryByLabelText(t("fallback.switch.label")),
        ).not.toBeInTheDocument();
        unmount();
        vi.mocked(api.limits).mockClear();
    }
    vi.mocked(api.limits).mockResolvedValue(limits(true));
    render(<App opening="dataset" />);
    await uploadTable();
    const box = await screen.findByLabelText(t("fallback.switch.label"));
    // Off to begin with, with the three facts under it.
    expect(box).not.toBeChecked();
    expect(screen.getByTestId("fallback-switch")).toHaveTextContent(
        t("fallback.switch.hint"),
    );
    // Under the task and the columns to fill, above the number of rows.
    expect(
        screen.getByLabelText(t("ws.fillColumn")).compareDocumentPosition(box) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
        box.compareDocumentPosition(screen.getByTestId("run-rows")) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
});
it("remembers the switch in this browser", async () => {
    const { unmount } = render(<App opening="dataset" />);
    await uploadTable();
    await userEvent.click(
        await screen.findByLabelText(t("fallback.switch.label")),
    );
    expect(switchBox()).toBeChecked();
    expect(readFallbackSwitch()).toBe(true);
    unmount();
    render(<App opening="dataset" />);
    await uploadTable();
    expect(
        await screen.findByLabelText(t("fallback.switch.label")),
    ).toBeChecked();
    await userEvent.click(switchBox());
    expect(switchBox()).not.toBeChecked();
    expect(readFallbackSwitch()).toBe(false);
});
it("takes a storage that cannot be read as off, and still lets the switch be used", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("blocked");
    });
    render(<App opening="dataset" />);
    await uploadTable();
    const box = await screen.findByLabelText(t("fallback.switch.label"));
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    expect(switchBox()).toBeChecked();
    vi.restoreAllMocks();
});
it("shows the switch in Advanced as well", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    expect(
        await screen.findByLabelText(t("fallback.switch.label")),
    ).not.toBeChecked();
});

// --- With the switch on ------------------------------------------------------------

it("with the switch on, builds the search plan at once for a task the planner refuses, and shows it as the plan", async () => {
    await toPlan();
    // One request for the planner, then one for the search, with the defaults.
    expect(api.createPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledWith(defaultRequest);
    // At the top, in plain words, why and what happened instead.
    const notice = screen.getByTestId("fallback-auto-notice");
    expect(notice).toHaveTextContent(t("fallback.auto.refused"));
    expect(notice).toHaveTextContent("The first ascent is not in the sources.");
    expect(notice).toHaveTextContent(t("fallback.auto.bySwitch"));
    expect(screen.getByTestId("fallback-banner")).toBeVisible();
    // The columns that are sent, and the way to change them.
    expect(screen.getByTestId("fallback-sent")).toHaveTextContent(
        t("fallback.sent", { columns: "Mountain, Country" }),
    );
    expect(button("fallback.change.open")).toBeEnabled();
    // Approving this plan is the consent: the button is there, nothing was run.
    expect(button("ws.approveRun")).toBeEnabled();
    expect(api.approvePlan).not.toHaveBeenCalled();
    expect(api.startRun).not.toHaveBeenCalled();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        t("plan.checked"),
    );
});
it("says that the search plan is being built, in place of the refusal", async () => {
    let finish: (draft: PlanDraft) => void = () => undefined;
    vi.mocked(api.createFallbackPlan).mockReturnValue(
        new Promise((resolve) => (finish = resolve)),
    );
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask("fallback-progress");
    expect(screen.getByTestId("fallback-progress")).toHaveTextContent(
        t("fallback.auto.building"),
    );
    expect(screen.queryByTestId("plan-refusal")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    finish(fallbackDraft);
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
    expect(screen.queryByTestId("fallback-progress")).not.toBeInTheDocument();
});
it("does the same when the planner's plan fails the check, and says so", async () => {
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask("fallback-plan");
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledWith(defaultRequest);
    const notice = screen.getByTestId("fallback-auto-notice");
    expect(notice).toHaveTextContent(t("fallback.auto.invalid"));
    expect(notice).not.toHaveTextContent(t("fallback.auto.refused"));
    expect(notice).toHaveTextContent(t("fallback.auto.bySwitch"));
    expect(button("ws.approveRun")).toBeEnabled();
    // What the planner answered is behind a fold: its messages, not its plan.
    const answer = screen.getByTestId("fallback-planner");
    expect(answer).not.toHaveAttribute("open");
    await userEvent.click(within(answer).getByText(t("fallback.planner.show")));
    expect(within(answer).getByTestId("plan-invalid")).toBeVisible();
    expect(answer).toHaveTextContent(
        "Enrichment plan must contain at least one step",
    );
});
it("does not use the search when the planner built a plan that passed the check", async () => {
    vi.mocked(api.createPlan).mockResolvedValue(ordinary);
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask("validation");
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(
        screen.queryByTestId("fallback-auto-notice"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("fallback-planner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
});
it("does not search for another reason than the planner's refusal", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...refused,
        planning_status: "other",
    });
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask();
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
});
it("does not search where the server cannot, whatever the browser remembers", async () => {
    vi.mocked(api.limits).mockResolvedValue(limits(false));
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask();
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    expect(screen.getByTestId("plan-refusal")).toBeVisible();
});
it("shows the planner's refusal and the form when the search plan cannot be built", async () => {
    vi.mocked(api.createFallbackPlan).mockRejectedValueOnce(
        new WorkspaceApiError(
            "fallback_plan_invalid",
            "The column 'Rank' is not usable.",
            422,
        ),
    );
    switchOn();
    render(<App opening="dataset" />);
    await refusedTask();
    const form = await screen.findByTestId("fallback-form");
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    // The planner's view as before, the server's message, and the form to fix it.
    expect(screen.getByTestId("plan-refusal")).toBeVisible();
    expect(
        screen.queryByTestId("fallback-auto-notice"),
    ).not.toBeInTheDocument();
    const alert = within(form).getByRole("alert");
    expect(alert).toHaveAttribute("data-error-code", "fallback_plan_invalid");
    expect(alert).toHaveTextContent(t("error.fallback_plan_invalid"));
    expect(alert).toHaveTextContent("The column 'Rank' is not usable.");
    // The form starts from what was sent.
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        refused.instruction,
    );
    for (const name of ["Mountain", "Country", "Rank"])
        expect(
            context().getByRole("checkbox", { name: new RegExp(`^${name}`) }),
        ).toBeChecked();
    // Fixed and sent again: the plan appears, asked for by the person.
    await choose("Rank");
    await userEvent.click(button("fallback.build"));
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.createFallbackPlan).mock.calls[1][0]).toEqual({
        ...defaultRequest,
        context_columns: ["Mountain", "Country"],
    });
    expect(screen.getByTestId("fallback-auto-notice")).toHaveTextContent(
        t("fallback.auto.byButton"),
    );
});
it("asks for a column that identifies the row when the table has none to propose", async () => {
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        range_ref: "A1:B11",
        profile: {
            ...preview.profile,
            columns: [column("Born", "Date"), column("Height", "Number", 10)],
        },
    });
    switchOn();
    render(<App opening="dataset" />);
    await uploadTable();
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: refused.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillColumn")),
        "B",
    );
    await userEvent.click(button("ws.buildPlan"));
    const form = await screen.findByTestId("fallback-form");
    // Nothing is sent to the server for it.
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(within(form).getByRole("alert")).toHaveAttribute(
        "data-error-code",
        "fallback_plan_invalid",
    );
    expect(screen.getByTestId("fallback-need")).toHaveTextContent(
        t("fallback.need.context"),
    );
    expect(button("fallback.build")).toBeDisabled();
});

// --- With the switch off -----------------------------------------------------------

it("offers one button and one checkbox under the explanation when the planner could not build a plan", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    const offer = await screen.findByTestId("fallback-offer");
    expect(screen.getByTestId("fallback-lead")).toHaveTextContent(
        t("fallback.text"),
    );
    // After the explanation and the list of what the system can do, and apart from them.
    const refusal = screen.getByTestId("plan-refusal");
    expect(
        refusal.compareDocumentPosition(offer) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(refusal).not.toContainElement(offer);
    // One button, one checkbox, no form, no dialog.
    const inside = within(offer);
    expect(inside.getAllByRole("button")).toHaveLength(1);
    expect(inside.getByRole("button")).toHaveAccessibleName(
        t("fallback.searchNow"),
    );
    expect(inside.getAllByRole("checkbox")).toHaveLength(1);
    expect(inside.getByLabelText(t("fallback.autoNext"))).not.toBeChecked();
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
});
it("builds the same search plan as the switch would when the button is pressed", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await userEvent.click(button("fallback.searchNow"));
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledWith(defaultRequest);
    const notice = screen.getByTestId("fallback-auto-notice");
    expect(notice).toHaveTextContent(t("fallback.auto.refused"));
    expect(notice).toHaveTextContent(t("fallback.auto.byButton"));
    expect(notice).not.toHaveTextContent(t("fallback.auto.bySwitch"));
    // Pressing it is not switching it on.
    expect(readFallbackSwitch()).toBe(false);
    expect(button("ws.approveRun")).toBeEnabled();
});
it("waits with the button disabled while the search plan is built", async () => {
    let finish: (draft: PlanDraft) => void = () => undefined;
    vi.mocked(api.createFallbackPlan).mockReturnValue(
        new Promise((resolve) => (finish = resolve)),
    );
    render(<App opening="dataset" />);
    await refusedTask();
    await userEvent.click(button("fallback.searchNow"));
    await screen.findByTestId("fallback-progress");
    finish(fallbackDraft);
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
});
it("turns the switch on from the checkbox, without building anything yet, and uses it for the next plan", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await userEvent.click(screen.getByLabelText(t("fallback.autoNext")));
    expect(readFallbackSwitch()).toBe(true);
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.getByTestId("plan-refusal")).toBeVisible();
    // The same switch is the one on the task step.
    await nav("dataset");
    expect(
        await screen.findByLabelText(t("fallback.switch.label")),
    ).toBeChecked();
    await userEvent.click(button("ws.buildPlan"));
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("fallback-auto-notice")).toHaveTextContent(
        t("fallback.auto.bySwitch"),
    );
});

// --- Changing what is sent ---------------------------------------------------------

it("opens the form from the plan with the choices of this plan, not the defaults", async () => {
    await toPlan();
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    await change();
    expect(screen.getByTestId("fallback-form")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // The plan has Mountain and Country (not Rank), a unit and three kinds.
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        "Find when each mountain was first climbed",
    );
    const box = (name: string) =>
        context().getByRole("checkbox", { name: new RegExp(`^${name}`) });
    expect(box("Mountain")).toBeChecked();
    expect(box("Country")).toBeChecked();
    expect(box("Rank")).not.toBeChecked();
    const kind = (name: string) =>
        targets().getByLabelText(t("fallback.kind", { name }));
    expect(kind("Height")).toHaveValue("number");
    expect(kind("First ascent")).toHaveValue("text");
    expect(kind("Climbed in winter")).toHaveValue("yes_no");
    expect(
        targets().getByLabelText(t("fallback.unit", { name: "Height" })),
    ).toHaveValue("m");
    expect(
        within(screen.getByTestId("fallback-form")).getByLabelText(
            t("sources.title"),
        ),
    ).toHaveValue("notes");
    // The plan stays on the screen meanwhile.
    expect(screen.getByTestId("fallback-plan")).toBeVisible();
    // It can be closed again without a change.
    await userEvent.click(button("fallback.change.cancel"));
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
});
it("builds the plan again from the saved choices and returns to the plan", async () => {
    await toPlan();
    vi.mocked(api.createFallbackPlan).mockResolvedValue(searchPlan);
    await change();
    await choose("Country");
    await userEvent.selectOptions(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
        "yes_no",
    );
    await userEvent.selectOptions(
        targets().getByLabelText(t("fallback.kind", { name: "Height" })),
        "text",
    );
    fireEvent.change(screen.getByLabelText(t("fallback.question")), {
        target: { value: "When was it first climbed?" },
    });
    await userEvent.click(button("fallback.change.save"));
    await waitFor(() =>
        expect(api.createFallbackPlan).toHaveBeenCalledTimes(2),
    );
    expect(vi.mocked(api.createFallbackPlan).mock.calls[1][0]).toEqual({
        ...defaultRequest,
        question: "When was it first climbed?",
        context_columns: ["Mountain"],
        targets: [
            { column: "Height", kind: "text" },
            { column: "First ascent", kind: "yes_no" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
    });
    // Back on the plan view with the new plan, the notice and the planner's answer.
    await waitFor(() =>
        expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("fallback-plan")).toBeVisible();
    expect(screen.getByTestId("fallback-auto-notice")).toHaveTextContent(
        t("fallback.auto.bySwitch"),
    );
    expect(screen.getByTestId("fallback-planner")).toBeInTheDocument();
    await userEvent.click(button("ws.approveRun"));
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-changed",
        searchPlan.fingerprint,
    );
});
it("keeps the plan and the entered choices when the new plan is refused", async () => {
    await toPlan();
    vi.mocked(api.createFallbackPlan).mockRejectedValueOnce(
        new WorkspaceApiError("fallback_plan_invalid", "Not usable.", 422),
    );
    await change();
    await userEvent.selectOptions(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
        "yes_no",
    );
    await userEvent.click(button("fallback.change.save"));
    const alert = await within(screen.getByTestId("fallback-form")).findByRole(
        "alert",
    );
    expect(alert).toHaveTextContent("Not usable.");
    expect(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
    ).toHaveValue("yes_no");
    expect(screen.getByTestId("fallback-plan")).toBeVisible();
    expect(button("ws.approveRun")).toBeEnabled();
});
it("shows what the planner answered again, read-only, after the search plan took its place", async () => {
    await toPlan();
    const answer = screen.getByTestId("fallback-planner");
    expect(answer).not.toHaveAttribute("open");
    expect(screen.getByTestId("plan-refusal")).not.toBeVisible();
    await userEvent.click(within(answer).getByText(t("fallback.planner.show")));
    expect(answer).toHaveAttribute("open");
    const refusal = within(answer).getByTestId("plan-refusal");
    expect(refusal).toBeVisible();
    expect(refusal).toHaveTextContent(
        "The first ascent is not in the sources.",
    );
    expect(refusal).toHaveTextContent(t("plan.refused.unsupported"));
    // Nothing to press in it.
    expect(within(answer).queryByRole("button")).not.toBeInTheDocument();
    expect(
        within(answer).queryByTestId("fallback-offer"),
    ).not.toBeInTheDocument();
});

// --- The form -----------------------------------------------------------------------

it("keeps the columns to fill and the other kinds of column out of the choice", async () => {
    await toPlan();
    await change();
    const box = (name: string) =>
        context().getByRole("checkbox", { name: new RegExp(`^${name}`) });
    // The columns to fill cannot identify a row, and say why.
    for (const name of ["Height", "First ascent", "Climbed in winter"]) {
        expect(box(name)).toBeDisabled();
        expect(box(name)).toHaveAccessibleName(
            new RegExp(t("fallback.ctx.target")),
        );
    }
    // A column of dates cannot either.
    expect(box("Born")).toBeDisabled();
    expect(box("Born")).toHaveAccessibleName(
        new RegExp(t("fallback.ctx.date")),
    );
    for (const name of ["Mountain", "Country", "Rank"])
        expect(box(name)).toBeEnabled();
});
it("asks for the kind of each column to fill, and for a unit only of a number", async () => {
    await toPlan();
    await change();
    const kind = (name: string) =>
        targets().getByLabelText(t("fallback.kind", { name }));
    expect(
        [...(kind("Height") as HTMLSelectElement).options].map((o) => o.text),
    ).toEqual(["Text", "Number", "Yes/No"]);
    const unit = (name: string) =>
        targets().queryByLabelText(t("fallback.unit", { name }));
    expect(unit("Height")).toBeInTheDocument();
    expect(unit("First ascent")).not.toBeInTheDocument();
    await userEvent.selectOptions(kind("Height"), "yes_no");
    expect(unit("Height")).not.toBeInTheDocument();
    await userEvent.selectOptions(kind("Height"), "number");
    // The unit that was there is still there.
    expect(unit("Height")).toHaveValue("m");
    // No letters or slots anywhere in what the person reads.
    expect(screen.getByTestId("fallback-form")).not.toHaveTextContent(
        /value_\d|ctx_\d/,
    );
});
it("needs a column that identifies the row, and says what leaves the server and what it costs", async () => {
    await toPlan();
    await change();
    const notice = screen.getByTestId("fallback-notice");
    expect(notice).toHaveTextContent(t("fallback.notice.requests"));
    expect(notice).toHaveTextContent(t("fallback.notice.sends"));
    // The rows of this run: the table has ten, the server takes twenty.
    expect(notice).toHaveTextContent(t("fallback.notice.rows", { rows: 10 }));
    expect(notice).toHaveTextContent(t("fallback.notice.cost"));
    expect(notice).toHaveTextContent(t("fallback.notice.unverified"));
    expect(
        notice.compareDocumentPosition(button("fallback.change.save")) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await choose("Mountain");
    await choose("Country");
    expect(button("fallback.change.save")).toBeDisabled();
    expect(screen.getByTestId("fallback-need")).toHaveTextContent(
        t("fallback.need.context"),
    );
});
it("lets the person change where the links go", async () => {
    await toPlan();
    await change();
    const sources = within(screen.getByTestId("fallback-form")).getByLabelText(
        t("sources.title"),
    );
    expect(screen.getByText(t("fallback.sourcesWhy"))).toBeVisible();
    await userEvent.selectOptions(sources, "sheet");
    expect(sources).toHaveValue("sheet");
});
it("waits with the button disabled and a progress mark while the plan is built again", async () => {
    await toPlan();
    let finish: (draft: PlanDraft) => void = () => undefined;
    vi.mocked(api.createFallbackPlan).mockReturnValue(
        new Promise((resolve) => (finish = resolve)),
    );
    await change();
    await userEvent.click(button("fallback.change.save"));
    await waitFor(() => expect(button("fallback.change.save")).toBeDisabled());
    expect(
        button("fallback.change.save").querySelector(".spin"),
    ).not.toBeNull();
    // The plan itself stays; the progress text is for a task without a plan.
    expect(screen.queryByTestId("fallback-progress")).not.toBeInTheDocument();
    finish(searchPlan);
    await waitFor(() =>
        expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument(),
    );
});
it("speaks the other languages too", async () => {
    await toPlan();
    await userEvent.selectOptions(screen.getByLabelText(t("language")), "uk");
    const notice = screen.getByTestId("fallback-auto-notice");
    expect(notice).toHaveTextContent("Планувальник не знайшов");
    expect(notice).toHaveTextContent("Оскільки ви ввімкнули пошук в інтернеті");
    expect(screen.getByTestId("fallback-sent")).toHaveTextContent(
        "Значення цих стовпців надсилаються до OpenAI для кожного рядка: Mountain, Country.",
    );
    await change();
    expect(screen.getByTestId("fallback-notice")).toHaveTextContent("OpenAI");
    await userEvent.selectOptions(screen.getByLabelText(t("language")), "de");
    expect(screen.getByTestId("fallback-auto-notice")).toHaveTextContent(
        "Weil Sie die Websuche eingeschaltet haben",
    );
});

// --- The plan, the run, the results ---------------------------------------------------

it("tells the plan of the search in words, without its stored form, and warns", async () => {
    await toPlan();
    const plan = screen.getByTestId("fallback-plan");
    expect(plan).toHaveTextContent("Search the web with AI (not verified)");
    expect(plan).toHaveTextContent(t("cap.web_lookup.text"));
    expect(plan).toHaveTextContent(
        t("fallback.plan.question", {
            question: "Find when each mountain was first climbed",
        }),
    );
    expect(plan).toHaveTextContent(
        t("fallback.plan.context", { columns: "Mountain, Country" }),
    );
    const fills = [...plan.querySelectorAll(".fallback-fill")].map(
        (item) => item.textContent,
    );
    expect(fills).toContain("“Height” (E): a number in m");
    expect(fills).toContain("“First ascent” (F): text");
    expect(fills).toContain("“Climbed in winter” (G): yes or no");
    // Nothing of the stored task is shown in Basic.
    expect(plan).not.toHaveTextContent(/value_\d|ctx_\d|\{|slot|"kind"/);
    expect(screen.getByTestId("fallback-banner")).toHaveTextContent(
        "Values will be found by an AI web search and are not verified.",
    );
    expect(screen.getByTestId("validation")).toHaveTextContent(
        t("plan.checked"),
    );
});
it("keeps the plan of the search in the graph, with its stored form, in Advanced", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    switchOn();
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    fireEvent.change(await screen.findByLabelText("Enrichment instruction"), {
        target: { value: refused.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 1", { exact: true }),
        "E",
    );
    expect(switchBox()).toBeChecked();
    await userEvent.click(screen.getByLabelText(t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    // At once, with no further press: the same plan as in Basic.
    expect(await screen.findByTestId("fallback-banner")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.createFallbackPlan).mock.calls[0][0]).toMatchObject({
        question: refused.instruction,
        context_columns: ["Mountain", "Country", "Rank", "Climbed in winter"],
        targets: [{ column: "Height", kind: "number" }],
        output_columns: { Height: "E" },
        sources_in_workbook: "notes",
    });
    expect(screen.getByRole("region", { name: t("ws.graph") })).toBeVisible();
    expect(screen.getAllByText("web_lookup")[0]).toBeVisible();
    // The notice, the columns that are sent and the way to change them.
    expect(screen.getByTestId("fallback-auto-notice")).toHaveTextContent(
        t("fallback.auto.bySwitch"),
    );
    expect(screen.getByTestId("fallback-sent")).toBeVisible();
    await change();
    expect(screen.getByTestId("fallback-form")).toBeVisible();
    expect(screen.getByTestId("fallback-planner")).toBeInTheDocument();
});
it("approves and runs the search with the banner and the reason for the notes", async () => {
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-fallback",
        fallbackDraft.fingerprint,
    );
    await waitFor(() => expect(api.startRun).toHaveBeenCalled());
    // The run view: the same warning, and what the notes are for.
    expect(await screen.findByTestId("fallback-banner")).toBeVisible();
    expect(screen.getByTestId("fallback-sources")).toHaveTextContent(
        t("fallback.runSources.notes"),
    );
});
it("shows each value with its basis, filters the concluded ones and says what was found", async () => {
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const summary = await screen.findByTestId("fallback-summary");
    expect(summary).toHaveTextContent(
        t("results.lookup.summary", { asked: 3, found: 5, concluded: 1 }),
    );
    expect(summary).toHaveTextContent(t("fallback.bannerDone"));
    // The provenance of the column, as for any other step.
    expect(screen.getAllByTestId("result-provenance")[0]).toHaveTextContent(
        t("provenance.ai_found"),
    );
    const rows = screen.getAllByTestId("row-outcome");
    expect(rows).toHaveLength(3);
    const marks = within(rows[0]).getAllByTestId("basis-mark");
    expect(marks.map((mark) => mark.textContent)).toEqual([
        "stated on the page",
        "concluded by the model",
        "stated on the page",
    ]);
    // The concluded cell is marked in the row, the others are not.
    const cells = rows[0].querySelectorAll("td[data-basis]");
    expect([...cells].map((cell) => cell.getAttribute("data-basis"))).toEqual([
        "stated",
        "concluded",
        "stated",
    ]);
    expect(rows[0]).toHaveAttribute("data-concluded", "true");
    expect(rows[1]).toHaveAttribute("data-concluded", "false");
    // A value that was not found has no mark, and its reason is in words.
    expect(within(rows[1]).getAllByTestId("basis-mark")).toHaveLength(2);
    expect(rows[1]).toHaveTextContent(
        t("results.lookupGap", { column: "Height" }),
    );
    expect(rows[2]).toHaveTextContent(t("results.cause.nothing_found"));
    // Two of three values written: partly filled, not empty; none: empty.
    expect(within(rows[0]).getByTestId("row-label")).toHaveTextContent(
        t("results.filled"),
    );
    expect(within(rows[1]).getByTestId("row-label")).toHaveTextContent(
        t("results.partial"),
    );
    expect(within(rows[1]).getByTestId("row-label")).toHaveClass(
        "outcome-partial",
    );
    expect(within(rows[2]).getByTestId("row-label")).toHaveTextContent(
        t("results.empty"),
    );
    for (const [key, count] of [
        ["filled", 1],
        ["partial", 1],
        ["empty", 1],
    ] as const) {
        expect(
            screen.getByRole("button", {
                name: t(`results.filter.${key}`, { count }),
            }),
        ).toBeVisible();
    }
    expect(screen.getByTestId("fallback-summary")).not.toHaveTextContent(
        "openai-web-lookup",
    );
    expect(screen.queryByTestId("fallback-spend")).not.toBeInTheDocument();
    // The filter, with its count.
    const filter = screen.getByRole("button", {
        name: t("results.filter.concluded", { count: 1 }),
    });
    await userEvent.click(filter);
    expect(screen.getAllByTestId("row-outcome")).toHaveLength(1);
    expect(screen.getByTestId("row-outcome")).toHaveTextContent("Everest");
    // Nothing of the plan's slots in Basic.
    expect(
        screen.getByRole("region", { name: t("ws.outcomePreview") }),
    ).not.toHaveTextContent(/value_\d|ctx_\d/);
});
it("offers no filter of concluded values when there are none", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
        ...lookupReport,
        rows: lookupReport.rows!.map((row) => ({
            ...row,
            ai_found: row.ai_found?.map((item) => ({
                ...item,
                basis: "stated" as const,
            })),
        })),
    });
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    await screen.findByTestId("fallback-summary");
    expect(
        screen.queryByRole("button", { name: /^Concluded by AI/ }),
    ).not.toBeInTheDocument();
});
it("gives Advanced the numbers of the run and the configuration", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    localStorage.setItem(SWITCH, "on");
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    fireEvent.change(await screen.findByLabelText("Enrichment instruction"), {
        target: { value: refused.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 1", { exact: true }),
        "E",
    );
    await userEvent.click(screen.getByLabelText(t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("fallback-banner");
    await userEvent.click(
        await screen.findByRole("button", { name: t("ws.approveContinue") }),
    );
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    expect(await screen.findByTestId("fallback-spend")).toHaveTextContent(
        t("results.lookup.spend", {
            requests: 3,
            input: 3000,
            output: 600,
            searches: 6,
            configuration: "openai-web-lookup-p1-gpt-6-luna-low",
        }),
    );
});
it("says what became of a row that was not asked in time", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
        ...lookupReport,
        execution: {
            provider_requests: {},
            records: lookupReport.execution!.records.map((record, index) =>
                index === 2
                    ? {
                          ...record,
                          row_result: {
                              steps: [
                                  {
                                      step_id: "lookup",
                                      result: {
                                          status: "failure" as const,
                                          value: null,
                                          error_code: "DEADLINE_REACHED",
                                          message: "x",
                                          evidence: [],
                                      },
                                  },
                              ],
                          },
                      }
                    : record,
            ),
        },
    });
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const row = (await screen.findAllByTestId("row-outcome"))[2];
    expect(row).toHaveTextContent(t("results.cause.deadline"));
    // Running the plan again continues it, and the text names no lifetime that could drift
    // from the one the server keeps answers for.
    expect(t("results.cause.deadline")).not.toMatch(/\d/);
    expect(row).toHaveTextContent(t("results.empty"));
});

it("tells a run that needs review that there is nothing to choose, and leads on to the results", async () => {
    const needs = { ...lookupJob, status: "needs_review" as const };
    vi.mocked(api.getJob).mockResolvedValue(needs);
    vi.mocked(api.getReport).mockResolvedValue({
        ...lookupReport,
        status: "needs_review",
    });
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    // The run says the same in its own words.
    expect(await screen.findByTestId("job-status")).toBeVisible();
    expect(
        screen.queryByText(t("exec.outcome.unconfirmed")),
    ).not.toBeInTheDocument();
    expect(
        await screen.findByText(t("exec.outcome.lookupEmpty")),
    ).toBeVisible();
    await nav("review");
    const nothing = await screen.findByTestId("fallback-review");
    // The page does not call itself a list of choices.
    expect(
        screen.getByRole("heading", { name: t("fallback.review.title") }),
    ).toBeVisible();
    expect(screen.getByText(t("fallback.reviewHint"))).toBeVisible();
    expect(nothing).toHaveTextContent(t("fallback.review.nothing"));
    expect(nothing).toHaveTextContent(t("fallback.review.empty"));
    // Not the texts of a review of names, and nothing that looks empty or broken.
    expect(screen.queryByText(t("review.loading"))).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-intro")).not.toBeInTheDocument();
    expect(
        screen.queryByText(t("review.informational")),
    ).not.toBeInTheDocument();
    expect(
        screen.queryByText(t("review.nothingToChoose")),
    ).not.toBeInTheDocument();
    await userEvent.click(
        within(nothing).getByRole("button", { name: t("ws.toExport") }),
    );
    expect(await screen.findByTestId("fallback-summary")).toBeVisible();
});

it("mentions the search on the overview only when the server has it", async () => {
    const { unmount } = render(<App />);
    expect(await screen.findByText(t("overview.know.lookup"))).toBeVisible();
    unmount();
    vi.mocked(api.limits).mockResolvedValue(limits(false));
    render(<App />);
    await screen.findByText(t("overview.know.limit", { max: 20 }));
    expect(
        screen.queryByText(t("overview.know.lookup")),
    ).not.toBeInTheDocument();
});

// The planner handed over a plan the check refused.
it("offers the short block below the validator's messages when the planner's plan failed the check", async () => {
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    const offer = await screen.findByTestId("fallback-offer");
    const invalid = screen.getByTestId("plan-invalid");
    // The messages and the title are as they were.
    expect(invalid).toHaveTextContent(t("plan.invalid.title"));
    expect(invalid).toHaveTextContent(t("plan.invalid.details"));
    expect(invalid).toHaveTextContent(
        "Enrichment plan must contain at least one step",
    );
    expect(invalid).toHaveTextContent(t("plan.rephrase"));
    expect(
        invalid.compareDocumentPosition(offer) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(invalid).not.toContainElement(offer);
    // The lead-in of this case, shortened, with the advice kept.
    expect(screen.getByTestId("fallback-lead")).toHaveTextContent(
        t("fallback.invalidText"),
    );
    expect(offer).not.toHaveTextContent(t("fallback.text"));
    expect(within(offer).getAllByRole("button")).toHaveLength(1);
    expect(within(offer).getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("plan-refusal")).not.toBeInTheDocument();
});
it("builds the same request from the invalid view as from a refusal", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await userEvent.click(button("fallback.searchNow"));
    await waitFor(() => expect(api.createFallbackPlan).toHaveBeenCalled());
    const fromRefusal = vi.mocked(api.createFallbackPlan).mock.calls[0][0];
    cleanup();
    vi.mocked(api.createFallbackPlan).mockClear();
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    await userEvent.click(button("fallback.searchNow"));
    await waitFor(() => expect(api.createFallbackPlan).toHaveBeenCalled());
    const fromInvalid = vi.mocked(api.createFallbackPlan).mock.calls[0][0];
    expect(fromInvalid).toEqual(fromRefusal);
    expect(fromInvalid).toEqual(defaultRequest);
    expect(await screen.findByTestId("fallback-auto-notice")).toHaveTextContent(
        t("fallback.auto.invalid"),
    );
});
it("does not offer it for an invalid plan where the server cannot do it", async () => {
    vi.mocked(api.limits).mockResolvedValue(limits(false));
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    expect(screen.getByTestId("plan-invalid")).toHaveTextContent(
        "Enrichment plan must contain at least one step",
    );
});
it("does not offer it for a valid plan or for an invalid draft of the search itself", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...fallbackDraft,
        validation: invalidPlan.validation,
    });
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
});
it("reads the limits again when the planner's plan fails the check", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(limits(false));
    vi.mocked(api.limits).mockResolvedValue(limits(true));
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    expect(await screen.findByTestId("fallback-offer")).toBeVisible();
    expect(api.limits).toHaveBeenCalledTimes(2);
});
