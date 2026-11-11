import { createContext, useContext, type ReactNode } from "react";
import type { Mode } from "./preferences";

export const ModeContext = createContext<Mode>("basic");
export function useAdvanced() {
    return useContext(ModeContext) === "advanced";
}
/** Renders its children in Advanced mode only. */
export function Advanced({ children }: { children: ReactNode }) {
    return useAdvanced() ? <>{children}</> : null;
}

// True on a hosted demo (the server asks for an access code): the provider is
// configured on the server, and instructions for a local start do not apply.
export const HostedContext = createContext(false);
export function useHosted() {
    return useContext(HostedContext);
}
