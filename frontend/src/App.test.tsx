import {
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api, WorkspaceApiError } from "./api";
import i18n, { messages } from "./i18n";

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
        upload: vi.fn(),
        preview: vi.fn(),
        remove: vi.fn(),
    },
}));
const metadata = {
    dataset_id: "opaque",
    filename: "book.xlsx",
    worksheets: [
        { name: "First", columns: ["name", "value"], row_count: 10 },
        { name: "Second", columns: ["place"], row_count: 2 },
    ],
};
const preview = {
    dataset_id: "opaque",
    sheet_name: "Second",
    range_ref: "B2:B4",
    sample_limit: 5,
    profile: {
        row_count: 2,
        columns: [
            {
                name: "place",
                inferred_type: "Text",
                null_count: 1,
                warnings: [],
            },
        ],
        sample_records: [{ place: "Berlin" }, { place: null }],
        schema_warnings: [],
    },
};

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
    vi.mocked(api.upload).mockResolvedValue(metadata);
    vi.mocked(api.preview).mockResolvedValue(preview);
});
async function upload() {
    await userEvent.upload(
        screen.getByLabelText(i18n.t("uploadWorkbook")),
        new File(["test"], "book.xlsx"),
    );
    await screen.findByText("book.xlsx");
}
async function previewLoaded() {
    await upload();
    await userEvent.click(
        screen.getByRole("button", { name: i18n.t("previewAction") }),
    );
    await screen.findByText("Berlin");
}
async function advanced(value: string) {
    await userEvent.click(
        screen.getByRole("button", { name: i18n.t("advanced") }),
    );
    const input = screen.getByLabelText(i18n.t("tableRange"));
    await userEvent.clear(input);
    if (value) await userEvent.type(input, value);
    return input;
}

