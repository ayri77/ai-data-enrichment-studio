import { CircleAlert, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ExtractionKind } from "../api";
import {
    blankColumn,
    columnProblem,
    EXTRACTION_KINDS,
    MAX_EXTRACTION_COLUMNS,
    MAX_EXTRACTION_HEADER,
    MAX_EXTRACTION_OPTION,
    MAX_EXTRACTION_OPTIONS,
    MAX_EXTRACTION_QUESTION,
    type ColumnProblem,
    type ExtractionColumnDraft,
    oneLine,
} from "../mailModel";
import { useHosted } from "../mode";
import type { Preview } from "../api";
import type { Workflow } from "../useWorkflow";
import { Button } from "./ui/button";

/**
 * The one line that must stay true wherever an extraction plan is shown: the
 * text of every mail in the run leaves for OpenAI, and what is read from it is
 * not verified beyond the quote it rests on.
 */
export function ExtractionBanner({ done = false }: { done?: boolean }) {
    const { t } = useTranslation();
    return (
        <p
            className="notice fallback-banner"
            role="note"
            data-testid="extraction-banner"
        >
            <ShieldCheck aria-hidden="true" />
            {t(done ? "extract.bannerDone" : "extract.banner")}
        </p>
    );
}

/** What a problem with one column says; its values are the bounds the server has. */
function problemValues(problem: ColumnProblem) {
    switch (problem) {
        case "long":
            return { max: MAX_EXTRACTION_HEADER };
        case "optionCount":
            return { max: MAX_EXTRACTION_OPTIONS };
        case "optionLong":
            return { max: MAX_EXTRACTION_OPTION };
        default:
            return {};
    }
}

/**
 * Where a table of e-mails cannot be read yet: one sentence saying what is
 * missing, from what the server tells openly about its provider, and the way to
 * set the key where the application has one in its interface.
 */
function ExtractionUnavailable({
    w,
    onOpenSettings,
}: {
    w: Workflow;
    onOpenSettings?: () => void;
}) {
    const { t } = useTranslation();
    const hosted = useHosted();
    const reason = !w.provider
        ? w.planningReadiness === "loading"
            ? "loading"
            : "other"
        : !w.provider.key_present
          ? "key"
          : !w.provider.server_network_allowed
            ? "network"
            : "other";
    return (
        <p
            className="notice"
            role="status"
            data-testid="extraction-unavailable"
            data-reason={reason}
        >
            {t(`extract.unavailable.${reason}`)}
            {reason === "key" && !hosted && onOpenSettings && (
                <>
                    {" "}
                    <button
                        type="button"
                        className="inline-link"
                        onClick={onOpenSettings}
                    >
                        {t("ws.need.openSettings")}
                    </button>
                </>
            )}
        </p>
    );
}

/**
 * What to read from each mail: the question and the columns to fill, each with
 * its kind (and, for a choice, its options). The server's bounds are checked
 * here with a message next to the column; the draft is the workflow's, so it
 * stays when the person goes to another step and back.
 */
