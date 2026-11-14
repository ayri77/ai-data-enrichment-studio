import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import { describePlan } from "../planModel";
import type { StepOutcome } from "../runModel";
import { dependencies, graphLayout } from "../workflowModel";

// A node and the room for the arrow that leaves it.
const COLUMN = 252;
const ROW = 144;
const NODE = 212;

/**
 * The plan as a picture for Basic: how its steps connect. Each node carries
 * the number and the words the list below uses, the column it fills and, after
 * a run, how many rows passed it. There are no identifiers and no inspector;
 * selecting a node points at its line in the list, where the details are.
 */
export function PlanDiagram({
    draft,
    outcomes,
    selected,
    onSelect,
}: {
    draft: PlanDraft;
    outcomes?: Map<string, StepOutcome>;
    selected: string;
    onSelect: (id: string) => void;
}) {
    const { t } = useTranslation();
    const plan = draft.plan;
    if (!plan) return null;
    const lines = describePlan(draft);
    const { positions, width, height } = graphLayout(plan, {
        column: COLUMN,
        row: ROW,
        left: 4,
        top: 8,
        // A node is taller once it also says how many rows passed it.
        last: outcomes ? 136 : 104,
    });
    return (
        <div
            className="plan-diagram"
            role="group"
            aria-label={t("plan.diagram.title")}
            data-testid="plan-diagram"
        >
            <div className="graph-canvas" style={{ width, height }}>
                <svg
                    width={width}
                    height={height}
                    className="graph-edges"
                    aria-hidden="true"
                >
                    <defs>
                        <marker
                            id="diagram-arrow"
                            markerWidth="8"
                            markerHeight="8"
                            refX="7"
                            refY="4"
                            orient="auto"
                        >
                            <path d="M0,0 L8,4 L0,8" fill="currentColor" />
                        </marker>
                    </defs>
                    {plan.steps.flatMap((step) =>
                        dependencies(step).map((parent, index) => {
                            const from = positions.get(parent.step);
                            const to = positions.get(step.id);
                            if (!from || !to) return null;
                            const start = from.x + NODE;
                            return (
                                <path
                                    key={`${step.id}:${index}`}
                                    d={`M${start},${from.y + 40} C${start + 22},${from.y + 40} ${to.x - 22},${to.y + 40} ${to.x},${to.y + 40}`}
                                    markerEnd="url(#diagram-arrow)"
                                />
                            );
                        }),
                    )}
                </svg>
                {lines.map((line) => {
                    const at = positions.get(line.id)!;
                    const outcome = outcomes?.get(line.id);
                    return (
                        <button
                            type="button"
                            key={line.id}
                            className="graph-node diagram-node"
                            style={{ left: at.x, top: at.y, width: NODE }}
                            aria-pressed={selected === line.id}
                            data-step={line.id}
                            onClick={() =>
                                onSelect(selected === line.id ? "" : line.id)
                            }
                        >
                            <strong>
                                <b aria-hidden="true">{line.number}</b>
                                <span className="sr-only">
                                    {t("plan.diagram.step", {
                                        number: line.number,
                                    })}
                                </span>
                                {t(`plan.do.${line.action}`, line.values)}
                            </strong>
                            {line.writes.map((write) => (
                                <span
                                    className="plan-step-writes"
                                    key={write.name}
                                >
                                    {t("plan.diagram.writes", { ...write })}
                                </span>
                            ))}
                            {outcome && (
                                <span
                                    className="node-rows"
                                    data-outcome={
                                        outcome.filled >= outcome.total
                                            ? "all"
                                            : outcome.filled
                                              ? "some"
                                              : "none"
                                    }
                                >
                                    {t("exec.step.short", {
                                        filled: outcome.filled,
                                        total: outcome.total,
                                    })}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
