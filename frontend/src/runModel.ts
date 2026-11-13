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
