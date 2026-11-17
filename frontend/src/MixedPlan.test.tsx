import {
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import {
    api,
    WorkspaceApiError,
    type PlanDraft,
    type Preview,
    type Review,
} from "./api";
import i18n from "./i18n";
import { readFallbackSwitch } from "./fallbackModel";
import {
    fallbackDraft,
    mixedDraft,
    mixedJob,
    mixedReport,
    partialDraft,
} from "./fallbackFixtures";

// The interface of a mixed plan: the planner's steps for the columns it has a
// source for, one web search for the others. The server is mocked; what the
// page sends and shows is what is tested.
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
        resumeJob: vi.fn(),
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
const limits = (web_lookup?: boolean) => ({
    max_run_rows: 20,
    max_timeout_seconds: 300,
    default_timeout_seconds: 180,
    ...(web_lookup === undefined ? {} : { web_lookup }),
});
const t = (key: string, values?: Record<string, unknown>) =>
    i18n.t(key, { ...values }) as string;
const SWITCH = "enrichment.fallbackSearch";
const switchOn = () => localStorage.setItem(SWITCH, "on");

// What the page asks of the server for the planner's partial plan, with no
// choice of the person: the draft's own id, the task, the identifying columns
// no output writes, the unfilled column as text, the links as notes.
const defaultRequest = {
    plan_id: "plan-partial",
    question: partialDraft.instruction,
    context_columns: ["Mountain", "Country", "Rank"],
    targets: [{ column: "First ascent", kind: "text" }],
    sources_in_workbook: "notes",
};
// A mixed plan the server builds again from the person's changes.
const rebuilt: PlanDraft = {
    ...mixedDraft,
    plan_id: "plan-mixed-2",
    fingerprint: "9".repeat(64),
};
const itemOfK2 = {
    operation_id: "op-k2",
    capability: "resolve_entity",
    provider_id: "wikidata",
    query: { name: "K2", entity_type: "mountain", context: {} },
    status: "needs_review",
    reason_codes: ["ambiguous_or_unverified"],
    search_complete: true,
    affected: [{ record_id: "Sheet1!3", row: 3, step_id: "resolve" }],
    candidates: [
        {
            source: "wikidata",
            entity_id: "Q43512",
            label: "K2",
            entity_type: "mountain",
            verification: "verified" as const,
            attributes: {},
            evidence: [{ source: "wikidata", reference: "Q43512" }],
            selectable: true,
            blocker: null,
            recommended: false,
            recommendation_reasons: [],
            reason_codes: ["verified_context_match"],
        },
        {
            source: "wikidata",
            entity_id: "Q99999",
            label: "K2 (peak)",
            entity_type: "mountain",
            verification: "verified" as const,
            attributes: {},
            evidence: [{ source: "wikidata", reference: "Q99999" }],
            selectable: true,
            blocker: null,
            recommended: false,
            recommendation_reasons: [],
            reason_codes: ["verified_context_match"],
        },
    ],
};
const mixedReview: Review = {
    job_id: mixedJob.job_id,
    previous_job_id: null,
    items: [itemOfK2],
    informational: [],
    affected_rows: 1,
};

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
    vi.mocked(api.createPlan).mockResolvedValue(partialDraft);
    vi.mocked(api.createFallbackPlan).mockResolvedValue(mixedDraft);
    vi.mocked(api.getPlan).mockResolvedValue(partialDraft);
    vi.mocked(api.approvePlan).mockImplementation(async (id) => ({
        ...(id === partialDraft.plan_id ? partialDraft : mixedDraft),
        status: "approved",
        approved_at: "2026-11-10T09:00:00Z",
    }));
    vi.mocked(api.startRun).mockResolvedValue({
        ...mixedJob,
        status: "queued",
        artifacts: {},
    });
    vi.mocked(api.getJob).mockResolvedValue(mixedJob);
    vi.mocked(api.getReport).mockResolvedValue(mixedReport);
    vi.mocked(api.getReview).mockResolvedValue(mixedReview);
});
const button = (key: string, values?: Record<string, unknown>) =>
    screen.getByRole("button", { name: t(key, values) });
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
const context = () => within(screen.getByTestId("fallback-context"));
const targets = () => within(screen.getByTestId("fallback-targets"));

