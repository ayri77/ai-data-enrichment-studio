import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { api, type Capability } from "./api";
import {
    CapabilitiesContext,
    CategoriesContext,
    groupCapabilities,
    readable,
    sourceNames,
} from "./capabilities";
import { OverviewWorkspace } from "./components/OverviewWorkspace";
import i18n from "./i18n";
import { HostedContext, ModeContext } from "./mode";

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
    trust: "resolved_name",
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
    trust: "computed",
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
    // A kind of trust this interface has no wording for.
    trust: "found_on_the_web",
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

const openCard = (id: string) =>
    userEvent.click(card(id).getByRole("button", { name: /^Details/ }));

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
        city.getByText(
            i18n.t("provenance.resolved_name", { source: "Open-Meteo" }),
        ),
    ).toBeVisible();
    expect(
        card("ratio").getByText(i18n.t("provenance.computed")),
    ).toBeVisible();
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
    // The contract is in the opened card, not before.
    expect(screen.queryByTestId("capability-contract")).toBeNull();
    await openCard("geocode");
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
    await openCard("ratio");
    expect(card("ratio").getByText("value: Number")).toBeVisible();
    // Without wording of its own, the description is not printed twice.
    await openCard("currency_rate");
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
        "web_lookup",
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
            for (const part of ["title", "text", "needs", "gives", "example"])
                expect(t(`cap.${id}.${part}`, { defaultValue: "" })).not.toBe(
                    "",
                );
        for (const category of [
            "all",
            "lookup",
            "distance",
            "calculate",
            "web_search",
        ])
            for (const part of ["title", "text"])
                expect(
                    t(`cat.${category}.${part}`, { defaultValue: "" }),
                ).not.toBe("");
        for (const source of ["open-meteo", "wikidata", "openrouteservice"])
            expect(t(`source.${source}`, { defaultValue: "" })).not.toBe("");
    }
});

// The catalog: tiles that filter, groups, cards that open in place.
const withCategory = (item: Capability, category?: string): Capability => ({
    ...item,
    category,
});
const geocodeL = withCategory(geocode, "lookup");
const roads = withCategory(
    {
        ...ratio,
        id: "road_distance",
        source: "openrouteservice",
        trust: "service",
    },
    "distance",
);
const ratioC = withCategory(ratio, "calculate");

function renderCatalog({
    items,
    categories,
    webLookup = false,
    mode = "basic",
}: {
    items: Capability[];
    categories: string[] | null;
    webLookup?: boolean;
    mode?: "basic" | "advanced";
}) {
    return render(
        <ModeContext.Provider value={mode}>
            <CapabilitiesContext.Provider value={items}>
                <CategoriesContext.Provider value={categories}>
                    <OverviewWorkspace
                        maxRows={20}
                        capabilities={items.map((item) => item.id)}
                        webLookup={webLookup}
                        samples={[]}
                        busy={false}
                        onStart={() => {}}
                        onSample={() => {}}
                    />
                </CategoriesContext.Provider>
            </CapabilitiesContext.Provider>
        </ModeContext.Provider>,
    );
}
const tile = (name: string | RegExp) =>
    within(screen.getByTestId("category-tiles")).getByRole("button", { name });
const groupIds = () =>
    [...document.querySelectorAll(".capability-group")].map((group) =>
        group.getAttribute("data-category"),
    );
const catalog = {
    items: [geocodeL, roads, ratioC],
    categories: ["lookup", "distance", "calculate"],
};

