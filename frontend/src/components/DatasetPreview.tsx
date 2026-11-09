import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Columns3, Info, LoaderCircle, Table2 } from "lucide-react";
import type { Preview } from "../api";
import { columnLetter, parseRange } from "../range";

export function DatasetPreview({
    preview,
    filename,
    busy,
    hasWorkbook,
}: {
    preview: Preview | null;
    filename?: string;
    busy: boolean;
    hasWorkbook: boolean;
}) {
    const { t } = useTranslation();
    const [selectedColumn, setSelectedColumn] = useState("");
    const [width, setWidth] = useState(300);
    const columns = preview?.profile.columns ?? [];
    const selected =
        columns.find((c) => c.name === selectedColumn) ?? columns[0];
    const bounds = preview ? parseRange(preview.range_ref).bounds : undefined;
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
                  col.warnings.map((w) => warning(w, col.name)),
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
                className="dataset-split"
                style={{ "--inspector-width": `${width}px` } as CSSProperties}
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
                            {preview
                                ? t("sampleCount", {
                                      shown: preview.profile.sample_records
                                          .length,
                                      total: preview.profile.row_count,
                                  })
                                : t("readOnly")}
                        </span>
                    </header>
                    {preview ? (
                        <>
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
                                                {t("sampleIndex")}
                                            </th>
                                            {columns.map((column, index) => (
                                                <th
                                                    key={column.name}
                                                    scope="col"
                                                    className={
                                                        selected?.name ===
                                                        column.name
                                                            ? "is-selected"
                                                            : ""
                                                    }
                                                >
                                                    <button
                                                        type="button"
                                                        aria-pressed={
                                                            selected?.name ===
                                                            column.name
                                                        }
                                                        onClick={() =>
                                                            setSelectedColumn(
                                                                column.name,
                                                            )
                                                        }
                                                    >
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
                                                        <small>
                                                            {typeLabel(
                                                                column.inferred_type,
                                                            )}
                                                        </small>
                                                    </button>
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {preview.profile.sample_records.map(
                                            (record, index) => (
                                                <tr key={index}>
                                                    <th
                                                        scope="row"
                                                        className="sample-index mono"
                                                    >
                                                        {index + 1}
                                                    </th>
                                                    {columns.map((col) => (
                                                        <td
                                                            key={col.name}
                                                            className={
                                                                selected?.name ===
                                                                col.name
                                                                    ? "is-selected"
                                                                    : ""
                                                            }
                                                        >
                                                            {record[col.name] ==
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
                                                                    record[
                                                                        col.name
                                                                    ],
                                                                )
                                                            )}
                                                        </td>
                                                    ))}
                                                </tr>
                                            ),
                                        )}
                                    </tbody>
                                </table>
                            </div>
                            {preview.profile.row_count === 0 && (
                                <p className="header-only">{t("noData")}</p>
                            )}
                            <div className="table-footer">
                                <span>{t("sampleNote")}</span>
                                <span>
                                    {t("sampleLimit", {
                                        count: preview.sample_limit,
                                    })}
                                </span>
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
                                <p>
                                    {hasWorkbook
                                        ? t("readyHint")
                                        : t("emptyHint")}
                                </p>
                            </div>
                        </div>
                    )}
                </section>
                <aside className="column-inspector" aria-label={t("inspector")}>
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
                            <label className="inspector-width">
                                {t("width")} / {width}px
                                <input
                                    type="range"
                                    min="260"
                                    max="420"
                                    step="10"
                                    value={width}
                                    onChange={(e) =>
                                        setWidth(Number(e.target.value))
                                    }
                                />
                            </label>
                        </>
                    ) : (
                        <p>{t("selectColumn")}</p>
                    )}
                </aside>
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
                        {hasWorkbook ? t("pendingSelection") : t("emptyHint")}
                    </p>
                )}
            </section>
        </section>
    );
}
