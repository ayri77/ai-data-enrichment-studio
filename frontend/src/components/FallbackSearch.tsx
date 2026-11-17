import { useState } from "react";
import { ArrowRight, CircleAlert, LoaderCircle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LookupKind, PlanDraft, Preview, SourcesInWorkbook } from "../api";
import {
    columnGroups,
    contextBlocker,
    defaultFallbackForm,
    defaultMixedForm,
    fallbackOffer,
    formFromDraft,
    formProblems,
    MAX_QUESTION,
    MAX_UNIT,
    mixedTargets,
    readLookupTask,
    searchedMapping,
    settingFor,
    targetNames,
    type FallbackForm,
    type TargetSetting,
} from "../fallbackModel";
import { columnLabels } from "../planModel";
import type { OutputTarget } from "../workflowModel";
import type { PlannerAnswer, SearchRefusal, Workflow } from "../useWorkflow";
import { PlanInvalid, PlanRefusal } from "./PlanDescription";
import { Button } from "./ui/button";

/**
 * The one line that must stay true wherever a universal-search plan is shown.
 * For a mixed plan it names the columns the search fills: only those values
 * are found by it and not verified.
 */
export function FallbackBanner({
    done = false,
    searched,
}: {
    done?: boolean;
    /** The columns of a mixed plan that the web search fills, as the page names them. */
    searched?: string;
}) {
    const { t } = useTranslation();
    return (
        <p
            className="notice fallback-banner"
            role="note"
            data-testid="fallback-banner"
        >
            <Search aria-hidden="true" />
            {searched
                ? t(
                      done
                          ? "fallback.mixed.bannerDone"
                          : "fallback.mixed.banner",
                      {
                          columns: searched,
                      },
                  )
                : t(done ? "fallback.bannerDone" : "fallback.banner")}
        </p>
    );
}

