import { useState, type ReactNode } from "react";
import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import { LOOKUP_CAPABILITY, readLookupTask } from "../fallbackModel";
import {
    columnLabels,
    describePlan,
    unfilledColumns,
    type PlanRef,
} from "../planModel";
import type { StepOutcome } from "../runModel";
import { CanDo } from "./CanDo";
import { PlanDiagram } from "./PlanDiagram";

/** How many rows passed one step, as a bar and in words. */
export function StepRows({ outcome }: { outcome: StepOutcome }) {
    const { t } = useTranslation();
    const share = (rows: number) =>
        `${outcome.total ? (rows / outcome.total) * 100 : 0}%`;
    return (
        <span
            className="step-rows"
            data-outcome={
                outcome.filled >= outcome.total
                    ? "all"
                    : outcome.filled
                      ? "some"
                      : "none"
            }
        >
            <span className="step-meter" aria-hidden="true">
                <i
                    className="filled"
                    style={{ width: share(outcome.filled) }}
                />
                <i
                    className="failed"
                    style={{ width: share(outcome.failed) }}
                />
                <i
                    className="skipped"
                    style={{ width: share(outcome.skipped) }}
                />
            </span>
            <span>
                {[
                    t("exec.step.filled", {
                        filled: outcome.filled,
                        total: outcome.total,
                    }),
                    ...(outcome.chosen
                        ? [t("exec.step.chosen", { n: outcome.chosen })]
                        : []),
                    ...(outcome.failed
                        ? [t("exec.step.failed", { n: outcome.failed })]
                        : []),
                    ...(outcome.skipped
                        ? [t("exec.step.skipped", { n: outcome.skipped })]
                        : []),
                ].join(" · ")}
            </span>
        </span>
    );
}

// The plan in words: what is done for each row and where the result goes.
// With the outcomes of a finished run, each step also says how many rows
// passed it. A plan of several steps is drawn above the list as well, because
// the list cannot show that steps branch and join; a step selected in the
// picture is pointed at in the list.
export function PlanDescription({
    draft,
    outcomes,
}: {
    draft: PlanDraft;
    outcomes?: Map<string, StepOutcome>;
}) {
    const { t } = useTranslation();
    const lines = describePlan(draft);
    const [selected, setSelected] = useState("");
    const select = (id: string) => {
        setSelected(id);
        if (id)
            document
                .querySelector(`.plan-steps [data-step="${CSS.escape(id)}"]`)
                ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    };
    const lookup = readLookupTask(draft.plan, draft.output_columns);
    const reference = (ref: PlanRef) =>
        ref.kind === "column"
            ? t("plan.ref.column", { name: ref.name })
            : ref.kind === "step"
              ? t("plan.ref.step", { number: ref.number })
              : ref.kind === "literal"
                ? t("plan.ref.literal", { value: ref.value })
                : "?";
    // The universal search is told in words about its question and columns;
    // its task is never shown as the text it is stored in.
    if (lookup && draft.kind === "fallback" && lines.length === 1)
        return (
            <LookupDescription
                lookup={lookup}
                stepId={lines[0].id}
                outcome={outcomes?.get(lines[0].id)}
            />
        );
    return (
        <div className="plan-description">
            {lines.length > 1 && (
                <>
                    <p className="muted">{t("plan.diagram.hint")}</p>
                    <PlanDiagram
                        draft={draft}
                        outcomes={outcomes}
                        selected={selected}
                        onSelect={select}
                    />
                </>
            )}
            <p className="muted">{t("plan.perRow")}</p>
            <ol className="plan-steps">
                {lines.map((line) =>
                    lookup &&
                    draft.kind === "mixed" &&
                    line.capability === LOOKUP_CAPABILITY ? (
                        <LookupStep
                            key={line.id}
                            lookup={lookup}
                            stepId={line.id}
                            outcome={outcomes?.get(line.id)}
                            selected={selected === line.id}
                        />
                    ) : (
                        <li
                            key={line.id}
                            data-step={line.id}
                            aria-current={
                                selected === line.id ? "true" : undefined
                            }
                        >
                            <strong>
                                {t(`plan.do.${line.action}`, line.values)}
                            </strong>
                            {line.inputs.length > 0 && (
                                <span className="plan-step-inputs">
                                    {line.inputs
                                        .map(
                                            (input) =>
                                                `${t(
                                                    `plan.port.${input.port}`,
                                                    {
                                                        defaultValue:
                                                            input.port,
                                                    },
                                                )}: ${reference(input.ref)}`,
                                        )
                                        .join(" · ")}
                                </span>
                            )}
                            {line.notes.map((note) => (
                                <span className="muted" key={note}>
                                    {t(`plan.note.${note}`)}
                                </span>
                            ))}
                            {line.writes.map((write) => (
                                <span
                                    className="plan-step-writes"
                                    key={write.name}
                                >
                                    {write.field
                                        ? t("plan.writesField", {
                                              ...write,
                                              field: t(
                                                  `plan.field.${write.field}`,
                                                  {
                                                      defaultValue: write.field,
                                                  },
                                              ),
                                          })
                                        : t(
                                              write.unit
                                                  ? "plan.writesUnit"
                                                  : "plan.writes",
                                              { ...write },
                                          )}
                                </span>
                            ))}
                            {outcomes?.get(line.id) && (
                                <StepRows outcome={outcomes.get(line.id)!} />
                            )}
                        </li>
                    ),
                )}
            </ol>
        </div>
    );
}

