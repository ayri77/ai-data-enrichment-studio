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
const open = () => userEvent.click(button("fallback.open"));
const context = () => within(screen.getByTestId("fallback-context"));
const targets = () => within(screen.getByTestId("fallback-targets"));
const choose = (name: string) =>
    userEvent.click(
        context().getByRole("checkbox", { name: new RegExp(`^${name}`) }),
    );

it("offers the search below the explanation when the planner could not build a plan", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    const offer = await screen.findByTestId("fallback-offer");
    expect(within(offer).getByRole("heading")).toHaveTextContent(
        "Search the web with AI instead",
    );
    expect(offer).toHaveTextContent(t("fallback.text"));
    // After the explanation and the list of what the system can do, and apart from them.
    const refusal = screen.getByTestId("plan-refusal");
    expect(
        refusal.compareDocumentPosition(offer) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(refusal).not.toContainElement(offer);
    // A button first; the form is not open and it is not a dialog.
    expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await open();
    expect(screen.getByTestId("fallback-form")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
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

it("starts with nothing chosen and the task the person wrote", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        refused.instruction,
    );
    for (const box of context().getAllByRole("checkbox"))
        expect(box).not.toBeChecked();
    // Without a column that identifies the row nothing can be sent.
    expect(button("fallback.build")).toBeDisabled();
    expect(screen.getByTestId("fallback-need")).toHaveTextContent(
        t("fallback.need.context"),
    );
});
it("keeps the columns to fill and the other kinds of column out of the choice", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
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
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    const kind = (name: string) =>
        targets().getByLabelText(t("fallback.kind", { name }));
    // A column of numbers is proposed as Number, any other as Text.
    expect(kind("Height")).toHaveValue("number");
    expect(kind("First ascent")).toHaveValue("text");
    expect(kind("Climbed in winter")).toHaveValue("text");
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
    expect(unit("Height")).toHaveValue("");
    // No letters or slots anywhere in what the person reads.
    expect(screen.getByTestId("fallback-form")).not.toHaveTextContent(
        /value_\d|ctx_\d/,
    );
});
it("says what leaves the server and what it costs, with the numbers of the page", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    const notice = screen.getByTestId("fallback-notice");
    expect(notice).toHaveTextContent(t("fallback.notice.requests"));
    expect(notice).toHaveTextContent(t("fallback.notice.sends"));
    // The rows of this run: the table has ten, the server takes twenty.
    expect(notice).toHaveTextContent(t("fallback.notice.rows", { rows: 10 }));
    expect(notice).toHaveTextContent(t("fallback.notice.cost"));
    expect(notice).toHaveTextContent(t("fallback.notice.unverified"));
    // Above the button, which is the last thing in the form.
    expect(
        notice.compareDocumentPosition(button("fallback.build")) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
});
it("preselects the cell notes, with the reason, and lets the person change them", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    const form = within(screen.getByTestId("fallback-form"));
    const sources = form.getByLabelText(t("sources.title"));
    expect(sources).toHaveValue("notes");
    expect(form.getByText(t("fallback.sourcesWhy"))).toBeVisible();
    await userEvent.selectOptions(sources, "sheet");
    expect(sources).toHaveValue("sheet");
});

