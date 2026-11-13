import type {
    Review,
    ReviewCandidate,
    ReviewDecision,
    ReviewItem,
    ReviewValue,
} from "./api";

/**
 * A chosen candidate, another name to search the source for, or an explicit
 * "leave this query empty".
 */
export type Choice =
    { source: string; entity_id: string } | { name: string } | "empty";
export type Choices = Record<string, Choice>;

export const candidateKey = (c: { source: string; entity_id: string }) =>
    `${c.source}:${c.entity_id}`;
export const isCandidate = (
    choice: Choice | undefined,
): choice is { source: string; entity_id: string } =>
    !!choice && choice !== "empty" && "entity_id" in choice;
export const isName = (
    choice: Choice | undefined,
): choice is { name: string } =>
    !!choice && choice !== "empty" && "name" in choice;

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

/** Drop choices that the current review no longer offers. */
export function validChoices(review: Review | null, choices: Choices): Choices {
    const next: Choices = {};
    for (const item of review?.items ?? []) {
        const choice = choices[item.operation_id];
        if (
            choice === "empty" ||
            (isName(choice) && item.other_name) ||
            (isCandidate(choice) &&
                item.candidates.some(
                    (c) =>
                        c.selectable &&
                        candidateKey(c) === candidateKey(choice),
                ))
        )
            next[item.operation_id] = choice;
    }
    // A name with no match offers nothing to choose, only another name.
    for (const item of review?.informational ?? []) {
        const choice = choices[item.operation_id];
        if (isName(choice) && item.other_name) next[item.operation_id] = choice;
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
        // A name already given for this row is a choice too.
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
    const named = review.informational.flatMap((item) => {
        const choice = choices[item.operation_id];
        return isName(choice)
            ? [{ operation_id: item.operation_id, name: choice.name }]
            : [];
    });
    return [...chosen, ...named];
}
/**
 * A resume needs at least one chosen candidate or one other name to search
 * for; "leave empty" alone changes nothing.
 */
export const hasCandidateChoice = (review: Review | null, choices: Choices) =>
    !!review && decisions(review, choices).some((d) => d.entity_id || d.name);

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
