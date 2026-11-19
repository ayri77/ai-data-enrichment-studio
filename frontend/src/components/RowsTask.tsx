import { useRef, type ReactNode } from "react";
import { Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Preview, RowSource, RowSourceField } from "../api";
import {
    MAX_CONDITIONS,
    MAX_DAYS,
    MAX_TEXT,
    operatorsFor,
    valueKind,
    type ConditionDraft,
} from "../rowsModel";
import type { Workflow } from "../useWorkflow";
import { Button } from "./ui/button";

/** The label of a source or a field, in the interface language where there is one. */
export function useRowsLabels() {
    const { t } = useTranslation();
    return {
        source: (source: Pick<RowSource, "source_id" | "label">) =>
            t(`rows.source.${source.source_id}`, {
                defaultValue: source.label,
            }),
        field: (field: Pick<RowSourceField, "field_id" | "label">) =>
            t(`rows.field.${field.field_id}`, { defaultValue: field.label }),
    };
}

/** One condition of "take only": a field, an operator of its kind, a value, remove. */
function ConditionRow({
    w,
    source,
    condition,
    n,
}: {
    w: Workflow;
    source: RowSource;
    condition: ConditionDraft;
    n: number;
}) {
    const { t } = useTranslation();
    const label = useRowsLabels();
    const rows = w.rows;
    const field =
        source.fields.find((f) => f.field_id === condition.field_id) ??
        source.fields[0];
    const kind = valueKind(condition.op);
    const incomplete = condition.value === "";
    const value = { "aria-label": t("rows.condValue", { n }) };
    return (
        <div className="rows-condition" data-testid="rows-condition">
            <select
                aria-label={t("rows.condField", { n })}
                value={field.field_id}
                onChange={(e) =>
                    w.edit(() =>
                        rows.changeCondition(condition.id, {
                            field: e.target.value,
                        }),
                    )
                }
            >
                {source.fields.map((f) => (
                    <option key={f.field_id} value={f.field_id}>
                        {label.field(f)}
                    </option>
                ))}
            </select>
            <select
                aria-label={t("rows.condOp", { n })}
                value={condition.op}
                onChange={(e) =>
                    w.edit(() =>
                        rows.changeCondition(condition.id, {
                            op: e.target.value,
                        }),
                    )
                }
            >
                {operatorsFor(field.kind).map((op) => (
                    <option key={op} value={op}>
                        {t(`rows.op.${op}`)}
                    </option>
                ))}
            </select>
            {kind === "days" ? (
                <input
                    {...value}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={MAX_DAYS}
                    value={condition.value}
                    aria-invalid={incomplete}
                    onChange={(e) =>
                        w.edit(() =>
                            rows.changeCondition(condition.id, {
                                value: e.target.value,
                            }),
                        )
                    }
                />
            ) : kind === "date" ? (
                <input
                    {...value}
                    type="date"
                    value={condition.value}
                    aria-invalid={incomplete}
                    onChange={(e) =>
                        w.edit(() =>
                            rows.changeCondition(condition.id, {
                                value: e.target.value,
                            }),
                        )
                    }
                />
            ) : (
                <input
                    {...value}
                    type="text"
                    maxLength={MAX_TEXT}
                    value={condition.value}
                    aria-invalid={incomplete}
                    onChange={(e) =>
                        w.edit(() =>
                            rows.changeCondition(condition.id, {
                                value: e.target.value,
                            }),
                        )
                    }
                />
            )}
            <button
                type="button"
                className="icon-button"
                aria-label={t("rows.condRemove", { n })}
                onClick={() => w.edit(() => rows.removeCondition(condition.id))}
            >
                <X aria-hidden="true" />
            </button>
        </div>
    );
}

/**
 * Where new rows come from: the source, the files that connect it, what it
 * gives and the conditions that choose among the rows. It says before anything
 * is chosen that nothing leaves this computer.
 */
