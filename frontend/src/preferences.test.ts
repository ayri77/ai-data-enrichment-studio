import { afterEach, describe, expect, it, vi } from "vitest";
import {
    initialLocale,
    initialTheme,
    readPreference,
    savePreference,
} from "./preferences";
import { messages } from "./i18n";

afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
});
describe("workspace preferences and resources", () => {
    it("supports exactly three complete locale bundles", () => {
        expect(Object.keys(messages)).toEqual(["en", "uk", "de"]);
        const keys = Object.keys(messages.en).sort();
        expect(Object.keys(messages.uk).sort()).toEqual(keys);
        expect(Object.keys(messages.de).sort()).toEqual(keys);
        for (const resource of Object.values(messages))
            for (const value of Object.values(resource))
                expect(value.trim()).not.toBe("");
    });
    it("defaults to English and dark and restores only supported saved choices", () => {
        localStorage.clear();
        expect(initialLocale()).toBe("en");
        expect(initialTheme()).toBe("dark");
        savePreference("language", "de");
        savePreference("theme", "light");
        expect(initialLocale()).toBe("de");
        expect(initialTheme()).toBe("light");
        savePreference("language", "fr");
        savePreference("theme", "unexpected");
        expect(initialLocale()).toBe("en");
        expect(initialTheme()).toBe("dark");
    });
    it("remains usable when browser storage is blocked", () => {
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        expect(readPreference("language")).toBeNull();
        expect(() => savePreference("theme", "light")).not.toThrow();
        expect(initialLocale()).toBe("en");
        expect(initialTheme()).toBe("dark");
    });
});