// Basic: two columns to fill, Height and First ascent; the planner answers with
// a plan for the first and names the second as having no source.
async function partialTask(view = "unfilled-notice") {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: partialDraft.instruction },
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
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId(view);
}
async function toMixedPlan() {
    switchOn();
    render(<App opening="dataset" />);
    await partialTask("mixed-notice");
}

// --- With the switch on ------------------------------------------------------------

it("builds the mixed plan at once for a plan with columns without a source, with the defaults, and shows it as the plan", async () => {
    await toMixedPlan();
    // One request for the planner, then exactly one for the search part.
    expect(api.createPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledWith(defaultRequest);
    // At the top, in plain words, both groups of columns with their letters.
    const notice = screen.getByTestId("mixed-notice");
    expect(screen.getByTestId("mixed-filled")).toHaveTextContent(
        t("fallback.mixed.filled", { columns: "“Height” (E)" }),
    );
    expect(screen.getByTestId("mixed-searched")).toHaveTextContent(
        t("fallback.mixed.searched", { columns: "“First ascent” (F)" }),
    );
    expect(notice).toHaveTextContent(/not verified/);
    // The banner says that some values, and which, are found by the search.
    expect(screen.getByTestId("fallback-banner")).toHaveTextContent(
        t("fallback.mixed.banner", { columns: "“First ascent” (F)" }),
    );
    // The steps: the planner's, then the search, with the existing description.
    const steps = document.querySelectorAll(".plan-steps > li");
    expect(steps).toHaveLength(3);
    expect(steps[2]).toHaveAttribute("data-lookup", "true");
    expect(steps[2]).toHaveTextContent(t("cap.web_lookup.title"));
    expect(steps[2]).toHaveTextContent(
        t("fallback.plan.question", { question: partialDraft.instruction }),
    );
    expect(steps[2]).toHaveTextContent(
        t("fallback.plan.context", { columns: "Mountain, Country, Rank" }),
    );
    expect(steps[2]).toHaveTextContent(
        t("fallback.plan.column", {
            name: "First ascent",
            letter: "F",
            kind: t("fallback.plan.kind.text"),
        }),
    );
    // The picture marks the search as not verified; no slot names or JSON in Basic.
    const diagram = screen.getByTestId("plan-diagram");
    expect(within(diagram).getAllByRole("button")).toHaveLength(3);
    expect(within(diagram).getByTestId("node-ai-search")).toHaveTextContent(
        t("plan.diagram.aiSearch"),
    );
    expect(
        screen.getByTestId("mixed-extras").closest(".plan-words"),
    ).not.toBeNull();
    expect(document.querySelector(".plan-words")!.textContent).not.toMatch(
        /value_\d|ctx_\d|"question"|"slots?"/,
    );
    // Under the description: what is sent, and the two ways on.
    expect(screen.getByTestId("fallback-sent")).toHaveTextContent(
        t("fallback.sent", { columns: "Mountain, Country, Rank" }),
    );
    expect(button("fallback.change.open")).toBeEnabled();
    expect(button("fallback.mixed.only")).toBeEnabled();
    // The planner's own notice for the columns is gone; nothing ran yet.
    expect(screen.queryByTestId("unfilled-notice")).not.toBeInTheDocument();
    expect(button("ws.approveRun")).toBeEnabled();
    expect(api.approvePlan).not.toHaveBeenCalled();
    expect(api.startRun).not.toHaveBeenCalled();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        t("plan.checked"),
    );
});
it("says how many columns have no source while the web search is added", async () => {
    let finish: (draft: PlanDraft) => void = () => undefined;
    vi.mocked(api.createFallbackPlan).mockReturnValue(
        new Promise((resolve) => (finish = resolve)),
    );
    switchOn();
    render(<App opening="dataset" />);
    await partialTask("fallback-progress");
    expect(screen.getByTestId("fallback-progress")).toHaveTextContent(
        "No source for 1 column. Adding a web search for it…",
    );
    expect(screen.queryByTestId("unfilled-notice")).not.toBeInTheDocument();
    finish(mixedDraft);
    expect(await screen.findByTestId("mixed-notice")).toBeVisible();
    expect(screen.queryByTestId("fallback-progress")).not.toBeInTheDocument();
});
it("does not ask for a mixed plan when the planner's plan fills every column", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...partialDraft,
        unfilled_targets: [],
        output_columns: { Height: "E" },
    });
    switchOn();
    render(<App opening="dataset" />);
    await partialTask("validation");
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.queryByTestId("unfilled-notice")).not.toBeInTheDocument();
    expect(screen.queryByTestId("mixed-notice")).not.toBeInTheDocument();
});
it("leaves the whole-task search as it was for a plan the planner refused", async () => {
    vi.mocked(api.createPlan).mockResolvedValue({
        ...fallbackDraft,
        kind: undefined,
        instruction: partialDraft.instruction,
        plan: null,
        planning_status: "unsupported",
        reason: "Nothing in the sources.",
        validation: { valid: false, errors: [] },
    });
    vi.mocked(api.createFallbackPlan).mockResolvedValue(fallbackDraft);
    switchOn();
    render(<App opening="dataset" />);
    await partialTask("fallback-plan");
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    const sent = vi.mocked(api.createFallbackPlan).mock.calls[0][0];
    // The request of the search for the whole task: the table, not a plan id.
    expect(sent).not.toHaveProperty("plan_id");
    expect(sent).toMatchObject({ dataset_id: "source", sheet_name: "Sheet1" });
    expect(screen.queryByTestId("mixed-notice")).not.toBeInTheDocument();
    expect(screen.getByTestId("fallback-auto-notice")).toBeVisible();
});

