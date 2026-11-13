import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import { describePlan, type PlanRef } from "../planModel";
import { CanDo } from "./CanDo";

// The plan in words: what is done for each row and where the result goes.
export function PlanDescription({ draft }: { draft: PlanDraft }) {
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
