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
import { api, WorkspaceApiError } from "./api";
import i18n from "./i18n";
import {
    extractionDraft,
    extractionJob,
    extractionReport,
    mailDataset,
    mailPreview,
} from "./extractionFixtures";
import { readBytes } from "./mailModel";

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
        uploadMail: vi.fn(),
        preview: vi.fn(),
        remove: vi.fn(),
        createPlan: vi.fn(),
        createFallbackPlan: vi.fn(),
        createExtractionPlan: vi.fn(),
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

const limits = (mail?: boolean, text_extraction?: boolean) => ({
    max_run_rows: 20,
    max_timeout_seconds: 300,
    default_timeout_seconds: 180,
    ...(mail === undefined ? {} : { mail }),
    ...(text_extraction === undefined ? {} : { text_extraction }),
});
const t = (key: string, values?: Record<string, unknown>) =>
    i18n.t(key, { ...values }) as string;
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
const keyPresent: Awaited<ReturnType<typeof api.providerSettings>> = {
    provider: "openai",
    supported_models: [{ id: "gpt-4.1-mini", label: "gpt-4.1-mini" }],
    model: "gpt-4.1-mini",
    key_present: true,
    server_network_allowed: true,
    live_ready: true,
    planning_mode: "live",
    configuration_enabled: true,
    storage: "process_memory",
};

beforeEach(async () => {
    cleanup();
    vi.resetAllMocks();
    localStorage.clear();
    await i18n.changeLanguage("en");
    vi.mocked(api.limits).mockResolvedValue(limits(true, true));
    vi.mocked(api.providerSettings).mockResolvedValue({ ...keyPresent });
    vi.mocked(api.uploadMail).mockResolvedValue(mailDataset);
    vi.mocked(api.preview).mockResolvedValue(mailPreview);
    vi.mocked(api.createExtractionPlan).mockResolvedValue(extractionDraft);
    vi.mocked(api.approvePlan).mockResolvedValue({
        ...extractionDraft,
        status: "approved",
        approved_at: "2026-11-19T09:00:00Z",
    });
    vi.mocked(api.startRun).mockResolvedValue({
        ...extractionJob,
        status: "queued",
        artifacts: {},
    });
    vi.mocked(api.getJob).mockResolvedValue(extractionJob);
    vi.mocked(api.getReport).mockResolvedValue(extractionReport);
    vi.mocked(api.getReview).mockResolvedValue({
        job_id: extractionJob.job_id,
        previous_job_id: null,
        items: [],
        informational: [],
        affected_rows: 0,
    });
});

const files = () => [
    new File(["Subject: a\r\n\r\none"], "private-one.eml"),
    new File(["Subject: b\r\n\r\ntwo"], "private-two.eml"),
];
async function imported() {
    render(<App opening="dataset" />);
    await userEvent.upload(
        await screen.findByLabelText(t("mail.input")),
        files(),
    );
    await screen.findByTestId("mail-counts");
}
async function fillEditor() {
    fireEvent.change(screen.getByLabelText(t("extract.question")), {
        target: { value: "Payments: what was paid?" },
    });
    fireEvent.change(screen.getByLabelText(t("extract.name", { n: 1 })), {
        target: { value: "Amount" },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("extract.kind", { n: 1 })),
        "number",
    );
}
async function toPlan() {
    await imported();
    await fillEditor();
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("extraction-plan");
}

it("offers the mail entry only where the server says so, and leaves no trace elsewhere", async () => {
    for (const stated of [limits(), limits(false), limits(false, false)]) {
        vi.mocked(api.limits).mockResolvedValue(stated);
        const { unmount } = render(<App opening="dataset" />);
        await screen.findByLabelText("Upload Excel workbook");
        await waitFor(() => expect(api.limits).toHaveBeenCalled());
        expect(screen.queryByTestId("mail-import")).not.toBeInTheDocument();
        expect(screen.queryByText(t("mail.title"))).not.toBeInTheDocument();
        expect(screen.queryByLabelText(t("mail.input"))).toBeNull();
        unmount();
        vi.mocked(api.limits).mockClear();
    }
    vi.mocked(api.limits).mockResolvedValue(limits(true, true));
    render(<App opening="dataset" />);
    expect(await screen.findByTestId("mail-import")).toBeVisible();
    expect(screen.getByTestId("mail-privacy")).toHaveTextContent(
        t("mail.privacy.where"),
    );
});

