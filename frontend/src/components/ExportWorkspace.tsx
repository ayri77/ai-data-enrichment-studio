import { Fragment, useMemo, useState } from "react";
import {
    ArrowDown,
    ArrowUp,
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    Download,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { isTerminal } from "../api";
import { useAdvanced } from "../mode";
import type { Workflow } from "../useWorkflow";
import {
    columnStats,
    describeValue,
    filterRows,
    formatValue,
    inputColumns,
    outputColumns,
    resultRows,
    sortRows,
    type ResultColumn,
    type ResultRow,
    type RowFilter,
    type Sort,
} from "../resultsModel";
import { Button } from "./ui/button";
import { Back, EmptyState, ModeNotice, StartOver } from "./WorkspaceViews";

function SortHeader({
    label,
    sortKey,
    sort,
    onSort,
}: {
    label: string;
    sortKey: string;
    sort: Sort;
    onSort: (key: string) => void;
}) {
    const { t } = useTranslation();
    const active = sort.key === sortKey;
    return (
        <th
            aria-sort={
                active ? (sort.descending ? "descending" : "ascending") : "none"
            }
        >
            <button
                type="button"
                className="sort-header"
                title={t("results.sortBy", { column: label })}
                onClick={() => onSort(sortKey)}
            >
                {label}
                {active &&
                    (sort.descending ? (
                        <ArrowDown aria-hidden="true" />
                    ) : (
                        <ArrowUp aria-hidden="true" />
                    ))}
            </button>
        </th>
    );
}

/** Why a cell is empty, or which value exists only because the user chose it. */
function Note({ row, w }: { row: ResultRow; w: Workflow }) {
    const { t } = useTranslation();
    const canReview =
        w.job?.status === "needs_review" &&
        w.review?.job_id === w.job.job_id &&
        w.review.items.length > 0;
    return (
        <>
            {row.kept.length > 0 ? (
                <span className="result-kept" data-testid="result-kept">
                    {t("results.kept")}
                </span>
            ) : (
                <span
                    className={
                        row.filled ? "outcome-success" : "outcome-failure"
                    }
                >
                    {t(row.filled ? "results.filled" : "results.empty")}
                </span>
            )}
            {Object.entries(row.replaced).map(([column, old]) => (
                <span
                    key={column}
                    className="result-replaced"
                    data-testid="result-replaced"
                >
                    {t("results.replaced", { old: String(old) })}
                </span>
            ))}
            {row.chosen.length > 0 && (
                <span className="result-chosen">
                    {t("results.chosen", { names: row.chosen.join(", ") })}
                </span>
            )}
            {row.automatic.length > 0 && (
                <span className="result-automatic">
                    {t("results.automatic", {
                        names: row.automatic.join(", "),
                    })}
                </span>
            )}
            {!row.filled && row.kept.length === 0 && row.cause && (
                <span className="result-cause">
                    {t(`results.cause.${row.cause.kind}`, {
                        subject: row.cause.subject ?? t("results.thisRow"),
                        code: row.cause.code,
                    })}
                    {row.cause.kind === "ambiguous" && canReview && (
                        <>
                            {" "}
                            <button
                                type="button"
                                className="inline-link"
                                onClick={() => w.navigate("review")}
                            >
                                {t("results.toReview")}
                            </button>
                        </>
                    )}
                </span>
            )}
        </>
    );
}

/** Second layer: every step of one row with its value and source record. */
function Detail({ row, span }: { row: ResultRow; span: number }) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? "en";
    return (
        <tr className="result-detail" data-advanced="true">
            <td colSpan={span}>
                <table>
                    <thead>
                        <tr>
                            <th>{t("results.detail.step")}</th>
                            <th>{t("results.detail.returned")}</th>
                            <th>{t("results.detail.evidence")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {row.steps.map((step) => (
                            <tr key={step.stepId}>
                                <td className="mono">
                                    {step.stepId}
                                    {step.capability
                                        ? ` · ${step.capability}`
                                        : ""}
                                </td>
                                <td
                                    className={
                                        step.ok
                                            ? "mono"
                                            : "mono outcome-failure"
                                    }
                                >
                                    {step.ok
                                        ? describeValue(step.value, locale)
                                        : (step.errorCode ?? "—")}
                                </td>
                                <td className="mono">
                                    {step.evidence
                                        .map(
                                            (e) => `${e.source} ${e.reference}`,
                                        )
                                        .join(" · ") || "—"}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                <p className="muted mono">
                    {row.recordId} · {t(`workflow.row.${row.status}`)}
                </p>
            </td>
        </tr>
    );
}

function Stats({
    rows,
    columns,
}: {
    rows: ResultRow[];
    columns: ResultColumn[];
}) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? "en";
    return (
        <ul className="result-stats" data-testid="result-stats">
            {columns.map((column) => {
                const stats = columnStats(rows, column.key);
                const number = (value: number) =>
                    formatValue(value, locale, column.unit);
                return (
                    <li key={column.key}>
                        <strong>{column.label}</strong>
                        <span>
                            {t("results.stats.filled", {
                                filled: stats.filled,
                                total: stats.total,
                            })}
                        </span>
                        {stats.min !== undefined && (
                            <>
                                <span>
                                    {t("results.stats.min", {
                                        value: number(stats.min),
                                    })}
                                </span>
                                <span>
                                    {t("results.stats.max", {
                                        value: number(stats.max!),
                                    })}
                                </span>
                                <span>
                                    {t("results.stats.mean", {
                                        value: number(stats.mean!),
                                    })}
                                </span>
                            </>
                        )}
                    </li>
                );
            })}
        </ul>
    );
}

function ResultTable({ w }: { w: Workflow }) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? "en";
    const plan = w.plan?.plan ?? null;
    const rows = useMemo(() => resultRows(w.report, plan), [w.report, plan]);
    const inputs = useMemo(() => inputColumns(w.report), [w.report]);
    const outputs = useMemo(() => outputColumns(plan), [plan]);
    const [filter, setFilter] = useState<RowFilter>("all");
    const [sort, setSort] = useState<Sort>({ key: "row", descending: false });
    const [open, setOpen] = useState<string | null>(null);
    const shown = sortRows(filterRows(rows, filter), sort, locale);
    // The two provenance filters appear only when such rows exist.
    const filters = (
        ["all", "filled", "empty", "chosen", "automatic"] as const
    ).filter(
        (key) =>
            (key !== "chosen" && key !== "automatic") ||
            filterRows(rows, key).length > 0,
    );
    const onSort = (key: string) =>
        setSort((old) => ({
            key,
            descending: old.key === key ? !old.descending : false,
        }));
    // The per-step detail of a row is an Advanced tool, and so is its column.
    const advanced = useAdvanced();
    const span = inputs.length + outputs.length + (advanced ? 3 : 2);
    return (
        <>
            <div className="result-toolbar">
                <div
                    className="result-filter"
                    role="group"
                    aria-label={t("results.filter.label")}
                >
                    {filters.map((key) => (
                        <button
                            key={key}
                            type="button"
                            aria-pressed={filter === key}
                            data-filter={key}
                            onClick={() => setFilter(key)}
                        >
                            {t(`results.filter.${key}`, {
                                count: filterRows(rows, key).length,
                            })}
                        </button>
                    ))}
                </div>
                <Stats rows={rows} columns={outputs} />
            </div>
            <div
                className="outcome-table panel-scroll"
                role="region"
                aria-label={t("ws.outcomePreview")}
                tabIndex={0}
            >
                <table>
                    <thead>
                        <tr>
                            <SortHeader
                                label={t("results.row")}
                                sortKey="row"
                                sort={sort}
                                onSort={onSort}
                            />
                            {inputs.map((name) => (
                                <SortHeader
                                    key={name}
                                    label={name}
                                    sortKey={`in:${name}`}
                                    sort={sort}
                                    onSort={onSort}
                                />
                            ))}
                            {outputs.map((column) => (
                                <SortHeader
                                    key={column.key}
                                    label={column.label}
                                    sortKey={`out:${column.key}`}
                                    sort={sort}
                                    onSort={onSort}
                                />
                            ))}
                            <SortHeader
                                label={t("results.result")}
                                sortKey="status"
                                sort={sort}
                                onSort={onSort}
                            />
                            {advanced && (
                                <th>
                                    <span className="sr-only">
                                        {t("results.details")}
                                    </span>
                                </th>
                            )}
                        </tr>
                    </thead>
                    <tbody>
                        {shown.map((row) => (
                            <Fragment key={row.recordId}>
                                <tr
                                    data-testid="row-outcome"
                                    data-filled={row.filled}
                                    data-chosen={row.chosen.length > 0}
                                    data-automatic={row.automatic.length > 0}
                                >
                                    <td className="mono">
                                        {row.row ?? row.recordId}
                                    </td>
                                    {inputs.map((name) => (
                                        <td key={name}>
                                            {formatValue(
                                                row.inputs[name],
                                                locale,
                                            )}
                                        </td>
                                    ))}
                                    {outputs.map((column) => {
                                        const value = row.outputs[column.key];
                                        // A kept cell holds its earlier value;
                                        // what was found is not in the file.
                                        if (row.kept.includes(column.key))
                                            return (
                                                <td
                                                    key={column.key}
                                                    className="result-value muted"
                                                    title={
                                                        value === undefined
                                                            ? undefined
                                                            : t(
                                                                  "results.keptFound",
                                                                  {
                                                                      value: formatValue(
                                                                          value,
                                                                          locale,
                                                                          column.unit,
                                                                      ),
                                                                  },
                                                              )
                                                    }
                                                >
                                                    {t("results.keptCell")}
                                                </td>
                                            );
                                        return (
                                            <td
                                                key={column.key}
                                                className="result-value"
                                                title={
                                                    value === undefined
                                                        ? undefined
                                                        : String(value)
                                                }
                                            >
                                                {formatValue(
                                                    value,
                                                    locale,
                                                    column.unit,
                                                )}
                                            </td>
                                        );
                                    })}
                                    <td className="result-note">
                                        <Note row={row} w={w} />
                                    </td>
                                    {advanced && (
                                        <td>
                                            <button
                                                type="button"
                                                className="row-toggle"
                                                aria-expanded={
                                                    open === row.recordId
                                                }
                                                aria-label={t(
                                                    "results.detailsFor",
                                                    {
                                                        row:
                                                            row.row ??
                                                            row.recordId,
                                                    },
                                                )}
                                                onClick={() =>
                                                    setOpen((old) =>
                                                        old === row.recordId
                                                            ? null
                                                            : row.recordId,
                                                    )
                                                }
                                            >
                                                {open === row.recordId ? (
                                                    <ChevronDown aria-hidden="true" />
                                                ) : (
                                                    <ChevronRight aria-hidden="true" />
                                                )}
                                            </button>
                                        </td>
                                    )}
                                </tr>
                                {advanced && open === row.recordId && (
                                    <Detail row={row} span={span} />
                                )}
                            </Fragment>
                        ))}
                        {!shown.length && (
                            <tr>
                                <td colSpan={span} className="muted">
                                    {t("results.noRows")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
            <p className="muted">{t("ws.outputPreviewHint")}</p>
        </>
    );
}

export function ExportWorkspace({
    w,
    onStartOver,
}: {
    w: Workflow;
    onStartOver: () => void;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const reviewable = advanced || w.job?.status === "needs_review";
    return (
        <div className="stage-layout">
            <section className="workspace-panel panel-scroll summary-content">
                <div className="panel-title">
                    <Download aria-hidden="true" />
                    <h2>{t("ws.outputWorkbook")}</h2>
                    {/* A changed source is always reported; the routine
                        confirmation is an Advanced detail. */}
                    {w.report?.source_unchanged !== undefined &&
                        (advanced || !w.report.source_unchanged) && (
                            <span
                                className={`source-check ${
                                    w.report.source_unchanged
                                        ? "success-text"
                                        : "validation-note"
                                }`}
                            >
                                <CheckCircle2 aria-hidden="true" />{" "}
                                {t("workflow.sourceVerified")}:{" "}
                                {t(
                                    w.report.source_unchanged
                                        ? "ws.yes"
                                        : "ws.no",
                                )}
                            </span>
                        )}
                </div>
                <ModeNotice w={w} />
                {w.report?.rows?.length ? (
                    <ResultTable w={w} />
                ) : (
                    <EmptyState text={t("ws.exportPending")} />
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("ws.downloads")}</h2>
                    {advanced && (
                        <label>
                            {t("ws.filename")}
                            <input
                                value={w.downloadName}
                                maxLength={160}
                                onChange={(e) =>
                                    w.setDownloadName(e.target.value)
                                }
                            />
                        </label>
                    )}
                    <Button
                        disabled={
                            w.busy ||
                            !w.job ||
                            !isTerminal(w.job.status) ||
                            !w.job.artifacts.result
                        }
                        onClick={() =>
                            void w.action(
                                () => w.download("result"),
                                "download",
                            )
                        }
                    >
                        <Download />
                        {t("workflow.downloadXlsx")}
                    </Button>
                    {advanced && (
                        <Button
                            variant="outline"
                            disabled={
                                w.busy ||
                                !w.job ||
                                !isTerminal(w.job.status) ||
                                !w.job.artifacts.report
                            }
                            onClick={() =>
                                void w.action(
                                    () => w.download("report"),
                                    "download",
                                )
                            }
                        >
                            {t("workflow.downloadReport")}
                        </Button>
                    )}
                    <p className="muted">{t("ws.exportDetail")}</p>
                    {w.job?.status === "needs_review" && (
                        <p className="notice">{t("workflow.unresolved")}</p>
                    )}
                    {w.job && isTerminal(w.job.status) && (
                        <StartOver w={w} onStartOver={onStartOver} />
                    )}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to={reviewable ? "review" : "execution"} />
                </div>
            </aside>
        </div>
    );
}
