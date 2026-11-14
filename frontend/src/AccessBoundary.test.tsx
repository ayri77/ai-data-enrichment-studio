import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import "./i18n";
import i18n from "i18next";
import de from "./locales/de.json";
import uk from "./locales/uk.json";
import { AccessBoundary } from "./components/AccessBoundary";
import { api, WorkspaceApiError } from "./api";
import { useHosted } from "./mode";

vi.mock("./api", async (original) => {
    const actual = await original<typeof import("./api")>();
    return { ...actual, setAccessCode: vi.fn(), api: { access: vi.fn() } };
});
const { setAccessCode } = await import("./api");

const studio = () =>
    render(
        <AccessBoundary>
            <p>studio</p>
        </AccessBoundary>,
    );

beforeEach(async () => {
    vi.resetAllMocks();
    sessionStorage.clear();
    await i18n.changeLanguage("en");
});

it("shows the studio at once when no code is required", async () => {
    vi.mocked(api.access).mockResolvedValue({ required: false, granted: true });
    studio();
    // Mounted immediately, so a local start has no delay.
    expect(screen.getByText("studio")).toBeInTheDocument();
    await waitFor(() =>
        expect(screen.getByText("studio").parentElement).toHaveAttribute(
            "data-access",
            "open",
        ),
    );
    expect(screen.queryByLabelText("Access code")).toBeNull();
});

it("asks for the code, rejects a wrong one and opens with the right one", async () => {
    vi.mocked(api.access).mockResolvedValue({ required: true, granted: false });
    studio();
    const input = await screen.findByLabelText("Access code");
    expect(screen.queryByText("studio")).toBeNull();
    // Before entering, a visitor is told what is counted and what is not.
    expect(screen.getByText(i18n.t("access.counted"))).toBeVisible();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();

    await userEvent.type(input, "wrong-code");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        "That code is not correct.",
    );
    expect(setAccessCode).toHaveBeenLastCalledWith("");
    expect(sessionStorage.length).toBe(0);

    // The gate is usable in every interface language.
    await userEvent.selectOptions(screen.getByRole("combobox"), "uk");
    expect(screen.getByLabelText(uk["access.label"])).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "de");
    expect(
        screen.getByRole("button", { name: de["access.submit"] }),
    ).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "en");

    vi.mocked(api.access).mockResolvedValue({ required: true, granted: true });
    await userEvent.clear(screen.getByLabelText("Access code"));
    await userEvent.type(
        screen.getByLabelText("Access code"),
        " demo-code-1234 ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("studio")).toBeInTheDocument();
    expect(setAccessCode).toHaveBeenLastCalledWith("demo-code-1234");
    // Remembered for this tab only.
    expect(sessionStorage.getItem("enrichment-access-code")).toBe(
        "demo-code-1234",
    );
});

it("reuses a remembered code and drops it when the server no longer accepts it", async () => {
    sessionStorage.setItem("enrichment-access-code", "demo-code-1234");
    vi.mocked(api.access).mockResolvedValue({ required: true, granted: true });
    const first = studio();
    expect(setAccessCode).toHaveBeenCalledWith("demo-code-1234");
    await waitFor(() =>
        expect(screen.getByText("studio").parentElement).toHaveAttribute(
            "data-access",
            "open",
        ),
    );
    first.unmount();

    vi.mocked(api.access).mockResolvedValue({ required: true, granted: false });
    studio();
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(setAccessCode).toHaveBeenLastCalledWith("");
});

it("explains throttling and an unreachable server instead of calling the code wrong", async () => {
    vi.mocked(api.access).mockResolvedValueOnce({
        required: true,
        granted: false,
    });
    studio();
    await userEvent.type(await screen.findByLabelText("Access code"), "x1");
    vi.mocked(api.access).mockRejectedValueOnce(
        new WorkspaceApiError("access_throttled", "wait", 429),
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        "Too many attempts",
    );
    vi.mocked(api.access).mockRejectedValueOnce(new Error("offline"));
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
            "The server did not answer",
        ),
    );
});

it("does not hide the studio behind a failed check", async () => {
    vi.mocked(api.access).mockRejectedValue(new Error("offline"));
    studio();
    await waitFor(() =>
        expect(screen.getByText("studio").parentElement).toHaveAttribute(
            "data-access",
            "open",
        ),
    );
});

it("tells the studio whether it runs as a hosted demo", async () => {
    const Probe = () => <p>{useHosted() ? "hosted" : "local"}</p>;
    vi.mocked(api.access).mockResolvedValue({ required: true, granted: true });
    render(
        <AccessBoundary>
            <Probe />
        </AccessBoundary>,
    );
    expect(await screen.findByText("hosted")).toBeInTheDocument();
    cleanup();
    vi.mocked(api.access).mockResolvedValue({ required: false, granted: true });
    render(
        <AccessBoundary>
            <Probe />
        </AccessBoundary>,
    );
    await waitFor(() => expect(api.access).toHaveBeenCalledTimes(2));
    expect(screen.getByText("local")).toBeInTheDocument();
});
