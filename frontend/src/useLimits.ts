import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** What one run accepts. The demo's bounds apply until the server answers. */
export interface Limits {
    /** Data rows one run fills. */
    maxRows: number;
    maxTimeout: number;
    /** Data rows and cells of a table that can be read and looked through. */
    maxTableRows: number;
    maxTableCells: number;
    /** Ids of what a plan may use on this server. */
    capabilities: string[];
    /** The universal search can be asked for on this server. */
    webLookup: boolean;
}
export const DEMO_LIMITS: Limits = {
    maxRows: 20,
    maxTimeout: 300,
    maxTableRows: 5000,
    maxTableCells: 100_000,
    // Until the server says otherwise, nothing that needs a configured source.
    capabilities: [
        "geocode",
        "geodesic_distance",
        "resolve_entity",
        "lookup_property",
    ],
    webLookup: false,
};

const sameLimits = (a: Limits, b: Limits) =>
    a.maxRows === b.maxRows &&
    a.maxTimeout === b.maxTimeout &&
    a.maxTableRows === b.maxTableRows &&
    a.maxTableCells === b.maxTableCells &&
    a.webLookup === b.webLookup &&
    a.capabilities.length === b.capabilities.length &&
    a.capabilities.every((id, i) => id === b.capabilities[i]);

/**
 * What the server accepts, read when the page opens and again on request
 * (`refresh`): the server's answer depends on settings a person can change
 * meanwhile, such as the model key. The last known value stays on an error,
 * an unchanged answer keeps the same object (no re-render), and an answer that
 * arrives after a newer request, or after unmount, is dropped.
 */
export function useLimits(): { limits: Limits; refresh: () => Promise<void> } {
    const [limits, setLimits] = useState(DEMO_LIMITS);
    const latest = useRef(0);
    const mounted = useRef(true);
    const refresh = useCallback(async () => {
        const request = ++latest.current;
        try {
            const stated = await api.limits();
            if (!mounted.current || request !== latest.current || !stated)
                return;
            const next: Limits = {
                maxRows: stated.max_run_rows,
                maxTimeout: stated.max_timeout_seconds,
                maxTableRows: stated.max_table_rows ?? DEMO_LIMITS.maxTableRows,
                maxTableCells:
                    stated.max_table_cells ?? DEMO_LIMITS.maxTableCells,
                capabilities: stated.capabilities ?? DEMO_LIMITS.capabilities,
                webLookup: stated.web_lookup === true,
            };
            setLimits((current) =>
                sameLimits(current, next) ? current : next,
            );
        } catch {
            // The last known bounds stay; the server still checks every value.
        }
    }, []);
    useEffect(() => {
        mounted.current = true;
        void refresh();
        return () => {
            mounted.current = false;
        };
    }, [refresh]);
    return { limits, refresh };
}
