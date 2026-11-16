import { useState } from "react";
import { ArrowRight, CircleAlert, LoaderCircle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LookupKind, PlanDraft, Preview, SourcesInWorkbook } from "../api";
import {
    contextBlocker,
    formProblems,
    type FallbackOffer,
    MAX_QUESTION,
    MAX_UNIT,
    settingFor,
    targetNames,
    type FallbackForm,
    type TargetSetting,
} from "../fallbackModel";
import type { Workflow } from "../useWorkflow";
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

const KINDS: LookupKind[] = ["text", "number", "yes_no"];

/**
 * The offer on the view that says why no plan could be built: another way of
 * working, set up here and built into a draft by the server. It is not a
 * dialog; the plan it builds replaces this view.
 */
export function FallbackSearch({
    w,
    draft,
    preview,
    offer = "refused",
}: {
    w: Workflow;
    draft: PlanDraft;
    preview: Preview;
    /** Why it is offered: the planner refused, or its plan failed the check. */
    offer?: FallbackOffer;
}) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const [question, setQuestion] = useState(draft.instruction);
    // Nothing is chosen for the person: these values leave the server.
    const [context, setContext] = useState<string[]>([]);
    const [settings, setSettings] = useState<Record<string, TargetSetting>>({});
    // The links of each value are written next to it unless the person says no.
    const [sources, setSources] = useState<SourcesInWorkbook>("notes");
    const [refusal, setRefusal] = useState<{
        code: string;
        message: string;
    } | null>(null);
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
        <section
            className="fallback-offer"
            data-testid="fallback-offer"
            aria-labelledby="fallback-title"
        >
            <h3 id="fallback-title">
                <Search aria-hidden="true" />
                {t("fallback.title")}
            </h3>
            <p data-testid="fallback-lead">
                {t(
                    offer === "invalid"
                        ? "fallback.invalidText"
                        : "fallback.text",
                )}
            </p>
            {!open ? (
                <div>
                    <Button variant="outline" onClick={() => setOpen(true)}>
                        {t("fallback.open")}
                    </Button>
                </div>
            ) : (
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
                                <label
                                    key={column.name}
                                    className="fallback-check"
                                >
                                    <input
                                        type="checkbox"
                                        checked={
                                            !blocker &&
                                            context.includes(column.name)
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
                            const setting = settingFor(
                                form,
                                name,
                                w.mapping,
                                preview,
                            );
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
                                                    kind: e.target
                                                        .value as LookupKind,
                                                })
                                            }
                                        >
                                            {KINDS.map((kind) => (
                                                <option key={kind} value={kind}>
                                                    {t(
                                                        `fallback.kinds.${kind}`,
                                                    )}
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
                                                placeholder={t(
                                                    "fallback.unitExample",
                                                )}
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
                            {(["none", "notes", "sheet"] as const).map(
                                (choice) => (
                                    <option key={choice} value={choice}>
                                        {t(`sources.${choice}`)}
                                    </option>
                                ),
                            )}
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
                                            {t("fallback.serverSays")}{" "}
                                            {refusal.message}
                                        </span>
                                    </>
                                )}
                            </span>
                        </p>
                    )}
                    <div>
                        <Button
                            type="submit"
                            disabled={problems.length > 0 || w.locked}
                        >
                            {t("fallback.build")}
                            {waiting ? (
                                <LoaderCircle
                                    className="spin"
                                    aria-hidden="true"
                                />
                            ) : (
                                <ArrowRight aria-hidden="true" />
                            )}
                        </Button>
                    </div>
                </form>
            )}
        </section>
    );
}
