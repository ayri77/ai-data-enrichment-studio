import {
    CircleAlert,
    Info,
    ListChecks,
    LoaderCircle,
    Play,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Preview, RowSource, RowsCondition, RowsRefusal } from "../api";
import {
    columnChoices,
    noMatchNotice,
    runBlock,
    valueKind,
} from "../rowsModel";
import type { RowsPlanState } from "../useRows";
import type { Workflow } from "../useWorkflow";
import { Button } from "./ui/button";
import { useRowsLabels } from "./RowsTask";
import { Back, EmptyState } from "./WorkspaceViews";

/** A condition of "take only" in words, with the name of its field. */
function useConditionWords() {
    const { t } = useTranslation();
    const label = useRowsLabels();
    return (condition: RowsCondition, source: RowSource) => {
        const field = source.fields.find(
            (f) => f.field_id === condition.field_id,
        );
        const name = field ? label.field(field) : condition.field_id;
        return valueKind(condition.op) === "days"
            ? t("rows.cond.days", {
                  field: name,
                  count: Number(condition.value),
              })
            : t(`rows.cond.${condition.op}`, {
                  field: name,
                  value: condition.value,
              });
    };
}

/** Why the plan is refused: the text of its code and the numbers it carries. */
function Refusal({ refused }: { refused: RowsRefusal }) {
    const { t } = useTranslation();
    const numbers =
        refused.count !== null && refused.row !== null
            ? t("rows.plan.refusedBoth", {
                  count: refused.count,
                  row: refused.row,
              })
            : refused.count !== null
              ? t("rows.plan.refusedCount", { count: refused.count })
              : refused.row !== null
                ? t("rows.plan.refusedRow", { row: refused.row })
                : "";
    return (
        <div
            className="error-banner rows-refusal"
            role="alert"
            data-error-code={refused.code}
        >
            <CircleAlert aria-hidden="true" />
            <span>
                {t(`error.${refused.code}`, {
                    defaultValue: t("error.request_failed"),
                })}{" "}
                {numbers}
            </span>
        </div>
    );
}

/** The numbers of the plan as one block: the sentence first, the other parts only when not zero. */
function Counts({ state }: { state: RowsPlanState }) {
    const { t } = useTranslation();
    const count = state.answer.count;
    return (
        <div
            className="rows-counts"
            role="status"
            aria-busy={state.refreshing}
            data-testid="rows-counts"
        >
            {state.refreshing && (
                <p className="muted rows-busy">
                    <LoaderCircle className="spin" aria-hidden="true" />
                    {t("rows.plan.checking")}
                </p>
            )}
            {!state.refreshing && !state.stale && count && (
                <>
                    <p data-count="sentence">
                        {t("rows.plan.counts", {
                            in_source: count.in_source,
                            in_table: count.duplicates_in_table,
                            added: count.added,
                        })}
                    </p>
                    {state.shown.on_duplicate === "add_marked" &&
                        count.written > count.added && (
                            <p data-count="marked">
                                {t("rows.plan.markedToo", {
                                    count: count.written - count.added,
                                })}
                            </p>
                        )}
                    {count.left_out > 0 && (
                        <p data-count="left_out">
                            {t("rows.plan.leftOut", { count: count.left_out })}
                        </p>
                    )}
                    {count.without_key > 0 && (
                        <p data-count="without_key">
                            {t("rows.plan.withoutKey", {
                                count: count.without_key,
                            })}
                        </p>
                    )}
                    {count.duplicates_in_source > 0 && (
                        <p data-count="twice">
                            {t("rows.plan.twice", {
                                count: count.duplicates_in_source,
                            })}
                        </p>
                    )}
                </>
            )}
        </div>
    );
}

