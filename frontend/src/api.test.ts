import { afterEach, describe, expect, it, vi } from "vitest";
import { api, WorkspaceApiError } from "./api";

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
        const failure = await api
            .preview("opaque", "Sheet1", "bad")
            .catch((error) => error);
        expect(failure).toBeInstanceOf(WorkspaceApiError);
        expect(failure).toMatchObject({
            message: "Check the range.",
            code: "invalid_selection",
            status: 422,
        });
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
    it("keeps unknown server codes without presenting them as translated messages", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn(
                async () =>
                    new Response(
                        JSON.stringify({
                            error: {
                                code: "future_error",
                                message: "New server message",
                            },
                        }),
                        { status: 409 },
                    ),
            ),
        );
        const failure = await api
            .preview("opaque", "Sheet1", "A1:B2")
            .catch((error) => error);
        expect(failure).toBeInstanceOf(WorkspaceApiError);
        expect(failure.code).toBe("future_error");
    });
});
