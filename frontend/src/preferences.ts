export type Theme = "dark" | "light";
export const locales = ["en", "uk", "de"] as const;
export type Locale = (typeof locales)[number];
export function readPreference(key: string): string | null {
    try {
        return localStorage.getItem(`enrichment.${key}`);
    } catch {
        return null;
    }
}
export function savePreference(key: string, value: string) {
    try {
        localStorage.setItem(`enrichment.${key}`, value);
    } catch {
        /* Preferences remain usable when storage is blocked. */
    }
}
export function initialLocale(): Locale {
    const stored = readPreference("language");
    return locales.includes(stored as Locale) ? (stored as Locale) : "en";
}
export function initialTheme(): Theme {
    return readPreference("theme") === "light" ? "light" : "dark";
}
// Basic hides everything a person filling a table does not need; Advanced
// shows the plan internals, limits and diagnostics as well.
export type Mode = "basic" | "advanced";
export function initialMode(): Mode {
    return readPreference("mode") === "advanced" ? "advanced" : "basic";
}