it("sends the choices with the same table and columns as the failed request, and shows the plan", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    await choose("Country");
    await choose("Mountain");
    await userEvent.type(
        targets().getByLabelText(t("fallback.unit", { name: "Height" })),
        "m",
    );
    await userEvent.selectOptions(
        targets().getByLabelText(
            t("fallback.kind", { name: "Climbed in winter" }),
        ),
        "yes_no",
    );
    fireEvent.change(screen.getByLabelText(t("fallback.question")), {
        target: { value: "When was it first climbed?" },
    });
    expect(button("fallback.build")).toBeEnabled();
    await userEvent.click(button("fallback.build"));
    await waitFor(() =>
        expect(api.createFallbackPlan).toHaveBeenCalledTimes(1),
    );
    expect(api.createFallbackPlan).toHaveBeenCalledWith({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:G11",
        question: "When was it first climbed?",
        context_columns: ["Mountain", "Country"],
        targets: [
            { column: "Height", kind: "number", unit: "m" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
        output_columns: {
            Height: "E",
            "First ascent": "F",
            "Climbed in winter": "G",
        },
        existing_outputs: "keep",
        sources_in_workbook: "notes",
    });
    // The draft becomes the current one: the same view now shows the plan.
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
    expect(screen.queryByTestId("fallback-offer")).not.toBeInTheDocument();
    // No model made an assumption here: the sentence is headed by how the
    // values are obtained.
    expect(
        screen.getByRole("heading", { name: t("fallback.howObtained") }),
    ).toBeVisible();
    expect(
        screen.queryByRole("heading", { name: t("workflow.assumptions") }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("plan-refusal")).not.toBeInTheDocument();
});
it("waits with the button disabled and a progress mark", async () => {
    let finish: (draft: PlanDraft) => void = () => undefined;
    vi.mocked(api.createFallbackPlan).mockReturnValue(
        new Promise((resolve) => (finish = resolve)),
    );
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    await choose("Mountain");
    await userEvent.click(button("fallback.build"));
    await waitFor(() => expect(button("fallback.build")).toBeDisabled());
    expect(button("fallback.build").querySelector(".spin")).not.toBeNull();
    finish(fallbackDraft);
    expect(await screen.findByTestId("fallback-plan")).toBeVisible();
});
it("shows a refusal next to the form with the server's message, and keeps what was entered", async () => {
    vi.mocked(api.createFallbackPlan).mockRejectedValueOnce(
        new WorkspaceApiError(
            "fallback_plan_invalid",
            "The column 'Born' has date values; only text and number columns can identify a row.",
            422,
        ),
    );
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    await choose("Country");
    fireEvent.change(screen.getByLabelText(t("fallback.question")), {
        target: { value: "Something else" },
    });
    await userEvent.selectOptions(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
        "yes_no",
    );
    await userEvent.click(button("fallback.build"));
    const alert = await within(screen.getByTestId("fallback-form")).findByRole(
        "alert",
    );
    expect(alert).toHaveAttribute("data-error-code", "fallback_plan_invalid");
    expect(alert).toHaveTextContent(t("error.fallback_plan_invalid"));
    expect(alert).toHaveTextContent(
        "only text and number columns can identify a row",
    );
    // Not as the banner of the page, and nothing was lost.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        "Something else",
    );
    expect(context().getByRole("checkbox", { name: /^Country/ })).toBeChecked();
    expect(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
    ).toHaveValue("yes_no");
    expect(button("fallback.build")).toBeEnabled();
    // The refusal of the planner is still what the page shows.
    expect(screen.getByTestId("plan-refusal")).toBeVisible();
});
it("speaks the other languages too", async () => {
    render(<App opening="dataset" />);
    await refusedTask();
    await userEvent.selectOptions(screen.getByLabelText(t("language")), "uk");
    expect(screen.getByTestId("fallback-offer")).toHaveTextContent(
        "Натомість шукати в інтернеті за допомогою ШІ",
    );
    await open();
    expect(screen.getByTestId("fallback-notice")).toHaveTextContent("OpenAI");
});

// --- The plan, the run, the results ---------------------------------------------------

async function toPlan() {
    render(<App opening="dataset" />);
    await refusedTask();
    await open();
    await choose("Mountain");
    await userEvent.click(button("fallback.build"));
    await screen.findByTestId("fallback-plan");
}
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
    await screen.findByTestId("plan-refusal");
    await open();
    await choose("Mountain");
    await userEvent.click(button("fallback.build"));
    expect(await screen.findByTestId("fallback-banner")).toBeVisible();
    expect(screen.getByRole("region", { name: t("ws.graph") })).toBeVisible();
    expect(screen.getAllByText("web_lookup")[0]).toBeVisible();
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
    await screen.findByTestId("plan-refusal");
    await open();
    await choose("Mountain");
    await userEvent.click(button("fallback.build"));
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
    expect(row).not.toHaveTextContent(/run again/i);
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
it("offers the search below the validator's messages when the planner's plan failed the check", async () => {
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
    // The lead-in of this case, not that of a refusal.
    expect(screen.getByTestId("fallback-lead")).toHaveTextContent(
        t("fallback.invalidText"),
    );
    expect(offer).not.toHaveTextContent(t("fallback.text"));
    expect(screen.queryByTestId("plan-refusal")).not.toBeInTheDocument();
});
it("builds the same request from the invalid view as from a refusal", async () => {
    const fill = async () => {
        await open();
        await choose("Mountain");
        await choose("Country");
        await userEvent.click(button("fallback.build"));
        await waitFor(() => expect(api.createFallbackPlan).toHaveBeenCalled());
        return vi.mocked(api.createFallbackPlan).mock.calls[0][0];
    };
    render(<App opening="dataset" />);
    await refusedTask();
    const fromRefusal = await fill();
    cleanup();
    vi.mocked(api.createFallbackPlan).mockClear();
    vi.mocked(api.createPlan).mockResolvedValue(invalidPlan);
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    await screen.findByTestId("fallback-offer");
    // Nothing is preselected among the identifying columns; the task is the question.
    await open();
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        refused.instruction,
    );
    for (const box of context().getAllByRole("checkbox"))
        expect(box).not.toBeChecked();
    cleanup();
    vi.mocked(api.createFallbackPlan).mockClear();
    render(<App opening="dataset" />);
    await refusedTask("plan-invalid");
    await screen.findByTestId("fallback-offer");
    const fromInvalid = await fill();
    expect(fromInvalid).toEqual(fromRefusal);
    expect(fromInvalid).toMatchObject({
        dataset_id: "source",
        sheet_name: "Sheet1",
        range_ref: "A1:G11",
        output_columns: {
            Height: "E",
            "First ascent": "F",
            "Climbed in winter": "G",
        },
    });
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
