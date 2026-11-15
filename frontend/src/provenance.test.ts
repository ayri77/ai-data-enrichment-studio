import { beforeEach, expect, it } from "vitest";
import { provenanceLabel } from "./capabilities";
import i18n from "./i18n";
import { outputColumns } from "./resultsModel";
import type { EnrichmentPlan } from "./api";

beforeEach(async () => {
    await i18n.changeLanguage("en");
});

const label = (source: string, trust?: string) =>
    provenanceLabel(i18n.t, { source, trust });

it("says in plain words how the values of each kind of trust were obtained", () => {
    expect(label("wikidata", "record")).toBe("Read from a record of Wikidata");
    expect(label("local", "computed")).toBe(
        "Calculated here from other values of the row",
    );
    expect(label("openrouteservice", "service")).toBe(
        "Calculated by openrouteservice (OpenStreetMap data); there is no record to check it against",
    );
    expect(label("open-meteo", "resolved_name")).toBe(
        "Found by name in Open-Meteo and matched to one of its records",
    );
});

it("names the source for a kind of trust it does not know, and for an older server", () => {
    // A later server may send kinds this interface has no wording for.
    expect(label("ecb", "found_on_the_web")).toBe("Source: ecb");
    expect(label("wikidata", "found_on_the_web")).toBe("Source: Wikidata");
    // No kind at all: a calculation done here still says so.
    expect(label("local")).toBe("Calculated here from other values of the row");
    expect(label("wikidata")).toBe("Source: Wikidata");
    // A capability the server did not list says nothing.
    expect(provenanceLabel(i18n.t, undefined)).toBeNull();
});

it("words the label in each language", async () => {
    for (const language of ["uk", "de"]) {
        await i18n.changeLanguage(language);
        const text = label("wikidata", "record");
        expect(text).toContain("Wikidata");
        expect(text).not.toBe("Read from a record of Wikidata");
    }
});

it("ties each output column to the capability of the step that fills it", () => {
    const plan = {
        version: "1.0",
        steps: [
            { id: "city", capability: "geocode", inputs: {}, parameters: {} },
            { id: "ratio", capability: "ratio", inputs: {}, parameters: {} },
        ],
        outputs: [
            { step_id: "city", value_field: "latitude", target_column: "lat" },
            { step_id: "ratio", value_field: "value", target_column: "r" },
        ],
        assumptions: [],
    } as unknown as EnrichmentPlan;
    expect(outputColumns(plan).map((c) => c.capability)).toEqual([
        "geocode",
        "ratio",
    ]);
});