it("packs the files under neutral names, posts them and shows counts and table", async () => {
    await imported();
    expect(api.uploadMail).toHaveBeenCalledTimes(1);
    const sent = vi.mocked(api.uploadMail).mock.calls[0][0];
    const text = new TextDecoder("latin1").decode(await readBytes(sent));
    expect(text).toContain("0001.eml");
    expect(text).toContain("0002.eml");
    expect(text).not.toContain("private");
    const counts = screen.getByTestId("mail-counts");
    expect(counts.querySelector("[data-count=read]")).toHaveTextContent(
        t("mail.counts.read", { count: 5 }),
    );
    expect(counts.querySelector("[data-count=skipped]")).toHaveTextContent(
        t("mail.counts.skipped", { count: 1 }),
    );
    expect(counts.querySelector("[data-count=truncated]")).toHaveTextContent(
        t("mail.counts.truncated", { count: 2 }),
    );
    expect(api.preview).toHaveBeenCalled();
});

it("shows a refusal of the server by its own text and an unsuitable file by the server's code", async () => {
    vi.mocked(api.uploadMail).mockRejectedValue(
        new WorkspaceApiError("mail_too_many", "x", 422),
    );
    render(<App opening="dataset" />);
    await userEvent.upload(
        await screen.findByLabelText(t("mail.input")),
        files(),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
        t("error.mail_too_many"),
    );
});

it("shows the editor for a mail table, with no planner, ideas or web switch", async () => {
    await imported();
    expect(await screen.findByTestId("extraction-form")).toBeVisible();
    expect(screen.getByTestId("extraction-trust")).toBeVisible();
    expect(screen.getByTestId("extraction-notice")).toBeVisible();
    expect(screen.queryByLabelText("Enrichment instruction")).toBeNull();
    expect(screen.queryByLabelText(t("ws.fillColumn"))).toBeNull();
    expect(screen.queryByText(t("ws.suggestTasks"))).toBeNull();
    expect(screen.queryByTestId("fallback-switch")).toBeNull();
    expect(screen.queryByTestId("fallback-planner")).toBeNull();
});

it("keeps the build button off until the form is valid, with the reason", async () => {
    await imported();
    expect(button("ws.buildPlan")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(t("extract.question")), {
        target: { value: "q" },
    });
    fireEvent.change(screen.getByLabelText(t("extract.name", { n: 1 })), {
        target: { value: "text" },
    });
    expect(
        screen.getByText(t("extract.problem.existing", { name: "text" })),
    ).toBeVisible();
    expect(button("ws.buildPlan")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(t("extract.name", { n: 1 })), {
        target: { value: "Kind" },
    });
    await userEvent.selectOptions(
        screen.getByLabelText(t("extract.kind", { n: 1 })),
        "one_of",
    );
    expect(button("ws.buildPlan")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(t("extract.options", { n: 1 })), {
        target: { value: "payment, renewal" },
    });
    expect(button("ws.buildPlan")).toBeEnabled();
    await userEvent.click(button("extract.add"));
    expect(screen.getAllByTestId("extraction-column")).toHaveLength(2);
    await userEvent.click(
        screen.getByRole("button", { name: t("extract.remove", { n: 2 }) }),
    );
    expect(screen.getAllByTestId("extraction-column")).toHaveLength(1);
});

it("builds the plan with the request body and shows it in plain words", async () => {
    await toPlan();
    expect(api.createExtractionPlan).toHaveBeenCalledWith({
        dataset_id: "mail-table",
        question: "Payments: what was paid?",
        text_column: "Text",
        targets: [{ column: "Amount", kind: "number" }],
        sheet_name: "Mail",
        range_ref: "A1:D6",
        sources_in_workbook: "notes",
    });
    expect(api.createPlan).not.toHaveBeenCalled();
    const view = screen.getByTestId("extraction-plan");
    expect(view).toHaveTextContent(t("extract.plan.title"));
    expect(screen.getByTestId("extraction-banner")).toHaveTextContent(
        t("extract.banner"),
    );
    expect(screen.queryByText(t("ws.editPlan"))).toBeNull();
});

