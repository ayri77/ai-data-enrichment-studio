import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    ArrowDown,
    ArrowUp,
    ArrowUpDown,
    Columns3,
    Info,
    LoaderCircle,
    Table2,
} from "lucide-react";
import type { Preview } from "../api";
import {
    filterRows,
    nextSort,
    PAGE_ROWS,
    sortRows,
    tableRows,
    type Sort,
} from "../tableModel";
import { useAdvanced } from "../mode";
import { CanDo } from "./CanDo";
import { columnLetter, parseRange } from "../range";

export function DatasetPreview({
    preview,
    filename,
    busy,
    hasWorkbook,
    filled = [],
    maxRows = 20,
}: {
    preview: Preview | null;
    filename?: string;
    busy: boolean;
    hasWorkbook: boolean;
    /** Columns chosen to be filled: being empty is expected of them. */
    filled?: string[];
    maxRows?: number;
}) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? "en";
    const [selectedColumn, setSelectedColumn] = useState("");
    // Looking through the table: a text filter, one sorted column, and only as
    // many rows drawn as were asked for. None of it changes the selection.
    const [query, setQuery] = useState("");
    const [sort, setSort] = useState<Sort>(null);
    const [limit, setLimit] = useState(PAGE_ROWS);
    useEffect(() => {
        setQuery("");
        setSort(null);
        setLimit(PAGE_ROWS);
    }, [preview]);
    // The server sends every row; without them only the sample can be shown.
    const whole = !!preview?.rows;
    const all = useMemo(() => (preview ? tableRows(preview) : []), [preview]);
    const ordered = useMemo(
        () => sortRows(filterRows(all, query, locale), sort, locale),
        [all, query, sort, locale],
    );
    const visible = ordered.slice(0, limit);
    // Column types and the per-column inspector are Advanced tools.
    const advanced = useAdvanced();
    const columns = preview?.profile.columns ?? [];
    const selected =
        columns.find((c) => c.name === selectedColumn) ?? columns[0];
    const bounds = preview ? parseRange(preview.range_ref).bounds : undefined;
    // Without the inspector there is no selected column to point at.
    const highlighted = advanced ? selected?.name : undefined;
    const selectedIndex = columns.findIndex((c) => c.name === selected?.name);
    const selectedLetter = bounds
        ? columnLetter(bounds.firstColumn + selectedIndex)
        : "";
    const typeLabel = (type: string) =>
        t(`type.${type}`, { defaultValue: t("type.Unknown") });
    function warning(text: string, column = "") {
        if (text.startsWith("Column contains no non-null values"))
            return t("warning.emptyColumn", { column });
        if (text.startsWith("Mixed observed types"))
            return t("warning.mixed", { column });
        if (text.startsWith("Excel error cells:"))
            return t("warning.excel", {
                column,
                count: Number(text.split(":")[1]),
            });
        if (text.startsWith("Time-only values"))
            return t("warning.time", {
                column,
                count: Number(text.split(":").at(-1)),
            });
        if (text.includes("max_profile_bytes")) return t("warning.truncated");
        return t("warning.generic", { column });
    }
    const warnings = preview
        ? [
              ...preview.profile.schema_warnings.map((w) => warning(w)),
              ...columns.flatMap((col) =>
                  col.warnings
                      .filter(
                          (w) =>
                              advanced ||
                              !filled.includes(col.name) ||
                              !w.startsWith(
                                  "Column contains no non-null values",
                              ),
                      )
                      .map((w) => warning(w, col.name)),
              ),
          ]
        : [];
    return (
        <section
            className="preview-workspace"
            aria-label={t("previewTitle")}
            aria-busy={busy}
        >
            <div
                className={
                    advanced ? "dataset-split" : "dataset-split no-inspector"
                }
            >
                <section className="source-grid">
                    <header className="section-head">
                        <strong>
                            <Table2 aria-hidden="true" />
                            {t("previewTitle")}
                            {preview && (
                                <span className="mono source-reference">
                                    {filename} / {preview.sheet_name} /{" "}
                                    {preview.range_ref}
                                </span>
                            )}
                        </strong>
                        <span>
                            {!preview
                                ? t("readOnly")
                                : !whole
                                  ? t("sampleCount", {
                                        shown: preview.profile.sample_records
                                            .length,
                                        total: preview.profile.row_count,
                                    })
                                  : query.trim()
                                    ? t("rowsMatching", {
                                          shown: ordered.length,
                                          total: all.length,
                                      })
                                    : t("rowsTotal", { total: all.length })}
                        </span>
                    </header>
                    {preview ? (
                        <>
                            {whole && all.length > 0 && (
                                <div className="table-tools">
                                    <input
                                        type="search"
                                        value={query}
                                        placeholder={t("filterRows")}
                                        aria-label={t("filterRows")}
                                        onChange={(event) => {
                                            setQuery(event.target.value);
                                            setLimit(PAGE_ROWS);
                                        }}
                                    />
                                    <span className="muted">
                                        {t("tableToolsHint")}
                                    </span>
                                </div>
                            )}
                            <div
                                className="table-scroll"
                                role="region"
                                aria-label={t("scrollTable")}
                                tabIndex={0}
                                onKeyDown={(event) => {
                                    if (
                                        event.target !== event.currentTarget ||
                                        event.altKey ||
                                        event.ctrlKey ||
                                        event.metaKey
                                    )
                                        return;
                                    if (
                                        event.key === "ArrowLeft" ||
                                        event.key === "ArrowRight"
                                    ) {
                                        event.preventDefault();
                                        event.currentTarget.scrollLeft +=
                                            event.key === "ArrowRight"
                                                ? 96
                                                : -96;
                                    }
                                }}
                            >
                                <table>
                                    <thead>
                                        <tr>
                                            <th
                                                className="sample-heading"
                                                scope="col"
                                            >
                                                {t(
                                                    whole
                                                        ? "rowNumber"
                                                        : "sampleIndex",
                                                )}
                                            </th>
                                            {columns.map((column, index) => {
                                                const head = (
                                                    <>
                                                        <span className="column-address mono">
                                                            {bounds
                                                                ? columnLetter(
                                                                      bounds.firstColumn +
                                                                          index,
                                                                  )
                                                                : ""}
                                                        </span>
                                                        <span className="mono">
                                                            {column.name}
                                                        </span>
                                                        {advanced && (
                                                            <small>
                                                                {typeLabel(
                                                                    column.inferred_type,
                                                                )}
                                                            </small>
                                                        )}
                                                    </>
                                                );
                                                return (
                                                    <th
                                                        key={column.name}
                                                        scope="col"
                                                        aria-sort={
                                                            sort?.column !==
                                                            index
                                                                ? undefined
                                                                : sort.descending
                                                                  ? "descending"
                                                                  : "ascending"
                                                        }
                                                        className={
                                                            highlighted ===
                                                            column.name
                                                                ? "is-selected"
                                                                : ""
                                                        }
                                                    >
                                                        {advanced ? (
                                                            <button
                                                                type="button"
                                                                className="column-head"
                                                                aria-pressed={
                                                                    highlighted ===
                                                                    column.name
                                                                }
                                                                onClick={() =>
                                                                    setSelectedColumn(
                                                                        column.name,
                                                                    )
                                                                }
                                                            >
                                                                {head}
                                                            </button>
                                                        ) : (
                                                            <span className="column-head">
                                                                {head}
                                                            </span>
                                                        )}
                                                        {whole && (
                                                            <button
                                                                type="button"
                                                                className="sort-button"
                                                                aria-label={t(
                                                                    "sortBy",
                                                                    {
                                                                        column: column.name,
                                                                    },
                                                                )}
                                                                aria-pressed={
                                                                    sort?.column ===
                                                                    index
                                                                }
                                                                onClick={() => {
                                                                    setSort(
                                                                        nextSort(
                                                                            sort,
                                                                            index,
                                                                        ),
                                                                    );
                                                                    setLimit(
                                                                        PAGE_ROWS,
                                                                    );
                                                                }}
                                                            >
                                                                {sort?.column !==
                                                                index ? (
                                                                    <ArrowUpDown aria-hidden="true" />
                                                                ) : sort.descending ? (
                                                                    <ArrowDown aria-hidden="true" />
                                                                ) : (
                                                                    <ArrowUp aria-hidden="true" />
                                                                )}
                                                            </button>
                                                        )}
                                                    </th>
                                                );
                                            })}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {visible.map((row) => (
                                            <tr key={row.row}>
                                                <th
                                                    scope="row"
                                                    className="sample-index mono"
                                                >
                                                    {row.row}
                                                </th>
                                                {columns.map((col, index) => (
                                                    <td
                                                        key={col.name}
                                                        className={
                                                            highlighted ===
                                                            col.name
                                                                ? "is-selected"
                                                                : ""
                                                        }
                                                    >
                                                        {row.cells[index] ==
                                                        null ? (
                                                            <span
                                                                className="empty-value"
                                                                aria-label={t(
                                                                    "emptyValue",
                                                                )}
                                                                title={t(
                                                                    "emptyValue",
                                                                )}
                                                            >
                                                                ∅
                                                            </span>
                                                        ) : (
                                                            String(
                                                                row.cells[
                                                                    index
                                                                ],
                                                            )
                                                        )}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            {preview.profile.row_count === 0 && (
                                <p className="header-only">{t("noData")}</p>
                            )}
                            {all.length > 0 && ordered.length === 0 && (
                                <p className="header-only" role="status">
                                    {t("noMatches")}
                                </p>
                            )}
                            <div className="table-footer">
                                <span>
                                    {t(whole ? "tableNote" : "sampleNote")}
                                </span>
                                {ordered.length > visible.length ? (
                                    <span>
                                        {t("rowsShown", {
                                            shown: visible.length,
                                            total: ordered.length,
                                        })}{" "}
                                        <button
                                            type="button"
                                            className="inline-link"
                                            onClick={() =>
                                                setLimit(limit + PAGE_ROWS)
                                            }
                                        >
                                            {t("showMoreRows", {
                                                count: Math.min(
                                                    PAGE_ROWS,
                                                    ordered.length -
                                                        visible.length,
                                                ),
                                            })}
                                        </button>
                                    </span>
                                ) : (
                                    <span>
                                        {whole
                                            ? t("valueLimit")
                                            : t("sampleLimit", {
                                                  count: preview.sample_limit,
                                              })}
                                    </span>
                                )}
                            </div>
                            <p className="scroll-hint">{t("scrollHint")}</p>
                        </>
                    ) : (
                        <div className="empty-preview" role="status">
                            {busy ? (
                                <LoaderCircle className="spin" />
                            ) : (
                                <Table2 />
                            )}
                            <div>
                                <h3>{busy ? t("reading") : t("emptyTitle")}</h3>
                                {hasWorkbook ? (
                                    <p>{t("readyHint")}</p>
                                ) : (
                                    <CanDo maxRows={maxRows} />
                                )}
                            </div>
                        </div>
                    )}
                </section>
                {advanced && (
                    <aside
                        className="column-inspector"
                        aria-label={t("inspector")}
                    >
                        <div className="inspector-label">
                            <Columns3 aria-hidden="true" />
                            {t("inspector")}
                            {selected && (
                                <span className="mono">/ {selectedLetter}</span>
                            )}
                        </div>
                        {selected && preview ? (
                            <>
                                <h2 className="mono">{selected.name}</h2>
                                <dl>
                                    <dt>{t("inferredType")}</dt>
                                    <dd>{typeLabel(selected.inferred_type)}</dd>
                                    <dt>{t("rows")}</dt>
                                    <dd>{preview.profile.row_count}</dd>
                                    <dt>{t("empty")}</dt>
                                    <dd>{selected.null_count}</dd>
                                    <dt>{t("selectedCells")}</dt>
                                    <dd className="mono">
                                        {bounds
                                            ? `${selectedLetter}${bounds.headerRow}:${selectedLetter}${bounds.lastRow}`
                                            : "—"}
                                    </dd>
                                </dl>
                                <p>{t("observed")}</p>
                            </>
                        ) : (
                            <p>{t("selectColumn")}</p>
                        )}
                    </aside>
                )}
            </div>
            <section className="diagnostics" aria-label={t("diagnostics")}>
                <h2>
                    <Info aria-hidden="true" />
                    {t("diagnostics")}
                </h2>
                {preview ? (
                    warnings.length ? (
                        warnings.map((w, index) => (
                            <p className="schema-warning" key={index}>
                                {w}
                            </p>
                        ))
                    ) : (
                        <p>{t("noWarnings")}</p>
                    )
                ) : (
                    <p>
                        {hasWorkbook
                            ? t("pendingSelection")
                            : t(advanced ? "emptyHint" : "ws.need.workbook")}
                    </p>
                )}
            </section>
        </section>
    );
}
