import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { DEMO_LIMITS, useLimits } from "./useLimits";

vi.mock("./api", async (original) => ({
    ...(await original<typeof import("./api")>()),
    api: { limits: vi.fn() },
}));

const stated = (web_lookup: boolean, max_run_rows = 20) => ({
    max_run_rows,
    max_timeout_seconds: 300,
    default_timeout_seconds: 180,
    web_lookup,
});
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => (resolve = r));
    return { promise, resolve };
};

beforeEach(() => vi.resetAllMocks());

it("reads again on refresh and takes the new answer", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(stated(false));
    vi.mocked(api.limits).mockResolvedValueOnce(stated(true));
    const { result } = renderHook(() => useLimits());
    await waitFor(() => expect(api.limits).toHaveBeenCalledTimes(1));
    await act(() => result.current.refresh());
    expect(result.current.limits.webLookup).toBe(true);
});
it("keeps the last known limits when a refresh fails", async () => {
    vi.mocked(api.limits).mockResolvedValueOnce(stated(true, 50));
    vi.mocked(api.limits).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useLimits());
    await waitFor(() => expect(result.current.limits.maxRows).toBe(50));
    await act(() => result.current.refresh());
    expect(result.current.limits.maxRows).toBe(50);
    expect(result.current.limits.webLookup).toBe(true);
});
it("keeps the same object when the answer did not change", async () => {
    vi.mocked(api.limits).mockResolvedValue(stated(true));
    const { result } = renderHook(() => useLimits());
    await waitFor(() => expect(result.current.limits.webLookup).toBe(true));
    const before = result.current.limits;
    await act(() => result.current.refresh());
    expect(result.current.limits).toBe(before);
});
it("drops an older answer that arrives after a newer one", async () => {
    const first = deferred<ReturnType<typeof stated>>();
    const second = deferred<ReturnType<typeof stated>>();
    vi.mocked(api.limits).mockReturnValueOnce(first.promise);
    vi.mocked(api.limits).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useLimits());
    let refreshed!: Promise<void>;
    act(() => {
        refreshed = result.current.refresh();
    });
    await act(async () => {
        second.resolve(stated(true, 40));
        await refreshed;
    });
    await act(async () => {
        first.resolve(stated(false, 10));
        await Promise.resolve();
    });
    expect(result.current.limits.maxRows).toBe(40);
    expect(result.current.limits.webLookup).toBe(true);
});
it("ignores an answer that arrives after unmount", async () => {
    const late = deferred<ReturnType<typeof stated>>();
    vi.mocked(api.limits).mockReturnValueOnce(late.promise);
    const { result, unmount } = renderHook(() => useLimits());
    unmount();
    late.resolve(stated(true, 99));
    await Promise.resolve();
    expect(result.current.limits).toBe(DEMO_LIMITS);
});
