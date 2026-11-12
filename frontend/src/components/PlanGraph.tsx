import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { PlanDraft } from "../api";
import { dependencies, stepDepths } from "../workflowModel";

export function PlanGraph({ draft }: { draft: PlanDraft }) {
    const { t } = useTranslation();
    const [selected, setSelected] = useState("");
    const plan = draft.plan;
    if (!plan) return null;
    const step = plan.steps.find((x) => x.id === selected) ?? plan.steps[0];
    // Presentation only: server validation remains authoritative, including cycles.
    const depths = stepDepths(plan);
    const levels = [...new Set(depths.values())].sort((a, b) => a - b);
    const positions = new Map(
        plan.steps.map((s) => {
            const level = depths.get(s.id)!;
            const peers = plan.steps.filter((n) => depths.get(n.id) === level);
            return [
                s.id,
                { x: level * 280 + 24, y: peers.indexOf(s) * 150 + 30 },
            ];
        }),
    );
    const width = Math.max(520, (Math.max(0, ...levels) + 1) * 280 + 20);
    const height = Math.max(
        270,
        ...levels.map(
            (l) =>
                plan.steps.filter((n) => depths.get(n.id) === l).length * 150 +
                30,
        ),
    );
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
                                        d={`M${from.x + 224},${from.y + 48} C${from.x + 250},${from.y + 48} ${to.x - 26},${to.y + 48} ${to.x},${to.y + 48}`}
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
function formatReference(raw: unknown): string {
    if (!raw || typeof raw !== "object") return JSON.stringify(raw);
    const ref = raw as Record<string, unknown>;
    if (ref.kind === "column") return `column · ${ref.name}`;
    if (ref.kind === "step_output") return `step_output · ${ref.step_id}`;
    return `literal · ${JSON.stringify(ref.value)}`;
}
