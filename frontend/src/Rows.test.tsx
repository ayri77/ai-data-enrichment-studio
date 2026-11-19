import {
    cleanup,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { api, WorkspaceApiError, type RowsPreview } from "./api";
import i18n from "./i18n";
import {
    answer,
    candidates,
    count,
    grownPreview,
    mailSource,
    register,
    registerPreview,
    run,
} from "./rowsFixtures";

vi.mock("./api", async (original) => ({
    ...(await original<typeof import("./api")>()),
    api: {
        limits: vi.fn(),
        capabilities: vi.fn(),
        access: vi.fn(),
        samples: vi.fn(),
        providerSettings: vi.fn(),
        upload: vi.fn(),
        preview: vi.fn(),
        remove: vi.fn(),
        rowSources: vi.fn(),
        readCandidates: vi.fn(),
        forgetCandidates: vi.fn(),
        previewRows: vi.fn(),
        runRows: vi.fn(),
        workbook: vi.fn(),
    },
}));

const t = (key: string, values?: Record<string, unknown>) =>
    i18n.t(key, { ...values }) as string;
const button = (key: string) => screen.getByRole("button", { name: t(key) });
const check = (key: string) => screen.getByLabelText(t(key));
const eml = () => [new File(["Subject: a\r\n\r\none"], "private-one.eml")];

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => (resolve = done));
    return { promise, resolve };
}

beforeEach(async () => {
    cleanup();
    vi.resetAllMocks();
    localStorage.clear();
    await i18n.changeLanguage("en");
    vi.mocked(api.limits).mockResolvedValue({
        max_run_rows: 20,
        max_timeout_seconds: 300,
        default_timeout_seconds: 180,
    });
    vi.mocked(api.upload).mockResolvedValue(register);
    vi.mocked(api.preview).mockResolvedValue(registerPreview);
    vi.mocked(api.rowSources).mockResolvedValue({ sources: [mailSource] });
    vi.mocked(api.readCandidates).mockResolvedValue(candidates);
    vi.mocked(api.previewRows).mockResolvedValue(answer());
    vi.mocked(api.runRows).mockResolvedValue(run());
});

async function uploadRegister() {
    await userEvent.upload(
        await screen.findByLabelText(t("uploadWorkbook")),
        new File(["x"], "register.xlsx"),
    );
    await screen.findByTestId("row-count");
}
async function open() {
    render(<App opening="dataset" />);
    await uploadRegister();
    await screen.findByRole("heading", { name: t("rows.rowsHeading") });
}
async function rowsOnly() {
    await open();
    await userEvent.click(check("rows.addRows"));
    await userEvent.click(check("rows.fillRows"));
}
async function chooseFiles() {
    await userEvent.upload(screen.getByLabelText(t("rows.input")), eml());
    await waitFor(() =>
        expect(screen.getByTestId("rows-read")).toHaveTextContent(
            t("rows.files.read", { count: 10, skipped: 0 }),
        ),
    );
}
async function toPlan(first: RowsPreview = answer()) {
    vi.mocked(api.previewRows).mockResolvedValueOnce(first);
    await rowsOnly();
    await chooseFiles();
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("rows-plan-rows");
}
async function toResults(ran = run()) {
    vi.mocked(api.runRows).mockResolvedValue(ran);
    vi.mocked(api.previewRows).mockResolvedValue(
        answer({
            count: count({ added: 0, written: 0, duplicates_in_table: 10 }),
        }),
    );
    vi.mocked(api.preview).mockImplementation(async (id) =>
        id === "grown" ? grownPreview : registerPreview,
    );
    await userEvent.click(button("ws.approveRun"));
    await screen.findByTestId("rows-tiles");
}

