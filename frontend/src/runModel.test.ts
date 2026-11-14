import { expect, it } from "vitest";
import type { AuditReport, EnrichmentPlan, Job, Review } from "./api";
import {
    liveStepOutcomes,
    remainingSeconds,
    runOutcome,
    stepOutcomes,
} from "./runModel";

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
    // Names the source may know under another name are counted, with or
    // without names to choose between.
    const searchable = (items: number) => ({
        ...review(items),
        informational: [
            { other_name: true },
            { other_name: false },
            { other_name: true },
        ],
    });
    expect(
        runOutcome(job, report(false, false), plan, searchable(0) as Review)
            ?.more,
    ).toEqual([
        { key: "unconfirmed", values: {} },
        { key: "searchable", values: { count: 2 } },
    ]);
    expect(
        runOutcome(job, report(true, false), plan, searchable(1) as Review)
            ?.more,
    ).toEqual([
        { key: "choices", values: { count: 1 } },
        { key: "searchable", values: { count: 2 } },
    ]);
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

it("counts for each step the rows that passed it, failed in it or never reached it", () => {
    const branching: EnrichmentPlan = {
        version: "1.0",
        steps: ["from", "to", "straight", "road", "detour"].map((id) => ({
            id,
            capability: "x",
            inputs: {},
            parameters: {},
        })),
        outputs: [],
        assumptions: [],
    };
    const ok = (source: string) => ({
        status: "success",
        value: 1,
        error_code: null,
        message: null,
        evidence: [{ source, reference: "x" }],
    });
    const no = (error_code: string | null) => ({
        status: "failure",
        value: null,
        error_code,
        message: "",
        evidence: [],
    });
    const skipped = no("DEPENDENCY_FAILED");
    // Row 0: everything. Row 1: no route. Row 2: a city needs a choice.
    // Row 3: the routing service did not answer.
    const results = [
        [
            ok("open-meteo"),
            ok("open-meteo"),
            ok("calculation"),
            ok("openrouteservice"),
            ok("calculation"),
        ],
        [
            ok("open-meteo"),
            ok("open-meteo"),
            ok("calculation"),
            no("NO_ROUTE"),
            skipped,
        ],
        [ok("open-meteo"), no("AMBIGUOUS"), skipped, skipped, skipped],
        [
            ok("open-meteo"),
            ok("open-meteo"),
            ok("calculation"),
            no(null),
            skipped,
        ],
    ];
    const audit = {
        ...job,
        rows: results.map((_, i) => ({
            record_id: `r${i}`,
            status: "x",
            user_chosen_steps: i === 0 ? ["from"] : [],
        })),
        execution: {
            provider_requests: {},
            records: results.map((steps, i) => ({
                record_id: `r${i}`,
                row_result: {
                    steps: steps.map((result, at) => ({
                        step_id: branching.steps[at].id,
                        result,
                    })),
                },
            })),
        },
    } as unknown as AuditReport;
    const outcomes = stepOutcomes(job, audit, branching);
    expect([...outcomes.keys()]).toEqual(branching.steps.map((s) => s.id));
    expect(outcomes.get("from")).toEqual({
        total: 4,
        filled: 4,
        failed: 0,
        skipped: 0,
        chosen: 1,
        codes: [],
        sources: ["open-meteo"],
    });
    expect(outcomes.get("to")).toMatchObject({
        filled: 3,
        failed: 1,
        skipped: 0,
        codes: [{ code: "AMBIGUOUS", count: 1 }],
    });
    // The straight-line distance is not reached only where a city is missing.
    expect(outcomes.get("straight")).toMatchObject({ filled: 3, skipped: 1 });
    expect(outcomes.get("road")).toMatchObject({
        filled: 1,
        failed: 2,
        skipped: 1,
        // A failure without a code is still counted, under a name of its own.
        codes: [
            { code: "NO_ROUTE", count: 1 },
            { code: "UNKNOWN", count: 1 },
        ],
        sources: ["openrouteservice"],
    });
    // Every row is accounted for in every step.
    for (const outcome of outcomes.values())
        expect(outcome.filled + outcome.failed + outcome.skipped).toBe(4);
    expect(outcomes.get("detour")).toMatchObject({ filled: 1, skipped: 3 });
});