// --- With the switch off -----------------------------------------------------------

it("keeps the partial plan and offers the search for its columns in the notice, when the switch is off", async () => {
    render(<App opening="dataset" />);
    await partialTask();
    const notice = screen.getByTestId("unfilled-notice");
    expect(notice).toHaveTextContent(
        t("plan.unfilled", { columns: "“First ascent” (F)" }),
    );
    // The button and the checkbox are in the notice, nothing was asked of the server.
    const inside = within(notice);
    expect(inside.getAllByRole("button")).toHaveLength(1);
    expect(inside.getByRole("button")).toHaveAccessibleName(
        t("fallback.mixed.search"),
    );
    expect(inside.getByLabelText(t("fallback.autoNext"))).not.toBeChecked();
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.queryByTestId("mixed-notice")).not.toBeInTheDocument();
    // The partial plan can be approved and run as it is.
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalled());
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-partial",
        partialDraft.fingerprint,
    );
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
});
it("builds the mixed plan with the same request as the switch when the button is pressed, without turning the switch on", async () => {
    render(<App opening="dataset" />);
    await partialTask();
    await userEvent.click(button("fallback.mixed.search"));
    expect(await screen.findByTestId("mixed-notice")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    expect(api.createFallbackPlan).toHaveBeenCalledWith(defaultRequest);
    expect(readFallbackSwitch()).toBe(false);
    expect(button("ws.approveRun")).toBeEnabled();
});
it("turns the switch on from the checkbox without building anything, and uses it for the next plan", async () => {
    render(<App opening="dataset" />);
    await partialTask();
    await userEvent.click(screen.getByLabelText(t("fallback.autoNext")));
    expect(readFallbackSwitch()).toBe(true);
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.getByTestId("unfilled-notice")).toBeVisible();
});
it("only says that the columns stay as they are where the server cannot search the web", async () => {
    for (const stated of [limits(false), limits()]) {
        vi.mocked(api.limits).mockResolvedValue(stated);
        switchOn();
        const { unmount } = render(<App opening="dataset" />);
        await partialTask();
        const notice = screen.getByTestId("unfilled-notice");
        expect(notice).toHaveTextContent(
            t("plan.unfilled", { columns: "“First ascent” (F)" }),
        );
        expect(within(notice).queryByRole("button")).not.toBeInTheDocument();
        expect(within(notice).queryByRole("checkbox")).not.toBeInTheDocument();
        expect(api.createFallbackPlan).not.toHaveBeenCalled();
        expect(button("ws.approveRun")).toBeEnabled();
        unmount();
    }
});
it("offers the button when the key came after the page was loaded", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(limits(false));
    vi.mocked(api.limits).mockResolvedValue(limits(true));
    render(<App opening="dataset" />);
    await partialTask();
    expect(await screen.findByTestId("unfilled-search")).toBeVisible();
    expect(api.limits).toHaveBeenCalledTimes(2);
});

