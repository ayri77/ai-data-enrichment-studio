import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, WorkspaceApiError, type UsageSummary } from "../api";
import { initialTheme } from "../preferences";
import { Button } from "./ui/button";

const NUMBER = new Intl.NumberFormat();
const time = (value: string | null, language: string) =>
    value
        ? new Date(value).toLocaleString(language, {
              dateStyle: "short",
              timeStyle: "medium",
          })
        : "—";
// What an event carried besides who, when and what: numbers and fixed words.
function details(event: UsageSummary["recent"][number]) {
    return Object.entries(event)
        .filter(([key]) => !["at", "who", "kind"].includes(key))
        .map(
            ([key, value]) =>
                `${key}=${
                    value !== null && typeof value === "object"
                        ? JSON.stringify(value)
                        : String(value)
                }`,
        )
        .join(" · ");
}

/**
 * Who used a hosted demo and for what. Opened at `#usage` by the holder of the
 * admin code; the code stays in this page's memory and is asked for again
 * after a reload.
 */
export function UsagePage() {
    const { t, i18n } = useTranslation();
    const language = i18n.resolvedLanguage ?? "en";
    const [code, setCode] = useState("");
    const [summary, setSummary] = useState<UsageSummary | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        document.documentElement.dataset.theme = initialTheme();
        document.documentElement.lang = language;
        document.title = `${t("usage.title")} · AI Data Enrichment Studio`;
    }, [language, t]);
    async function load(event?: FormEvent) {
        event?.preventDefault();
        if (!code.trim() || busy) return;
        setBusy(true);
        setError("");
        try {
            setSummary(await api.usage(code.trim()));
        } catch (failure) {
            const known = ["admin_required", "access_throttled", "not_found"];
            setError(
                failure instanceof WorkspaceApiError &&
                    known.includes(failure.code)
                    ? failure.code
                    : "network_error",
            );
            setSummary(null);
        }
        setBusy(false);
    }
    if (!summary)
        return (
            <main className="access-gate">
                <form onSubmit={load}>
                    <h1>{t("usage.title")}</h1>
                    <p>{t("usage.hint")}</p>
                    <label>
                        {t("usage.code")}
                        <input
                            type="password"
                            autoComplete="off"
                            autoFocus
                            maxLength={128}
                            value={code}
                            onChange={(e) => setCode(e.target.value)}
                        />
                    </label>
                    {error && <p role="alert">{t(`error.${error}`)}</p>}
                    <Button type="submit" disabled={!code.trim() || busy}>
                        {t("usage.show")}
                    </Button>
                    <a href="/">{t("usage.back")}</a>
                </form>
            </main>
        );
    const seen = new Set(summary.visitors.map((visitor) => visitor.who));
    const absent = summary.labels.filter((label) => !seen.has(label));
    const sources = (requests: Record<string, number>) =>
        Object.entries(requests)
            .map(([source, count]) => `${source} ${NUMBER.format(count)}`)
            .join(" · ") || "—";
    return (
        <main className="usage-page">
            <header>
                <h1>{t("usage.title")}</h1>
                <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => load()}
                >
                    {t("usage.refresh")}
                </Button>
                <a href="/">{t("usage.back")}</a>
            </header>
            <p className="muted">
                {t("usage.since", {
                    started: time(summary.started_at, language),
                    now: time(summary.now, language),
                })}
            </p>
            <p className="muted">{t("usage.restart")}</p>
            {error && <p role="alert">{t(`error.${error}`)}</p>}
            <h2>{t("usage.visitors")}</h2>
            <div className="usage-scroll">
                <table data-testid="usage-visitors">
                    <thead>
                        <tr>
                            {[
                                "who",
                                "lastSeen",
                                "visits",
                                "examples",
                                "uploads",
                                "plans",
                                "runs",
                                "rows",
                                "downloads",
                                "model",
                                "tokens",
                                "sources",
                            ].map((column) => (
                                <th key={column}>{t(`usage.col.${column}`)}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {summary.visitors.map((visitor) => (
                            <tr key={visitor.who}>
                                <th scope="row">{visitor.who}</th>
                                <td>{time(visitor.last_seen, language)}</td>
                                <td>{visitor.visits}</td>
                                <td>{visitor.examples}</td>
                                <td>{visitor.uploads}</td>
                                <td>
                                    {visitor.plans}
                                    {visitor.plans_failed > 0 &&
                                        ` (${t("usage.failed", { n: visitor.plans_failed })})`}
                                </td>
                                <td>{visitor.runs}</td>
                                <td>
                                    {visitor.rows_filled} / {visitor.rows}
                                </td>
                                <td>{visitor.downloads}</td>
                                <td>{visitor.model_requests}</td>
                                <td>
                                    {NUMBER.format(visitor.input_tokens)} /{" "}
                                    {NUMBER.format(visitor.output_tokens)}
                                </td>
                                <td>{sources(visitor.source_requests)}</td>
                            </tr>
                        ))}
                        {absent.map((label) => (
                            <tr key={label} className="muted">
                                <th scope="row">{label}</th>
                                <td colSpan={11}>{t("usage.notYet")}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <h2>{t("usage.events")}</h2>
            <p className="usage-kinds" data-testid="usage-kinds">
                {Object.entries(summary.events)
                    .map(([kind, count]) => `${kind} ${count}`)
                    .join(" · ") || "—"}
            </p>
            <h2>{t("usage.recent")}</h2>
            <div className="usage-scroll">
                <table data-testid="usage-recent">
                    <thead>
                        <tr>
                            <th>{t("usage.col.at")}</th>
                            <th>{t("usage.col.who")}</th>
                            <th>{t("usage.col.kind")}</th>
                            <th>{t("usage.col.details")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {summary.recent.map((event, index) => (
                            <tr key={index}>
                                <td>{time(event.at, language)}</td>
                                <td>{event.who}</td>
                                <td>{event.kind}</td>
                                <td>{details(event)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </main>
    );
}
