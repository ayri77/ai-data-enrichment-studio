import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api } from "./api";

vi.mock("./api", () => ({
    api: { upload: vi.fn(), preview: vi.fn(), remove: vi.fn() },
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
                inferred_type: "text",
                null_count: 1,
                warnings: [],
            },
        ],
        sample_records: [{ place: "Berlin" }, { place: null }],
        schema_warnings: [],
    },
};
beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.upload).mockResolvedValue(metadata);
    vi.mocked(api.preview).mockResolvedValue(preview);
});
async function upload() {
    await userEvent.upload(
        screen.getByLabelText("Upload Excel workbook"),
        new File(["test"], "book.xlsx"),
    );
    await screen.findByText("book.xlsx");
}

describe("Dataset workspace", () => {
    it("starts empty and prevents selection until upload", () => {
        render(<App />);
        expect(screen.getByLabelText(/Worksheet/)).toBeDisabled();
        expect(
            screen.getByRole("button", { name: /Preview selection/ }),
        ).toBeDisabled();
        expect(screen.getByRole("status")).toHaveTextContent(
            "Upload an Excel workbook",
        );
    });
    it("uploads, selects a worksheet and an explicit range, then renders returned rows", async () => {
        render(<App />);
        await upload();
        await userEvent.selectOptions(
            screen.getByLabelText(/Worksheet/),
            "Second",
        );
        const range = screen.getByLabelText(/Table range/);
        await userEvent.clear(range);
        await userEvent.type(range, "B2:B4");
        await userEvent.click(
            screen.getByRole("button", { name: /Preview selection/ }),
        );
        expect(api.preview).toHaveBeenCalledWith("opaque", "Second", "B2:B4");
        expect(await screen.findByText("Berlin")).toBeInTheDocument();
        expect(
            screen.getByRole("columnheader", { name: /place/ }),
        ).toBeInTheDocument();
        expect(screen.getByText("null")).toBeInTheDocument();
        await userEvent.type(range, "5");
        expect(screen.queryByText("Berlin")).not.toBeInTheDocument();
    });
    it("supports file drop and reports validation failures", async () => {
        render(<App />);
        fireEvent.drop(screen.getByText("Drop your Excel workbook here"), {
            dataTransfer: { files: [new File(["x"], "bad.csv")] },
        });
        expect(screen.getByRole("alert")).toHaveTextContent("Only .xlsx");
        expect(api.upload).not.toHaveBeenCalled();
        fireEvent.drop(screen.getByText("Drop your Excel workbook here"), {
            dataTransfer: { files: [new File(["x"], "book.xlsx")] },
        });
        await screen.findByText("book.xlsx");
        vi.mocked(api.preview).mockRejectedValueOnce(
            new Error("Check the range."),
        );
        await userEvent.click(
            screen.getByRole("button", { name: /Preview selection/ }),
        );
        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Check the range.",
        );
    });
    it("shows a loading state and allows retry after upload failure", async () => {
        let reject!: (error: Error) => void;
        vi.mocked(api.upload).mockReturnValueOnce(
            new Promise((_, fail) => {
                reject = fail;
            }),
        );
        render(<App />);
        await userEvent.upload(
            screen.getByLabelText("Upload Excel workbook"),
            new File(["x"], "book.xlsx"),
        );
        expect(screen.getByText("Inspecting workbook…")).toBeInTheDocument();
        expect(screen.getByLabelText("Upload Excel workbook")).toBeDisabled();
        reject(new Error("Cannot read workbook."));
        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Cannot read workbook.",
        );
        await upload();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
    it("removes the current dataset when replacing or clearing a workbook", async () => {
        render(<App />);
        await upload();
        await upload();
        await waitFor(() => expect(api.remove).toHaveBeenCalledWith("opaque"));
        await userEvent.click(
            screen.getByRole("button", { name: "Remove workbook" }),
        );
        expect(screen.getByLabelText(/Worksheet/)).toBeDisabled();
    });
});