// --- When the server refuses ---------------------------------------------------------

it("keeps the partial plan with the server's message and opens the form for the search part", async () => {
    vi.mocked(api.createFallbackPlan).mockRejectedValueOnce(
        new WorkspaceApiError(
            "fallback_plan_invalid",
            "The column 'Rank' is not usable.",
            422,
        ),
    );
    switchOn();
    render(<App opening="dataset" />);
    await partialTask();
    const form = await screen.findByTestId("fallback-form");
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    // The partial plan stays, approvable, in the notice that now has the form.
    expect(
        within(screen.getByTestId("unfilled-notice")).getByTestId(
            "fallback-form",
        ),
    ).toBe(form);
    expect(button("ws.approveRun")).toBeEnabled();
    const alert = within(form).getByRole("alert");
    expect(alert).toHaveAttribute("data-error-code", "fallback_plan_invalid");
    expect(alert).toHaveTextContent(t("error.fallback_plan_invalid"));
    expect(alert).toHaveTextContent("The column 'Rank' is not usable.");
    // Restricted to the searched column: no other column to fill, none filled by the planner.
    expect(targets().getAllByRole("combobox")).toHaveLength(1);
    expect(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
    ).toBeVisible();
    expect(targets().queryByText(/Height/)).not.toBeInTheDocument();
    expect(context().getByRole("checkbox", { name: /^Height/ })).toBeDisabled();
    expect(
        context().getByRole("checkbox", { name: /^First ascent/ }),
    ).toBeDisabled();
    for (const name of ["Mountain", "Country", "Rank"])
        expect(
            context().getByRole("checkbox", { name: new RegExp(`^${name}`) }),
        ).toBeChecked();
    // Fixed and sent again: the mixed plan appears.
    await userEvent.click(context().getByRole("checkbox", { name: /^Rank/ }));
    await userEvent.click(button("fallback.mixed.build"));
    expect(await screen.findByTestId("mixed-notice")).toBeVisible();
    expect(vi.mocked(api.createFallbackPlan).mock.calls[1][0]).toEqual({
        ...defaultRequest,
        context_columns: ["Mountain", "Country"],
    });
});
it("asks for a column that identifies the row when no column of the table can", async () => {
    vi.mocked(api.preview).mockResolvedValue({
        ...preview,
        profile: {
            ...preview.profile,
            columns: [
                column("Born", "Date"),
                column("Height", "Number", 10),
                column("First ascent", "Unknown", 10),
            ],
        },
    });
    switchOn();
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await screen.findByLabelText(t("ws.fillColumn"));
    fireEvent.change(screen.getByLabelText("Enrichment instruction"), {
        target: { value: partialDraft.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillColumn")),
        "B",
    );
    await userEvent.click(button("ws.addFill"));
    await userEvent.selectOptions(
        screen.getByLabelText(t("ws.fillAnother")),
        "C",
    );
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("fallback-form");
    // Nothing is sent to the server for it.
    expect(api.createFallbackPlan).not.toHaveBeenCalled();
    expect(screen.getByTestId("fallback-need")).toHaveTextContent(
        t("fallback.need.context"),
    );
    expect(button("fallback.mixed.build")).toBeDisabled();
    expect(button("ws.approveRun")).toBeEnabled();
});

// --- Changing and going back ------------------------------------------------------------

it("changes the search part in a form limited to the searched columns and builds again from the same planner draft", async () => {
    await toMixedPlan();
    vi.mocked(api.createFallbackPlan).mockResolvedValue(rebuilt);
    await userEvent.click(button("fallback.change.open"));
    // The choices of this plan, not the defaults; only the searched column is on offer.
    expect(screen.getByLabelText(t("fallback.question"))).toHaveValue(
        partialDraft.instruction,
    );
    expect(targets().getAllByRole("combobox")).toHaveLength(1);
    expect(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
    ).toHaveValue("text");
    expect(context().getByRole("checkbox", { name: /^Height/ })).toBeDisabled();
    expect(context().getByRole("checkbox", { name: /^Rank/ })).toBeChecked();
    await userEvent.click(context().getByRole("checkbox", { name: /^Rank/ }));
    await userEvent.selectOptions(
        targets().getByLabelText(t("fallback.kind", { name: "First ascent" })),
        "number",
    );
    await userEvent.type(
        targets().getByLabelText(t("fallback.unit", { name: "First ascent" })),
        "year",
    );
    await userEvent.click(button("fallback.change.save"));
    await waitFor(() =>
        expect(api.createFallbackPlan).toHaveBeenCalledTimes(2),
    );
    // The same planner draft, the searched columns fixed, nothing of the table repeated.
    expect(vi.mocked(api.createFallbackPlan).mock.calls[1][0]).toEqual({
        plan_id: "plan-partial",
        question: partialDraft.instruction,
        context_columns: ["Mountain", "Country"],
        targets: [{ column: "First ascent", kind: "number", unit: "year" }],
        sources_in_workbook: "notes",
    });
    // The new plan is the one that is approved.
    await waitFor(() =>
        expect(screen.queryByTestId("fallback-form")).not.toBeInTheDocument(),
    );
    await userEvent.click(button("ws.approveRun"));
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-mixed-2",
        rebuilt.fingerprint,
    );
});
it("keeps the mixed plan and the choices when the new plan is refused", async () => {
    await toMixedPlan();
    vi.mocked(api.createFallbackPlan).mockRejectedValueOnce(
        new WorkspaceApiError("web_lookup_no_key", "No key.", 422),
    );
    await userEvent.click(button("fallback.change.open"));
    await userEvent.click(context().getByRole("checkbox", { name: /^Rank/ }));
    await userEvent.click(button("fallback.change.save"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveAttribute("data-error-code", "web_lookup_no_key");
    expect(screen.getByTestId("mixed-notice")).toBeVisible();
    expect(
        context().getByRole("checkbox", { name: /^Rank/ }),
    ).not.toBeChecked();
});
it("goes back to the planner's plan with the system's sources only, without building anything", async () => {
    await toMixedPlan();
    await userEvent.click(button("fallback.mixed.only"));
    // The stored draft is fetched by its id; nothing is built.
    await waitFor(() =>
        expect(api.getPlan).toHaveBeenCalledWith("plan-partial"),
    );
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    // The partial plan again, with the notice and the button to return, although
    // the switch is on: it was not asked for again.
    const notice = await screen.findByTestId("unfilled-notice");
    expect(
        within(notice).getByRole("button", {
            name: t("fallback.mixed.search"),
        }),
    ).toBeEnabled();
    expect(screen.queryByTestId("mixed-notice")).not.toBeInTheDocument();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(1);
    // It is the plan that is approved and run, and the unfilled column is not in it.
    await userEvent.click(button("ws.approveRun"));
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-partial",
        partialDraft.fingerprint,
    );
});
it("returns to the mixed plan from the planner's plan with the button", async () => {
    await toMixedPlan();
    await userEvent.click(button("fallback.mixed.only"));
    await screen.findByTestId("unfilled-notice");
    await userEvent.click(button("fallback.mixed.search"));
    expect(await screen.findByTestId("mixed-notice")).toBeVisible();
    expect(api.createFallbackPlan).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.createFallbackPlan).mock.calls[1][0]).toEqual(
        defaultRequest,
    );
});