it("has no step outcomes without the audit of this very run", () => {
    expect(stepOutcomes(job, null, plan).size).toBe(0);
    expect(stepOutcomes(null, report(true), plan).size).toBe(0);
    expect(stepOutcomes(job, report(true), null).size).toBe(0);
    // The audit of an earlier job says nothing about the current one.
    expect(
        stepOutcomes({ ...job, job_id: "later" }, report(true), plan).size,
    ).toBe(0);
    expect(stepOutcomes(job, report(true, false), plan).get("d")).toMatchObject(
        { total: 2, filled: 1, failed: 1 },
    );
});

it("turns the counts of a running job into step outcomes over the rows finished so far", () => {
    const two: EnrichmentPlan = {
        ...plan,
        steps: [
            { id: "d", capability: "x", inputs: {}, parameters: {} },
            { id: "e", capability: "x", inputs: {}, parameters: {} },
        ],
    };
    const running: Job = {
        ...job,
        status: "running",
        progress: {
            rows_done: 4,
            rows_total: 10,
            steps: {
                d: { filled: 2, failed: 1, skipped: 1 },
                e: { filled: 4, failed: 0, skipped: 0 },
                gone: { filled: 1, failed: 1, skipped: 2 },
            },
        },
    };
    const live = liveStepOutcomes(running, two);
    // The plan's steps, in its order; what the audit alone knows stays empty.
    expect([...live.keys()]).toEqual(["d", "e"]);
    expect(live.get("d")).toEqual({
        total: 4,
        filled: 2,
        failed: 1,
        skipped: 1,
        chosen: 0,
        codes: [],
        sources: [],
    });
    expect(live.get("e")).toMatchObject({ total: 4, filled: 4 });
});

it("has no live step outcomes before a row is finished or without counts", () => {
    const counts = { d: { filled: 0, failed: 0, skipped: 0 } };
    const progress = (rows_done: number, steps?: typeof counts) => ({
        ...job,
        status: "running" as const,
        progress: { rows_done, rows_total: 10, steps },
    });
    expect(liveStepOutcomes(progress(0, counts), plan).size).toBe(0);
    expect(liveStepOutcomes(progress(3, {} as typeof counts), plan).size).toBe(
        0,
    );
    expect(liveStepOutcomes(progress(3), plan).size).toBe(0);
    expect(liveStepOutcomes({ ...job, progress: null }, plan).size).toBe(0);
    expect(liveStepOutcomes(null, plan).size).toBe(0);
    expect(liveStepOutcomes(progress(3, counts), null).size).toBe(0);
});

it("agrees with the audit on filled, failed and skipped for a finished run", () => {
    const two: EnrichmentPlan = {
        ...plan,
        steps: [
            { id: "d", capability: "x", inputs: {}, parameters: {} },
            { id: "e", capability: "x", inputs: {}, parameters: {} },
        ],
    };
    const result = (status: string, error_code: string | null) => ({
        status,
        value: status === "success" ? 1 : null,
        error_code,
        message: "",
        evidence: [],
    });
    const rows = [
        [result("success", null), result("success", null)],
        [
            result("failure", "NOT_FOUND"),
            result("failure", "DEPENDENCY_FAILED"),
        ],
        [result("failure", null), result("failure", "DEPENDENCY_FAILED")],
    ];
    const audit = {
        ...job,
        rows: rows.map((_, i) => ({ record_id: `r${i}`, status: "x" })),
        execution: {
            provider_requests: {},
            records: rows.map((steps, i) => ({
                record_id: `r${i}`,
                row_result: {
                    steps: steps.map((r, at) => ({
                        step_id: two.steps[at].id,
                        result: r,
                    })),
                },
            })),
        },
    } as unknown as AuditReport;
    const finished: Job = {
        ...job,
        progress: {
            rows_done: 3,
            rows_total: 3,
            steps: {
                d: { filled: 1, failed: 2, skipped: 0 },
                e: { filled: 1, failed: 0, skipped: 2 },
            },
        },
    };
    const live = liveStepOutcomes(finished, two);
    const audited = stepOutcomes(finished, audit, two);
    for (const id of ["d", "e"])
        expect(live.get(id)).toMatchObject({
            total: audited.get(id)!.total,
            filled: audited.get(id)!.filled,
            failed: audited.get(id)!.failed,
            skipped: audited.get(id)!.skipped,
        });
});