/** The one step of a universal-search plan, in the words a person would use. */
function LookupDescription({
    lookup,
    stepId,
    outcome,
}: {
    lookup: NonNullable<ReturnType<typeof readLookupTask>>;
    stepId: string;
    outcome?: StepOutcome;
}) {
    const { t } = useTranslation();
    return (
        <div className="plan-description" data-testid="fallback-plan">
            <p className="muted">{t("plan.perRow")}</p>
            <ol className="plan-steps">
                <LookupStep lookup={lookup} stepId={stepId} outcome={outcome} />
            </ol>
        </div>
    );
}

/**
 * The web search as a line of the list: its question, the columns that
 * identify a row and the columns it fills with their kinds. In a mixed plan it
 * stands among the planner's steps and says so in its first line.
 */
function LookupStep({
    lookup,
    stepId,
    outcome,
    selected = false,
}: {
    lookup: NonNullable<ReturnType<typeof readLookupTask>>;
    stepId: string;
    outcome?: StepOutcome;
    selected?: boolean;
}) {
    const { t } = useTranslation();
    return (
        <li
            data-step={stepId}
            data-lookup="true"
            aria-current={selected ? "true" : undefined}
        >
            <strong>{t("cap.web_lookup.title")}</strong>
            <span className="muted">{t("cap.web_lookup.text")}</span>
            <span className="plan-step-inputs">
                {t("fallback.plan.question", {
                    question: lookup.question,
                })}
            </span>
            <span className="plan-step-inputs">
                {t("fallback.plan.context", {
                    columns: lookup.context.join(", "),
                })}
            </span>
            <span className="plan-step-writes">{t("fallback.plan.fills")}</span>
            <span className="fallback-fills">
                {lookup.columns.map((column) => (
                    <span className="fallback-fill" key={column.header}>
                        {t("fallback.plan.column", {
                            name: column.header,
                            letter: column.letter ?? "?",
                            kind:
                                column.kind === "number" && column.unit
                                    ? t("fallback.plan.numberUnit", {
                                          unit: column.unit,
                                      })
                                    : t(`fallback.plan.kind.${column.kind}`),
                        })}
                    </span>
                ))}
            </span>
            {outcome && <StepRows outcome={outcome} />}
        </li>
    );
}

// Chosen columns the system has no source for. The plan fills the others; the
// listed ones stay as they are, unless the page offers a web search for them
// (its button and checkbox are the caller's, inside the notice). Shown above
// the steps in Basic and Advanced.
export function UnfilledNotice({
    draft,
    children,
}: {
    draft: PlanDraft;
    children?: ReactNode;
}) {
    const { t } = useTranslation();
    const columns = unfilledColumns(draft);
    if (columns.length === 0) return null;
    return (
        <div className="notice" data-testid="unfilled-notice">
            <p>
                {t("plan.unfilled", {
                    columns: columnLabels(columns),
                })}
            </p>
            {children}
        </div>
    );
}

// The planner answered, but with a refusal instead of a plan. Its reason is
// the model's own text and is shown as such. What follows the explanation (the
// offer of another way of working) is the caller's.
export function PlanRefusal({
    draft,
    maxRows,
    capabilities,
    children,
}: {
    draft: PlanDraft;
    maxRows: number;
    capabilities?: string[];
    children?: ReactNode;
}) {
    const { t } = useTranslation();
    const status =
        draft.planning_status === "unsupported" ||
        draft.planning_status === "ambiguous"
            ? draft.planning_status
            : "other";
    return (
        <>
            <div className="plan-problem" data-testid="plan-refusal">
                <h3>
                    <CircleAlert aria-hidden="true" />
                    {t("plan.refused.title")}
                </h3>
                <p>{t(`plan.refused.${status}`)}</p>
                {draft.reason && (
                    <p>
                        <span className="muted">{t("plan.modelSays")}</span>{" "}
                        {draft.reason}
                    </p>
                )}
                <CanDo maxRows={maxRows} capabilities={capabilities} />
                <p>{t("plan.rephrase")}</p>
            </div>
            {children}
        </>
    );
}

// A plan exists but the check against the table rejected it. The messages are
// the validator's own; the codes stay in Advanced.
// What follows the messages (another way of working) is the caller's.
export function PlanInvalid({
    draft,
    children,
}: {
    draft: PlanDraft;
    children?: ReactNode;
}) {
    const { t } = useTranslation();
    const { validation } = draft;
    return (
        <>
            <div className="plan-problem" data-testid="plan-invalid">
                <h3>
                    <CircleAlert aria-hidden="true" />
                    {t("plan.invalid.title")}
                </h3>
                <p>
                    {t(
                        validation.contract_valid
                            ? "plan.invalid.conflict"
                            : "plan.invalid.text",
                    )}
                </p>
                {validation.errors.length > 0 && (
                    <details>
                        <summary>{t("plan.invalid.details")}</summary>
                        <ul>
                            {validation.errors.map((err, i) => (
                                <li key={i}>{err.message}</li>
                            ))}
                        </ul>
                    </details>
                )}
                <p>{t("plan.rephrase")}</p>
            </div>
            {children}
        </>
    );
}
