import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it } from "vitest";
import type { Preview } from "./api";
import { DatasetPreview } from "./components/DatasetPreview";
import i18n from "./i18n";

const TOTAL = 120;
/** Row r holds "City r", a population that falls as r grows, and a gap at r = 3. */
const preview: Preview = {
    dataset_id: "source",
    sheet_name: "Sheet1",
    range_ref: `A1:B${TOTAL + 1}`,
    sample_limit: 5,
    profile: {
        row_count: TOTAL,
        columns: [
            {
                name: "city",
                inferred_type: "String",
                null_count: 0,
                warnings: [],
            },
            {
                name: "population",
                inferred_type: "Integer",
                null_count: 1,
                warnings: [],
            },
        ],
        sample_records: [{ city: "City 2", population: 1000 }],
        schema_warnings: [],
    },
    rows: {
        row_numbers: Array.from({ length: TOTAL }, (_, index) => index + 2),
        values: Array.from({ length: TOTAL }, (_, index) => [
            `City ${index + 2}`,
            index + 2 === 3 ? null : 1000 - index,
        ]),
    },
};
const show = () =>
    render(<DatasetPreview preview={preview} busy={false} hasWorkbook />);
const shown = () =>
    within(screen.getAllByRole("rowgroup")[1])
        .getAllByRole("row")
        .map((row) => within(row).getByRole("rowheader").textContent);
const sortBy = (column: string) =>
    screen.getByRole("button", { name: i18n.t("sortBy", { column }) });

beforeEach(async () => {
    await i18n.changeLanguage("en");
});

it("shows as many rows as asked for, with worksheet row numbers, and all on request", async () => {
    show();
    expect(screen.getByTestId("row-count")).toHaveTextContent(
        i18n.t("rowsTotal", { total: TOTAL }),
    );
    expect(
        screen.getByRole("columnheader", { name: i18n.t("rowNumber") }),
    ).toBeInTheDocument();
    const shownRows = screen.getByRole("combobox", {
        name: i18n.t("rowsShow"),
    });
    expect(shownRows).toHaveValue("50");
    expect(shown()).toHaveLength(50);
    expect(shown().slice(0, 3)).toEqual(["2", "3", "4"]);
    // What is not shown is said, not hidden.
    expect(
        screen.getByText(i18n.t("rowsShown", { shown: 50, total: TOTAL })),
    ).toBeInTheDocument();
    await userEvent.selectOptions(shownRows, "20");
    expect(shown()).toHaveLength(20);
    await userEvent.selectOptions(shownRows, i18n.t("rowsShowAll"));
    expect(shown()).toHaveLength(TOTAL);
    expect(screen.getByText(i18n.t("valueLimit"))).toBeInTheDocument();
});

it("dims the rows a run does not take and leaves them to look through", () => {
    render(
        <DatasetPreview
            preview={preview}
            busy={false}
            hasWorkbook
            runLast={21}
        />,
    );
    const rows = within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
    // Worksheet rows 2–21 are the run's; row 22 onwards is only shown.
    expect(rows[19]).not.toHaveAttribute("data-out-of-run");
    expect(rows[20]).toHaveAttribute("data-out-of-run", "true");
});

it("filters by text and says how many rows match", async () => {
    show();
    const filter = screen.getByRole("searchbox", {
        name: i18n.t("filterRows"),
    });
    await userEvent.type(filter, "city 12");
    // City 12 and City 120, 121.
    expect(shown()).toEqual(["12", "120", "121"]);
    expect(screen.getByTestId("row-count")).toHaveTextContent(
        i18n.t("rowsMatching", { shown: 3, total: TOTAL }),
    );
    await userEvent.clear(filter);
    await userEvent.type(filter, "no such text");
    expect(screen.getByRole("status")).toHaveTextContent(i18n.t("noMatches"));
    await userEvent.clear(filter);
    expect(shown()).toHaveLength(50);
});

it("sorts by a column in both directions and back to the worksheet order", async () => {
    show();
    const population = () =>
        screen.getByRole("columnheader", { name: /population/ });
    expect(population()).not.toHaveAttribute("aria-sort");
    // Ascending: the smallest population is in the last row; the empty cell is not first.
    await userEvent.click(sortBy("population"));
    expect(population()).toHaveAttribute("aria-sort", "ascending");
    expect(shown().slice(0, 2)).toEqual([String(TOTAL + 1), String(TOTAL)]);
    await userEvent.click(sortBy("population"));
    expect(population()).toHaveAttribute("aria-sort", "descending");
    expect(shown().slice(0, 2)).toEqual(["2", "4"]);
    await userEvent.click(sortBy("population"));
    expect(population()).not.toHaveAttribute("aria-sort");
    expect(shown().slice(0, 3)).toEqual(["2", "3", "4"]);
    // Text sorts as a person reads it: City 2, City 3, … not City 10 first.
    await userEvent.click(sortBy("city"));
    expect(shown().slice(0, 3)).toEqual(["2", "3", "4"]);
    await userEvent.click(sortBy("city"));
    expect(shown()[0]).toBe(String(TOTAL + 1));
});

it("falls back to the sample when the server sent no rows", () => {
    render(
        <DatasetPreview
            preview={{ ...preview, rows: undefined }}
            busy={false}
            hasWorkbook
        />,
    );
    expect(shown()).toEqual(["1"]);
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(
        screen.queryByRole("button", {
            name: i18n.t("sortBy", { column: "city" }),
        }),
    ).toBeNull();
});