/** Card 1: the rows that come from the source, and every choice about them. */
function RowsCard({
    w,
    state,
    source,
    preview,
}: {
    w: Workflow;
    state: RowsPlanState;
    source: RowSource;
    preview: Preview | null;
}) {
    const { t } = useTranslation();
    const label = useRowsLabels();
    const words = useConditionWords();
    const rows = w.rows;
    const columns = columnChoices(preview);
    const frozen = !!rows.result || w.locked;
    const { shown, answer } = state;
    const mapped = source.fields.filter((f) => f.field_id in shown.mapping);
    const proposed = (
        <small className="rows-tag" data-testid="proposed">
            {t("rows.plan.proposed")}
        </small>
    );
    return (
        <section className="rows-card" data-testid="rows-plan-rows">
            <h3>
                {t("rows.plan.rowsHeading")}
                <span className="rows-tag">{t("rows.plan.noAi")}</span>
            </h3>
            <div className="rows-line">
                <span className="rows-label">{t("rows.plan.source")}</span>
                <span>
                    {label.source(source)}
                    {rows.task.candidates && (
                        <>
                            {" · "}
                            {t("rows.plan.sourceRead", {
                                count: rows.task.candidates.count,
                            })}
                        </>
                    )}
                </span>
            </div>
            <div className="rows-line">
                <span className="rows-label">{t("rows.plan.take")}</span>
                {shown.conditions.length ? (
                    <ul className="rows-words" data-testid="rows-conditions">
                        {shown.conditions.map((condition, index) => (
                            <li key={index}>{words(condition, source)}</li>
                        ))}
                    </ul>
                ) : (
                    <span>{t("rows.everything")}</span>
                )}
            </div>
            <div className="rows-line">
                <span className="rows-label">{t("rows.plan.gives")}</span>
                <div className="rows-fields">
                    {source.fields.map((field) => {
                        const id = `rows-column-${field.field_id}`;
                        return (
                            <div className="rows-field" key={field.field_id}>
                                <label htmlFor={id}>
                                    {t("rows.plan.columnFor", {
                                        field: label.field(field),
                                    })}
                                </label>
                                <select
                                    id={id}
                                    value={shown.mapping[field.field_id] ?? ""}
                                    disabled={frozen}
                                    onChange={(e) =>
                                        void rows.change({
                                            type: "column",
                                            field: field.field_id,
                                            column: e.target.value,
                                        })
                                    }
                                >
                                    <option value="">
                                        {t("rows.plan.notWritten")}
                                    </option>
                                    {columns.map((column) => (
                                        <option
                                            key={column.letter}
                                            value={column.letter}
                                        >
                                            {column.letter} · {column.name}
                                        </option>
                                    ))}
                                </select>
                                {state.proposed.includes(field.field_id) &&
                                    proposed}
                            </div>
                        );
                    })}
                </div>
            </div>
            <div className="rows-line">
                <span className="rows-label" id="rows-key-label">
                    {t("rows.plan.key")}
                    {state.keyProposed && proposed}
                </span>
                <div>
                    <div
                        className="rows-keys"
                        role="group"
                        aria-labelledby="rows-key-label"
                    >
                        {mapped.map((field) => (
                            <label className="rows-check" key={field.field_id}>
                                <input
                                    type="checkbox"
                                    checked={shown.key.includes(field.field_id)}
                                    disabled={frozen}
                                    onChange={(e) =>
                                        void rows.change({
                                            type: "key",
                                            field: field.field_id,
                                            on: e.target.checked,
                                        })
                                    }
                                />
                                {label.field(field)}
                            </label>
                        ))}
                    </div>
                    <p className="muted">{t("rows.plan.keyHint")}</p>
                </div>
            </div>
            <div className="rows-line">
                <label className="rows-label" htmlFor="rows-on-duplicate">
                    {t("rows.plan.onDuplicate")}
                </label>
                <select
                    id="rows-on-duplicate"
                    value={shown.on_duplicate}
                    disabled={frozen}
                    onChange={(e) =>
                        void rows.change({
                            type: "duplicate",
                            value: e.target.value as "skip" | "add_marked",
                        })
                    }
                >
                    <option value="skip">{t("rows.plan.skip")}</option>
                    <option value="add_marked">
                        {t("rows.plan.addMarked")}
                    </option>
                </select>
            </div>
            <Counts state={state} />
            {answer.refused && !state.refreshing && !state.stale && (
                <Refusal refused={answer.refused} />
            )}
            {!state.refreshing &&
                !state.stale &&
                noMatchNotice(answer.count, state.tableRows) && (
                    <p className="notice" data-testid="rows-no-match">
                        <Info aria-hidden="true" />
                        {t("rows.plan.noMatch")}
                    </p>
                )}
        </section>
    );
}

