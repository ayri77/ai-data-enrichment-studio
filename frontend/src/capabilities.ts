import { createContext, useContext, useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { api, type Capability } from "./api";
import { LOOKUP_CAPABILITY } from "./fallbackModel";

// What a plan may be built from is the server's list, not one kept here: a
// capability added there is shown without a change to the interface. Null
// until the server has answered, and for a server that does not say.
export const CapabilitiesContext = createContext<Capability[] | null>(null);
export function useOffered() {
    return useContext(CapabilitiesContext);
}

// The categories the server lists, in its order; null for a server that does
// not say, and until it has answered.
export const CategoriesContext = createContext<string[] | null>(null);
export function useCategories() {
    return useContext(CategoriesContext);
}

export interface Catalog {
    capabilities: Capability[];
    categories: string[] | null;
}

export function useCatalog(): Catalog | null {
    const [listed, setListed] = useState<Catalog | null>(null);
    useEffect(() => {
        let active = true;
        void (async () => {
            try {
                const stated = await api.capabilities();
                if (active && Array.isArray(stated?.capabilities))
                    setListed({
                        capabilities: stated.capabilities,
                        categories: Array.isArray(stated.categories)
                            ? stated.categories
                            : null,
                    });
            } catch {
                // Without the list the page names what it knows by id.
            }
        })();
        return () => {
            active = false;
        };
    }, []);
    return listed;
}

export function useCapabilities(): Capability[] | null {
    return useCatalog()?.capabilities ?? null;
}

/** The category of the AI web search: the interface's own, never the server's list. */
export const WEB_SEARCH_CATEGORY = "web_search";

export interface CapabilityGroup {
    /** The category id; empty for a list that states none. */
    id: string;
    items: Capability[];
}

/**
 * The server's capabilities grouped by category, in the server's order. A
 * category an item names but the list does not is added after it, in the order
 * first met; an item without one (when others have) goes to "other". A server
 * that states no categories at all gives one group with no id, so that the page
 * shows everything together and no tiles.
 */
export function groupCapabilities(
    items: Capability[],
    categories?: string[] | null,
): CapabilityGroup[] {
    if (!items.some((item) => item.category)) return [{ id: "", items }];
    const order = [...(categories ?? [])];
    const idOf = (item: Capability) => item.category || "other";
    for (const item of items)
        if (!order.includes(idOf(item))) order.push(idOf(item));
    return order
        .map((id) => ({ id, items: items.filter((item) => idOf(item) === id) }))
        .filter((group) => group.items.length > 0);
}

/** "currency_rate" → "Currency rate": a name for an id that has no wording yet. */
export function readable(id: string) {
    const words = id.replaceAll("_", " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The interface's own wording where it has one, the id made readable otherwise. */
export function capabilityTitle(t: TFunction, id: string) {
    return t(`cap.${id}.title`, { defaultValue: readable(id) });
}

/**
 * How the values of a capability were obtained, in one plain sentence. The
 * wording follows the server's kind of trust and the source's name; a kind this
 * interface does not know yet (a later server may send one) is not guessed at,
 * it is named by its source, and a capability the server did not list says
 * nothing.
 */
export function provenanceLabel(
    t: TFunction,
    item: Pick<Capability, "source" | "trust"> | undefined,
) {
    if (!item) return null;
    const source = t(`source.${item.source}`, { defaultValue: item.source });
    switch (item.trust) {
        case "computed":
        case "record":
        case "resolved_name":
        case "service":
        case "ai_found":
        case "extracted":
            return t(`provenance.${item.trust}`, { source });
    }
    // An older server states no kind: a calculation done here still says so.
    if (item.source === "local") return t("provenance.computed");
    return t("overview.source", { source });
}

/**
 * Where the values of a capability come from and what stands behind them. The
 * universal search is not on the server's list of what a plan may be built
 * from (a plan with it is made by the server alone), so its own wording is
 * kept here.
 */
export function capabilityProvenance(
    offered: Capability[] | null | undefined,
    id: string | undefined,
): Pick<Capability, "source" | "trust"> | undefined {
    if (id === LOOKUP_CAPABILITY)
        return { source: "openai_web_search", trust: "ai_found" };
    return offered?.find((item) => item.id === id);
}

/** The names of the outside sources a run may ask, each once, in list order. */
export function sourceNames(t: TFunction, offered: Capability[]) {
    const ids = [...new Set(offered.map((item) => item.source))];
    return ids
        .filter((id) => id !== "local")
        .map((id) => t(`source.${id}`, { defaultValue: id }));
}
