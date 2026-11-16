import { useState } from "react";
import { ArrowRight, CircleAlert, LoaderCircle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LookupKind, PlanDraft, Preview, SourcesInWorkbook } from "../api";
import {
    contextBlocker,
    defaultFallbackForm,
    fallbackOffer,
    formFromDraft,
    formProblems,
    MAX_QUESTION,
    MAX_UNIT,
    readLookupTask,
    settingFor,
    targetNames,
    type FallbackForm,
    type TargetSetting,
} from "../fallbackModel";
import type { PlannerAnswer, SearchRefusal, Workflow } from "../useWorkflow";
import { PlanInvalid, PlanRefusal } from "./PlanDescription";
import { Button } from "./ui/button";

/** The one line that must stay true wherever a universal-search plan is shown. */
export function FallbackBanner({ done = false }: { done?: boolean }) {
    const { t } = useTranslation();
    return (
        <p
            className="notice fallback-banner"
            role="note"
            data-testid="fallback-banner"
        >
            <Search aria-hidden="true" />
            {t(done ? "fallback.bannerDone" : "fallback.banner")}
        </p>
    );
}

/**
 * The one switch, where the task is written: when the system has no source for
 * the task, search the web with AI. Shown only where the server can do it; the
 * person's choice is remembered in this browser.
 */