function SourceBlock({ w, source }: { w: Workflow; source: RowSource }) {
    const { t } = useTranslation();
    const label = useRowsLabels();
    const rows = w.rows;
    const input = useRef<HTMLInputElement>(null);
    const candidates = rows.task.candidates;
    return (
        <div className="rows-source" data-testid="rows-source">
            <fieldset className="rows-radios">
                <legend>{t("rows.where")}</legend>
                <label className="rows-check">
                    <input
                        type="radio"
                        name="rows-where"
                        checked
                        onChange={() => undefined}
                    />
                    {t("rows.have")}
                </label>
                <label className="rows-check">
                    <input type="radio" name="rows-where" disabled />
                    <span>
                        {t("rows.find")}{" "}
                        <span className="rows-tag">{t("rows.later")}</span>
                    </span>
                </label>
            </fieldset>
            <label>
                {t("rows.source")}
                <select
                    value={source.source_id}
                    onChange={(e) =>
                        w.edit(() => rows.setSource(e.target.value))
                    }
                >
                    {rows.sources.map((s) => (
                        <option
                            key={s.source_id}
                            value={s.source_id}
                            disabled={!s.available}
                        >
                            {label.source(s)}
                        </option>
                    ))}
                </select>
            </label>
            <div className="rows-block">
                <span className="rows-label">{t("rows.connect")}</span>
                <input
                    ref={input}
                    type="file"
                    multiple
                    accept={source.file_suffixes.join(",")}
                    className="sr-only"
                    aria-label={t("rows.input")}
                    onChange={(e) => {
                        const files = [...(e.target.files ?? [])];
                        e.target.value = "";
                        void w.action(() => rows.readFiles(files), "rowsFiles");
                    }}
                />
                <div className="rows-connect">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => input.current?.click()}
                    >
                        {t("rows.choose")}
                    </Button>
                    <span
                        className="muted"
                        role="status"
                        data-testid="rows-read"
                    >
                        {candidates
                            ? t("rows.files.read", {
                                  count: candidates.count,
                                  skipped: candidates.skipped,
                              })
                            : ""}
                    </span>
                </div>
            </div>
            <div className="rows-block">
                <span className="rows-label">{t("rows.gives")}</span>
                <ul className="rows-chips" data-testid="rows-fields">
                    {source.fields.map((f) => (
                        <li className="rows-chip" key={f.field_id}>
                            {label.field(f)}
                        </li>
                    ))}
                </ul>
            </div>
            <div className="rows-block">
                <span className="rows-label">{t("rows.take")}</span>
                {rows.task.conditions.map((condition, index) => (
                    <ConditionRow
                        key={condition.id}
                        w={w}
                        source={source}
                        condition={condition}
                        n={index + 1}
                    />
                ))}
                <div>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={rows.task.conditions.length >= MAX_CONDITIONS}
                        onClick={() => w.edit(rows.addCondition)}
                    >
                        <Plus aria-hidden="true" />
                        {t("rows.addCondition")}
                    </Button>
                </div>
            </div>
            <p className="muted" data-testid="rows-privacy">
                {t("rows.privacy")}
            </p>
        </div>
    );
}

/**
 * The Rows part of the task on the first step: two optional parts, not two
 * modes. `children` is what belongs under the first one (the number of rows
 * to fill). Both parts at once are not possible in one run yet, and the page
 * says so and what to do instead.
 */
export function RowsSection({
    w,
    children,
}: {
    w: Workflow;
    preview?: Preview | null;
    children?: ReactNode;
}) {
    const { t } = useTranslation();
    const rows = w.rows;
    const { task, source } = rows;
    return (
        <section className="rows-section" aria-labelledby="rows-heading">
            <h3 id="rows-heading">{t("rows.rowsHeading")}</h3>
            <label className="rows-check">
                <input
                    type="checkbox"
                    checked={task.fill}
                    onChange={(e) =>
                        w.edit(() => rows.setFill(e.target.checked))
                    }
                />
                {t("rows.fillRows")}
            </label>
            {task.fill && children}
            <label className="rows-check">
                <input
                    type="checkbox"
                    checked={task.add}
                    onChange={(e) =>
                        w.edit(() => rows.setAdd(e.target.checked))
                    }
                />
                {t("rows.addRows")}
            </label>
            {task.add && source && <SourceBlock w={w} source={source} />}
            <div role="status">
                {rows.mode === "both" && (
                    <p className="notice" data-testid="rows-both">
                        {t("rows.both.notice")}
                    </p>
                )}
            </div>
        </section>
    );
}

/** The Columns part of the task: its heading, and one line when nothing is filled. */
export function ColumnsHeading({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return (
        <>
            <h3 id="columns-heading">{t("rows.columnsHeading")}</h3>
            {!w.rows.task.fill && (
                <p className="muted" data-testid="rows-no-columns">
                    {t("rows.noColumns")}
                </p>
            )}
        </>
    );
}

/** What is still missing before a plan of new rows can be built. */
export function RowsNeeds({
    w,
    hasWorkbook,
}: {
    w: Workflow;
    hasWorkbook: boolean;
}) {
    const { t } = useTranslation();
    if (!w.rows.needs.length) return null;
    const text = {
        preview: t(hasWorkbook ? "ws.need.preview" : "ws.need.workbook"),
        part: t("rows.need.part"),
        files: t("rows.need.files"),
        conditions: t("rows.need.conditions"),
    };
    return (
        <div className="build-checklist" role="status">
            <strong>{t("ws.need.title")}</strong>
            <ul>
                {w.rows.needs.map((key) => (
                    <li key={key} data-requirement={key}>
                        {text[key]}
                    </li>
                ))}
            </ul>
        </div>
    );
}