describe("Dataset UI Foundation", () => {
    it("starts empty, exposes preferences, the overview and five workspaces", () => {
        render(<App opening="dataset" />);
        expect(screen.getByLabelText("Worksheet")).toBeDisabled();
        expect(
            screen.getByRole("button", { name: "Preview selection" }),
        ).toBeDisabled();
        expect(screen.getByText("No table selected")).toBeInTheDocument();
        expect(
            screen.getByRole("navigation").querySelectorAll("button"),
        ).toHaveLength(6);
        expect(screen.getByRole("button", { name: "Overview" })).toBeEnabled();
        expect(screen.getByRole("button", { name: "Plan" })).toBeEnabled();
        expect(
            screen.queryByText(/verified distance/i),
        ).not.toBeInTheDocument();
    });
    it("guides offset boundaries and uses the real request contract", async () => {
        render(<App opening="dataset" />);
        await upload();
        await userEvent.selectOptions(
            screen.getByLabelText("Worksheet"),
            "Second",
        );
        await userEvent.selectOptions(
            screen.getByLabelText("First column"),
            "B",
        );
        expect(
            screen.getByRole("button", { name: "Preview selection" }),
        ).toBeDisabled();
        await userEvent.selectOptions(
            screen.getByLabelText("Last column"),
            "B",
        );
        for (const [label, value] of [
            ["Header row", "2"],
            ["Last row", "4"],
        ]) {
            const field = screen.getByLabelText(label);
            await userEvent.clear(field);
            await userEvent.type(field, value);
        }
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        expect(api.preview).toHaveBeenCalledWith("opaque", "Second", "B2:B4");
        expect(await screen.findByText("Berlin")).toBeInTheDocument();
        expect(
            screen.getByRole("columnheader", { name: "Sample index" }),
        ).toBeInTheDocument();
        expect(
            screen.getByText(/Sample indices are not Excel row numbers/),
        ).toBeInTheDocument();
        expect(screen.getByLabelText("Empty source value")).toBeInTheDocument();
        const inspector = screen.getByRole("complementary", {
            name: "Column inspector",
        });
        expect(within(inspector).getByText("B2:B4")).toBeInTheDocument();
    });
    it("preserves advanced A1 entry, normalizes references and clears stale previews", async () => {
        render(<App opening="dataset" />);
        await upload();
        await userEvent.selectOptions(
            screen.getByLabelText("Worksheet"),
            "Second",
        );
        const range = await advanced(" $b$2:$b$4 ");
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        expect(api.preview).toHaveBeenCalledWith("opaque", "Second", "B2:B4");
        await screen.findByText("Berlin");
        await userEvent.type(range, "5");
        expect(screen.queryByText("Berlin")).not.toBeInTheDocument();
    });
    it("blocks malformed and reversed syntax before calling the API", async () => {
        render(<App opening="dataset" />);
        await upload();
        await advanced("B4:A2");
        expect(screen.getByRole("alert")).toHaveTextContent("must not precede");
        expect(
            screen.getByRole("button", { name: "Preview selection" }),
        ).toBeDisabled();
        expect(api.preview).not.toHaveBeenCalled();
        const field = screen.getByLabelText("Table range");
        await userEvent.clear(field);
        await userEvent.type(field, "Sheet1!A1:B4");
        expect(screen.getByRole("alert")).toHaveTextContent("Enter an A1");
        expect(api.preview).not.toHaveBeenCalled();
    });
    it("lets guided controls recover the last boundaries from invalid advanced input", async () => {
        render(<App opening="dataset" />);
        await upload();
        await advanced("bad");
        await userEvent.click(
            screen.getByRole("button", { name: "Guided selection" }),
        );
        expect(screen.getByLabelText("First column")).toHaveValue("A");
        expect(screen.getByLabelText("Last column")).toHaveValue("B");
        expect(
            screen.getByRole("button", { name: "Preview selection" }),
        ).toBeEnabled();
        expect(api.preview).not.toHaveBeenCalled();
    });
    it("keeps syntactic validity separate from authoritative backend table validation", async () => {
        render(<App opening="dataset" />);
        await upload();
        await advanced("A2:B4");
        vi.mocked(api.preview).mockRejectedValueOnce(
            new WorkspaceApiError("invalid_selection", "backend text", 422),
        );
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        expect(await screen.findByRole("alert")).toHaveTextContent(
            "unique, non-empty text headers",
        );
        expect(screen.getByRole("alert")).toHaveAttribute(
            "data-error-code",
            "invalid_selection",
        );
        expect(api.preview).toHaveBeenCalledWith("opaque", "First", "A2:B4");
    });
    it("supports file drop and validates extensions and upload size locally", async () => {
        render(<App opening="dataset" />);
        const target = screen.getByRole("region", { name: "Source workbook" });
        fireEvent.drop(target, {
            dataTransfer: { files: [new File(["x"], "bad.csv")] },
        });
        expect(screen.getByRole("alert")).toHaveTextContent("Only .xlsx");
        const oversized = new File(["x"], "big.xlsx");
        Object.defineProperty(oversized, "size", {
            value: 10 * 1024 * 1024 + 1,
        });
        fireEvent.drop(target, { dataTransfer: { files: [oversized] } });
        expect(screen.getByRole("alert")).toHaveTextContent("10 MiB");
        expect(api.upload).not.toHaveBeenCalled();
        fireEvent.drop(target, {
            dataTransfer: { files: [new File(["x"], "book.xlsx")] },
        });
        await screen.findByText("book.xlsx");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
    it("does not discard the dataset, range, preview or inspector selection on language/theme changes", async () => {
        render(<App opening="dataset" />);
        await previewLoaded();
        const initialRange = screen
            .getByLabelText("Last row")
            .getAttribute("value");
        for (const locale of ["uk", "de", "en"] as const) {
            await userEvent.selectOptions(
                screen.getByLabelText(i18n.t("language")),
                locale,
            );
            expect(screen.getByText("Berlin")).toBeInTheDocument();
            expect(screen.getByText("book.xlsx")).toBeInTheDocument();
            expect(screen.getByLabelText(messages[locale].lastRow)).toHaveValue(
                Number(initialRange),
            );
            expect(document.documentElement.lang).toBe(locale);
            expect(localStorage.getItem("enrichment.language")).toBe(locale);
        }
        await userEvent.selectOptions(screen.getByLabelText("Theme"), "light");
        expect(document.querySelector(".studio")).toHaveAttribute(
            "data-theme",
            "light",
        );
        expect(localStorage.getItem("enrichment.theme")).toBe("light");
        expect(screen.getByText("Berlin")).toBeInTheDocument();
        expect(api.upload).toHaveBeenCalledTimes(1);
        expect(api.preview).toHaveBeenCalledTimes(1);
    });
    it("restores a saved theme after remount", async () => {
        const first = render(<App opening="dataset" />);
        await userEvent.selectOptions(screen.getByLabelText("Theme"), "light");
        first.unmount();
        render(<App opening="dataset" />);
        expect(screen.getByLabelText("Theme")).toHaveValue("light");
    });
    it("localizes existing API errors in all languages without refetching", async () => {
        render(<App opening="dataset" />);
        await upload();
        vi.mocked(api.preview).mockRejectedValueOnce(
            new WorkspaceApiError(
                "invalid_selection",
                "path-free API message",
                422,
            ),
        );
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        await screen.findByRole("alert");
        for (const locale of ["uk", "de", "en"] as const) {
            await userEvent.selectOptions(
                screen.getByLabelText(i18n.t("language")),
                locale,
            );
            expect(screen.getByRole("alert")).toHaveTextContent(
                messages[locale]["error.invalid_selection"],
            );
            expect(screen.getByRole("alert")).toHaveAttribute(
                "data-error-code",
                "invalid_selection",
            );
        }
        expect(api.preview).toHaveBeenCalledTimes(1);
    });
    it("supports switching language/theme during upload and recovering from failure", async () => {
        let reject!: (error: Error) => void;
        vi.mocked(api.upload).mockReturnValueOnce(
            new Promise((_, fail) => {
                reject = fail;
            }),
        );
        render(<App opening="dataset" />);
        await userEvent.upload(
            screen.getByLabelText("Upload Excel workbook"),
            new File(["x"], "book.xlsx"),
        );
        expect(screen.getByLabelText("Upload Excel workbook")).toBeDisabled();
        await userEvent.selectOptions(screen.getByLabelText("Language"), "de");
        await userEvent.selectOptions(
            screen.getByLabelText("Darstellung"),
            "light",
        );
        reject(new WorkspaceApiError("network_error", "network message"));
        expect(await screen.findByRole("alert")).toHaveTextContent(
            messages.de["error.network_error"],
        );
        await upload();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
    it("replaces workbook/schema and clears the previous preview", async () => {
        render(<App opening="dataset" />);
        await previewLoaded();
        vi.mocked(api.upload).mockResolvedValueOnce({
            dataset_id: "mountains",
            filename: "mountains.xlsx",
            worksheets: [
                {
                    name: "Mountains",
                    columns: ["Mountain", "Country", "height"],
                    row_count: 10,
                },
            ],
        });
        await userEvent.upload(
            screen.getByLabelText("Upload Excel workbook"),
            new File(["x"], "mountains.xlsx"),
        );
        await screen.findByText("mountains.xlsx");
        expect(api.remove).toHaveBeenCalledWith("opaque");
        expect(screen.queryByText("Berlin")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Worksheet")).toHaveValue("Mountains");
        expect(screen.getByLabelText("Last column")).toHaveValue("C");
    });
    it("uses complete profile counts and leaves zero/boolean source values unchanged", async () => {
        vi.mocked(api.preview).mockResolvedValueOnce({
            ...preview,
            profile: {
                row_count: 10,
                columns: [
                    {
                        name: "amount",
                        inferred_type: "Integer",
                        null_count: 7,
                        warnings: [],
                    },
                    {
                        name: "flag",
                        inferred_type: "Boolean",
                        null_count: 0,
                        warnings: [],
                    },
                ],
                sample_records: [{ amount: 0, flag: false }],
                schema_warnings: [],
            },
        });
        render(<App opening="dataset" />);
        await upload();
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        await screen.findByText("false");
        const inspector = screen.getByRole("complementary", {
            name: "Column inspector",
        });
        expect(within(inspector).getByText("7")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: /flag/ }));
        expect(
            within(inspector).getByRole("heading", { name: "flag" }),
        ).toBeInTheDocument();
        await userEvent.selectOptions(screen.getByLabelText("Language"), "uk");
        expect(screen.getByText("false")).toBeInTheDocument();
        expect(
            within(inspector).getByRole("heading", { name: "flag" }),
        ).toBeInTheDocument();
    });
    it("shows headers-only selections and supports explicit removal", async () => {
        vi.mocked(api.preview).mockResolvedValueOnce({
            ...preview,
            profile: { ...preview.profile, row_count: 0, sample_records: [] },
        });
        render(<App opening="dataset" />);
        await upload();
        await userEvent.click(
            screen.getByRole("button", { name: "Preview selection" }),
        );
        expect(await screen.findByText(/Headers found/)).toBeInTheDocument();
        await userEvent.click(
            screen.getByRole("button", { name: "Remove workbook" }),
        );
        await waitFor(() =>
            expect(screen.getByLabelText("Worksheet")).toBeDisabled(),
        );
        expect(api.remove).toHaveBeenCalledWith("opaque");
    });
});