export function FallbackSwitch({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    if (!w.webLookup) return null;
    return (
        <div className="fallback-switch" data-testid="fallback-switch">
            <label className="fallback-check">
                <input
                    type="checkbox"
                    checked={w.autoSearch}
                    onChange={(e) => w.setAutoSearch(e.target.checked)}
                />
                <span>{t("fallback.switch.label")}</span>
            </label>
            <p className="muted">{t("fallback.switch.hint")}</p>
        </div>
    );
}

/** What the view shows while the search plan is built. */
export function FallbackProgress() {
    const { t } = useTranslation();
    return (
        <p
            className="fallback-progress"
            role="status"
            data-testid="fallback-progress"
        >
            <LoaderCircle className="spin" aria-hidden="true" />
            {t("fallback.auto.building")}
        </p>
    );
}

const KINDS: LookupKind[] = ["text", "number", "yes_no"];

/**
 * Under the planner's refusal, or under the view of a plan that failed the
 * check: one way on, built for the person. A press of the button does what the
 * switch does; the checkbox turns the switch on. The form appears only when
 * the plan could not be built, so that the choices can be fixed.
 */
export function FallbackOffer({
    w,
    draft,
    preview,
}: {
    w: Workflow;
    draft: PlanDraft;
    preview: Preview;
}) {
    const { t } = useTranslation();
    const invalid = fallbackOffer(draft, true) === "invalid";
    return (
        <section
            className="fallback-offer"
            data-testid="fallback-offer"
            aria-label={t("fallback.searchNow")}
        >
            <p data-testid="fallback-lead">
                {t(invalid ? "fallback.invalidText" : "fallback.text")}
            </p>
            {w.searchRefusal ? (
                <FallbackEditor
                    w={w}
                    preview={preview}
                    initial={defaultFallbackForm(
                        draft.instruction,
                        w.mapping,
                        preview,
                    )}
                    refusal={w.searchRefusal}
                    submit="fallback.build"
                />
            ) : (
                <div className="fallback-offer-actions">
                    <Button
                        variant="outline"
                        disabled={w.locked}
                        onClick={() =>
                            void w.action(w.searchInstead, "fallback")
                        }
                    >
                        <Search aria-hidden="true" />
                        {t("fallback.searchNow")}
                    </Button>
                    <label className="fallback-check">
                        <input
                            type="checkbox"
                            checked={w.autoSearch}
                            onChange={(e) => w.setAutoSearch(e.target.checked)}
                        />
                        <span>{t("fallback.autoNext")}</span>
                    </label>
                </div>
            )}
        </section>
    );
}

/** At the top of a search plan: why the planner's way was not taken. */
export function FallbackPlanNotice({ planner }: { planner: PlannerAnswer }) {
    const { t } = useTranslation();
    const invalid = !!planner.draft.plan;
    return (
        <div
            className="notice fallback-auto-notice"
            role="note"
            data-testid="fallback-auto-notice"
        >
            <p>
                {t(invalid ? "fallback.auto.invalid" : "fallback.auto.refused")}
            </p>
            {planner.draft.reason && (
                <p>
                    <span className="muted">{t("plan.modelSays")}</span>{" "}
                    {planner.draft.reason}
                </p>
            )}
            <p>
                {t(
                    planner.by === "switch"
                        ? "fallback.auto.bySwitch"
                        : "fallback.auto.byButton",
                )}
            </p>
        </div>
    );
}

/** The planner's own answer, read-only, after the search plan took its place. */
export function PlannerAnswerView({
    w,
    planner,
}: {
    w: Workflow;
    planner: PlannerAnswer;
}) {
    const { t } = useTranslation();
    return (
        <details className="fallback-planner" data-testid="fallback-planner">
            <summary>{t("fallback.planner.show")}</summary>
            {planner.draft.plan ? (
                <PlanInvalid draft={planner.draft} />
            ) : (
                <PlanRefusal
                    draft={planner.draft}
                    maxRows={w.maxRows}
                    capabilities={w.capabilities}
                />
            )}
        </details>
    );
}

/**
 * Under the description of a search plan: which columns are sent, and a way to
 * change what is sent and the kinds of values. Saving builds the plan again.
 */
export function FallbackPlanExtras({
    w,
    draft,
    preview,
}: {
    w: Workflow;
    draft: PlanDraft;
    preview: Preview | null;
}) {
    const { t } = useTranslation();
    const [editing, setEditing] = useState(false);
    const lookup = readLookupTask(draft.plan, draft.output_columns);
    return (
        <div className="fallback-extras">
            {lookup && (
                <p data-testid="fallback-sent">
                    {t("fallback.sent", { columns: lookup.context.join(", ") })}
                </p>
            )}
            {preview && !w.job && !editing && (
                <div>
                    <Button
                        variant="outline"
                        disabled={w.locked}
                        onClick={() => setEditing(true)}
                    >
                        {t("fallback.change.open")}
                    </Button>
                </div>
            )}
            {preview && !w.job && editing && (
                <section
                    className="fallback-offer"
                    data-testid="fallback-change"
                    aria-label={t("fallback.change.open")}
                >
                    <FallbackEditor
                        w={w}
                        preview={preview}
                        initial={
                            formFromDraft(draft) ??
                            defaultFallbackForm(
                                draft.instruction,
                                w.mapping,
                                preview,
                            )
                        }
                        submit="fallback.change.save"
                        onCancel={() => setEditing(false)}
                    />
                </section>
            )}
        </div>
    );
}

/**
 * The form: the question, the columns that identify a row, the columns to fill
 * with their kinds, where the links go. It starts from the choices it is given
 * and builds the search plan from what is in it.
 */
function FallbackEditor({
    w,
    preview,
    initial,
    refusal: shown = null,
    submit,
    onCancel,
}: {
    w: Workflow;
    preview: Preview;
    initial: FallbackForm;
    refusal?: SearchRefusal | null;
    submit: "fallback.build" | "fallback.change.save";
    onCancel?: () => void;
}) {
    const { t } = useTranslation();
    const [question, setQuestion] = useState(initial.question);
    const [context, setContext] = useState<string[]>(initial.context);
    const [settings, setSettings] = useState<Record<string, TargetSetting>>(
        initial.settings,
    );
    // The links of each value are written next to it unless the person says no.
    const [sources, setSources] = useState<SourcesInWorkbook>(initial.sources);
    const [refusal, setRefusal] = useState<SearchRefusal | null>(shown);
    const form: FallbackForm = { question, context, settings, sources };
    const targets = targetNames(w.mapping);
    const letters = new Map(
        w.mapping.map((entry) => [
            entry.mode === "existing" ? entry.name : entry.name.trim(),
            entry.column,
        ]),
    );
    const problems = formProblems(form, w.mapping, preview);
    const waiting = w.operation?.kind === "fallback";
    const rows = /^\d+$/.test(w.runCount)
        ? Math.min(Number(w.runCount), w.maxRows)
        : w.maxRows;
    const toggle = (name: string) =>
        setContext((old) =>
            old.includes(name)
                ? old.filter((other) => other !== name)
                : [...old, name],
        );
    const change = (name: string, change: Partial<TargetSetting>) =>
        setSettings((old) => ({
            ...old,
            [name]: {
                ...settingFor({ settings: old }, name, w.mapping, preview),
                ...change,
            },
        }));
    return (
        <form
            className="fallback-form"
            data-testid="fallback-form"
            onSubmit={(event) => {
                event.preventDefault();
                if (problems.length || w.locked) return;
                void w.action(async () => {
                    setRefusal(await w.createFallback(form));
                }, "fallback");
            }}
        >
            <label>
                {t("fallback.question")}
                <textarea
                    value={question}
                    maxLength={MAX_QUESTION}
                    disabled={w.locked}
                    aria-describedby="fallback-question-hint"
                    onChange={(e) => setQuestion(e.target.value)}
                />
            </label>
            <p className="muted" id="fallback-question-hint">
                {t("fallback.questionHint")}
            </p>
            <fieldset
                className="fallback-columns"
                disabled={w.locked}
                data-testid="fallback-context"
            >
                <legend>{t("fallback.context")}</legend>
                <p className="muted">{t("fallback.contextHint")}</p>
                {preview.profile.columns.map((column) => {
                    const blocker = targets.includes(column.name)
                        ? "target"
                        : contextBlocker(column.inferred_type);
                    return (
                        <label key={column.name} className="fallback-check">
                            <input
                                type="checkbox"
                                checked={
                                    !blocker && context.includes(column.name)
                                }
                                disabled={!!blocker}
                                onChange={() => toggle(column.name)}
                            />
                            <span>
                                {column.name}
                                {blocker && (
                                    <span className="muted">
                                        {" · "}
                                        {t(`fallback.ctx.${blocker}`)}
                                    </span>
                                )}
                            </span>
                        </label>
                    );
                })}
            </fieldset>
            <fieldset
                className="fallback-columns"
                disabled={w.locked}
                data-testid="fallback-targets"
            >
                <legend>{t("fallback.targets")}</legend>
                <p className="muted">{t("fallback.targetsHint")}</p>
                {targets.map((name) => {
                    const setting = settingFor(form, name, w.mapping, preview);
                    return (
                        <div className="fallback-target" key={name}>
                            <strong>
                                {name}
                                <span className="muted">
                                    {" "}
                                    ({letters.get(name)})
                                </span>
                            </strong>
                            <label>
                                {t("fallback.kind", { name })}
                                <select
                                    value={setting.kind}
                                    onChange={(e) =>
                                        change(name, {
                                            kind: e.target.value as LookupKind,
                                        })
                                    }
                                >
                                    {KINDS.map((kind) => (
                                        <option key={kind} value={kind}>
                                            {t(`fallback.kinds.${kind}`)}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            {setting.kind === "number" && (
                                <label>
                                    {t("fallback.unit", { name })}
                                    <input
                                        value={setting.unit}
                                        maxLength={MAX_UNIT}
                                        placeholder={t("fallback.unitExample")}
                                        onChange={(e) =>
                                            change(name, {
                                                unit: e.target.value,
                                            })
                                        }
                                    />
                                </label>
                            )}
                        </div>
                    );
                })}
            </fieldset>
            <label>
                {t("sources.title")}
                <select
                    value={sources}
                    disabled={w.locked}
                    onChange={(e) =>
                        setSources(e.target.value as SourcesInWorkbook)
                    }
                >
                    {(["none", "notes", "sheet"] as const).map((choice) => (
                        <option key={choice} value={choice}>
                            {t(`sources.${choice}`)}
                        </option>
                    ))}
                </select>
            </label>
            <p className="muted">{t("fallback.sourcesWhy")}</p>
            <div
                className="notice fallback-notice"
                role="note"
                data-testid="fallback-notice"
            >
                <strong>{t("fallback.notice.title")}</strong>
                <ul>
                    <li>{t("fallback.notice.requests")}</li>
                    <li>{t("fallback.notice.sends")}</li>
                    <li>{t("fallback.notice.rows", { rows })}</li>
                    <li>{t("fallback.notice.cost")}</li>
                    <li>{t("fallback.notice.unverified")}</li>
                </ul>
            </div>
            {problems.includes("context") && (
                <p className="muted" data-testid="fallback-need">
                    {t("fallback.need.context")}
                </p>
            )}
            {refusal && (
                <p
                    className="validation-note fallback-refusal"
                    role="alert"
                    data-error-code={refusal.code}
                >
                    <CircleAlert aria-hidden="true" />
                    <span>
                        {t(`error.${refusal.code}`, {
                            defaultValue: t("error.request_failed"),
                        })}
                        {refusal.message && (
                            <>
                                {" "}
                                <span className="fallback-says">
                                    {t("fallback.serverSays")} {refusal.message}
                                </span>
                            </>
                        )}
                    </span>
                </p>
            )}
            <div className="fallback-form-actions">
                <Button
                    type="submit"
                    disabled={problems.length > 0 || w.locked}
                >
                    {t(submit)}
                    {waiting ? (
                        <LoaderCircle className="spin" aria-hidden="true" />
                    ) : (
                        <ArrowRight aria-hidden="true" />
                    )}
                </Button>
                {onCancel && (
                    <Button
                        type="button"
                        variant="outline"
                        disabled={w.locked}
                        onClick={onCancel}
                    >
                        {t("fallback.change.cancel")}
                    </Button>
                )}
            </div>
        </form>
    );
}
