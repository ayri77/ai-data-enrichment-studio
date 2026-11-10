import { afterEach, describe, expect, it, vi } from "vitest";
import { api, WorkspaceApiError } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("API error contract", () => {
    it("binds approval to the displayed fingerprint and sends bounded run settings", async () => {
        const fetcher = vi
            .fn()
            .mockImplementation(async () => new Response("{}"));
        vi.stubGlobal("fetch", fetcher);
        await api.approvePlan("plan", "a".repeat(64));
        expect(fetcher).toHaveBeenLastCalledWith(
            "/api/plans/plan/approve",
            expect.objectContaining({
                body: JSON.stringify({
                    approved: true,
                    fingerprint: "a".repeat(64),
                }),
            }),
        );
        await api.startRun("plan", true, 180);
        expect(fetcher).toHaveBeenLastCalledWith(
            "/api/plans/plan/runs",
            expect.objectContaining({
                body: JSON.stringify({
                    allow_network: true,
                    timeout_seconds: 180,
                }),
            }),
        );
    });
    it("downloads binary XLSX bytes without parsing them as JSON", async () => {
        const bytes = new Uint8Array([80, 75, 3, 4, 0, 255]);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(bytes)));
        const blob = await api.artifact("job", "result");
        const buffer = await new Promise<ArrayBuffer>((done) => {
            const reader = new FileReader();
            reader.onload = () => done(reader.result as ArrayBuffer);
            reader.readAsArrayBuffer(blob);
        });
        expect(new Uint8Array(buffer)).toEqual(bytes);
    });
    it("does not download an API error as a workbook", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue(
                new Response(
                    JSON.stringify({
                        error: {
                            code: "artifact_not_ready",
                            message: "Not published",
                        },
                    }),
                    { status: 409 },
                ),
            ),
        );
        await expect(api.artifact("job", "result")).rejects.toMatchObject({
            code: "artifact_not_ready",
            status: 409,
        });
    });
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