it("shows the server's message when the plan is refused, next to the form", async () => {
    vi.mocked(api.createExtractionPlan).mockRejectedValue(
        new WorkspaceApiError(
            "extraction_plan_invalid",
            "Column names must be unique.",
            422,
        ),
    );
    await imported();
    await fillEditor();
    await userEvent.click(button("ws.buildPlan"));
    expect(await screen.findByTestId("extraction-refusal")).toHaveTextContent(
        "Column names must be unique.",
    );
    expect(screen.getByTestId("extraction-form")).toBeVisible();
});

it("takes a question typed over several lines as one line", async () => {
    await imported();
    fireEvent.change(screen.getByLabelText(t("extract.question")), {
        target: { value: "What was paid,\nand when?" },
    });
    expect(screen.getByLabelText(t("extract.question"))).toHaveValue(
        "What was paid, and when?",
    );
});

it("keeps what was typed when going to another step and back", async () => {
    await imported();
    await fillEditor();
    await nav("overview");
    await nav("dataset");
    expect(screen.getByLabelText(t("extract.question"))).toHaveValue(
        "Payments: what was paid?",
    );
    expect(screen.getByLabelText(t("extract.name", { n: 1 }))).toHaveValue(
        "Amount",
    );
});

it("says what is missing when the table is there but extraction is not", async () => {
    vi.mocked(api.limits).mockResolvedValue(limits(true, false));
    vi.mocked(api.providerSettings).mockResolvedValue({
        ...keyPresent,
        key_present: false,
        live_ready: false,
    });
    await imported();
    const sentence = await screen.findByTestId("extraction-unavailable");
    await waitFor(() => expect(sentence).toHaveAttribute("data-reason", "key"));
    expect(sentence).toHaveTextContent(t("extract.unavailable.key"));
    expect(screen.queryByTestId("extraction-form")).toBeNull();
    cleanup();
    vi.mocked(api.providerSettings).mockResolvedValue({
        ...keyPresent,
        server_network_allowed: false,
        live_ready: false,
    });
    await imported();
    const other = await screen.findByTestId("extraction-unavailable");
    await waitFor(() =>
        expect(other).toHaveAttribute("data-reason", "network"),
    );
});

it("runs without a time limit and shows counts with the mail that is not about the task", async () => {
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.startRun).toHaveBeenCalled());
    expect(api.startRun).toHaveBeenCalledWith(
        "plan-extraction",
        true,
        undefined,
    );
    expect(await screen.findByTestId("extraction-banner")).toBeVisible();
    expect(screen.getByTestId("extraction-notes")).toHaveTextContent(
        t("extract.runNotes"),
    );
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    expect(
        await screen.findByText(t("exec.outcome.notApplicable", { count: 1 })),
    ).toBeVisible();
});

it("shows marks, quotes, the mail that is not about the task and the note on the download", async () => {
    await toPlan();
    await userEvent.click(button("ws.approveRun"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    await nav("export");
    const summary = await screen.findByTestId("extraction-summary");
    expect(summary).toHaveTextContent(t("extract.bannerDone"));
    expect(summary).toHaveTextContent(
        t("results.extract.summary", { asked: 5, found: 2, notApplicable: 1 }),
    );
    const marks = screen.getAllByTestId("basis-mark");
    expect(marks.map((m) => m.textContent)).toContain(
        t("extract.basis.literal"),
    );
    expect(marks.map((m) => m.textContent)).toContain(
        t("extract.basis.derived"),
    );
    expect(screen.getAllByTestId("result-quote")[0]).toHaveTextContent(
        "Total: 9,99 EUR",
    );
    const na = document.querySelector(
        "[data-not-applicable=true]",
    ) as HTMLElement;
    expect(na).not.toBeNull();
    expect(within(na).getByTestId("result-not-applicable")).toHaveTextContent(
        t("results.extract.notApplicable"),
    );
    expect(screen.getByTestId("extraction-download-note")).toHaveTextContent(
        t("extract.download"),
    );
});

it("counts the mail that is not about the task as its own number, not a failure", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    await imported();
    await fillEditor();
    await userEvent.click(screen.getByLabelText(t("workflow.network")));
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("extraction-plan");
    await userEvent.click(button("ws.approveContinue"));
    await nav("execution");
    await userEvent.click(button("workflow.run"));
    await waitFor(() => expect(api.getReport).toHaveBeenCalled());
    // The job counts three rows as not successful; one of them is not about the task.
    expect(
        await screen.findByTestId("metric-not_applicable"),
    ).toHaveTextContent("1");
    expect(screen.getByTestId("metric-failed_rows")).toHaveTextContent("2");
});
