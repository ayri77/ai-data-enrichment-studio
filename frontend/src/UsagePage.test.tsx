import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import "./i18n";
import i18n from "i18next";
import { UsagePage } from "./components/UsagePage";
import { api, WorkspaceApiError, type UsageSummary } from "./api";

vi.mock("./api", async (original) => {
    const actual = await original<typeof import("./api")>();
    return { ...actual, api: { usage: vi.fn() } };
});

const summary: UsageSummary = {
    started_at: "2026-11-14T09:00:00+00:00",
    process_started_at: "2026-11-14T09:00:00+00:00",
    now: "2026-11-14T10:00:00+00:00",
    since: "2026-11-10T08:30:00+00:00",
    persistent: true,
    skipped_lines: 0,
    events: { plan: 2, run_finished: 1, visit: 3 },
    labels: ["guest01", "guest02", "guest03"],
    visitors: [
        {
            who: "guest02",
            first_seen: "2026-11-14T09:10:00+00:00",
            last_seen: "2026-11-14T09:40:00+00:00",
            visits: 2,
            uploads: 1,
            examples: 1,
            plans: 2,
            plans_failed: 1,
            runs: 1,
            rows: 10,
            rows_filled: 8,
            downloads: 1,
            model_requests: 3,
            input_tokens: 4200,
            output_tokens: 900,
            source_requests: { "open-meteo": 11, openrouteservice: 8 },
        },
        {
            who: "guest01",
            first_seen: "2026-11-14T09:05:00+00:00",
            last_seen: "2026-11-14T09:05:00+00:00",
            visits: 1,
            uploads: 0,
            examples: 0,
            plans: 0,
            plans_failed: 0,
            runs: 0,
            rows: 0,
            rows_filled: 0,
            downloads: 0,
            model_requests: 0,
            input_tokens: 0,
            output_tokens: 0,
            source_requests: {},
        },
    ],
    recent: [
        {
            at: "2026-11-14T09:40:00+00:00",
            who: "guest02",
            kind: "run_finished",
            status: "needs_review",
            rows: 10,
            source_requests: { "open-meteo": 11 },
        },
        { at: "2026-11-14T09:05:00+00:00", who: "guest01", kind: "visit" },
    ],
};

beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
});

// A server that wants the admin code: the first, code-less request is refused.
function needsTheCode(opened: UsageSummary = summary) {
    vi.mocked(api.usage).mockImplementation(async (code: string) => {
        if (!code) throw new WorkspaceApiError("admin_required", "x");
        return opened;
    });
}

it("opens with the admin code and shows what each visitor used", async () => {
    needsTheCode();
    render(<UsagePage />);
    const show = await screen.findByRole("button", { name: "Show" });
    expect(show).toBeDisabled();
    const field = screen.getByLabelText("Admin code");
    // The code is typed like a password and is not kept anywhere.
    expect(field).toHaveAttribute("type", "password");
    await userEvent.type(field, " admin-code-for-tests ");
    await userEvent.click(show);
    expect(api.usage).toHaveBeenNthCalledWith(1, "");
    expect(api.usage).toHaveBeenCalledWith("admin-code-for-tests");
    const visitors = within(await screen.findByTestId("usage-visitors"));
    const cells = (label: string) =>
        within(visitors.getByRole("rowheader", { name: label }).closest("tr")!)
            .getAllByRole("cell")
            .map((cell) => cell.textContent);
    expect(cells("guest02").slice(1)).toEqual([
        "2",
        "1",
        "1",
        "2 (1 failed)",
        "1",
        "8 / 10",
        "1",
        "3",
        "4,200 / 900",
        "open-meteo 11 · openrouteservice 8",
    ]);
    expect(cells("guest01").at(-1)).toBe("—");
    // A code nobody has used yet is listed too.
    expect(cells("guest03")).toEqual(["has not come yet"]);
    expect(screen.getByTestId("usage-kinds")).toHaveTextContent(
        "plan 2 · run_finished 1 · visit 3",
    );
    const recent = within(screen.getByTestId("usage-recent"));
    expect(recent.getAllByRole("row")[1]).toHaveTextContent(
        'guest02run_finishedstatus=needs_review · rows=10 · source_requests={"open-meteo":11}',
    );
    expect(
        JSON.stringify([{ ...sessionStorage }, { ...localStorage }]),
    ).not.toContain("admin-code");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.usage).toHaveBeenCalledTimes(3);
});

it.each([
    ["admin_required", "That admin code is not correct."],
    ["access_throttled", "Too many attempts. Wait a minute and try again."],
    ["usage_disabled", "The usage summary is switched off on this server"],
    ["something_else", "The server cannot be reached."],
])("says why the summary did not open: %s", async (code, text) => {
    vi.mocked(api.usage).mockImplementation(async (offered: string) => {
        throw new WorkspaceApiError(offered ? code : "admin_required", "x");
    });
    render(<UsagePage />);
    // The first request, without a code, only decides whether to ask for one.
    await userEvent.type(
        await screen.findByLabelText("Admin code"),
        "some-code",
    );
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.queryByTestId("usage-visitors")).toBeNull();
});

it("opens at once, without asking for a code, when the server needs none", async () => {
    vi.mocked(api.usage).mockResolvedValue(summary);
    render(<UsagePage />);
    expect(await screen.findByTestId("usage-visitors")).toBeVisible();
    expect(api.usage).toHaveBeenCalledTimes(1);
    expect(api.usage).toHaveBeenCalledWith("");
    expect(screen.queryByLabelText("Admin code")).toBeNull();
    // Refresh works without a code too.
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.usage).toHaveBeenCalledTimes(2);
    expect(api.usage).toHaveBeenLastCalledWith("");
});

it("asks for the code, and says nothing wrong yet, when the server requires it", async () => {
    needsTheCode();
    render(<UsagePage />);
    expect(await screen.findByLabelText("Admin code")).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("usage-visitors")).toBeNull();
});

it("says since when the numbers are kept across restarts", async () => {
    vi.mocked(api.usage).mockResolvedValue(summary);
    render(<UsagePage />);
    const line = await screen.findByTestId("usage-since");
    expect(line).toHaveTextContent("Kept across restarts of the server since");
    // The first counted event, not the start of this process.
    expect(line).toHaveTextContent(
        new Date(summary.since!).toLocaleString("en", {
            dateStyle: "short",
            timeStyle: "medium",
        }),
    );
    expect(line).not.toHaveTextContent("cleared by a restart");
});

it.each([
    ["off", "switched off (ENRICHMENT_USAGE_FILE)"],
    ["write_failed", "could not be written"],
] as const)(
    "says that a restart clears the numbers, and why: %s",
    async (reason, text) => {
        vi.mocked(api.usage).mockResolvedValue({
            ...summary,
            persistent: false,
            reason,
        });
        render(<UsagePage />);
        const line = await screen.findByTestId("usage-since");
        expect(line).toHaveTextContent("cleared by a restart");
        expect(line).not.toHaveTextContent("Kept across restarts");
        expect(screen.getByText(text, { exact: false })).toBeVisible();
    },
);

it("shows skipped lines of the usage file only when there are some", async () => {
    vi.mocked(api.usage).mockResolvedValue(summary);
    const { unmount } = render(<UsagePage />);
    await screen.findByTestId("usage-since");
    expect(screen.queryByTestId("usage-skipped")).toBeNull();
    unmount();
    vi.mocked(api.usage).mockResolvedValue({ ...summary, skipped_lines: 3 });
    render(<UsagePage />);
    expect(await screen.findByTestId("usage-skipped")).toHaveTextContent(
        "skipped: 3",
    );
});
