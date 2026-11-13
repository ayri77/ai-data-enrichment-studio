import { useEffect, useState } from "react";
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
};

export function useLimits(): Limits {
    const [limits, setLimits] = useState(DEMO_LIMITS);
    useEffect(() => {
        let active = true;
        void (async () => {
            try {
                const stated = await api.limits();
                if (active && stated)
                    setLimits({
                        maxRows: stated.max_run_rows,
                        maxTimeout: stated.max_timeout_seconds,
                        maxTableRows:
                            stated.max_table_rows ?? DEMO_LIMITS.maxTableRows,
                        maxTableCells:
                            stated.max_table_cells ?? DEMO_LIMITS.maxTableCells,
                        capabilities:
                            stated.capabilities ?? DEMO_LIMITS.capabilities,
                    });
            } catch {
                // The demo's bounds stay; the server still checks every value.
            }
        })();
        return () => {
            active = false;
        };
    }, []);
    return limits;
}
