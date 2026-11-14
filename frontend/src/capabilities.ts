import { createContext, useContext, useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { api, type Capability } from "./api";

// What a plan may be built from is the server's list, not one kept here: a
// capability added there is shown without a change to the interface. Null
// until the server has answered, and for a server that does not say.
export const CapabilitiesContext = createContext<Capability[] | null>(null);
export function useOffered() {
    return useContext(CapabilitiesContext);
}

export function useCapabilities(): Capability[] | null {
    const [listed, setListed] = useState<Capability[] | null>(null);
    useEffect(() => {
        let active = true;
        void (async () => {
            try {
                const stated = await api.capabilities();
                if (active && Array.isArray(stated?.capabilities))
                    setListed(stated.capabilities);
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

/** "currency_rate" → "Currency rate": a name for an id that has no wording yet. */
export function readable(id: string) {
    const words = id.replaceAll("_", " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The interface's own wording where it has one, the id made readable otherwise. */
export function capabilityTitle(t: TFunction, id: string) {
    return t(`cap.${id}.title`, { defaultValue: readable(id) });
}

/** The names of the outside sources a run may ask, each once, in list order. */
export function sourceNames(t: TFunction, offered: Capability[]) {
    const ids = [...new Set(offered.map((item) => item.source))];
    return ids
        .filter((id) => id !== "local")
        .map((id) => t(`source.${id}`, { defaultValue: id }));
}