export function ExtractionTask({
    w,
    preview,
    onOpenSettings,
}: {
    w: Workflow;
    preview: Preview;
    onOpenSettings?: () => void;
}) {
    const { t } = useTranslation();
    if (!w.textExtraction)
        return <ExtractionUnavailable w={w} onOpenSettings={onOpenSettings} />;
    const columns = w.extractionColumns;
    const tableColumns = preview.profile.columns.map((column) => column.name);
    const set = (next: ExtractionColumnDraft[]) =>
        w.edit(() => w.setExtractionColumns(next));
    const update = (id: number, change: Partial<ExtractionColumnDraft>) =>
        set(
            columns.map((column) =>
                column.id === id ? { ...column, ...change } : column,
            ),
        );
    return (
        <div className="extract-form" data-testid="extraction-form">
            <h3>{t("extract.title")}</h3>
            <label>
                {t("extract.question")}
                <textarea
                    rows={3}
                    value={w.instruction}
                    maxLength={MAX_EXTRACTION_QUESTION}
                    placeholder={t("extract.questionExample")}
                    aria-describedby="extract-question-hint"
                    onChange={(e) =>
                        w.edit(() => w.setInstruction(oneLine(e.target.value)))
                    }
                />
            </label>
            <p className="muted" id="extract-question-hint">
                {t("extract.questionHint")}
            </p>
            <fieldset
                className="fallback-columns"
                data-testid="extraction-columns"
            >
                <legend>{t("extract.columns")}</legend>
                <p className="muted">{t("extract.columnsHint")}</p>
                {columns.map((column, index) => {
                    const problem = columnProblem(
                        column,
                        columns,
                        tableColumns,
                    );
                    const n = index + 1;
                    return (
                        <div
                            className="extract-column"
                            key={column.id}
                            data-testid="extraction-column"
                        >
                            <label>
                                {t("extract.name", { n })}
                                <input
                                    value={column.name}
                                    maxLength={MAX_EXTRACTION_HEADER}
                                    aria-invalid={
                                        !!problem && problem !== "blank"
                                    }
                                    onChange={(e) =>
                                        update(column.id, {
                                            name: e.target.value,
                                        })
                                    }
                                />
                            </label>
                            <label>
                                {t("extract.kind", { n })}
                                <select
                                    value={column.kind}
                                    onChange={(e) =>
                                        update(column.id, {
                                            kind: e.target
                                                .value as ExtractionKind,
                                        })
                                    }
                                >
                                    {EXTRACTION_KINDS.map((kind) => (
                                        <option key={kind} value={kind}>
                                            {t(`extract.kinds.${kind}`)}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <Button
                                variant="outline"
                                type="button"
                                disabled={columns.length === 1}
                                aria-label={t("extract.remove", { n })}
                                onClick={() =>
                                    set(columns.filter((c) => c !== column))
                                }
                            >
                                ×
                            </Button>
                            {column.kind === "one_of" && (
                                <label className="extract-options">
                                    {t("extract.options", { n })}
                                    <input
                                        value={column.options}
                                        placeholder={t(
                                            "extract.optionsExample",
                                        )}
                                        onChange={(e) =>
                                            update(column.id, {
                                                options: e.target.value,
                                            })
                                        }
                                    />
                                </label>
                            )}
                            {problem && problem !== "blank" && (
                                <p
                                    className="validation-note extract-problem"
                                    role="alert"
                                    data-problem={problem}
                                >
                                    <CircleAlert aria-hidden="true" />
                                    <span>
                                        {t(`extract.problem.${problem}`, {
                                            n,
                                            name: column.name.trim(),
                                            ...problemValues(problem),
                                        })}
                                    </span>
                                </p>
                            )}
                        </div>
                    );
                })}
                <div>
                    <Button
                        variant="outline"
                        type="button"
                        disabled={columns.length >= MAX_EXTRACTION_COLUMNS}
                        onClick={() =>
                            set([
                                ...columns,
                                blankColumn(
                                    Math.max(0, ...columns.map((c) => c.id)) +
                                        1,
                                ),
                            ])
                        }
                    >
                        {t("extract.add")}
                    </Button>
                </div>
            </fieldset>
            <div
                className="notice extract-trust"
                role="note"
                data-testid="extraction-trust"
            >
                <strong>{t("extract.trust.title")}</strong>
                <p>{t("extract.trust.rule")}</p>
                <p>{t("extract.trust.wording")}</p>
            </div>
            <div
                className="notice fallback-notice"
                role="note"
                data-testid="extraction-notice"
            >
                <strong>{t("extract.notice.title")}</strong>
                <ul>
                    <li>{t("extract.notice.sends")}</li>
                    <li>{t("extract.notice.masked")}</li>
                    <li>{t("extract.notice.cost")}</li>
                </ul>
            </div>
            {w.extractionRefusal && (
                <p
                    className="validation-note fallback-refusal"
                    role="alert"
                    data-error-code="extraction_plan_invalid"
                    data-testid="extraction-refusal"
                >
                    <CircleAlert aria-hidden="true" />
                    <span>
                        {t("error.extraction_plan_invalid")}{" "}
                        <span className="fallback-says">
                            {t("fallback.serverSays")} {w.extractionRefusal}
                        </span>
                    </span>
                </p>
            )}
        </div>
    );
}