it("groups the capabilities in the server's order and counts them in the tiles", () => {
    renderCatalog({
        items: [ratioC, geocodeL, withCategory(added, "lookup")],
        categories: ["lookup", "calculate"],
    });
    expect(groupIds()).toEqual(["lookup", "calculate"]);
    const lookup = within(
        document.querySelector<HTMLElement>(
            '.capability-group[data-category="lookup"]',
        )!,
    );
    expect(
        lookup.getByRole("heading", { name: i18n.t("cat.lookup.title") }),
    ).toBeVisible();
    expect(
        lookup
            .getAllByRole("listitem")
            .map((item) => item.getAttribute("data-capability")),
    ).toEqual(["geocode", "currency_rate"]);
    expect(tile(new RegExp(i18n.t("cat.all.title"))).textContent).toContain(
        "3",
    );
    expect(tile(new RegExp(i18n.t("cat.lookup.title"))).textContent).toContain(
        "2",
    );
    expect(
        tile(new RegExp(i18n.t("cat.calculate.title"))).textContent,
    ).toContain("1");
});

it("filters by a tile, shows everything again on a second press or on All", async () => {
    renderCatalog(catalog);
    const all = tile(new RegExp(`^${i18n.t("cat.all.title")}`));
    const distances = tile(new RegExp(`^${i18n.t("cat.distance.title")}`));
    expect(all).toHaveAttribute("aria-pressed", "true");
    expect(groupIds()).toEqual(["lookup", "distance", "calculate"]);
    await userEvent.click(distances);
    expect(distances).toHaveAttribute("aria-pressed", "true");
    expect(all).toHaveAttribute("aria-pressed", "false");
    expect(groupIds()).toEqual(["distance"]);
    await userEvent.click(distances);
    expect(groupIds()).toEqual(["lookup", "distance", "calculate"]);
    expect(all).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(distances);
    await userEvent.click(
        tile(new RegExp(`^${i18n.t("cat.calculate.title")}`)),
    );
    expect(groupIds()).toEqual(["calculate"]);
    await userEvent.click(all);
    expect(groupIds()).toEqual(["lookup", "distance", "calculate"]);
});

it("shows no tiles for one category", () => {
    renderCatalog({ items: [geocodeL], categories: ["lookup"] });
    expect(screen.queryByTestId("category-tiles")).toBeNull();
    expect(groupIds()).toEqual(["lookup"]);
});

it("renders an older server's list as one group without tiles", () => {
    renderCatalog({ items: [geocode, ratio], categories: null });
    expect(screen.queryByTestId("category-tiles")).toBeNull();
    expect(groupIds()).toEqual([""]);
    expect(screen.queryByRole("heading", { level: 3 })).toBeNull();
    expect(
        card("geocode").getByText(i18n.t("cap.geocode.title")),
    ).toBeVisible();
    expect(card("ratio").getByText(i18n.t("cap.ratio.title"))).toBeVisible();
    expect(groupCapabilities([geocode, ratio], ["lookup"])).toHaveLength(1);
});

it("shows a category the interface does not know under its readable id", async () => {
    renderCatalog({
        items: [geocodeL, withCategory(added, "exchange_rates")],
        categories: ["lookup", "exchange_rates"],
    });
    expect(groupIds()).toEqual(["lookup", "exchange_rates"]);
    const unknown = tile(/^Exchange rates/);
    await userEvent.click(unknown);
    expect(groupIds()).toEqual(["exchange_rates"]);
    expect(
        screen.getByRole("heading", { name: "Exchange rates" }),
    ).toBeVisible();
    expect(card("currency_rate").getByText(added.description)).toBeVisible();
});

it("opens and closes a card in place, several at a time", async () => {
    renderCatalog(catalog);
    const first = card("geocode").getByRole("button", { name: /^Details/ });
    const second = card("ratio").getByRole("button", { name: /^Details/ });
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(i18n.t("cap.geocode.needs"))).toBeNull();
    await userEvent.click(first);
    await userEvent.click(second);
    expect(first).toHaveAttribute("aria-expanded", "true");
    expect(second).toHaveAttribute("aria-expanded", "true");
    expect(
        document.getElementById(first.getAttribute("aria-controls")!),
    ).not.toBeNull();
    await userEvent.click(first);
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(second).toHaveAttribute("aria-expanded", "true");
});