// --- Run, review and results ---------------------------------------------------------

it("tells the run that some values are searched, and what the notes are for", async () => {
    await toMixedPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalled());
    expect(api.approvePlan).toHaveBeenCalledWith(
        "plan-mixed",
        mixedDraft.fingerprint,
    );
    expect(await screen.findByTestId("fallback-banner")).toHaveTextContent(
        t("fallback.mixed.banner", { columns: "“First ascent” (F)" }),
    );
    expect(screen.getByTestId("fallback-sources")).toHaveTextContent(
        t("fallback.runSources.notes"),
    );
});
it("reviews a mixed job like a planner job: its names can be chosen", async () => {
    await toMixedPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReview).toHaveBeenCalled());
    await nav("review");
    // The choices of the source step, not the panel for a run with nothing to choose.
    expect(await screen.findByTestId("review-intro")).toBeVisible();
    expect(screen.getByTestId("review-K2")).toBeVisible();
    expect(screen.queryByTestId("fallback-review")).not.toBeInTheDocument();
    expect(
        screen.queryByRole("heading", { name: t("fallback.review.title") }),
    ).not.toBeInTheDocument();
    expect(
        screen.getByRole("heading", { name: t("review.title") }),
    ).toBeVisible();
    expect(
        screen.getByRole("button", { name: t("review.resume") }),
    ).toBeVisible();
});
it("says there is nothing to choose in a mixed job only when there is nothing", async () => {
    vi.mocked(api.getReview).mockResolvedValue({
        ...mixedReview,
        items: [],
        affected_rows: 0,
    });
    await toMixedPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReview).toHaveBeenCalled());
    await nav("review");
    expect(await screen.findByText(t("review.nothingToChoose"))).toBeVisible();
    expect(screen.queryByTestId("review-intro")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-K2")).not.toBeInTheDocument();
    expect(button("ws.toExport")).toBeEnabled();
});
it("shows each column with its own kind of trust, the marks of the searched values, the filter and the summary", async () => {
    await toMixedPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const summary = await screen.findByTestId("fallback-summary");
    expect(summary).toHaveTextContent(
        t("fallback.mixed.bannerDone", { columns: "“First ascent” (F)" }),
    );
    // The summary names the searched columns and counts what the search found.
    expect(screen.getByTestId("lookup-columns")).toHaveTextContent(
        t("results.lookup.searched", { columns: "“First ascent” (F)" }),
    );
    expect(summary).toHaveTextContent(
        t("results.lookup.summary", { asked: 3, found: 2, concluded: 1 }),
    );
    // Rows are left open: the page does not claim that every value is verified.
    expect(
        screen.getByText(t("fallback.mixed.unresolved")),
    ).toBeInTheDocument();
    expect(
        screen.queryByText(t("workflow.unresolved")),
    ).not.toBeInTheDocument();
    // Height: what stands behind a record of a source; First ascent: a search that is not verified.
    const stats = within(screen.getByTestId("result-stats")).getAllByRole(
        "listitem",
    );
    expect(stats[0]).toHaveTextContent("Height");
    expect(stats[0]).toHaveTextContent(
        t("provenance.record", { source: "Wikidata" }),
    );
    expect(stats[1]).toHaveTextContent("First ascent");
    expect(stats[1]).toHaveTextContent(
        t("provenance.ai_found", {
            source: t("source.openai_web_search"),
        }),
    );
    // Only the searched cells carry a mark.
    const rows = screen.getAllByTestId("row-outcome");
    expect(rows).toHaveLength(3);
    expect(
        [...rows[0].querySelectorAll("td[data-basis]")].map((cell) =>
            cell.getAttribute("data-basis"),
        ),
    ).toEqual(["concluded"]);
    expect(within(rows[0]).getAllByTestId("basis-mark")).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("8,849 m");
    expect(rows[1]).toHaveTextContent("1954");
    // The first thing that went wrong in this row is the source's.
    expect(rows[2]).toHaveTextContent(
        t("results.cause.not_found", { subject: "Nowhere" }),
    );
    // The filter and its count.
    await userEvent.click(
        screen.getByRole("button", {
            name: t("results.filter.concluded", { count: 1 }),
        }),
    );
    expect(screen.getAllByTestId("row-outcome")).toHaveLength(1);
    expect(screen.getByTestId("row-outcome")).toHaveTextContent("Everest");
    // Not in Basic: the spend line, and no slot names.
    expect(screen.queryByTestId("fallback-spend")).not.toBeInTheDocument();
    expect(
        screen.getByRole("region", { name: t("ws.outcomePreview") }),
    ).not.toHaveTextContent(/value_\d|ctx_\d/);
});
it("does not call a gap in a source column a search that found no page", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
        ...mixedReport,
        rows: mixedReport.rows!.map((row, index) =>
            index === 0
                ? {
                      ...row,
                      empty_outputs: {
                          Height: { field: "value", code: "VALUE_NOT_GIVEN" },
                      },
                  }
                : row,
        ),
    });
    await toMixedPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const gap = await screen.findByTestId("result-gap");
    expect(gap).not.toHaveTextContent(
        t("results.lookupGap", { column: "Height" }),
    );
});
it("gives Advanced the spend of the search with the answers that were kept", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    switchOn();
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    fireEvent.change(await screen.findByLabelText("Enrichment instruction"), {
        target: { value: partialDraft.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 1", { exact: true }),
        "E",
    );
    await userEvent.click(button("workflow.addMapping"));
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 2", { exact: true }),
        "F",
    );
    await userEvent.click(screen.getByLabelText(t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("mixed-notice");
    await userEvent.click(
        await screen.findByRole("button", { name: t("ws.approveContinue") }),
    );
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const spend = await screen.findByTestId("fallback-spend");
    expect(spend).toHaveTextContent(
        t("results.lookup.spend", {
            requests: 1,
            input: 2000,
            output: 400,
            searches: 4,
            configuration: "openai-web-lookup-p1-gpt-6-luna-low",
        }),
    );
    expect(spend).toHaveTextContent(t("results.lookup.kept", { count: 2 }));
});
it("says nothing of kept answers when there are none", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
        ...mixedReport,
        web_lookup: { ...mixedReport.web_lookup!, answers_kept: 0 },
    });
    localStorage.setItem("enrichment.mode", "advanced");
    switchOn();
    render(<App opening="dataset" />);
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["x"], "mountains.xlsx"),
    );
    await userEvent.click(button("previewAction"));
    fireEvent.change(await screen.findByLabelText("Enrichment instruction"), {
        target: { value: partialDraft.instruction },
    });
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 1", { exact: true }),
        "E",
    );
    await userEvent.click(button("workflow.addMapping"));
    await userEvent.selectOptions(
        screen.getByLabelText("Output column 2", { exact: true }),
        "F",
    );
    await userEvent.click(screen.getByLabelText(t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("mixed-notice");
    await userEvent.click(
        await screen.findByRole("button", { name: t("ws.approveContinue") }),
    );
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    expect(await screen.findByTestId("fallback-spend")).not.toHaveTextContent(
        t("results.lookup.kept", { count: 0 }),
    );
    expect(screen.getByTestId("fallback-spend").textContent).not.toMatch(
        /kept/,
    );
});
it("speaks the other languages too", async () => {
    await toMixedPlan();
    for (const locale of ["uk", "de", "en"] as const) {
        await i18n.changeLanguage(locale);
        expect(await screen.findByTestId("mixed-notice")).toHaveTextContent(
            "“First ascent” (F)",
        );
        expect(screen.getByTestId("mixed-filled")).toHaveTextContent(
            t("fallback.mixed.filled", { columns: "“Height” (E)" }),
        );
        expect(screen.getByTestId("node-ai-search")).toHaveTextContent(
            t("plan.diagram.aiSearch"),
        );
    }
});
