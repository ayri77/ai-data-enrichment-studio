import { ArrowRight, CircleCheck, Download, Table2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Dataset } from "../api";
import { endOfTable, secondRun, sumOf, tiles, type TileId } from "../rowsModel";
import type { Workflow } from "../useWorkflow";
import { Button } from "./ui/button";
import { Back, EmptyState } from "./WorkspaceViews";

/** The words of one term of the written-out check: "7 added", "3 duplicates". */
function useTermWords() {
    const { t } = useTranslation();
    return (id: TileId, count: number) =>
        id === "added"
            ? t("rows.term.added", { count })
            : id === "duplicates"
              ? t("rows.term.duplicates", { count })
              : id === "left_out"
                ? t("rows.term.left_out", { count })
                : id === "without_key"
                  ? t("rows.term.without_key", { count })
                  : t("rows.term.twice", { count });
}

/**
 * The third step of a task that adds rows: the numbers with the check written
 * out, what a second run would do, the end of the new table and the two ways on:
 * save the workbook, or go on with the new table.
 */
export function RowsResults({
    w,
    onContinue,
}: {
    w: Workflow;
    onContinue: (dataset: Dataset) => void;
}) {
    const { t } = useTranslation();
    const termWords = useTermWords();
    const result = w.rows.result;
    if (!result)
        return (
            <div className="stage-layout">
                <section className="workspace-panel">
                    <EmptyState text={t("ws.exportPending")} />
                </section>
                <aside className="workspace-panel stage-sidebar">
                    <div className="workspace-actions">
                        <Back w={w} to="plan" />
                    </div>
                </aside>
            </div>
        );
    const { count } = result.run.rows;
    const marked = result.shown.on_duplicate === "add_marked";
    const sum = sumOf(count);
    const second = result.second
        ? secondRun(result.second, result.shown.on_duplicate)
        : null;
    const end = endOfTable(result.tail, {
        first: result.run.rows.first_row,
        last: result.run.rows.last_row,
    });
    const columns = result.tail?.profile.columns ?? [];
    return (
        <div className="stage-layout">
            <section className="workspace-panel panel-scroll summary-content rows-results">
                <div className="panel-title">
                    <Download aria-hidden="true" />
                    <h2>{t("rows.results.heading")}</h2>
                </div>
                <ul className="rows-tiles" data-testid="rows-tiles">
                    {tiles(count).map((tile) => (
                        <li
                            className="rows-tile"
                            key={tile.id}
                            data-tile={tile.id}
                        >
                            <strong>{tile.value}</strong>
                            <span>
                                {t(
                                    tile.id === "duplicates" && marked
                                        ? "rows.tile.duplicatesMarked"
                                        : `rows.tile.${tile.id}`,
                                )}
                            </span>
                        </li>
                    ))}
                </ul>
                {sum.holds && result.kept?.same !== false ? (
                    <div
                        className="notice rows-holds"
                        role="status"
                        data-testid="rows-check"
                    >
                        <strong>
                            <CircleCheck aria-hidden="true" />
                            {t("rows.results.holds")}
                        </strong>
                        <span data-testid="rows-sum">
                            {t("rows.results.sum", {
                                terms: sum.parts
                                    .map((part) =>
                                        termWords(part.id, part.value),
                                    )
                                    .join(" + "),
                                total: sum.total,
                            })}
                        </span>
                        {result.kept && (
                            <span data-testid="rows-kept">
                                {t("rows.results.untouched", {
                                    rows: result.kept.rows,
                                })}
                            </span>
                        )}
                    </div>
                ) : (
                    <div
                        className="error-banner"
                        role="alert"
                        data-testid="rows-check"
                    >
                        {t(
                            sum.holds
                                ? "rows.results.changed"
                                : "rows.results.fails",
                        )}
                    </div>
                )}
                {marked && count.written > count.added && (
                    <p className="muted" data-testid="rows-written">
                        {t("rows.results.written", { count: count.written })}
                    </p>
                )}
                <section className="rows-end" data-testid="rows-end">
                    <h3>
                        <Table2 aria-hidden="true" />
                        {t("rows.results.endTitle")}
                    </h3>
                    {end.length ? (
                        <>
                            <div
                                className="table-scroll"
                                role="region"
                                aria-label={t("scrollTable")}
                                tabIndex={0}
                            >
                                <table>
                                    <thead>
                                        <tr>
                                            <th scope="col">
                                                {t("rows.results.rowColumn")}
                                            </th>
                                            {columns.map((column) => (
                                                <th
                                                    scope="col"
                                                    key={column.name}
                                                >
                                                    {column.name}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {end.map((row) => (
                                            <tr
                                                key={row.row}
                                                data-added={row.added}
                                                data-testid="rows-end-row"
                                            >
                                                <td>
                                                    {row.row}
                                                    {row.added && (
                                                        <small className="rows-tag">
                                                            {t(
                                                                "rows.results.added",
                                                            )}
                                                        </small>
                                                    )}
                                                </td>
                                                {columns.map(
                                                    (column, index) => (
                                                        <td key={column.name}>
                                                            {row.cells[
                                                                index
                                                            ] === null ||
                                                            row.cells[index] ===
                                                                undefined
                                                                ? ""
                                                                : String(
                                                                      row.cells[
                                                                          index
                                                                      ],
                                                                  )}
                                                        </td>
                                                    ),
                                                )}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="muted">{t("rows.results.endHint")}</p>
                        </>
                    ) : (
                        <p className="muted">{t("rows.results.endMissing")}</p>
                    )}
                </section>
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("ws.downloads")}</h2>
                    <Button
                        disabled={w.locked}
                        onClick={() =>
                            void w.action(w.rows.download, "download")
                        }
                    >
                        <Download aria-hidden="true" />
                        {t("rows.results.download")}
                    </Button>
                    <p className="muted">{t("rows.results.downloadNote")}</p>
                    <Button
                        variant="outline"
                        disabled={w.locked}
                        onClick={() => onContinue(result.dataset)}
                    >
                        {t("rows.results.continue")}
                        <ArrowRight aria-hidden="true" />
                    </Button>
                    <p className="muted">{t("rows.results.continueNote")}</p>
                    <div className="rows-second" data-testid="rows-second">
                        <h3>{t("rows.results.secondTitle")}</h3>
                        <p
                            role="status"
                            className={
                                second?.kind === "none"
                                    ? "success-text"
                                    : undefined
                            }
                        >
                            {second === null
                                ? t("rows.results.secondUnknown")
                                : second.kind === "none"
                                  ? t("rows.results.secondNone")
                                  : second.kind === "some"
                                    ? t("rows.results.secondSome", {
                                          count: second.rows,
                                      })
                                    : t("rows.results.secondMarked", {
                                          count: second.rows,
                                      })}
                        </p>
                    </div>
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="plan" />
                </div>
            </aside>
        </div>
    );
}