/** The banner of a mixed plan, from the plan itself. */
export function MixedBanner({
    draft,
    done = false,
}: {
    draft: Pick<PlanDraft, "plan" | "output_columns" | "searched_targets">;
    done?: boolean;
}) {
    return (
        <FallbackBanner
            done={done}
            searched={columnLabels(columnGroups(draft).searched)}
        />
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

/**
 * What the view shows while the search plan is built; with `columns`, the
 * search for the columns a plan has no source for that is added to it.
 */
export function FallbackProgress({ columns }: { columns?: number }) {
    const { t } = useTranslation();
    return (
        <p
            className="fallback-progress"
            role="status"
            data-testid="fallback-progress"
        >
            <LoaderCircle className="spin" aria-hidden="true" />
            {columns !== undefined
                ? t("fallback.mixed.building", { count: columns })
                : t("fallback.auto.building")}
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
/**
 * Which columns the form fills, and what it does with what is chosen. The
 * default is the whole task's search plan; a mixed plan restricts the form to
 * the columns the planner left without a source and builds from its draft.
 */
interface EditorScope {
    /** The output columns the search fills; the others are not offered. */
    mapping: OutputTarget[];
    /** Further columns the plan writes: they cannot identify a row either. */
    written: string[];
    send: (form: FallbackForm) => Promise<SearchRefusal | null>;
}

function FallbackEditor({
    w,
    preview,
    initial,
    refusal: shown = null,
    submit,
    onCancel,
    scope,
}: {
    w: Workflow;
    preview: Preview;
    initial: FallbackForm;
    refusal?: SearchRefusal | null;
    submit: "fallback.build" | "fallback.change.save" | "fallback.mixed.build";
    onCancel?: () => void;
    scope?: EditorScope;
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
    const mapping = scope?.mapping ?? w.mapping;
    const written = scope?.written ?? [];
    const targets = targetNames(mapping);
    const letters = new Map(
        mapping.map((entry) => [
            entry.mode === "existing" ? entry.name : entry.name.trim(),
            entry.column,
        ]),
    );
    const problems = formProblems(form, mapping, preview);
    const waiting =
        w.operation?.kind === "fallback" || w.operation?.kind === "mixed";
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
                ...settingFor({ settings: old }, name, mapping, preview),
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
                    setRefusal(await (scope?.send ?? w.createFallback)(form));
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
                    const blocker =
                        targets.includes(column.name) ||
                        written.includes(column.name)
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
                    const setting = settingFor(form, name, mapping, preview);
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

/**
 * Under the notice for the columns a plan has no source for: a button that
 * adds a web search for them, and the checkbox that makes it automatic. A
 * press does what the switch does for this plan. When the server refused to
 * build the plan, its message stands with the form, and the plan on the
 * screen stays as it is.
 */
export function UnfilledSearch({
    w,
    draft,
    preview,
}: {
    w: Workflow;
    draft: PlanDraft;
    preview: Preview | null;
}) {
    const { t } = useTranslation();
    const targets = mixedTargets(draft);
    return (
        <div className="unfilled-search" data-testid="unfilled-search">
            <p>{t("fallback.mixed.offer")}</p>
            {w.searchRefusal && preview ? (
                <FallbackEditor
                    w={w}
                    preview={preview}
                    initial={defaultMixedForm(draft, w.mapping, preview)}
                    refusal={w.searchRefusal}
                    submit="fallback.mixed.build"
                    scope={{
                        mapping: searchedMapping(
                            targets,
                            w.mapping,
                            draft.output_columns,
                        ),
                        written: Object.keys(draft.output_columns),
                        send: (form) =>
                            w.createMixed(draft.plan_id, targets, form),
                    }}
                />
            ) : (
                <div className="fallback-offer-actions">
                    <Button
                        variant="outline"
                        disabled={w.locked}
                        onClick={() =>
                            void w.action(w.searchColumnsInstead, "mixed")
                        }
                    >
                        <Search aria-hidden="true" />
                        {t("fallback.mixed.search")}
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
        </div>
    );
}

/**
 * At the top of a mixed plan, in plain words: which columns the system fills
 * from its sources and which it has no source for, so that they are searched
 * on the web with AI.
 */
export function MixedPlanNotice({ draft }: { draft: PlanDraft }) {
    const { t } = useTranslation();
    const { filled, searched } = columnGroups(draft);
    return (
        <div
            className="notice mixed-notice"
            role="note"
            data-testid="mixed-notice"
        >
            <p data-testid="mixed-filled">
                {t("fallback.mixed.filled", { columns: columnLabels(filled) })}
            </p>
            <p data-testid="mixed-searched">
                {t("fallback.mixed.searched", {
                    columns: columnLabels(searched),
                })}
            </p>
        </div>
    );
}

/**
 * Under the description of a mixed plan: which columns are sent, how to change
 * what is sent and the kinds of values of the searched columns (the plan is
 * built again from the same planner draft), and the way back to the system's
 * sources only.
 */
export function MixedPlanExtras({
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
    const targets = draft.searched_targets ?? [];
    const base = draft.planner_plan_id;
    return (
        <div className="fallback-extras" data-testid="mixed-extras">
            {lookup && (
                <p data-testid="fallback-sent">
                    {t("fallback.sent", { columns: lookup.context.join(", ") })}
                </p>
            )}
            {preview && base && !w.job && !editing && (
                <div className="fallback-offer-actions">
                    <Button
                        variant="outline"
                        disabled={w.locked}
                        onClick={() => setEditing(true)}
                    >
                        {t("fallback.change.open")}
                    </Button>
                    <button
                        type="button"
                        className="inline-link"
                        disabled={w.locked}
                        onClick={() => void w.action(w.useSourcesOnly, "mixed")}
                    >
                        {t("fallback.mixed.only")}
                    </button>
                </div>
            )}
            {preview && base && !w.job && editing && (
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
                            defaultMixedForm(draft, w.mapping, preview)
                        }
                        submit="fallback.change.save"
                        onCancel={() => setEditing(false)}
                        scope={{
                            mapping: searchedMapping(
                                targets,
                                w.mapping,
                                draft.output_columns,
                            ),
                            written: Object.keys(draft.output_columns),
                            send: (form) => w.createMixed(base, targets, form),
                        }}
                    />
                </section>
            )}
        </div>
    );
}