it("tells in Basic what it needs and gives, with an example, and no contract", async () => {
    renderCatalog(catalog);
    await userEvent.click(
        card("geocode").getByRole("button", { name: /^Details/ }),
    );
    const city = card("geocode");
    expect(city.getByText(i18n.t("cap.geocode.needs"))).toBeVisible();
    expect(city.getByText(i18n.t("cap.geocode.gives"))).toBeVisible();
    expect(city.getByText(i18n.t("cap.geocode.example"))).toBeVisible();
    expect(i18n.t("cap.geocode.gives")).toMatch(/population/);
    expect(screen.queryByTestId("capability-contract")).toBeNull();
    // A calculation by a service says so, honestly.
    expect(
        card("road_distance").getByText(
            i18n.t("provenance.service", {
                source: "openrouteservice (OpenStreetMap data)",
            }),
        ),
    ).toBeVisible();
});

it("adds the contract in Advanced", async () => {
    renderCatalog({ ...catalog, mode: "advanced" });
    await userEvent.click(
        card("geocode").getByRole("button", { name: /^Details/ }),
    );
    const city = card("geocode");
    expect(city.getByText(i18n.t("cap.geocode.needs"))).toBeVisible();
    expect(city.getByTestId("capability-contract")).toBeVisible();
    expect(city.getByText("geocode · v1.1 · open-meteo")).toBeVisible();
});

it("shows the web search as a category of its own when the server allows it", async () => {
    renderCatalog({ ...catalog, webLookup: true });
    expect(groupIds()).toEqual([
        "lookup",
        "distance",
        "calculate",
        "web_search",
    ]);
    expect(
        tile(new RegExp(`^${i18n.t("cat.web_search.title")}`)).textContent,
    ).toContain("1");
    // The "All" tile counts what can be opened, the search included.
    expect(
        tile(new RegExp(`^${i18n.t("cat.all.title")}`)).textContent,
    ).toContain("4");
    const web = card("web_lookup");
    expect(
        web.getByText(
            i18n.t("provenance.ai_found", { source: "OpenAI web search" }),
        ),
    ).toBeVisible();
    await userEvent.click(web.getByRole("button", { name: /^Details/ }));
    expect(web.getByText(i18n.t("cap.web_lookup.needs"))).toBeVisible();
    expect(i18n.t("cap.web_lookup.needs")).toMatch(/OpenAI/);
    expect(i18n.t("cap.web_lookup.gives")).toMatch(/links/);
    // The page says it once, in the card, not again under "Good to know".
    expect(document.querySelector(".overview-know")!.textContent).not.toMatch(
        /switch it on under the task/i,
    );
});

it("hides the web search in Basic and mutes it in Advanced where the server does not allow it", () => {
    const { unmount } = renderCatalog(catalog);
    expect(groupIds()).toEqual(["lookup", "distance", "calculate"]);
    expect(
        screen
            .queryByTestId("capability-list")!
            .querySelector('[data-capability="web_lookup"]'),
    ).toBeNull();
    unmount();
    renderCatalog({ ...catalog, mode: "advanced" });
    const muted = card("web_lookup");
    expect(muted.getByText(/ENRICHMENT_ALLOW_WEB_LOOKUP/)).toBeVisible();
    expect(muted.queryByRole("button")).toBeNull();
    // It is not a category a person can choose.
    expect(
        screen.queryByRole("button", {
            name: new RegExp(`^${i18n.t("cat.web_search.title")}`),
        }),
    ).toBeNull();
});

it("shows the server's description for a capability without texts of its own", async () => {
    renderCatalog({
        items: [geocodeL, withCategory(added, "lookup")],
        categories: ["lookup"],
    });
    const unknown = card("currency_rate");
    expect(unknown.getByText(added.description)).toBeVisible();
    // Nothing more to say in Basic, so nothing to open.
    expect(unknown.queryByRole("button")).toBeNull();
});
