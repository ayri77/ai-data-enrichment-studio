import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { api, type Capability } from "./api";
import { CapabilitiesContext, readable, sourceNames } from "./capabilities";
import { OverviewWorkspace } from "./components/OverviewWorkspace";
import i18n from "./i18n";
import { HostedContext } from "./mode";

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

const geocode: Capability = {
    id: "geocode",
    version: "1.1",
    description: "Resolve geographical coordinates",
    source: "open-meteo",
    inputs: {
        city: { type: "Text", required: true, description: "City name" },
        administrative_area: {
            type: "Text",
            required: false,
            description: "Optional state, province or administrative region",
        },
    },
    parameters: {
        required_role: {
            type: "Text",
            required: false,
            allowed_values: ["country_capital"],
            description: "Optional hard constraint",
        },
    },
    output: {
        type: "GeoPoint",
        description: "Resolved coordinates",
        fields: {},
    },
};
const ratio: Capability = {
    id: "ratio",
    version: "1.0",
    description: "Divide one quantity by another quantity of the same unit.",
    source: "local",
    inputs: {
        numerator: {
            type: "Quantity",
            required: true,
            description: "Quantity to divide",
        },
    },
    parameters: {},
    output: {
        type: "Number",
        description: "Ratio of the two quantities, without a unit",
        fields: { value: "Number" },
    },
};
// Something the server offers and the interface has no wording for yet.
const added: Capability = {
    id: "currency_rate",
    version: "0.1",
    description: "Read the exchange rate of a currency on a date.",
    source: "ecb",
    inputs: {},
    parameters: {},
    output: { type: "Number", description: "Rate", fields: {} },
};
const card = (id: string) =>
    within(
        screen
            .getByTestId("capability-list")
            .querySelector<HTMLElement>(`[data-capability="${id}"]`)!,
    );

beforeEach(async () => {
    vi.resetAllMocks();
    localStorage.clear();
    await i18n.changeLanguage("en");
    vi.mocked(api.capabilities).mockResolvedValue({
        capabilities: [geocode, ratio, added],
    });
});