/**
 * The second step of a task that adds rows: what will be done, in the words
 * of this task, and the sidebar with the one button that runs exactly this plan.
 */
export function RowsPlan({
    w,
    preview = null,
}: {
    w: Workflow;
    preview?: Preview | null;
}) {
    const { t } = useTranslation();
    const rows = w.rows;
    const state = rows.plan;
    const source = rows.source;
    const block = state && !state.refreshing ? runBlock(state.answer) : null;
    const count = state?.answer.count ?? null;
    return (
        <div className="stage-layout">
            <section className="workspace-panel plan-stage">
                <div className="panel-title">
                    <ListChecks aria-hidden="true" />
                    <h2>{t("plan.title")}</h2>
                </div>
                {!state || !source ? (
                    <EmptyState icon="plan" text={t("ws.noPlan")} />
                ) : (
                    <div className="plan-words panel-scroll">
                        <RowsCard
                            w={w}
                            state={state}
                            source={source}
                            preview={preview}
                        />
                        <section
                            className="rows-card"
                            data-testid="rows-plan-columns"
                        >
                            <h3>{t("rows.plan.columnsHeading")}</h3>
                            <p>{t("rows.noColumns")}</p>
                        </section>
                        <section
                            className="rows-card"
                            data-testid="rows-plan-check"
                        >
                            <h3>{t("rows.plan.checkTitle")}</h3>
                            <ul className="rows-list">
                                <li>
                                    {t(
                                        count &&
                                            count.left_out +
                                                count.without_key +
                                                count.duplicates_in_source >
                                                0
                                            ? "rows.plan.check.sumMore"
                                            : "rows.plan.check.sum",
                                    )}
                                </li>
                                <li>
                                    {t(
                                        state.shown.on_duplicate ===
                                            "add_marked"
                                            ? "rows.plan.check.secondMarked"
                                            : "rows.plan.check.second",
                                    )}
                                </li>
                                <li>{t("rows.plan.check.untouched")}</li>
                            </ul>
                        </section>
                    </div>
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("workflow.planSummary")}</h2>
                    {state && (
                        <>
                            {w.instruction.trim() && <p>{w.instruction}</p>}
                            <p
                                className={
                                    state.answer.refused
                                        ? "validation-note"
                                        : "success-text"
                                }
                                data-testid="validation"
                            >
                                {t(
                                    state.answer.refused
                                        ? "rows.plan.refused"
                                        : "rows.plan.passed",
                                )}
                            </p>
                            <p>{t("rows.plan.noModel")}</p>
                            <p className="notice">{t("rows.plan.runNote")}</p>
                        </>
                    )}
                </div>
                <div className="workspace-actions">
                    <div role="status" className="build-checklist-slot">
                        {state && !rows.result && block && (
                            <p
                                className="validation-note"
                                data-testid="rows-cannot-run"
                            >
                                {t(`rows.plan.cantRun.${block}`)}
                            </p>
                        )}
                    </div>
                    <Back w={w} to="dataset" />
                    {rows.result ? (
                        <Button onClick={() => w.navigate("export")}>
                            {t("rows.plan.toResults")}
                        </Button>
                    ) : (
                        <Button
                            disabled={w.locked || !rows.canRun}
                            onClick={() => void w.action(rows.run, "rowsRun")}
                        >
                            {w.operation?.kind === "rowsRun" ? (
                                <LoaderCircle
                                    className="spin"
                                    aria-hidden="true"
                                />
                            ) : (
                                <Play aria-hidden="true" />
                            )}
                            {t("ws.approveRun")}
                        </Button>
                    )}
                </div>
            </aside>
        </div>
    );
}