it("leaves the panel as it was where no source of rows is available", async () => {
    for (const sources of [
        { sources: [{ ...mailSource, available: false }] },
        { sources: [] },
        undefined,
    ]) {
        vi.mocked(api.rowSources).mockResolvedValue(
            sources as Awaited<ReturnType<typeof api.rowSources>>,
        );
        const { unmount } = render(<App opening="dataset" />);
        await uploadRegister();
        await waitFor(() => expect(api.rowSources).toHaveBeenCalled());
        expect(screen.getByText(t("workflow.instructionHint"))).toBeVisible();
        expect(screen.getByTestId("run-rows")).toBeVisible();
        expect(screen.queryByText(t("rows.rowsHeading"))).toBeNull();
        expect(screen.queryByText(t("rows.columnsHeading"))).toBeNull();
        expect(screen.queryByLabelText(t("rows.addRows"))).toBeNull();
        expect(screen.queryByText(t("rows.noColumns"))).toBeNull();
        unmount();
        vi.mocked(api.rowSources).mockClear();
    }
    // The list is asked for once, and nothing else is.
    expect(api.readCandidates).not.toHaveBeenCalled();
});

it("offers two parts, the first on and the second off, with the number of rows under the first", async () => {
    await open();
    expect(api.rowSources).toHaveBeenCalledTimes(1);
    expect(
        screen.getByRole("heading", { name: t("rows.columnsHeading") }),
    ).toBeVisible();
    expect(check("rows.fillRows")).toBeChecked();
    expect(check("rows.addRows")).not.toBeChecked();
    expect(screen.queryByTestId("rows-source")).toBeNull();
    const fill = check("rows.fillRows");
    const numberOfRows = screen.getByTestId("run-rows");
    expect(
        fill.compareDocumentPosition(numberOfRows) &
            Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The columns part shows what it always showed.
    expect(screen.getByLabelText(t("workflow.instruction"))).toBeVisible();

    await userEvent.click(check("rows.addRows"));
    const source = screen.getByTestId("rows-source");
    expect(within(source).getByLabelText(t("rows.have"))).toBeChecked();
    expect(
        within(source).getByLabelText(new RegExp(t("rows.find"))),
    ).toBeDisabled();
    expect(within(source).getByText(t("rows.later"))).toBeVisible();
    expect(within(source).getByLabelText(t("rows.source"))).toHaveDisplayValue(
        t("rows.source.mail_files"),
    );
    expect(screen.getByLabelText(t("rows.input"))).toHaveAttribute(
        "accept",
        ".eml,.zip",
    );
    expect(screen.getByTestId("rows-fields")).toHaveTextContent(
        t("rows.field.sender"),
    );
    expect(screen.getByTestId("rows-privacy")).toHaveTextContent(
        t("rows.privacy"),
    );

    await userEvent.click(check("rows.fillRows"));
    expect(screen.queryByTestId("run-rows")).toBeNull();
    expect(screen.getByTestId("rows-no-columns")).toHaveTextContent(
        t("rows.noColumns"),
    );
    expect(screen.getByText(t("rows.instructionHint"))).toBeVisible();
    expect(screen.queryByLabelText(t("ws.targetColumn"))).toBeNull();
});

it("will not build a plan with both parts on, and says what to do instead", async () => {
    await open();
    await userEvent.click(check("rows.addRows"));
    await userEvent.upload(screen.getByLabelText(t("rows.input")), eml());
    await screen.findByText(t("rows.files.read", { count: 10, skipped: 0 }));
    expect(screen.getByTestId("rows-both")).toHaveTextContent(
        t("rows.both.notice"),
    );
    expect(button("ws.buildPlan")).toBeDisabled();
    await userEvent.click(check("rows.fillRows"));
    expect(screen.queryByTestId("rows-both")).toBeNull();
    expect(button("ws.buildPlan")).toBeEnabled();
});

it("asks for the files before it builds, packs them without their names and forgets a set that is replaced", async () => {
    await rowsOnly();
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(screen.getByText(t("rows.need.files"))).toBeVisible();
    await chooseFiles();
    expect(api.readCandidates).toHaveBeenCalledTimes(1);
    const [id, body] = vi.mocked(api.readCandidates).mock.calls[0];
    expect(id).toBe("mail_files");
    expect(body).toBeInstanceOf(Blob);
    expect(button("ws.buildPlan")).toBeEnabled();
    vi.mocked(api.readCandidates).mockResolvedValue({
        ...candidates,
        candidates_id: "set-2",
        count: 4,
        skipped: 1,
    });
    await userEvent.upload(screen.getByLabelText(t("rows.input")), eml());
    await waitFor(() =>
        expect(screen.getByTestId("rows-read")).toHaveTextContent(
            t("rows.files.read", { count: 4, skipped: 1 }),
        ),
    );
    expect(api.forgetCandidates).toHaveBeenCalledWith("set-1");
});

it("shows an error of the files by the text of its code", async () => {
    vi.mocked(api.readCandidates).mockRejectedValue(
        new WorkspaceApiError("mail_upload_too_large", "x", 413),
    );
    await rowsOnly();
    await userEvent.upload(screen.getByLabelText(t("rows.input")), eml());
    expect(await screen.findByRole("alert")).toHaveTextContent(
        t("error.mail_upload_too_large"),
    );
    expect(button("ws.buildPlan")).toBeDisabled();
});

it("builds the plan with nothing mapped, and the conditions the person set", async () => {
    await rowsOnly();
    await chooseFiles();
    await userEvent.click(button("rows.addCondition"));
    await userEvent.selectOptions(
        screen.getByLabelText(t("rows.condOp", { n: 1 })),
        "on_or_after",
    );
    const value = screen.getByLabelText(t("rows.condValue", { n: 1 }));
    expect(value).toHaveAttribute("type", "date");
    // A condition that is not complete stops the build.
    expect(button("ws.buildPlan")).toBeDisabled();
    expect(screen.getByText(t("rows.need.conditions"))).toBeVisible();
    await userEvent.type(value, "2026-11-01");
    await userEvent.click(button("ws.buildPlan"));
    await screen.findByTestId("rows-plan-rows");
    expect(api.previewRows).toHaveBeenCalledWith("register", {
        candidates_id: "set-1",
        sheet_name: "Register",
        range_ref: "A1:D4",
        mapping: null,
        key: null,
        on_duplicate: "skip",
        conditions: [
            { field_id: "received", op: "on_or_after", value: "2026-11-01" },
        ],
    });
});

it("shows the plan: the source, the proposals, the key and the counts, and no column is filled", async () => {
    await toPlan();
    const card = screen.getByTestId("rows-plan-rows");
    expect(card).toHaveTextContent(t("rows.plan.rowsHeading"));
    expect(card).toHaveTextContent(t("rows.plan.noAi"));
    expect(card).toHaveTextContent(
        `${t("rows.source.mail_files")} · ${t("rows.plan.sourceRead", { count: 10 })}`,
    );
    expect(card).toHaveTextContent(t("rows.everything"));
    expect(
        screen.getByLabelText(t("rows.plan.columnFor", { field: "Received" })),
    ).toHaveValue("A");
    expect(screen.getAllByTestId("proposed")).toHaveLength(4);
    expect(
        screen.getByLabelText("Received", { selector: "input" }),
    ).toBeChecked();
    expect(
        screen.getByLabelText("Subject", { selector: "input" }),
    ).not.toBeChecked();
    expect(screen.getByTestId("rows-counts")).toHaveTextContent(
        t("rows.plan.counts", { in_source: 10, in_table: 3, added: 7 }),
    );
    expect(
        screen.getByTestId("rows-counts").querySelectorAll("p"),
    ).toHaveLength(1);
    expect(screen.getByTestId("rows-plan-columns")).toHaveTextContent(
        t("rows.noColumns"),
    );
    expect(screen.getByTestId("rows-plan-check")).toHaveTextContent(
        t("rows.plan.check.second"),
    );
    expect(screen.queryByTestId("rows-no-match")).toBeNull();
    expect(screen.getByTestId("validation")).toHaveTextContent(
        t("rows.plan.passed"),
    );
    expect(button("ws.approveRun")).toBeEnabled();
    // Execution and Review are not steps of this task.
    const nav = screen.getByRole("navigation").querySelectorAll("button");
    expect(nav[3]).toBeDisabled();
    expect(nav[4]).toBeDisabled();
});

it("adds the other counts only when they are not zero", async () => {
    await toPlan(
        answer({
            count: count({
                in_source: 14,
                left_out: 1,
                without_key: 2,
                duplicates_in_source: 1,
            }),
        }),
    );
    const counts = screen.getByTestId("rows-counts");
    expect(counts.querySelector("[data-count=left_out]")).toHaveTextContent(
        t("rows.plan.leftOut", { count: 1 }),
    );
    expect(counts.querySelector("[data-count=without_key]")).toHaveTextContent(
        t("rows.plan.withoutKey", { count: 2 }),
    );
    expect(counts.querySelector("[data-count=twice]")).toHaveTextContent(
        t("rows.plan.twice", { count: 1 }),
    );
    expect(screen.getByTestId("rows-plan-check")).toHaveTextContent(
        t("rows.plan.check.sumMore"),
    );
});

it("asks the preview again with the explicit plan when something is changed, and ignores an answer an edit has overtaken", async () => {
    await toPlan();
    const slow = deferred<RowsPreview>();
    vi.mocked(api.previewRows).mockReset();
    vi.mocked(api.previewRows)
        .mockReturnValueOnce(slow.promise)
        .mockResolvedValueOnce(
            answer({
                proposed: { mapping: false, key: false },
                plan: answer().plan,
                count: count({ duplicates_in_table: 5, added: 5, written: 5 }),
            }),
        );
    await userEvent.selectOptions(
        screen.getByLabelText(t("rows.plan.columnFor", { field: "Subject" })),
        "",
    );
    expect(api.previewRows).toHaveBeenLastCalledWith("register", {
        candidates_id: "set-1",
        sheet_name: "Register",
        range_ref: "A1:D4",
        mapping: { received: "A", sender: "B" },
        key: ["received", "sender"],
        on_duplicate: "skip",
        conditions: [],
    });
    // While it is asked: a busy state, and the run waits.
    expect(screen.getByText(t("rows.plan.checking"))).toBeVisible();
    expect(screen.getByTestId("rows-counts")).toHaveAttribute(
        "aria-busy",
        "true",
    );
    expect(button("ws.approveRun")).toBeDisabled();
    // The mark of the field that was changed goes; the others stay.
    expect(screen.getAllByTestId("proposed")).toHaveLength(3);
    // A second change overtakes the first.
    await userEvent.selectOptions(
        screen.getByLabelText(t("rows.plan.onDuplicate")),
        "add_marked",
    );
    expect(api.previewRows).toHaveBeenLastCalledWith(
        "register",
        expect.objectContaining({
            on_duplicate: "add_marked",
            mapping: { received: "A", sender: "B" },
        }),
    );
    await waitFor(() =>
        expect(screen.getByTestId("rows-counts")).toHaveTextContent(
            t("rows.plan.counts", { in_source: 10, in_table: 5, added: 5 }),
        ),
    );
    // The first answer comes last and is not shown.
    slow.resolve(
        answer({
            count: count({ duplicates_in_table: 1, added: 9, written: 9 }),
        }),
    );
    await new Promise((done) => setTimeout(done, 20));
    expect(screen.getByTestId("rows-counts")).toHaveTextContent(
        t("rows.plan.counts", { in_source: 10, in_table: 5, added: 5 }),
    );
    expect(button("ws.approveRun")).toBeEnabled();
});

it("shows a refused plan as an alert in its card with the numbers, and the run is off", async () => {
    await toPlan(
        answer({
            count: null,
            refused: { code: "rows_key_not_comparable", count: 3, row: 2 },
        }),
    );
    const alert = within(screen.getByTestId("rows-plan-rows")).getByRole(
        "alert",
    );
    expect(alert).toHaveTextContent(t("error.rows_key_not_comparable"));
    expect(alert).toHaveTextContent(
        t("rows.plan.refusedBoth", { count: 3, row: 2 }),
    );
    expect(button("ws.approveRun")).toBeDisabled();
    expect(screen.getByTestId("rows-cannot-run")).toHaveTextContent(
        t("rows.plan.cantRun.refused"),
    );
    expect(screen.getByTestId("validation")).toHaveTextContent(
        t("rows.plan.refused"),
    );
    expect(screen.queryByTestId("rows-counts")?.querySelector("p")).toBeNull();
});

it("says which reason keeps the run off when nothing would be written", async () => {
    await toPlan(
        answer({
            count: count({ added: 0, written: 0, duplicates_in_table: 10 }),
        }),
    );
    expect(button("ws.approveRun")).toBeDisabled();
    expect(screen.getByTestId("rows-cannot-run")).toHaveTextContent(
        t("rows.plan.cantRun.nothing"),
    );
});

it("notes, without calling it an error, that no row of the table matches", async () => {
    await toPlan(
        answer({
            count: count({ duplicates_in_table: 0, added: 10, written: 10 }),
        }),
    );
    const notice = screen.getByTestId("rows-no-match");
    expect(notice).toHaveTextContent(t("rows.plan.noMatch"));
    expect(notice).not.toHaveAttribute("role", "alert");
    expect(button("ws.approveRun")).toBeEnabled();
});

it("runs exactly the plan shown and shows three tiles, the check, the second run and the end of the table", async () => {
    await toPlan();
    await toResults();
    expect(api.runRows).toHaveBeenCalledWith("register", {
        candidates_id: "set-1",
        sheet_name: "Register",
        range_ref: "A1:D4",
        mapping: { received: "A", sender: "B", subject: "C" },
        key: ["received", "sender"],
        on_duplicate: "skip",
        conditions: [],
    });
    // The new table is asked about with the same plan.
    expect(api.previewRows).toHaveBeenLastCalledWith(
        "grown",
        expect.objectContaining({
            range_ref: "A1:D11",
            sheet_name: "Register",
        }),
    );
    const tiles = screen.getByTestId("rows-tiles");
    expect(
        [...tiles.querySelectorAll("li")].map((li) => li.dataset.tile),
    ).toEqual(["in_source", "added", "duplicates"]);
    expect(tiles.querySelector("[data-tile=added] strong")).toHaveTextContent(
        "7",
    );
    expect(screen.getByTestId("rows-check")).toHaveTextContent(
        t("rows.results.holds"),
    );
    expect(screen.getByTestId("rows-sum")).toHaveTextContent(
        "7 added + 3 duplicates = 10 in the source",
    );
    expect(screen.getByTestId("rows-second")).toHaveTextContent(
        t("rows.results.secondNone"),
    );
    // The three rows the table had were compared with the new table.
    expect(screen.getByTestId("rows-kept")).toHaveTextContent(
        t("rows.results.untouched", { rows: 3 }),
    );
    const end = screen.getAllByTestId("rows-end-row");
    expect(end).toHaveLength(8);
    expect(end.filter((row) => row.dataset.added === "true")).toHaveLength(7);
    expect(button("rows.results.download")).toBeEnabled();
    expect(button("rows.results.continue")).toBeEnabled();
});

it("shows more tiles when the counts say so and writes the check out from them", async () => {
    await toPlan();
    await toResults(
        run({
            count: count({
                in_source: 14,
                left_out: 1,
                without_key: 2,
                duplicates_in_source: 1,
            }),
        }),
    );
    const tiles = screen.getByTestId("rows-tiles");
    expect(
        [...tiles.querySelectorAll("li")].map((li) => li.dataset.tile),
    ).toEqual([
        "in_source",
        "added",
        "duplicates",
        "left_out",
        "without_key",
        "twice",
    ]);
    expect(screen.getByTestId("rows-sum")).toHaveTextContent(
        "7 added + 3 duplicates + 1 left out by the conditions + 2 without a key + 1 twice in the source = 14 in the source",
    );
});

it("calls the check broken, as an error, when it does not hold", async () => {
    await toPlan();
    await toResults(run({ count: count({ holds: false }) }));
    const banner = screen.getByTestId("rows-check");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent(t("rows.results.fails"));
    expect(screen.queryByTestId("rows-sum")).toBeNull();
});

it("says that duplicates are added again by design when they are added and marked", async () => {
    await toPlan();
    vi.mocked(api.previewRows).mockResolvedValueOnce(
        answer({
            plan: answer().plan,
            count: count({ written: 10 }),
        }),
    );
    await userEvent.selectOptions(
        screen.getByLabelText(t("rows.plan.onDuplicate")),
        "add_marked",
    );
    await waitFor(() =>
        expect(
            screen
                .getByTestId("rows-counts")
                .querySelector("[data-count=marked]"),
        ).not.toBeNull(),
    );
    vi.mocked(api.runRows).mockResolvedValue(
        run({ count: count({ written: 10 }) }),
    );
    vi.mocked(api.previewRows).mockResolvedValue(
        answer({
            count: count({ added: 0, written: 10, duplicates_in_table: 10 }),
        }),
    );
    vi.mocked(api.preview).mockResolvedValue(grownPreview);
    await userEvent.click(button("ws.approveRun"));
    await screen.findByTestId("rows-tiles");
    expect(
        screen
            .getByTestId("rows-tiles")
            .querySelector("[data-tile=duplicates]"),
    ).toHaveTextContent(t("rows.tile.duplicatesMarked"));
    expect(screen.getByTestId("rows-second")).toHaveTextContent(
        t("rows.results.secondMarked", { count: 10 }),
    );
    expect(screen.getByTestId("rows-written")).toBeVisible();
});

it("saves the workbook of the new table under the known name", async () => {
    await toPlan();
    await toResults();
    vi.mocked(api.workbook).mockResolvedValue(new Blob(["x"]));
    const create = vi.fn(() => "blob:rows");
    URL.createObjectURL = create;
    URL.revokeObjectURL = vi.fn();
    const clicked: string[] = [];
    const click = vi
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(function (this: HTMLAnchorElement) {
            clicked.push(this.download);
        });
    await userEvent.click(button("rows.results.download"));
    await waitFor(() => expect(clicked).toEqual(["register-rows-added.xlsx"]));
    expect(api.workbook).toHaveBeenCalledWith("grown");
    click.mockRestore();
});

it("goes on with the new table on the first step with a fresh task", async () => {
    await toPlan();
    await toResults();
    vi.mocked(api.preview).mockResolvedValue(grownPreview);
    await userEvent.click(button("rows.results.continue"));
    expect(await screen.findByText("register-rows-added.xlsx")).toBeVisible();
    await waitFor(() =>
        expect(api.preview).toHaveBeenLastCalledWith(
            "grown",
            "Register",
            "A1:D11",
        ),
    );
    expect(await screen.findByTestId("row-count")).toHaveTextContent("10");
    expect(check("rows.fillRows")).toBeChecked();
    expect(check("rows.addRows")).not.toBeChecked();
    expect(screen.queryByTestId("rows-source")).toBeNull();
    expect(api.remove).toHaveBeenCalledWith("register");
    expect(api.forgetCandidates).toHaveBeenCalledWith("set-1");
    expect(screen.getByRole("heading", { name: t("dataset") })).toBeVisible();
});

it("starts the rows task again, and forgets the files, when the workbook is replaced", async () => {
    await rowsOnly();
    await chooseFiles();
    expect(button("ws.buildPlan")).toBeEnabled();
    vi.mocked(api.upload).mockResolvedValue({
        ...register,
        dataset_id: "another",
        filename: "another.xlsx",
    });
    vi.mocked(api.preview).mockResolvedValue({
        ...registerPreview,
        dataset_id: "another",
    });
    await userEvent.upload(
        screen.getByLabelText(t("uploadWorkbook")),
        new File(["x"], "another.xlsx"),
    );
    await screen.findByText("another.xlsx");
    await waitFor(() =>
        expect(api.forgetCandidates).toHaveBeenCalledWith("set-1"),
    );
    expect(check("rows.fillRows")).toBeChecked();
    expect(check("rows.addRows")).not.toBeChecked();
    expect(screen.queryByTestId("rows-read")).toBeNull();
});

it("goes back to the first step from the plan and keeps what was chosen", async () => {
    await toPlan();
    await userEvent.click(
        within(
            document.querySelector(".stage-sidebar") as HTMLElement,
        ).getByRole("button", { name: t("dataset") }),
    );
    expect(check("rows.addRows")).toBeChecked();
    expect(screen.getByTestId("rows-read")).toHaveTextContent(
        t("rows.files.read", { count: 10, skipped: 0 }),
    );
});
