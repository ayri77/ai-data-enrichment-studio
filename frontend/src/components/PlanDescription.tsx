import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import { describePlan, type PlanRef } from "../planModel";
import type { StepOutcome } from "../runModel";
import { CanDo } from "./CanDo";

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
// passed it.
export function PlanDescription({
    draft,
    outcomes,
}: {
    draft: PlanDraft;
    outcomes?: Map<string, StepOutcome>;
}) {
    const { t } = useTranslation();
    const lines = describePlan(draft);
    const reference = (ref: PlanRef) =>
        ref.kind === "column"
            ? t("plan.ref.column", { name: ref.name })
            : ref.kind === "step"
              ? t("plan.ref.step", { number: ref.number })
              : ref.kind === "literal"
                ? t("plan.ref.literal", { value: ref.value })
                : "?";
    return (
        <div className="plan-description">
            <p className="muted">{t("plan.perRow")}</p>
            <ol className="plan-steps">
                {lines.map((line) => (
                    <li key={line.id} data-step={line.id}>
                        <strong>
                            {t(`plan.do.${line.action}`, line.values)}
                        </strong>
                        {line.inputs.length > 0 && (
                            <span className="plan-step-inputs">
                                {line.inputs
                                    .map(
                                        (input) =>
                                            `${t(`plan.port.${input.port}`, {
                                                defaultValue: input.port,
                                            })}: ${reference(input.ref)}`,
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
                            <span className="plan-step-writes" key={write.name}>
                                {t(
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
                ))}
            </ol>
        </div>
    );
}

// The planner answered, but with a refusal instead of a plan. Its reason is
// the model's own text and is shown as such.
export function PlanRefusal({
    draft,
    maxRows,
    capabilities,
}: {
    draft: PlanDraft;
    maxRows: number;
    capabilities?: string[];
}) {
    const { t } = useTranslation();
    const status =
        draft.planning_status === "unsupported" ||
        draft.planning_status === "ambiguous"
            ? draft.planning_status
            : "other";
    return (
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
    );
}

// A plan exists but the check against the table rejected it. The messages are
// the validator's own; the codes stay in Advanced.
export function PlanInvalid({ draft }: { draft: PlanDraft }) {
    const { t } = useTranslation();
    const { validation } = draft;
    return (
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
    );
}
