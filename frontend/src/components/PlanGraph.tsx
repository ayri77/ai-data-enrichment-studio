import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import type { StepOutcome } from "../runModel";
import { dependencies, graphLayout } from "../workflowModel";
import { StepRows } from "./PlanDescription";

// One column of nodes: a node and the room for the arrows that leave it.
const COLUMN = 264;
const ROW = 160;

/**
 * The plan as a graph. With the outcomes of a finished run each node also
 * shows how many rows passed that step, and the inspector says why the others
 * did not.
 */
export function PlanGraph({
    draft,
    outcomes,
}: {
    draft: PlanDraft;
    outcomes?: Map<string, StepOutcome>;
}) {
    const { t } = useTranslation();
    const [selected, setSelected] = useState("");
    const plan = draft.plan;
    if (!plan) return null;
    const step = plan.steps.find((x) => x.id === selected) ?? plan.steps[0];
    // Presentation only: server validation remains authoritative, including cycles.
    const { positions, width, height } = graphLayout(plan, {
        column: COLUMN,
        row: ROW,
        minWidth: 520,
        minHeight: 270,
    });
    return (
        <div className="graph-inspector">
            <div
                className="graph-scroll panel-scroll"
                role="region"
                aria-label={t("ws.graph")}
                tabIndex={0}
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
                                id="arrow"
                                markerWidth="8"
                                markerHeight="8"
                                refX="7"
                                refY="4"
                                orient="auto"
                            >
                                <path d="M0,0 L8,4 L0,8" fill="currentColor" />
                            </marker>
                        </defs>
                        {plan.steps.flatMap((s) =>
                            dependencies(s).map((parent, i) => {
                                const from = positions.get(parent.step),
                                    to = positions.get(s.id);
                                if (!from || !to) return null;
                                return (
                                    <path
                                        key={`${s.id}:${i}`}
                                        d={`M${from.x + 224},${from.y + 48} C${from.x + 248},${from.y + 48} ${to.x - 24},${to.y + 48} ${to.x},${to.y + 48}`}
                                        markerEnd="url(#arrow)"
                                    />
                                );
                            }),
                        )}
                    </svg>
                    {plan.steps.map((s) => (
                        <button
                            type="button"
                            key={s.id}
                            className="graph-node"
                            style={{
                                left: positions.get(s.id)!.x,
                                top: positions.get(s.id)!.y,
                            }}
                            aria-pressed={step?.id === s.id}
                            data-step={s.id}
                            onClick={() => setSelected(s.id)}
                        >
                            <small>{s.capability}</small>
                            <strong>{s.id}</strong>
                            <span>
                                {dependencies(s).length
                                    ? dependencies(s)
                                          .map((p) => `${p.step} → ${p.port}`)
                                          .join(" · ")
                                    : t("ws.sourceInputs")}
                            </span>
                            {outcomes?.get(s.id) && (
                                <span
                                    className="node-rows"
                                    data-outcome={
                                        outcomes.get(s.id)!.filled >=
                                        outcomes.get(s.id)!.total
                                            ? "all"
                                            : outcomes.get(s.id)!.filled
                                              ? "some"
                                              : "none"
                                    }
                                >
                                    {t("exec.step.short", {
                                        filled: outcomes.get(s.id)!.filled,
                                        total: outcomes.get(s.id)!.total,
                                    })}
                                </span>
                            )}
                        </button>
                    ))}
                </div>
            </div>
            <aside
                className="node-inspector panel-scroll"
                data-advanced="true"
                aria-label={t("ws.nodeInspector")}
            >
                <h3>{t("ws.nodeInspector")}</h3>
                {step && (
                    <>
                        <h2>{step.id}</h2>
                        <code>{step.capability}</code>
                        {/* First, on a finished run: what this step did. */}
                        {outcomes?.get(step.id) && (
                            <StepRun outcome={outcomes.get(step.id)!} />
                        )}
                        <h3>{t("workflow.inputs")}</h3>
                        <dl className="node-bindings">
                            {Object.entries(step.inputs).map(([port, raw]) => (
                                <div key={port}>
                                    <dt>{port}</dt>
                                    <dd>{formatReference(raw)}</dd>
                                </div>
                            ))}
                        </dl>
                        <h3>{t("workflow.parameters")}</h3>
                        <pre>{JSON.stringify(step.parameters, null, 2)}</pre>
                        <h3>{t("ws.outputs")}</h3>
                        {plan.outputs
                            .filter((o) => o.step_id === step.id)
                            .map((o) => (
                                <p key={o.target_column}>
                                    {o.value_field} →{" "}
                                    <strong>
                                        {draft.output_columns[o.target_column]}{" "}
                                        · {o.target_column}
                                    </strong>{" "}
                                    {o.expected_unit && `(${o.expected_unit})`}
                                </p>
                            ))}
                    </>
                )}
            </aside>
        </div>
    );
}
/** What the selected step did in the run: counts, reasons and sources. */
function StepRun({ outcome }: { outcome: StepOutcome }) {
    const { t } = useTranslation();
    return (
        <div className="node-run" data-testid="node-run">
            <h3>{t("exec.step.inRun")}</h3>
            <StepRows outcome={outcome} />
            {outcome.codes.length > 0 && (
                <ul>
                    {outcome.codes.map((entry) => (
                        <li key={entry.code}>
                            <code>{entry.code}</code> × {entry.count}
                        </li>
                    ))}
                </ul>
            )}
            {outcome.sources.length > 0 && (
                <p>
                    {t("exec.step.sources", {
                        sources: outcome.sources.join(", "),
                    })}
                </p>
            )}
        </div>
    );
}
function formatReference(raw: unknown): string {
    if (!raw || typeof raw !== "object") return JSON.stringify(raw);
    const ref = raw as Record<string, unknown>;
    if (ref.kind === "column") return `column · ${ref.name}`;
    if (ref.kind === "step_output") return `step_output · ${ref.step_id}`;
    return `literal · ${JSON.stringify(ref.value)}`;
}
