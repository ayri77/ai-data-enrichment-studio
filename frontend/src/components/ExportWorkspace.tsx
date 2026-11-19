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
import {
    capabilityProvenance,
    provenanceLabel,
    useOffered,
} from "../capabilities";
import { columnGroups, isFallbackRun, isMixedRun } from "../fallbackModel";
import {
    MAIL_IDENTITY,
    extractionCounts,
    isExtractionRun,
    mailIdentities,
} from "../mailModel";
import { useAdvanced } from "../mode";
import type { Workflow } from "../useWorkflow";
import {
    columnStats,
    describeValue,
    filterRows,
    formatValue,
    inputColumns,
    lookupSummary,
    outputColumns,
    resultRows,
    searchedColumns,
    sortRows,
    stepTrust,
    type ResultColumn,
    type ResultRow,
    type RowFilter,
    type Sort,
} from "../resultsModel";
import { columnLabels, unfilledColumns } from "../planModel";
import { Button } from "./ui/button";
import { Back, EmptyState, ModeNotice, StartOver } from "./WorkspaceViews";
import { FallbackBanner, MixedBanner } from "./FallbackSearch";
import { ExtractionBanner } from "./ExtractionTask";

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
    // Only a column the web search fills has no page to name; the others are
    // empty for the source's own reasons.
    const searched = searchedColumns(w.plan?.plan ?? null);
    // A name the source did not find may be known to it under another one.
    const canSearch =
        !row.filled &&
        w.job?.status === "needs_review" &&
        w.review?.job_id === w.job.job_id &&
        w.review.informational.some(
            (item) => item.other_name && item.query.name === row.cause?.subject,
        );
    // A value found under another name says so itself; the rest are choices
    // between the matches of the row's own name.
    const plain = row.chosen.filter(
        (name) => !row.searched.some((found) => found.subject === name),
    );
    return (
        <>
            {row.skipped ? (
                <span className="result-kept" data-testid="result-skipped">
                    {t("results.skipped")}
                </span>
            ) : row.kept.length > 0 ? (
                <span className="result-kept" data-testid="result-kept">
                    {t("results.kept")}
                </span>
            ) : row.notApplicable ? (
                <span
                    className="outcome-not-applicable"
                    data-testid="row-label"
                    data-outcome="not-applicable"
                >
                    {t("results.notApplicable")}
                </span>
            ) : (
                <span
                    className={
                        row.filled
                            ? "outcome-success"
                            : row.partial
                              ? "outcome-partial"
                              : "outcome-failure"
                    }
                    data-testid="row-label"
                >
                    {t(
                        row.filled
                            ? "results.filled"
                            : row.partial
                              ? "results.partial"
                              : "results.empty",
                    )}
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
            {plain.length > 0 && (
                <span className="result-chosen">
                    {t("results.chosen", { names: plain.join(", ") })}
                </span>
            )}
            {row.searched.map((found) => (
                <span
                    key={`${found.subject}:${found.searchedAs}`}
                    className="result-chosen"
                    data-testid="result-searched"
                >
                    {t(`results.searchedAs.${found.origin ?? "unknown"}`, {
                        name: found.subject ?? t("results.thisRow"),
                        searched: found.searchedAs,
                    })}
                </span>
            ))}
            {row.automatic.length > 0 && (
                <span className="result-automatic">
                    {t("results.automatic", {
                        names: row.automatic.join(", "),
                    })}
                </span>
            )}
            {row.gaps
                .filter((gap) => !row.kept.includes(gap.column))
                .map((gap) => (
                    <span
                        key={gap.column}
                        className="result-cause"
                        data-testid="result-gap"
                    >
                        {row.extraction
                            ? t("results.extract.gap", { column: gap.column })
                            : row.lookup && searched.includes(gap.column)
                              ? t("results.lookupGap", { column: gap.column })
                              : t("results.cause.no_value", {
                                    field: t(`plan.field.${gap.field}`, {
                                        defaultValue: gap.field,
                                    }),
                                    subject:
                                        gap.subject ?? t("results.thisRow"),
                                })}
                    </span>
                ))}
            {row.notApplicable && (
                <span
                    className="result-cause"
                    data-testid="result-not-applicable"
                >
                    {t("results.extract.notApplicable")}
                </span>
            )}
            {row.extraction &&
                !row.filled &&
                row.kept.length === 0 &&
                row.cause && (
                    <span
                        className="result-cause"
                        data-testid="result-extraction-cause"
                    >
                        {row.cause.code === "NOTHING_FOUND"
                            ? t("results.extract.nothingFound")
                            : t("results.extract.failed", {
                                  code: row.cause.code,
                              })}
                    </span>
                )}
            {!row.extraction &&
                !row.filled &&
                row.kept.length === 0 &&
                row.cause && (
                    <span className="result-cause">
                        {t(`results.cause.${row.cause.kind}`, {
                            subject: row.cause.subject ?? t("results.thisRow"),
                            code: row.cause.code,
                        })}
                        {canSearch && (
                            <>
                                {" "}
                                <button
                                    type="button"
                                    className="inline-link"
                                    onClick={() => w.navigate("review")}
                                >
                                    {t("results.toSearch")}
                                </button>
                            </>
                        )}
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
    trust,
}: {
    rows: ResultRow[];
    columns: ResultColumn[];
    /** What the audit says about each step; the plan's capability otherwise. */
    trust: ReturnType<typeof stepTrust>;
}) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? "en";
    const offered = useOffered();
    return (
        <ul className="result-stats" data-testid="result-stats">
            {columns.map((column) => {
                const stats = columnStats(rows, column.key);
                const number = (value: number) =>
                    formatValue(value, locale, column.unit, column.digits);
                const how = provenanceLabel(
                    t,
                    (column.stepId ? trust.get(column.stepId) : undefined) ??
                        capabilityProvenance(offered, column.capability),
                );
                return (
                    <li key={column.key}>
                        <strong>{column.label}</strong>
                        {how && (
                            <span
                                className="result-provenance"
                                data-testid="result-provenance"
                            >
                                {how}
                            </span>
                        )}
                        <span>
                            {t("results.stats.filled", {
                                filled: stats.filled,
                                total: stats.total,
                            })}
                        </span>
                        {stats.skipped > 0 && (
                            <span>
                                {t("results.stats.skipped", {
                                    count: stats.skipped,
                                })}
                            </span>
                        )}
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
    // A run on a table of mails names each row by sender, subject and day,
    // read from the preview the browser holds; the text is never shown. A row
    // the preview does not hold keeps its number alone.
    const identities = useMemo(
        () =>
            isExtractionRun(w.job, w.plan)
                ? mailIdentities(w.preview)
                : new Map<number, Record<string, string>>(),
        [w.job, w.plan, w.preview],
    );
    const named = identities.size > 0;
    const rows = useMemo(() => {
        const made = resultRows(w.report, plan);
        return named
            ? made.map((row) => ({
                  ...row,
                  inputs: (row.row !== null && identities.get(row.row)) || {},
              }))
            : made;
    }, [w.report, plan, named, identities]);
    const inputs = useMemo(
        () => (named ? [] : inputColumns(w.report)),
        [w.report, named],
    );
    const outputs = useMemo(() => outputColumns(plan), [plan]);
    const [filter, setFilter] = useState<RowFilter>("all");
    const [sort, setSort] = useState<Sort>({ key: "row", descending: false });
    const [open, setOpen] = useState<string | null>(null);
    const shown = sortRows(filterRows(rows, filter), sort, locale);
    // The provenance filters and the one for rows that were not looked up
    // appear only when such rows exist.
    const filters = (
        [
            "all",
            "filled",
            "partial",
            "empty",
            "skipped",
            "chosen",
            "automatic",
            "concluded",
            "notApplicable",
        ] as const
    ).filter(
        (key) =>
            (key !== "chosen" &&
                key !== "automatic" &&
                key !== "skipped" &&
                key !== "partial" &&
                key !== "notApplicable" &&
                key !== "concluded") ||
            filterRows(rows, key).length > 0,
    );
    const lookup = isFallbackRun(w.job, w.plan) || rows.some((r) => r.lookup);
    const summary = lookupSummary(rows);
    const mixed = isMixedRun(w.job, w.plan);
    const trust = useMemo(() => stepTrust(w.report), [w.report]);
    // A mixed run names the columns the web search filled: the other columns
    // come from the system's sources.
    const searched = w.plan
        ? columnGroups({
              plan: w.plan.plan,
              output_columns: w.plan.output_columns,
              searched_targets:
                  w.plan.searched_targets ?? w.report?.searched_targets,
          }).searched
        : [];
    // Chosen columns the plan had no source for: named once, above the table.
    const unfilled = w.plan ? unfilledColumns(w.plan) : [];
    const spend = w.report?.web_lookup;
    const extraction = isExtractionRun(w.job, w.plan);
    const extracted = w.job ? extractionCounts(w.job, w.report) : null;
    const onSort = (key: string) =>
        setSort((old) => ({
            key,
            descending: old.key === key ? !old.descending : false,
        }));
    // The per-step detail of a row is an Advanced tool, and so is its column.
    const advanced = useAdvanced();
    const span =
        inputs.length + (named ? 1 : 0) + outputs.length + (advanced ? 3 : 2);
    const resultCell = (row: (typeof rows)[number]) => (
        <td className="result-note">
            <Note row={row} w={w} />
        </td>
    );
    const resultHeader = (
        <SortHeader
            label={t("results.result")}
            sortKey="status"
            sort={sort}
            onSort={onSort}
        />
    );
    return (
        <>
            {lookup && (
                <div
                    className="fallback-summary"
                    data-testid="fallback-summary"
                >
                    {mixed && w.plan ? (
                        <MixedBanner draft={w.plan} done />
                    ) : (
                        <FallbackBanner done />
                    )}
                    {mixed && searched.length > 0 && (
                        <p data-testid="lookup-columns">
                            {t("results.lookup.searched", {
                                columns: columnLabels(searched),
                            })}
                        </p>
                    )}
                    <p>
                        {t("results.lookup.summary", {
                            asked: summary.asked,
                            found: summary.found,
                            concluded: summary.concluded,
                        })}
                    </p>
                    {advanced && spend && (
                        <p className="muted mono" data-testid="fallback-spend">
                            {t("results.lookup.spend", {
                                requests: spend.requests,
                                input: spend.input_tokens,
                                output: spend.output_tokens,
                                searches: spend.web_searches,
                                configuration: spend.configuration_id,
                            })}
                            {!!spend.answers_kept &&
                                ` · ${t("results.lookup.kept", {
                                    count: spend.answers_kept,
                                })}`}
                        </p>
                    )}
                </div>
            )}
            {extraction && extracted && (
                <div
                    className="fallback-summary"
                    data-testid="extraction-summary"
                >
                    <ExtractionBanner done />
                    <p>
                        {t("results.extract.summary", {
                            asked: extracted.total,
                            found: extracted.found,
                            notApplicable: extracted.notApplicable,
                        })}
                    </p>
                </div>
            )}
            {unfilled.length > 0 && (
                <p className="muted" data-testid="unfilled-summary">
                    {t("results.unfilled", {
                        columns: columnLabels(unfilled),
                    })}
                </p>
            )}
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
                <Stats rows={rows} columns={outputs} trust={trust} />
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
                            {named && (
                                <SortHeader
                                    label={t("results.email")}
                                    sortKey="in:Received"
                                    sort={sort}
                                    onSort={onSort}
                                />
                            )}
                            {named && resultHeader}
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
                            {!named && resultHeader}
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
                                    data-not-applicable={row.notApplicable}
                                    data-skipped={row.skipped}
                                    data-chosen={row.chosen.length > 0}
                                    data-automatic={row.automatic.length > 0}
                                    data-concluded={row.concluded.length > 0}
                                >
                                    <td className="mono">
                                        {row.row ?? row.recordId}
                                    </td>
                                    {named && (
                                        <td
                                            className="result-email"
                                            data-testid="result-email"
                                        >
                                            <strong>
                                                {row.inputs.From ?? "—"}
                                            </strong>
                                            <span
                                                className="result-email-subject"
                                                title={String(
                                                    row.inputs.Subject ?? "",
                                                )}
                                            >
                                                {row.inputs.Subject}
                                            </span>
                                            <span className="muted">
                                                {row.inputs.Received}
                                            </span>
                                        </td>
                                    )}
                                    {named && resultCell(row)}
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
                                        if (
                                            !row.skipped &&
                                            row.kept.includes(column.key)
                                        )
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
                                                                          column.digits,
                                                                      ),
                                                                  },
                                                              )
                                                    }
                                                >
                                                    {t("results.keptCell")}
                                                </td>
                                            );
                                        const basis = row.found[column.key];
                                        const read = row.extracted[column.key];
                                        return (
                                            <td
                                                key={column.key}
                                                className="result-value"
                                                data-basis={
                                                    basis?.basis ?? read?.basis
                                                }
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
                                                    column.digits,
                                                )}
                                                {read &&
                                                    value !== undefined && (
                                                        <>
                                                            <span
                                                                className="basis-mark"
                                                                data-testid="basis-mark"
                                                            >
                                                                {t(
                                                                    `extract.basis.${read.basis}`,
                                                                )}
                                                            </span>
                                                            {read.quote && (
                                                                <span
                                                                    className="result-quote"
                                                                    data-testid="result-quote"
                                                                    title={t(
                                                                        "extract.quote",
                                                                        {
                                                                            quote: read.quote,
                                                                        },
                                                                    )}
                                                                >
                                                                    “
                                                                    {read.quote}
                                                                    ”
                                                                </span>
                                                            )}
                                                        </>
                                                    )}
                                                {basis &&
                                                    value !== undefined && (
                                                        <span
                                                            className="basis-mark"
                                                            data-testid="basis-mark"
                                                            title={t(
                                                                "results.lookup.links",
                                                                {
                                                                    count: basis.links,
                                                                },
                                                            )}
                                                        >
                                                            {t(
                                                                `fallback.basis.${basis.basis}`,
                                                            )}
                                                        </span>
                                                    )}
                                            </td>
                                        );
                                    })}
                                    {!named && resultCell(row)}
                                    {advanced && (
                                        <td>
                                            {/* No step ran for a row that was not looked up. */}
                                            {!row.skipped && (
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
                                            )}
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
    const extraction = isExtractionRun(w.job, w.plan);
    // The text column is emptied in the workbook of a table built from e-mails.
    const emptied =
        w.plan?.text_column_emptied === true ||
        w.report?.text_extraction?.text_column_emptied === true;
    // A run that reads texts has no names to settle: its review is only for the curious.
    const reviewable =
        advanced || (w.job?.status === "needs_review" && !extraction);
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
                    {/* The workbook of a table built from e-mails has no mail text in it. */}
                    {extraction && emptied && (
                        <p
                            className="muted"
                            data-testid="extraction-download-note"
                        >
                            {t("extract.download")}
                        </p>
                    )}
                    {w.job?.status === "needs_review" && (
                        <p className="notice">
                            {t(
                                extraction
                                    ? "extract.unresolved"
                                    : isFallbackRun(w.job, w.plan)
                                      ? "fallback.unresolved"
                                      : isMixedRun(w.job, w.plan)
                                        ? "fallback.mixed.unresolved"
                                        : "workflow.unresolved",
                            )}
                        </p>
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
