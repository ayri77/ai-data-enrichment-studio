import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("API error contract", () => {
    it("keeps structured validation errors readable", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue(
                new Response(
                    JSON.stringify({
                        error: {
                            code: "invalid_selection",
                            message: "Check the range.",
                        },
                    }),
                    { status: 422 },
                ),
            ),
        );
        await expect(api.preview("opaque", "Sheet1", "bad")).rejects.toThrow(
            "Check the range.",
        );
    });
    it("handles an unavailable backend", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
        );
        await expect(api.upload(new File(["x"], "book.xlsx"))).rejects.toThrow(
            "Cannot reach the local API",
        );
    });
    it("handles non-JSON proxy errors", async () => {
        vi.stubGlobal(
            "fetch",
            vi
                .fn()
                .mockResolvedValue(
                    new Response("proxy error", { status: 502 }),
                ),
        );
        await expect(api.preview("opaque", "Sheet1", "A1:B2")).rejects.toThrow(
            "The request could not be completed",
        );
    });
});
