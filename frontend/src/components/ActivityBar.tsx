import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { useDataset } from "../useDataset";
import type { Workflow } from "../useWorkflow";

/** Whole seconds since `since`, refreshed once a second while it is set. */
function useElapsed(since: number | null) {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (since === null) return;
        setNow(Date.now());
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, [since]);
    return since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}
export const clock = (seconds: number) =>
    `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/**
 * One line, on every screen, that says what the studio is waiting for, for how
 * long already, and what cannot be changed meanwhile. Short requests come
 * first; a running job shows whenever nothing else is in flight.
 */
export function ActivityBar({
    w,
    d,
}: {
    w: Workflow;
    d: ReturnType<typeof useDataset>;
}) {
    const { t } = useTranslation();
    const running = !w.operation && !d.busy && w.active;
    const since =
        w.operation?.since ??
        (d.busy ? d.busySince : null) ??
        (running ? w.jobSince : null);
    const elapsed = useElapsed(since);
    const kind = w.operation?.kind ?? d.busy ?? (running ? w.job!.status : "");
    if (!kind) return null;
    const detail = t(`activity.${kind}Detail`, {
        defaultValue: "",
        rows: d.preview?.profile.row_count ?? "",
        limit: w.job?.timeout_seconds ?? "",
    });
    return (
        <div className="activity-bar" data-activity={kind}>
            <LoaderCircle className="spin" aria-hidden="true" />
            <p role="status">
                <strong>{t(`activity.${kind}`)}</strong>
                {detail && <span>{detail}</span>}
                {running && w.workspace !== "execution" && (
                    <span>{t("activity.locked")}</span>
                )}
            </p>
            {running && w.workspace !== "execution" && (
                <button
                    type="button"
                    className="inline-link"
                    onClick={() => w.navigate("execution")}
                >
                    {t("activity.open")}
                </button>
            )}
            {/* A timer is not announced on every tick, unlike a status. */}
            <span
                className="activity-time mono"
                role="timer"
                aria-label={t("activity.elapsed")}
            >
                {clock(elapsed)}
            </span>
            <span className="activity-track" aria-hidden="true" />
        </div>
    );
}