it("opens on the overview and lists what the server offers, in plain words", async () => {
    render(<App />);
    expect(
        screen.getByRole("heading", { level: 1, name: i18n.t("overview") }),
    ).toBeVisible();
    expect(
        screen.getByRole("button", { name: i18n.t("overview") }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(i18n.t("overview.claim"))).toBeVisible();
    // The list is the server's: three entries, in its order.
    await waitFor(() =>
        expect(
            [
                ...screen
                    .getByTestId("capability-list")
                    .querySelectorAll("[data-capability]"),
            ].map((item) => item.getAttribute("data-capability")),
        ).toEqual(["geocode", "ratio", "currency_rate"]),
    );
    const city = card("geocode");
    expect(city.getByText(i18n.t("cap.geocode.title"))).toBeVisible();
    expect(city.getByText(i18n.t("cap.geocode.text"))).toBeVisible();
    expect(
        city.getByText(i18n.t("overview.source", { source: "Open-Meteo" })),
    ).toBeVisible();
    expect(card("ratio").getByText(i18n.t("overview.computed"))).toBeVisible();
    // A capability without wording here is still listed, as the server says it.
    const unknown = card("currency_rate");
    expect(unknown.getByText("Currency rate")).toBeVisible();
    expect(unknown.getByText(added.description)).toBeVisible();
    expect(
        unknown.getByText(i18n.t("overview.source", { source: "ecb" })),
    ).toBeVisible();
    // Contracts are the second layer.
    expect(screen.queryByTestId("capability-contract")).toBeNull();
    expect(screen.queryByText("required_role")).toBeNull();
    expect(
        screen.getByText(i18n.t("overview.know.limit", { max: 20 })),
    ).toBeVisible();
    // A local start is not a shared demo and says nothing about one.
    expect(screen.queryByText(i18n.t("overview.know.shared"))).toBeNull();
});

it("shows each contract in Advanced as the planning model is shown it", async () => {
    localStorage.setItem("enrichment.mode", "advanced");
    render(<App />);
    await screen.findByText(i18n.t("cap.geocode.title"));
    const city = card("geocode");
    expect(city.getByText("geocode · v1.1 · open-meteo")).toBeVisible();
    expect(city.getByText(geocode.description)).toBeVisible();
    const row = (name: string) =>
        within(city.getByRole("rowheader", { name }).closest("tr")!);
    expect(row("city").getByText("City name")).toBeVisible();
    expect(
        row("city").getByText(`Text · ${i18n.t("overview.contract.required")}`),
    ).toBeVisible();
    expect(
        row("administrative_area").getByText(
            `Text · ${i18n.t("overview.contract.optional")}`,
        ),
    ).toBeVisible();
    // A restricted parameter states the values it accepts.
    expect(
        row("required_role").getByText(
            `country_capital · ${i18n.t("overview.contract.optional")}`,
        ),
    ).toBeVisible();
    expect(row("GeoPoint").getByText("Resolved coordinates")).toBeVisible();
    expect(card("ratio").getByText("value: Number")).toBeVisible();
    // Without wording of its own, the description is not printed twice.
    expect(card("currency_rate").getAllByText(added.description)).toHaveLength(
        1,
    );
});

it("names what it knows by id while the server's list is missing", async () => {
    vi.mocked(api.capabilities).mockRejectedValue(new Error("offline"));
    vi.mocked(api.limits).mockResolvedValue({
        max_run_rows: 20,
        max_timeout_seconds: 300,
        default_timeout_seconds: 180,
        capabilities: ["geocode", "road_distance"],
    });
    render(<App />);
    expect(
        await screen.findByText(i18n.t("cap.road_distance.title")),
    ).toBeVisible();
    expect(screen.getByText(i18n.t("cap.geocode.text"))).toBeVisible();
    expect(screen.queryByText(i18n.t("cap.ratio.title"))).toBeNull();
});

it("leads to the first step, and opens an example from the overview", async () => {
    const example = {
        sample_id: "capitals",
        filename: "capitals.xlsx",
        instruction: "Calculate the distance between the two cities.",
        outputs: [{ name: "distance", column: "E" }],
        has_plan: false,
    };
    vi.mocked(api.samples).mockResolvedValue({ samples: [example] });
    vi.mocked(api.sampleWorkbook).mockResolvedValue(new Blob(["x"]));
    vi.mocked(api.upload).mockResolvedValue({
        dataset_id: "source",
        filename: "capitals.xlsx",
        worksheets: [{ name: "Sheet1", columns: ["city"], row_count: 2 }],
    });
    render(<App />);
    await userEvent.click(
        screen.getByRole("button", { name: i18n.t("overview.begin") }),
    );
    expect(
        screen.getByRole("heading", { level: 1, name: i18n.t("dataset") }),
    ).toBeVisible();
    expect(screen.getByLabelText("Upload Excel workbook")).toBeVisible();
    // The short list on the empty first step is the same server list.
    expect(await screen.findByText("Currency rate")).toBeVisible();
    // The overview stays one click away and leaves the step as it was.
    await userEvent.click(
        screen.getByRole("button", { name: i18n.t("overview") }),
    );
    await userEvent.click(
        await screen.findByRole("button", { name: i18n.t("sample.capitals") }),
    );
    await waitFor(() =>
        expect(api.sampleWorkbook).toHaveBeenCalledWith("capitals"),
    );
    expect(
        screen.getByRole("heading", { level: 1, name: i18n.t("dataset") }),
    ).toBeVisible();
});

it("says on a shared demo that it is one, and what is counted", () => {
    render(
        <HostedContext.Provider value={true}>
            <CapabilitiesContext.Provider value={[geocode]}>
                <OverviewWorkspace
                    maxRows={20}
                    capabilities={["geocode"]}
                    samples={[]}
                    busy={false}
                    onStart={() => {}}
                    onSample={() => {}}
                />
            </CapabilitiesContext.Provider>
        </HostedContext.Provider>,
    );
    expect(screen.getByText(i18n.t("overview.know.shared"))).toBeVisible();
    expect(screen.getByText(i18n.t("access.counted"))).toBeVisible();
});

it("names each outside source once and leaves calculations out", () => {
    expect(
        sourceNames(i18n.t, [geocode, ratio, { ...ratio, id: "x" }, added]),
    ).toEqual(["Open-Meteo", "ecb"]);
    expect(readable("road_distance")).toBe("Road distance");
});

it("words every capability and source the server can offer in each language", () => {
    const ids = [
        "geocode",
        "resolve_entity",
        "lookup_property",
        "geodesic_distance",
        "ratio",
        "road_distance",
    ];
    for (const language of ["en", "uk", "de"]) {
        const t = i18n.getFixedT(language);
        for (const id of ids)
            for (const part of ["title", "text"])
                expect(t(`cap.${id}.${part}`, { defaultValue: "" })).not.toBe(
                    "",
                );
        for (const source of ["open-meteo", "wikidata", "openrouteservice"])
            expect(t(`source.${source}`, { defaultValue: "" })).not.toBe("");
    }
});
