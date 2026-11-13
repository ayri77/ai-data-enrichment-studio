import type {
    Review,
    ReviewCandidate,
    ReviewDecision,
    ReviewItem,
    ReviewValue,
} from "./api";

/**
 * A chosen candidate, or an explicit "leave this query empty". A candidate
 * found by a search under another name carries that name.
 */
export type Choice =
    { source: string; entity_id: string; name?: string } | "empty";
export type Choices = Record<string, Choice>;
/** What the latest search under another name found, by operation. */
export type Searches = Record<string, ReviewItem>;

export const candidateKey = (c: { source: string; entity_id: string }) =>
    `${c.source}:${c.entity_id}`;
export const isCandidate = (
    choice: Choice | undefined,
): choice is { source: string; entity_id: string; name?: string } =>
    !!choice && choice !== "empty";
/** A candidate that a search under another name found. */
export const isSearched = (
    choice: Choice | undefined,
): choice is { source: string; entity_id: string; name: string } =>
    isCandidate(choice) && choice.name !== undefined;

const spelled = (text: string) => text.trim().replace(/\s+/g, " ");
/** The longest name the field accepts; the server allows more. */
export const MAX_OTHER_NAME = 200;
/**
 * The name as it will be sent, or null when it cannot help: blank, too long,
 * or the row's own value again.
 */
export function otherName(item: ReviewItem, text: string) {
    const name = spelled(text);
    const same =
        name.toLocaleLowerCase() ===
        spelled(item.query.name).toLocaleLowerCase();
    return name && name.length <= MAX_OTHER_NAME && !same ? name : null;
}
/** The candidates of a search that can be chosen. */
export const foundCandidates = (found: ReviewItem | undefined) =>
    (found?.candidates ?? []).filter((candidate) => candidate.selectable);
/** The candidate a choice stands for, among the listed or the searched ones. */
export function pickedCandidate(
    item: ReviewItem,
    choice: Choice | undefined,
    found: ReviewItem | undefined,
) {
    if (!isCandidate(choice)) return undefined;
    const from = isSearched(choice)
        ? found?.query.searched_as === choice.name
            ? found.candidates
            : []
        : item.candidates;
    return from.find(
        (candidate) => candidateKey(candidate) === candidateKey(choice),
    );
}

/** Drop choices that the current review no longer offers. */
export function validChoices(review: Review | null, choices: Choices): Choices {
    const next: Choices = {};
    for (const item of review?.items ?? []) {
        const choice = choices[item.operation_id];
        if (
            choice === "empty" ||
            // What a search found is checked by the server when it is sent.
            (isSearched(choice) && item.other_name) ||
            (isCandidate(choice) &&
                !isSearched(choice) &&
                item.candidates.some(
                    (c) =>
                        c.selectable &&
                        candidateKey(c) === candidateKey(choice),
                ))
        )
            next[item.operation_id] = choice;
    }
    // A name with no match offers nothing to choose but what a search finds.
    for (const item of review?.informational ?? []) {
        const choice = choices[item.operation_id];
        if (isSearched(choice) && item.other_name)
            next[item.operation_id] = choice;
    }
    return next;
}
export const recommendation = (item: ReviewItem) =>
    item.candidates.find((c) => c.recommended && c.selectable);

/** Fill only undecided queries; an explicit choice is never overwritten. */
export function withRecommendations(review: Review, choices: Choices): Choices {
    const next = { ...choices };
    for (const item of review.items) {
        const suggested = recommendation(item);
        if (suggested && !next[item.operation_id])
            next[item.operation_id] = {
                source: suggested.source,
                entity_id: suggested.entity_id,
            };
    }
    return next;
}
export function pendingRecommendations(
    review: Review | null,
    choices: Choices,
) {
    return (review?.items ?? []).filter(
        (item) => recommendation(item) && !choices[item.operation_id],
    ).length;
}
/** Undecided queries are omitted: the backend leaves them as they were. */
export function decisions(review: Review, choices: Choices): ReviewDecision[] {
    const chosen = review.items.flatMap((item) => {
        const choice = choices[item.operation_id];
        if (!choice) return [];
        return choice === "empty"
            ? [{ operation_id: item.operation_id }]
            : [{ operation_id: item.operation_id, ...choice }];
    });
    const searched = review.informational.flatMap((item) => {
        const choice = choices[item.operation_id];
        return isSearched(choice)
            ? [{ operation_id: item.operation_id, ...choice }]
            : [];
    });
    return [...chosen, ...searched];
}
/** A resume needs at least one chosen candidate; "leave empty" alone changes nothing. */
export const hasCandidateChoice = (review: Review | null, choices: Choices) =>
    !!review && decisions(review, choices).some((d) => d.entity_id);

export function rowList(item: ReviewItem) {
    const rows = [
        ...new Set(item.affected.map((a) => a.row ?? a.record_id)),
    ].sort((a, b) =>
        typeof a === "number" && typeof b === "number"
            ? a - b
            : String(a).localeCompare(String(b)),
    );
    return rows.join(", ");
}
export function countryName(code: ReviewValue | undefined, locale: string) {
    if (typeof code !== "string") return null;
    return code
        .split("/")
        .map((part) => {
            try {
                return (
                    new Intl.DisplayNames([locale], { type: "region" }).of(
                        part.toUpperCase(),
                    ) ?? part
                );
            } catch {
                return part;
            }
        })
        .join(" / ");
}
/** Link to an independent page that lets a person tell two namesakes apart. */
/** Where a candidate is, when the source recorded usable coordinates. */
export function placeOf(candidate: ReviewCandidate) {
    const { latitude, longitude } = candidate.attributes;
    return typeof latitude === "number" &&
        typeof longitude === "number" &&
        Math.abs(latitude) <= 90 &&
        Math.abs(longitude) <= 180
        ? { latitude, longitude }
        : null;
}
/** Half the width of the map, in degrees of longitude: close, regional, wide. */
export const MAP_SPANS = [0.06, 0.6, 5];
/**
 * OpenStreetMap's own embeddable map with a marker on the place. Coordinates
 * are numbers from the source, rounded; nothing else goes into the address.
 */
export function mapEmbedUrl(
    place: { latitude: number; longitude: number },
    span: number,
) {
    const { latitude, longitude } = place;
    const box = [
        longitude - span,
        latitude - span / 2,
        longitude + span,
        latitude + span / 2,
    ]
        .map((value) => value.toFixed(5))
        .join(",");
    return `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(box)}&layer=mapnik&marker=${latitude.toFixed(5)},${longitude.toFixed(5)}`;
}
export function sourceLink(candidate: ReviewCandidate) {
    if (candidate.source === "wikidata" && /^Q\d+$/.test(candidate.entity_id))
        return {
            kind: "wikidata" as const,
            href: `https://www.wikidata.org/wiki/${candidate.entity_id}`,
        };
    const { latitude, longitude } = candidate.attributes;
    if (typeof latitude === "number" && typeof longitude === "number")
        return {
            kind: "map" as const,
            href: `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=8/${latitude}/${longitude}`,
        };
    return null;
}
