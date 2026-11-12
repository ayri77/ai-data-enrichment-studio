import { expect, it } from "vitest";
import type { AuditReport, EnrichmentPlan, Job, Review } from "./api";
import { remainingSeconds, runOutcome } from "./runModel";

const job: Job = {
    job_id: "job",
    plan_id: "plan",
    fingerprint: "a".repeat(64),
    status: "needs_review",
    created_at: "2026-11-10T09:00:00Z",
    started_at: "2026-11-10T09:00:00Z",
    finished_at: "2026-11-10T09:00:10Z",
    error: null,
    metrics: { total_rows: 4, successful_rows: 1 },
    progress: { rows_done: 4, rows_total: 4 },
    artifacts: { result: "/r" },
    execution_mode: "live",
    timeout_seconds: 180,
};
const plan: EnrichmentPlan = {
    version: "1.0",
    steps: [{ id: "d", capability: "x", inputs: {}, parameters: {} }],
    outputs: [{ step_id: "d", value_field: "value", target_column: "km" }],
    assumptions: [],
};
const step = (ok: boolean) => ({
    step_id: "d",
    result: {
        status: ok ? "success" : "failure",
        value: ok ? { value: 12, unit: "km" } : null,
        error_code: ok ? null : "NOT_FOUND",
        message: "",
        evidence: [],
    },
});
const report = (...filled: boolean[]) =>
    ({
        ...job,
        source_sha256: "b".repeat(64),
        rows: filled.map((_, i) => ({ record_id: `r${i}`, status: "x" })),
        execution: {
            provider_requests: {},
            records: filled.map((ok, i) => ({
                record_id: `r${i}`,
                row_result: { steps: [step(ok)] },
            })),
        },
    }) as unknown as AuditReport;
const review = (items: number) =>
    ({
        job_id: "job",
        previous_job_id: null,
        affected_rows: items,
        items: Array.from({ length: items }, () => ({})),
        informational: [],
    }) as unknown as Review;

it("offers a time left only once two rows show the pace, rounded up to five seconds", () => {
    expect(remainingSeconds(10, 0, 10)).toBeNull();
    expect(remainingSeconds(10, 1, 10)).toBeNull();
    expect(remainingSeconds(0, 5, 10)).toBeNull();
    expect(remainingSeconds(10, 10, 10)).toBeNull();
    // 6 s for 2 rows: 8 rows more are 24 s, shown as 25.
    expect(remainingSeconds(6, 2, 10)).toBe(25);
    expect(remainingSeconds(27, 9, 10)).toBe(5);
});

it("counts filled rows the way the Results screen does", () => {
    // The job's own counter says 1; the rows of the report say 2 of 3.
    expect(runOutcome(job, report(true, false, true), plan, review(2))).toEqual(
        {
            key: "some",
            values: { filled: 2, total: 3 },
            more: [{ key: "choices", values: { count: 2 } }],
        },
    );
    // Nothing to choose: the rest simply stayed empty.
    expect(
        runOutcome(job, report(false, false), plan, review(0)),
    ).toMatchObject({ key: "none", more: [{ key: "unconfirmed" }] });
    expect(
        runOutcome(
            { ...job, status: "completed" },
            report(true, true),
            plan,
            null,
        ),
    ).toEqual({ key: "all", values: { filled: 2, total: 2 }, more: [] });
});

it("falls back to the job's counters until its own report and review arrive", () => {
    const other = { ...report(true, true, true), job_id: "earlier" };
    expect(
        runOutcome(job, other, plan, { ...review(5), job_id: "earlier" }),
    ).toEqual({ key: "some", values: { filled: 1, total: 4 }, more: [] });
    expect(runOutcome(job, null, plan, null)).toMatchObject({
        values: { filled: 1, total: 4 },
    });
});

it("says how a run without a result ended and how far it got", () => {
    const stopped = {
        ...job,
        artifacts: {},
        metrics: null,
        progress: { rows_done: 3, rows_total: 10 },
    };
    expect(
        runOutcome({ ...stopped, status: "timed_out" }, null, plan, null),
    ).toEqual({
        key: "timed_out",
        values: { limit: 180 },
        more: [
            { key: "reached", values: { done: 3, total: 10 } },
            { key: "fewer", values: {} },
        ],
    });
    expect(
        runOutcome(
            { ...stopped, status: "cancelled", progress: null },
            null,
            plan,
            null,
        ),
    ).toEqual({ key: "cancelled", values: {}, more: [] });
    expect(
        runOutcome({ ...stopped, status: "failed" }, null, plan, null),
    ).toEqual({ key: "failed", values: {}, more: [] });
    // A run that is still going has no outcome.
    expect(
        runOutcome({ ...stopped, status: "running" }, null, plan, null),
    ).toBeNull();
});
