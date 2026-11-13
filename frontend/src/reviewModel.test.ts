import { expect, it } from "vitest";
import type { Review, ReviewCandidate, ReviewItem } from "./api";
import {
    countryName,
    decisions,
    hasCandidateChoice,
    otherName,
    pendingRecommendations,
    rowList,
    sourceLink,
    validChoices,
    withRecommendations,
    type Choices,
} from "./reviewModel";

const candidate = (
    entity_id: string,
    extra: Partial<ReviewCandidate> = {},
): ReviewCandidate => ({
    source: "open-meteo",
    entity_id,
    label: "Berlin",
    entity_type: "city",
    verification: "verified",
    attributes: { latitude: 52.52, longitude: 13.4, country_code: "DE" },
    evidence: [],
    selectable: true,
    blocker: null,
    recommended: false,
    recommendation_reasons: [],
    reason_codes: [],
    ...extra,
});
const item = (
    operation_id: string,
    candidates: ReviewCandidate[],
    rows: (number | null)[] = [2],
): ReviewItem => ({
    operation_id,
    capability: "geocode",
    provider_id: "open-meteo",
    query: { name: operation_id, entity_type: "city", context: {} },
    status: "needs_review",
    reason_codes: [],
    search_complete: true,
    affected: rows.map((row, index) => ({
        record_id: `Sheet1!${row ?? `x${index}`}`,
        row,
        step_id: "from_city",
    })),
    candidates,
});
const review: Review = {
    job_id: "job",
    previous_job_id: null,
    affected_rows: 3,
    informational: [],
    items: [
        item("berlin", [
            candidate("1", {
                recommended: true,
                recommendation_reasons: ["country_capital_role"],
            }),
            candidate("2"),
        ]),
        item("kyiv", [candidate("3"), candidate("4")]),
        item("paris", [
            candidate("5", {
                selectable: false,
                blocker: "excluded",
                recommended: true,
            }),
            candidate("6"),
        ]),
    ],
};
const pick = (entity_id: string) => ({ source: "open-meteo", entity_id });

it("accepts recommendations only for undecided queries and only selectable ones", () => {
    expect(pendingRecommendations(review, {})).toBe(1);
    expect(withRecommendations(review, {})).toEqual({ berlin: pick("1") });
    // An explicit choice, including "leave empty", is never overwritten.
    const own: Choices = { berlin: "empty", kyiv: pick("4") };
    expect(withRecommendations(review, own)).toEqual(own);
    expect(pendingRecommendations(review, own)).toBe(0);
});

it("sends only explicit decisions and needs at least one chosen candidate", () => {
    expect(decisions(review, {})).toEqual([]);
    expect(hasCandidateChoice(review, { berlin: "empty" })).toBe(false);
    const choices: Choices = { berlin: "empty", kyiv: pick("4") };
    expect(decisions(review, choices)).toEqual([
        { operation_id: "berlin" },
        { operation_id: "kyiv", source: "open-meteo", entity_id: "4" },
    ]);
    expect(hasCandidateChoice(review, choices)).toBe(true);
    expect(hasCandidateChoice(null, choices)).toBe(false);
});

it("sends another name for a name with or without candidates, and it is enough to resume", () => {
    const unmatched = { ...item("oslo", []), other_name: true };
    const named: Review = {
        ...review,
        items: review.items.map((entry) => ({ ...entry, other_name: true })),
        informational: [unmatched, item("bergen", [])],
    };
    const choices: Choices = {
        kyiv: { name: "Kiev" },
        oslo: { name: "Kristiania" },
        // Not offered for this name: nothing is sent, and it does not survive.
        bergen: { name: "Bjørgvin" },
    };
    expect(decisions(named, choices)).toEqual([
        { operation_id: "kyiv", name: "Kiev" },
        { operation_id: "oslo", name: "Kristiania" },
        { operation_id: "bergen", name: "Bjørgvin" },
    ]);
    expect(validChoices(named, choices)).toEqual({
        kyiv: { name: "Kiev" },
        oslo: { name: "Kristiania" },
    });
    expect(hasCandidateChoice(named, { oslo: { name: "Kristiania" } })).toBe(
        true,
    );
    // A given name is a choice: a recommendation does not replace it.
    expect(
        withRecommendations(named, { berlin: { name: "Berlin (Spree)" } })
            .berlin,
    ).toEqual({ name: "Berlin (Spree)" });
});

it("takes another name only when it can help", () => {
    const row = item("München", []);
    expect(otherName(row, "  Munich  ")).toBe("Munich");
    expect(otherName(row, "Frankfurt   am  Main")).toBe("Frankfurt am Main");
    expect(otherName(row, " münchen ")).toBeNull();
    expect(otherName(row, "   ")).toBeNull();
    expect(otherName(row, "M".repeat(201))).toBeNull();
});

it("drops choices the current review does not offer", () => {
    expect(
        validChoices(review, {
            berlin: pick("2"),
            kyiv: pick("999"),
            paris: pick("5"), // Listed, but not selectable.
            gone: "empty",
        }),
    ).toEqual({ berlin: pick("2") });
    expect(validChoices(null, { berlin: "empty" })).toEqual({});
});

it("lists affected rows once, in order, and falls back to record IDs", () => {
    expect(rowList(item("x", [], [6, 2, 6]))).toBe("2, 6");
    expect(rowList(item("x", [], [null]))).toBe("Sheet1!x0");
});

it("localizes country codes and links to an independent page per source", () => {
    expect(countryName("UA", "en")).toBe("Ukraine");
    expect(countryName("NP/CN", "de")).toBe("Nepal / China");
    expect(countryName("??", "en")).toBe("??");
    expect(countryName(undefined, "en")).toBeNull();
    expect(sourceLink(candidate("1"))?.href).toBe(
        "https://www.openstreetmap.org/?mlat=52.52&mlon=13.4#map=8/52.52/13.4",
    );
    expect(
        sourceLink(candidate("Q513", { source: "wikidata", attributes: {} })),
    ).toEqual({ kind: "wikidata", href: "https://www.wikidata.org/wiki/Q513" });
    expect(sourceLink(candidate("1", { attributes: {} }))).toBeNull();
});
