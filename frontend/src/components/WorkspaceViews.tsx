import { useLayoutEffect, useRef, useState } from "react";
import {
    ArrowLeft,
    ArrowRight,
    CircleAlert,
    GitBranch,
    ListChecks,
    LoaderCircle,
    Play,
    RotateCcw,
    X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { isTerminal, type Preview } from "../api";
import { Advanced, useAdvanced, useHosted } from "../mode";
import type { Workflow, Workspace } from "../useWorkflow";
import { columnLetter, parseRange } from "../range";
import { remainingSeconds, runOutcome } from "../runModel";
import { normalizeColumn, type OutputTarget } from "../workflowModel";
import { clock, useElapsed } from "./ActivityBar";
import { Button } from "./ui/button";
import { ProviderSettings } from "./ProviderSettings";
import { PlanGraph } from "./PlanGraph";
import { PlanDescription, PlanInvalid, PlanRefusal } from "./PlanDescription";

/** What is still missing before a plan can be built, next to the button itself. */
function BuildChecklist({
    w,
    hasWorkbook,
    onOpenSettings,
}: {
    w: Workflow;
    hasWorkbook: boolean;
    onOpenSettings?: () => void;
}) {
    const { t } = useTranslation();
    const hosted = useHosted();
    if (!w.missing.length) return null;
    const text: Record<(typeof w.missing)[number], string> = {
        preview: t(hasWorkbook ? "ws.need.preview" : "ws.need.workbook"),
        selection: t("workflow.selectionLimit", { max: w.maxRows }),
        instruction: t("ws.need.instruction"),
        mapping: t("ws.need.mapping"),
        consent: t("provider.consentMissing"),
        provider: t(`provider.${w.planningReadiness}`),
    };
    const settings =
        w.planningReadiness === "modelMissing" ||
        w.planningReadiness === "keyMissing" ||
        w.planningReadiness === "unavailable";
    // A hosted demo is configured on the server; a visitor cannot fix it.
    if (hosted && w.planningReadiness !== "loading")
        text.provider = t("ws.need.hostedProvider");
    return (
        <div className="build-checklist" role="status">
            <strong>{t("ws.need.title")}</strong>
            <ul>
                {w.missing.map((key) => (
                    <li key={key} data-requirement={key}>
                        {text[key]}
                        {key === "provider" &&
                            settings &&
                            !hosted &&
                            onOpenSettings && (
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
                    </li>
                ))}
            </ul>
        </div>
    );
}

export function DatasetConfiguration({
    workflow: w,
    preview,
    hasWorkbook,
    onOpenSettings,
}: {
    workflow: Workflow;
    preview: Preview | null;
    hasWorkbook: boolean;
    onOpenSettings?: () => void;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const first = preview
        ? (parseRange(preview.range_ref).bounds?.firstColumn ?? 1)
        : 1;
    function update(index: number, value: Partial<OutputTarget>) {
        w.edit(() =>
            w.setMapping(
                w.mapping.map((x, i) => (i === index ? { ...x, ...value } : x)),
            ),
        );
    }
    // Basic asks one question: which column to fill. Several outputs, and a
    // new column at an address of the user's choice, are Advanced.
    const single = !advanced && w.mapping.length === 1;
    // Basic has no consent checkbox: pressing the button is the consent, so the
    // button says what leaves the browser. Nothing leaves it on fixture answers.
    const sends =
        advanced || !preview || !w.provider?.server_network_allowed
            ? []
            : [
                  ...(w.savedPlan.trim() || w.planningReadiness !== "ready"
                      ? []
                      : [t("ws.sends.ai")]),
                  t("ws.sends.sources"),
              ];
    return (
        <section className="configuration workspace-panel">
            <div className="panel-scroll configuration-fields">
                <div className="panel-title">
                    <GitBranch aria-hidden="true" />
                    <h2>{t("ws.enrichmentTask")}</h2>
                </div>
                {!preview && <p className="muted">{t("ws.taskHint")}</p>}
                {(w.plan || w.job) && (
                    <p className="notice">
                        {t(
                            advanced
                                ? "ws.invalidateWarning"
                                : "ws.invalidateWarningBasic",
                        )}
                    </p>
                )}
                <fieldset disabled={!preview || w.locked}>
                    <label>
                        {t("workflow.instruction")}
                        <textarea
                            maxLength={4000}
                            value={w.instruction}
                            placeholder={t("workflow.instructionExample")}
                            aria-describedby="instruction-hint"
                            onChange={(e) =>
                                w.edit(() => w.setInstruction(e.target.value))
                            }
                        />
                    </label>
                    <p className="muted" id="instruction-hint">
                        {t("workflow.instructionHint")}
                    </p>
                    {single ? (
                        <>
                            <label>
                                {t("ws.fillColumn")}
                                <select
                                    value={
                                        w.mapping[0].mode === "new"
                                            ? NEW_COLUMN
                                            : w.mapping[0].column
                                    }
                                    onChange={(e) => {
                                        const value = e.target.value;
                                        const col =
                                            preview?.profile.columns.find(
                                                (_, i) =>
                                                    columnLetter(first + i) ===
                                                    value,
                                            );
                                        update(
                                            0,
                                            value === NEW_COLUMN
                                                ? {
                                                      mode: "new",
                                                      column: w.freeColumn,
                                                      name: "",
                                                  }
                                                : {
                                                      mode: "existing",
                                                      column: value,
                                                      name: col?.name ?? "",
                                                  },
                                        );
                                    }}
                                >
                                    <option value="">
                                        {t("ws.chooseFill")}
                                    </option>
                                    {preview?.profile.columns.map((col, i) => (
                                        <option
                                            key={col.name}
                                            value={columnLetter(first + i)}
                                        >
                                            {col.name} (
                                            {columnLetter(first + i)})
                                        </option>
                                    ))}
                                    <option value={NEW_COLUMN}>
                                        {t("ws.newColumnOption")}
                                    </option>
                                </select>
                            </label>
                            {w.mapping[0].mode === "new" && (
                                <label>
                                    {t("ws.newColumnName")}
                                    <input
                                        value={w.mapping[0].name}
                                        maxLength={200}
                                        onChange={(e) =>
                                            update(0, { name: e.target.value })
                                        }
                                    />
                                </label>
                            )}
                        </>
                    ) : (
                        <>
                            <h3>{t("ws.outputs")}</h3>
                            <p className="muted">{t("ws.mappingHint")}</p>
                        </>
                    )}
                    {!single &&
                        w.mapping.map((entry, index) => (
                            <div className="output-target" key={index}>
                                <div className="mapping-row">
                                    <label>
                                        {t("ws.targetType")} {index + 1}
                                        <select
                                            aria-label={`${t("ws.targetType")} ${index + 1}`}
                                            value={entry.mode}
                                            onChange={(e) =>
                                                update(index, {
                                                    mode: e.target
                                                        .value as OutputTarget["mode"],
                                                    column: "",
                                                    name: "",
                                                })
                                            }
                                        >
                                            <option value="existing">
                                                {t("ws.existingColumn")}
                                            </option>
                                            <option value="new">
                                                {t("ws.newColumn")}
                                            </option>
                                        </select>
                                    </label>
                                    <Button
                                        variant="outline"
                                        disabled={w.mapping.length === 1}
                                        onClick={() =>
                                            w.edit(() =>
                                                w.setMapping(
                                                    w.mapping.filter(
                                                        (_, i) => i !== index,
                                                    ),
                                                ),
                                            )
                                        }
                                        aria-label={`${t("workflow.removeMapping")} ${index + 1}`}
                                    >
                                        ×
                                    </Button>
                                </div>
                                {entry.mode === "existing" ? (
                                    <label>
                                        {t("ws.targetColumn")} {index + 1}
                                        <select
                                            aria-label={`${t(entry.mode === "existing" ? "ws.targetColumn" : "ws.columnAddress")} ${index + 1}`}
                                            value={entry.column}
                                            onChange={(e) => {
                                                const col =
                                                    preview?.profile.columns.find(
                                                        (_, i) =>
                                                            columnLetter(
                                                                first + i,
                                                            ) ===
                                                            e.target.value,
                                                    );
                                                update(index, {
                                                    column: e.target.value,
                                                    name: col?.name ?? "",
                                                });
                                            }}
                                        >
                                            <option value="">
                                                {t("ws.chooseColumn")}
                                            </option>
                                            {preview?.profile.columns.map(
                                                (col, i) => (
                                                    <option
                                                        key={col.name}
                                                        value={columnLetter(
                                                            first + i,
                                                        )}
                                                    >
                                                        {columnLetter(
                                                            first + i,
                                                        )}{" "}
                                                        · {col.name}
                                                    </option>
                                                ),
                                            )}
                                        </select>
                                    </label>
                                ) : (
                                    <div className="mapping-row">
                                        <label>
                                            {t("ws.columnAddress")} {index + 1}
                                            <input
                                                aria-label={`${t("ws.columnAddress")} ${index + 1}`}
                                                value={entry.column}
                                                maxLength={3}
                                                onChange={(e) =>
                                                    update(index, {
                                                        column: normalizeColumn(
                                                            e.target.value,
                                                        ),
                                                    })
                                                }
                                                placeholder="F / 6"
                                            />
                                        </label>
                                        <label>
                                            {t("ws.columnHeader")} {index + 1}
                                            <input
                                                aria-label={`${t("ws.columnHeader")} ${index + 1}`}
                                                value={entry.name}
                                                maxLength={200}
                                                onChange={(e) =>
                                                    update(index, {
                                                        name: e.target.value,
                                                    })
                                                }
                                            />
                                        </label>
                                    </div>
                                )}
                            </div>
                        ))}
                    {!w.mappingValid &&
                        (single
                            ? w.mapping[0].mode === "new" &&
                              !!w.mapping[0].name.trim()
                            : w.mapping.some((x) => x.column || x.name)) && (
                            <p className="validation-note" role="alert">
                                {t("ws.mappingInvalid")}
                            </p>
                        )}
                    {!single && (
                        <Button
                            variant="outline"
                            disabled={w.mapping.length >= 20}
                            onClick={() =>
                                w.edit(() =>
                                    w.setMapping([
                                        ...w.mapping,
                                        {
                                            mode: "existing",
                                            name: "",
                                            column: "",
                                        },
                                    ]),
                                )
                            }
                        >
                            {t("workflow.addMapping")}
                        </Button>
                    )}
                    <Advanced>
                        <label>
                            {t("workflow.policy")}
                            <select
                                value={w.policy}
                                onChange={(e) =>
                                    w.edit(() =>
                                        w.setPolicy(
                                            e.target.value as typeof w.policy,
                                        ),
                                    )
                                }
                            >
                                <option value="verified_only">
                                    {t("workflow.verifiedOnly")}
                                </option>
                                <option value="strict">
                                    {t("workflow.strict")}
                                </option>
                            </select>
                        </label>
                        <p className="muted">{t("workflow.policyHint")}</p>
                    </Advanced>
                    <Advanced>
                        <label className="network-opt-in">
                            <input
                                type="checkbox"
                                checked={w.network}
                                onChange={(e) =>
                                    w.edit(() => w.setNetwork(e.target.checked))
                                }
                            />
                            {t("workflow.network")}
                        </label>
                    </Advanced>
                    <Advanced>
                        <details data-advanced="true">
                            <summary>{t("workflow.savedPlan")}</summary>
                            <label>
                                {t("workflow.planJson")}
                                <textarea
                                    className="mono"
                                    value={w.savedPlan}
                                    onChange={(e) =>
                                        w.edit(() =>
                                            w.setSavedPlan(e.target.value),
                                        )
                                    }
                                />
                            </label>
                        </details>
                        <AdvancedControls w={w} />
                    </Advanced>
                </fieldset>
                <Advanced>
                    {!w.savedPlan.trim() &&
                        (w.planningReadiness === "ready" ||
                            w.planningReadiness === "offline") && (
                            <p role="status">
                                {t(`provider.${w.planningReadiness}`)}
                            </p>
                        )}
                    <p className="muted">
                        {w.savedPlan.trim()
                            ? t("ws.offlineValidation")
                            : t("ws.aiRequirements")}
                    </p>
                </Advanced>
            </div>
            <div className="workspace-actions">
                {w.sample?.has_plan && !w.savedPlan.trim() && (
                    <p className="sample-plan muted">
                        {t("sample.planHint")}{" "}
                        <button
                            type="button"
                            className="inline-link"
                            disabled={w.locked}
                            onClick={() =>
                                void w.action(w.loadPreparedPlan, "prepared")
                            }
                        >
                            {t("sample.usePlan")}
                        </button>
                    </p>
                )}
                {!advanced && !!w.savedPlan.trim() && (
                    <p className="sample-plan muted">
                        {t("sample.planLoaded")}{" "}
                        <button
                            type="button"
                            className="inline-link"
                            disabled={w.locked}
                            onClick={() => w.edit(() => w.setSavedPlan(""))}
                        >
                            {t("sample.clearPlan")}
                        </button>
                    </p>
                )}
                {sends.length > 0 && (
                    <p className="sends muted" data-testid="sends">
                        {sends.join(" ")}
                    </p>
                )}
                <BuildChecklist
                    w={w}
                    hasWorkbook={hasWorkbook}
                    onOpenSettings={onOpenSettings}
                />
                <Button
                    disabled={!w.canBuild}
                    onClick={() =>
                        void w.action(
                            w.create,
                            w.savedPlan.trim() ? "check" : "plan",
                        )
                    }
                >
                    {t(
                        w.savedPlan.trim()
                            ? "workflow.submitPlan"
                            : "ws.buildPlan",
                    )}
                    {w.operation?.kind === "plan" ||
                    w.operation?.kind === "check" ? (
                        <LoaderCircle className="spin" aria-hidden="true" />
                    ) : (
                        <ArrowRight aria-hidden="true" />
                    )}
                </Button>
            </div>
        </section>
    );
}

const NEW_COLUMN = "__new__";

function AdvancedControls({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return (
        <details className="advanced-controls" data-advanced="true">
            <summary>{t("ws.advancedControls")}</summary>
            {(w.plan || w.job) && (
                <p className="notice">{t("ws.invalidateWarning")}</p>
            )}
            <label>
                {t("workflow.timeout")}
                <input
                    disabled={w.locked}
                    type="number"
                    min={1}
                    max={w.maxTimeout}
                    value={w.timeout}
                    onChange={(e) => w.setTimeout(e.target.value)}
                    aria-invalid={!w.timeoutValid}
                />
            </label>
        </details>
    );
}

export function PlanWorkspace({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const draft = w.plan;
    const runnable = !!draft?.plan && draft.validation.valid;
    // A long instruction would push the rest of the summary off the screen.
    const long = !advanced && (draft?.instruction.length ?? 0) > 220;
    return (
        <div className="stage-layout">
            <section className="workspace-panel plan-stage">
                {/* A refusal is its own heading: there is no workflow to title. */}
                {(!draft || draft.plan) && (
                    <div className="panel-title">
                        {advanced ? (
                            <GitBranch aria-hidden="true" />
                        ) : (
                            <ListChecks aria-hidden="true" />
                        )}
                        <h2>{t(advanced ? "ws.graph" : "plan.title")}</h2>
                    </div>
                )}
                {!draft ? (
                    <EmptyState icon="plan" text={t("ws.noPlan")} />
                ) : !draft.plan ? (
                    <div className="plan-words panel-scroll">
                        <PlanRefusal draft={draft} maxRows={w.maxRows} />
                    </div>
                ) : advanced ? (
                    <PlanGraph key={draft.fingerprint} draft={draft} />
                ) : (
                    <div className="plan-words panel-scroll">
                        {!draft.validation.valid && (
                            <PlanInvalid draft={draft} />
                        )}
                        <PlanDescription draft={draft} />
                    </div>
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("workflow.planSummary")}</h2>
                    {draft && (
                        <>
                            {long ? (
                                <details className="instruction-fold">
                                    <summary>
                                        {draft.instruction.slice(0, 160).trim()}
                                        …
                                    </summary>
                                    <p>{draft.instruction}</p>
                                </details>
                            ) : (
                                <p>{draft.instruction}</p>
                            )}
                            <Advanced>
                                <p className="mono">
                                    {draft.sheet_name} · {draft.range_ref}
                                </p>
                            </Advanced>
                            <p
                                className={
                                    draft.validation.valid
                                        ? "success-text"
                                        : "validation-note"
                                }
                                data-testid="validation"
                            >
                                {t(
                                    advanced
                                        ? draft.validation.valid
                                            ? "workflow.valid"
                                            : "workflow.invalid"
                                        : draft.validation.valid
                                          ? "plan.checked"
                                          : draft.plan
                                            ? "plan.blocked"
                                            : "plan.none",
                                )}
                            </p>
                            <Advanced>
                                {draft.validation.contract_valid &&
                                    !draft.validation.valid && (
                                        <p className="validation-note">
                                            {t("workflow.intentConflict")}
                                        </p>
                                    )}
                            </Advanced>
                            {/* Without a plan the reason is the main message
                                and is shown next to it, once. */}
                            {draft.plan && draft.reason && (
                                <p>{draft.reason}</p>
                            )}
                            <Advanced>
                                {draft.validation.errors.map((err, i) => (
                                    <p className="validation-note" key={i}>
                                        <code>{err.code}</code> {err.step_id} ·{" "}
                                        {err.message}
                                    </p>
                                ))}
                            </Advanced>
                            {draft.plan && (
                                <>
                                    <Advanced>
                                        <div className="plan-counts">
                                            <strong>
                                                {draft.plan.steps.length}
                                            </strong>{" "}
                                            {t("ws.steps")} ·{" "}
                                            <strong>
                                                {draft.plan.outputs.length}
                                            </strong>{" "}
                                            {t("ws.outputs")}
                                        </div>
                                    </Advanced>
                                    <h3>{t("ws.outputs")}</h3>
                                    {draft.plan.outputs.map((o) => (
                                        <p key={o.target_column}>
                                            <strong>
                                                {
                                                    draft.output_columns[
                                                        o.target_column
                                                    ]
                                                }{" "}
                                                · {o.target_column}
                                            </strong>{" "}
                                            {o.expected_unit &&
                                                `(${o.expected_unit})`}
                                        </p>
                                    ))}
                                    {draft.plan.assumptions.length > 0 && (
                                        <>
                                            <h3>{t("workflow.assumptions")}</h3>
                                            <ul>
                                                {draft.plan.assumptions.map(
                                                    (a, i) => (
                                                        <li key={i}>{a}</li>
                                                    ),
                                                )}
                                            </ul>
                                        </>
                                    )}
                                </>
                            )}
                            {(advanced || runnable) && (
                                <p className="notice">
                                    {t(
                                        draft.status === "approved"
                                            ? "ws.approvalRecorded"
                                            : advanced
                                              ? "ws.approvalHint"
                                              : "plan.runNote",
                                    )}
                                </p>
                            )}
                            <Advanced>
                                <Button
                                    variant="outline"
                                    disabled={w.locked || !!w.job}
                                    onClick={() =>
                                        void w.action(w.validate, "validate")
                                    }
                                >
                                    {t("workflow.validate")}
                                </Button>
                            </Advanced>
                        </>
                    )}
                    {!draft && w.canBuild && (
                        <Button
                            onClick={() =>
                                void w.action(
                                    w.create,
                                    w.savedPlan.trim() ? "check" : "plan",
                                )
                            }
                        >
                            {t("ws.buildPlan")}
                        </Button>
                    )}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="dataset" />
                    {advanced ? (
                        draft?.status === "approved" ? (
                            <Button onClick={() => w.navigate("execution")}>
                                {t("ws.toExecution")}
                                <ArrowRight />
                            </Button>
                        ) : (
                            <Button
                                disabled={w.locked || !runnable}
                                onClick={() =>
                                    void w.action(w.approve, "approve")
                                }
                            >
                                {t("ws.approveContinue")}
                                <ArrowRight />
                            </Button>
                        )
                    ) : w.job ? (
                        // The plan already has a run: one press must not
                        // start a second one.
                        <Button onClick={() => w.navigate("execution")}>
                            {t("ws.toExecution")}
                            <ArrowRight />
                        </Button>
                    ) : (
                        <Button
                            disabled={w.locked || !runnable}
                            onClick={() =>
                                void w.action(w.approveAndRun, "approve")
                            }
                        >
                            {w.operation?.kind === "approve" ||
                            w.operation?.kind === "start" ? (
                                <LoaderCircle
                                    className="spin"
                                    aria-hidden="true"
                                />
                            ) : (
                                <Play aria-hidden="true" />
                            )}
                            {t(
                                draft?.status === "approved"
                                    ? "workflow.run"
                                    : "ws.approveRun",
                            )}
                        </Button>
                    )}
                </div>
            </aside>
        </div>
    );
}
export function QualitySummary({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const keys = [
        "total_rows",
        "successful_rows",
        "partial_rows",
        "failed_rows",
    ];
    if (!w.job?.metrics) return null;
    return (
        <dl className="quality-summary">
            {keys.map((key, i) => (
                <div key={key} className={`quality-${i}`}>
                    <dt>{t(`workflow.metric.${key}`)}</dt>
                    <dd data-testid={`metric-${key}`}>
                        {w.job!.metrics![key] ?? "—"}
                    </dd>
                </div>
            ))}
        </dl>
    );
}

export function ModeNotice({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return w.job?.execution_mode === "offline_fixture" ? (
        <p className="notice fixture-notice">{t("workflow.fixture")}</p>
    ) : null;
}

/** How far a running job is: its rows, and a guess at the time left. */
function RunProgress({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const elapsed = useElapsed(w.active ? w.jobSince : null);
    if (!w.active) return null;
    const progress = w.job?.progress;
    const left = progress
        ? remainingSeconds(elapsed, progress.rows_done, progress.rows_total)
        : null;
    return (
        <div className="run-progress">
            {/* Without a value the element shows that work goes on, no more. */}
            <progress
                aria-label={t("exec.progressLabel")}
                {...(progress
                    ? { value: progress.rows_done, max: progress.rows_total }
                    : {})}
            />
            <p data-testid="run-progress">
                {progress
                    ? t("activity.rows", {
                          done: progress.rows_done,
                          total: progress.rows_total,
                      })
                    : t("exec.preparing")}
                {left !== null &&
                    ` · ${t("activity.remaining", { time: clock(left) })}`}
            </p>
        </div>
    );
}

/**
 * Leave this table and begin with another one. The server forgets a removed
 * workbook together with its results, so when there is a filled table to lose
 * the button asks first, on the page, without a browser dialog.
 */
export function StartOver({
    w,
    onStartOver,
}: {
    w: Workflow;
    onStartOver: () => void;
}) {
    const { t } = useTranslation();
    const [asking, setAsking] = useState(false);
    if (asking)
        return (
            <div className="start-over notice" role="group">
                <p>{t("startOver.warning")}</p>
                <div>
                    <Button variant="outline" onClick={() => setAsking(false)}>
                        {t("startOver.keep")}
                    </Button>
                    <Button onClick={onStartOver}>
                        {t("startOver.confirm")}
                    </Button>
                </div>
            </div>
        );
    return (
        <Button
            variant="outline"
            disabled={w.locked}
            onClick={() =>
                w.job?.artifacts.result ? setAsking(true) : onStartOver()
            }
        >
            <RotateCcw aria-hidden="true" />
            {t("startOver.action")}
        </Button>
    );
}

export function ExecutionWorkspace({
    w,
    onStartOver,
}: {
    w: Workflow;
    onStartOver: () => void;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const terminal = !!w.job && isTerminal(w.job.status);
    const toReview = w.job?.status === "needs_review";
    // A run that ended without a filled table: failed, cancelled or timed out.
    const produced = !!w.job?.artifacts.result;
    const stopped = terminal && !produced;
    // Basic has nothing left to control once a run exists and is not running.
    const controls = advanced || !w.job || w.active;
    const outcome =
        !advanced && w.job
            ? runOutcome(w.job, w.report, w.plan?.plan ?? null, w.review)
            : null;
    return (
        <div className="stage-layout">
            <section className="workspace-panel execution-stage">
                <div className="summary-content">
                    <div className="panel-title">
                        <Play aria-hidden="true" />
                        <h2>
                            {t(advanced ? "workflow.results" : "exec.title")}
                        </h2>
                    </div>
                    <p
                        className={`job-status status-${w.job?.status ?? "idle"}`}
                        role="status"
                        data-testid="job-status"
                    >
                        {advanced
                            ? w.job
                                ? t(`workflow.status.${w.job.status}`)
                                : t("ws.readyToRun")
                            : outcome
                              ? t(`exec.outcome.${outcome.key}`, outcome.values)
                              : w.job
                                ? t(`activity.${w.job.status}`)
                                : t("exec.idle")}
                    </p>
                    {outcome?.more.map((sentence) => (
                        <p key={sentence.key}>
                            {t(`exec.outcome.${sentence.key}`, sentence.values)}
                        </p>
                    ))}
                    <RunProgress w={w} />
                    <ModeNotice w={w} />
                    {w.job?.previous_job_id && (
                        <p className="muted">{t("review.continued")}</p>
                    )}
                    <Advanced>
                        <QualitySummary w={w} />
                        {w.job?.error && (
                            <p className="validation-note" role="alert">
                                <code>{w.job.error.code}</code> ·{" "}
                                {w.job.error.message}
                            </p>
                        )}
                    </Advanced>
                    {advanced ? (
                        <p className="muted">
                            {w.active
                                ? t("ws.runningHint")
                                : terminal
                                  ? t(
                                        w.job!.status === "needs_review"
                                            ? "workflow.unresolved"
                                            : "ws.terminalHint",
                                    )
                                  : t("ws.startHint")}
                        </p>
                    ) : (
                        // The outcome sentence already says how a run ended.
                        !terminal && (
                            <p className="muted">
                                {w.active
                                    ? t("ws.runningHint")
                                    : t(
                                          w.plan?.status === "approved"
                                              ? "exec.idleApproved"
                                              : "exec.idleNoPlan",
                                      )}
                            </p>
                        )
                    )}
                </div>
                <Advanced>
                    {w.plan?.plan && <PlanGraph draft={w.plan} />}
                </Advanced>
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t(controls ? "ws.runControls" : "ws.nextStep")}</h2>
                    {!w.plan && <p>{t("ws.noPlan")}</p>}
                    {!controls && terminal && (
                        <p>
                            {t(
                                stopped
                                    ? "exec.retryHint"
                                    : toReview
                                      ? "ws.reviewHint"
                                      : "ws.exportHint",
                            )}
                        </p>
                    )}
                    {!w.network && (
                        <p className="muted">{t("ws.executionNetwork")}</p>
                    )}
                    {advanced && w.job && (
                        <dl className="run-times">
                            {(["created", "started", "finished"] as const).map(
                                (key) => {
                                    const value = w.job![`${key}_at`];
                                    return value ? (
                                        <div key={key}>
                                            <dt>{t(`workflow.${key}`)}</dt>
                                            <dd>
                                                <time dateTime={value}>
                                                    {new Date(
                                                        value,
                                                    ).toLocaleTimeString(
                                                        undefined,
                                                        {
                                                            hour: "2-digit",
                                                            minute: "2-digit",
                                                            second: "2-digit",
                                                        },
                                                    )}
                                                </time>
                                            </dd>
                                        </div>
                                    ) : null;
                                },
                            )}
                        </dl>
                    )}
                    {w.active ? (
                        <Button
                            variant="outline"
                            disabled={w.busy}
                            onClick={() => void w.action(w.cancel, "cancel")}
                        >
                            {t("workflow.cancel")}
                        </Button>
                    ) : (
                        // Repeating a run that produced a table is a tool for
                        // inspection; repeating one that produced nothing is
                        // the way forward in either mode.
                        (advanced || !w.job || stopped) && (
                            <Button
                                disabled={!w.canRun}
                                onClick={() => void w.action(w.run, "start")}
                            >
                                {w.operation?.kind === "start" ? (
                                    <LoaderCircle
                                        className="spin"
                                        aria-hidden="true"
                                    />
                                ) : (
                                    <Play />
                                )}
                                {t(
                                    !w.job
                                        ? "workflow.run"
                                        : advanced
                                          ? "ws.retryPlan"
                                          : "exec.runAgain",
                                )}
                            </Button>
                        )
                    )}
                    {advanced && terminal && (
                        <p className="muted">{t("ws.retryHint")}</p>
                    )}
                    {terminal && <StartOver w={w} onStartOver={onStartOver} />}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="plan" />
                    <Button
                        disabled={advanced ? !terminal : !produced}
                        onClick={() =>
                            w.navigate(toReview ? "review" : "export")
                        }
                    >
                        {t(toReview ? "ws.reviewIssues" : "ws.viewOutput")}
                        <ArrowRight />
                    </Button>
                </div>
            </aside>
        </div>
    );
}

export function Back({ w, to }: { w: Workflow; to: Workspace }) {
    const { t } = useTranslation();
    return (
        <Button variant="outline" onClick={() => w.navigate(to)}>
            <ArrowLeft aria-hidden="true" />
            {t(to)}
        </Button>
    );
}
export function EmptyState({ text, icon }: { text: string; icon?: "plan" }) {
    return (
        <div className="workspace-empty">
            {icon === "plan" ? (
                <GitBranch aria-hidden="true" />
            ) : (
                <CircleAlert aria-hidden="true" />
            )}
            <p>{text}</p>
        </div>
    );
}

export function Diagnostics({
    w,
    preview,
    section,
    onClose,
}: {
    w: Workflow;
    preview: Preview | null;
    section: "details" | "settings";
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const ref = useRef<HTMLDialogElement>(null);
    const [chosen, setTab] = useState(section);
    // Basic reaches this dialog only for the provider settings.
    const tab = advanced ? chosen : "settings";
    useLayoutEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        const dialog = ref.current;
        dialog?.showModal();
        return () => {
            dialog?.close();
            previous?.focus();
        };
    }, []);
    return (
        <dialog
            ref={ref}
            className="diagnostics-dialog"
            aria-labelledby="diagnostics-title"
            data-advanced="true"
            onKeyDown={(event) => {
                if (event.key !== "Tab") return;
                const controls = [
                    ...event.currentTarget.querySelectorAll<HTMLElement>(
                        'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
                    ),
                ].filter((element) => element.getClientRects().length > 0);
                if (!controls.length) return;
                event.preventDefault();
                const index = controls.indexOf(
                    document.activeElement as HTMLElement,
                );
                controls[
                    (index + (event.shiftKey ? -1 : 1) + controls.length) %
                        controls.length
                ].focus();
            }}
            onCancel={(e) => {
                e.preventDefault();
                onClose();
            }}
        >
            <div className="dialog-heading">
                <h2 id="diagnostics-title">
                    {t(
                        tab === "settings"
                            ? "ws.providerSettings"
                            : "ws.diagnostics",
                    )}
                </h2>
                <button
                    className="icon-button"
                    aria-label={t("ws.close")}
                    onClick={onClose}
                >
                    <X />
                </button>
            </div>
            {advanced && (
                <div className="dialog-tabs">
                    <Button
                        variant="outline"
                        aria-pressed={tab === "details"}
                        onClick={() => setTab("details")}
                    >
                        {t("ws.diagnostics")}
                    </Button>
                    <Button
                        variant="outline"
                        aria-pressed={tab === "settings"}
                        onClick={() => setTab("settings")}
                    >
                        {t("ws.providerSettings")}
                    </Button>
                </div>
            )}
            <div className="panel-scroll diagnostics-content" tabIndex={0}>
                {tab === "settings" ? (
                    <ProviderSettings w={w} />
                ) : (
                    <>
                        <h3>{t("workflow.fullPlan")}</h3>
                        {w.plan && (
                            <>
                                <p>
                                    {t("workflow.fingerprint")}:{" "}
                                    <code data-testid="fingerprint">
                                        {w.plan.fingerprint}
                                    </code>
                                </p>
                                <p>
                                    {t("workflow.sourceHash")}:{" "}
                                    <code>{w.plan.source_sha256}</code>
                                </p>
                                <pre>{JSON.stringify(w.plan, null, 2)}</pre>
                            </>
                        )}
                        <h3>{t("diagnostics")}</h3>
                        <pre>
                            {JSON.stringify(preview?.profile ?? null, null, 2)}
                        </pre>
                        <h3>{t("workflow.providerRequests")}</h3>
                        <pre>
                            {JSON.stringify(
                                w.report?.execution?.provider_requests ?? null,
                                null,
                                2,
                            )}
                        </pre>
                        <h3>{t("workflow.metrics")}</h3>
                        <pre>
                            {JSON.stringify(w.job?.metrics ?? null, null, 2)}
                        </pre>
                        <h3>{t("ws.auditEvidence")}</h3>
                        <pre>{JSON.stringify(w.report ?? w.job, null, 2)}</pre>
                        <h3>{t("ws.previousRuns")}</h3>
                        <p>{t("ws.historyHint")}</p>
                        {w.history.map((entry) => (
                            <details key={entry.job.job_id}>
                                <summary>
                                    {entry.job.job_id} ·{" "}
                                    {t(`workflow.status.${entry.job.status}`)}
                                </summary>
                                <pre>{JSON.stringify(entry, null, 2)}</pre>
                            </details>
                        ))}
                    </>
                )}
            </div>
        </dialog>
    );
}
