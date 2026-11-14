import type { AuditReport, EnrichmentPlan, Job, Review } from "./api";
import { resultRows } from "./resultsModel";

/**
 * A guess at the time left, from the pace so far. Rows differ (a repeated name
 * costs nothing, a rare one several requests), so nothing is offered before
 * two rows are done, and the value is rounded up to five seconds: it is an
 * estimate and should look like one.
 */
export function remainingSeconds(
    elapsed: number,
    done: number,
    total: number,
): number | null {
    if (done < 2 || done >= total || elapsed <= 0) return null;
    return Math.ceil((elapsed * (total - done)) / done / 5) * 5;
}

export interface Outcome {
    /** Locale key under `exec.outcome.*`. */
    key: string;
    values: Record<string, number>;
    /** Further sentences, each a locale key with its values. */
    more: { key: string; values: Record<string, number> }[];
}

/**
 * What a finished run amounts to, in numbers a person can check on Results:
 * the filled count is taken from the same rows that screen lists, and falls
 * back to the job's own counters only until the report has arrived.
 */
export function runOutcome(
    job: Job,
    report: AuditReport | null,
    plan: EnrichmentPlan | null,
    review: Review | null,
): Outcome | null {
    const reached = job.progress
        ? [
              {
                  key: "reached",
                  values: {
                      done: job.progress.rows_done,
                      total: job.progress.rows_total,
                  },
              },
          ]
        : [];
    switch (job.status) {
        case "failed":
            return { key: "failed", values: {}, more: [] };
        case "cancelled":
            return { key: "cancelled", values: {}, more: reached };
        case "timed_out":
            return {
                key: "timed_out",
                values: { limit: job.timeout_seconds },
                more: [...reached, { key: "fewer", values: {} }],
            };
        case "completed":
        case "needs_review":
            break;
        default:
            return null;
    }
    const rows = report?.job_id === job.job_id ? resultRows(report, plan) : [];
    const total = rows.length || (job.metrics?.total_rows ?? 0);
    const filled = rows.length
        ? rows.filter((row) => row.filled).length
        : (job.metrics?.successful_rows ?? 0);
    const key = filled >= total ? "all" : filled === 0 ? "none" : "some";
    // Until the review has arrived nothing is said about the remaining rows:
    // a sentence that changes a second later reads as a mistake.
    const settled = review?.job_id === job.job_id ? review : null;
    // Names without a match that the source may know under another name.
    const searchable = (settled?.informational ?? []).filter(
        (item) => item.other_name,
    ).length;
    return {
        key,
        values: { filled, total },
        more:
            filled >= total || job.status !== "needs_review" || !settled
                ? []
                : [
                      settled.items.length > 0
                          ? {
                                key: "choices",
                                values: { count: settled.items.length },
                            }
                          : { key: "unconfirmed", values: {} },
                      ...(searchable
                          ? [
                                {
                                    key: "searchable",
                                    values: { count: searchable },
                                },
                            ]
                          : []),
                  ],
    };
}

/** What one step of the plan did over the rows of a finished run. */
export interface StepOutcome {
    /** Rows the run took. */
    total: number;
    /** Rows for which the step returned a value. */
    filled: number;
    /** Rows for which the step itself gave no value. */
    failed: number;
    /** Rows in which it was not reached: a step it needs gave no value. */
    skipped: number;
    /** Rows whose value follows a match the user chose during review. */
    chosen: number;
    /** The step's own failures by code, the most frequent first. */
    codes: { code: string; count: number }[];
    /** Where the returned values came from, as their evidence names it. */
    sources: string[];
}

/**
 * How many rows passed each step, read from the audit of the run. Nothing is
 * estimated: a row counts for a step only if the audit holds that step's
 * result for it, and a job without an audit has no outcomes at all.
 */
export function stepOutcomes(
    job: Job | null,
    report: AuditReport | null,
    plan: EnrichmentPlan | null,
): Map<string, StepOutcome> {
    const outcomes = new Map<string, StepOutcome>();
    const records = report?.execution?.records;
    if (!job || !plan || !records || report.job_id !== job.job_id)
        return outcomes;
    const chosen = new Map(
        (report.rows ?? []).map((row) => [
            row.record_id,
            row.user_chosen_steps ?? [],
        ]),
    );
    const codes = new Map<string, Map<string, number>>();
    for (const step of plan.steps)
        outcomes.set(step.id, {
            total: records.length,
            filled: 0,
            failed: 0,
            skipped: 0,
            chosen: 0,
            codes: [],
            sources: [],
        });
    for (const record of records)
        for (const step of record.row_result.steps) {
            const outcome = outcomes.get(step.step_id);
            if (!outcome) continue;
            if (step.result.status === "success") {
                outcome.filled += 1;
                if (chosen.get(record.record_id)?.includes(step.step_id))
                    outcome.chosen += 1;
                for (const evidence of step.result.evidence ?? [])
                    if (!outcome.sources.includes(evidence.source))
                        outcome.sources.push(evidence.source);
            } else if (step.result.error_code === "DEPENDENCY_FAILED") {
                outcome.skipped += 1;
            } else {
                outcome.failed += 1;
                const code = step.result.error_code ?? "UNKNOWN";
                const counted =
                    codes.get(step.step_id) ?? new Map<string, number>();
                counted.set(code, (counted.get(code) ?? 0) + 1);
                codes.set(step.step_id, counted);
            }
        }
    for (const [id, counted] of codes)
        outcomes.get(id)!.codes = [...counted]
            .map(([code, count]) => ({ code, count }))
            .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code));
    return outcomes;
}

/**
 * What each step has done over the rows finished so far, read from the job's
 * own progress while the run is going (or when it stopped before an audit
 * existed). The same shape as `stepOutcomes`, with what only the audit knows
 * left empty: the user's choices, the failure codes and the sources.
 */
export function liveStepOutcomes(
    job: Job | null,
    plan: EnrichmentPlan | null,
): Map<string, StepOutcome> {
    const outcomes = new Map<string, StepOutcome>();
    const progress = job?.progress;
    if (!plan || !progress?.steps || progress.rows_done < 1) return outcomes;
    for (const step of plan.steps) {
        const counts = progress.steps[step.id];
        if (!counts) continue;
        outcomes.set(step.id, {
            total: progress.rows_done,
            filled: counts.filled,
            failed: counts.failed,
            skipped: counts.skipped,
            chosen: 0,
            codes: [],
            sources: [],
        });
    }
    return outcomes;
}
