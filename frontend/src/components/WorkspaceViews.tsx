import { useLayoutEffect, useRef, useState } from "react";
import {
    ArrowLeft,
    ArrowRight,
    CheckCircle2,
    CircleAlert,
    Download,
    GitBranch,
    LockKeyhole,
    Play,
    X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { isTerminal, type Preview } from "../api";
import type { Workflow, Workspace } from "../useWorkflow";
import { columnLetter, parseRange } from "../range";
import {
    issueGroups,
    normalizeColumn,
    type OutputTarget,
} from "../workflowModel";
import { Button } from "./ui/button";
import { ProviderSettings } from "./ProviderSettings";
import { PlanGraph } from "./PlanGraph";

export function DatasetConfiguration({
    workflow: w,
    preview,
}: {
    workflow: Workflow;
    preview: Preview | null;
}) {
    const { t } = useTranslation();
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
    return (
        <section className="configuration workspace-panel">
            <div className="panel-scroll configuration-fields">
                <div className="panel-title">
                    <GitBranch aria-hidden="true" />
                    <h2>{t("ws.enrichmentTask")}</h2>
                </div>
                {!preview && <p className="muted">{t("ws.taskHint")}</p>}
                {(w.plan || w.job) && (
                    <p className="notice">{t("ws.invalidateWarning")}</p>
                )}
                {!preview && <p className="notice">{t("ws.previewFirst")}</p>}
                <fieldset disabled={!preview || w.locked}>
                    <label>
                        {t("workflow.instruction")}
                        <textarea
                            maxLength={4000}
                            value={w.instruction}
                            onChange={(e) =>
                                w.edit(() => w.setInstruction(e.target.value))
                            }
                        />
                    </label>
                    <h3>{t("ws.outputs")}</h3>
                    <p className="muted">{t("ws.mappingHint")}</p>
                    {w.mapping.map((entry, index) => (
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
                                                        ) === e.target.value,
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
                                                    {columnLetter(first + i)} ·{" "}
                                                    {col.name}
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
                        w.mapping.some((x) => x.column || x.name) && (
                            <p className="validation-note" role="alert">
                                {t("ws.mappingInvalid")}
                            </p>
                        )}
                    <Button
                        variant="outline"
                        disabled={w.mapping.length >= 20}
                        onClick={() =>
                            w.edit(() =>
                                w.setMapping([
                                    ...w.mapping,
                                    { mode: "existing", name: "", column: "" },
                                ]),
                            )
                        }
                    >
                        {t("workflow.addMapping")}
                    </Button>
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
                    <details data-advanced="true">
                        <summary>{t("workflow.savedPlan")}</summary>
                        <label>
                            {t("workflow.planJson")}
                            <textarea
                                className="mono"
                                value={w.savedPlan}
                                onChange={(e) =>
                                    w.edit(() => w.setSavedPlan(e.target.value))
                                }
                            />
                        </label>
                    </details>
                    <AdvancedControls w={w} />
                </fieldset>
                {preview && !w.withinBounds && (
                    <p role="alert">{t("workflow.selectionLimit")}</p>
                )}
                {!w.savedPlan.trim() && (
                    <>
                        <p role="status">
                            {t(`provider.${w.planningReadiness}`)}
                        </p>
                        {!w.network && <p>{t("provider.consentMissing")}</p>}
                    </>
                )}
                <p className="muted">
                    {w.savedPlan.trim()
                        ? t("ws.offlineValidation")
                        : t("ws.aiRequirements")}
                </p>
            </div>
            <div className="workspace-actions">
                <Button
                    disabled={!w.canBuild}
                    onClick={() => void w.action(w.create)}
                >
                    {t(
                        w.savedPlan.trim()
                            ? "workflow.submitPlan"
                            : "ws.buildPlan",
                    )}
                    <ArrowRight aria-hidden="true" />
                </Button>
            </div>
        </section>
    );
}

function AdvancedControls({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return (
        <details className="advanced-controls" data-advanced="true">
            <summary>{t("ws.advancedControls")}</summary>
            {(w.plan || w.job) && (
                <p className="notice">{t("ws.invalidateWarning")}</p>
            )}
            <label>
                {t("workflow.policy")}
                <select
                    disabled={w.locked}
                    value={w.policy}
                    onChange={(e) =>
                        w.edit(() =>
                            w.setPolicy(e.target.value as typeof w.policy),
                        )
                    }
                >
                    <option value="strict">{t("workflow.strict")}</option>
                    <option value="verified_only">
                        {t("workflow.verifiedOnly")}
                    </option>
                </select>
            </label>
            <label>
                {t("workflow.timeout")}
                <input
                    disabled={w.locked}
                    type="number"
                    min={1}
                    max={300}
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
    return (
        <div className="stage-layout">
            <section className="workspace-panel plan-stage">
                <div className="panel-title">
                    <GitBranch aria-hidden="true" />
                    <h2>{t("ws.graph")}</h2>
                    <span className="muted">{t("ws.staticGraph")}</span>
                </div>
                {w.plan?.plan ? (
                    <PlanGraph key={w.plan.fingerprint} draft={w.plan} />
                ) : (
                    <EmptyState icon="plan" text={t("ws.noPlan")} />
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("workflow.planSummary")}</h2>
                    {w.plan && (
                        <>
                            <p>{w.plan.instruction}</p>
                            <p className="mono">
                                {w.plan.sheet_name} · {w.plan.range_ref}
                            </p>
                            <p
                                className={
                                    w.plan.validation.valid
                                        ? "success-text"
                                        : "validation-note"
                                }
                                data-testid="validation"
                            >
                                {t(
                                    w.plan.validation.valid
                                        ? "workflow.valid"
                                        : "workflow.invalid",
                                )}
                            </p>
                            {w.plan.validation.contract_valid &&
                                !w.plan.validation.valid && (
                                    <p className="validation-note">
                                        {t("workflow.intentConflict")}
                                    </p>
                                )}
                            {w.plan.reason && <p>{w.plan.reason}</p>}
                            {w.plan.validation.errors.map((err, i) => (
                                <p className="validation-note" key={i}>
                                    <code>{err.code}</code> {err.step_id} ·{" "}
                                    {err.message}
                                </p>
                            ))}
                            {w.plan.plan && (
                                <>
                                    <div className="plan-counts">
                                        <strong>
                                            {w.plan.plan.steps.length}
                                        </strong>{" "}
                                        {t("ws.steps")} ·{" "}
                                        <strong>
                                            {w.plan.plan.outputs.length}
                                        </strong>{" "}
                                        {t("ws.outputs")}
                                    </div>
                                    <p className="notice">
                                        {t("workflow.parameterHint")}
                                    </p>
                                    <h3>{t("ws.outputs")}</h3>
                                    {w.plan.plan.outputs.map((o) => (
                                        <p key={o.target_column}>
                                            <strong>
                                                {
                                                    w.plan!.output_columns[
                                                        o.target_column
                                                    ]
                                                }{" "}
                                                · {o.target_column}
                                            </strong>{" "}
                                            {o.expected_unit &&
                                                `(${o.expected_unit})`}
                                        </p>
                                    ))}
                                    {w.plan.plan.assumptions.length > 0 && (
                                        <>
                                            <h3>{t("workflow.assumptions")}</h3>
                                            <ul>
                                                {w.plan.plan.assumptions.map(
                                                    (a, i) => (
                                                        <li key={i}>{a}</li>
                                                    ),
                                                )}
                                            </ul>
                                        </>
                                    )}
                                </>
                            )}
                            <p className="notice">
                                {t(
                                    w.plan.status === "approved"
                                        ? "ws.approvalRecorded"
                                        : "ws.approvalHint",
                                )}
                            </p>
                            <Button
                                variant="outline"
                                disabled={w.locked || !!w.job}
                                onClick={() => void w.action(w.validate)}
                            >
                                {t("workflow.validate")}
                            </Button>
                        </>
                    )}
                    <AdvancedControls w={w} />
                    {!w.plan && w.canBuild && (
                        <Button onClick={() => void w.action(w.create)}>
                            {t("ws.buildPlan")}
                        </Button>
                    )}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="dataset" />
                    {w.plan?.status === "approved" ? (
                        <Button onClick={() => w.navigate("execution")}>
                            {t("ws.toExecution")}
                            <ArrowRight />
                        </Button>
                    ) : (
                        <Button
                            disabled={
                                w.locked ||
                                !w.plan?.plan ||
                                !w.plan.validation.valid
                            }
                            onClick={() => void w.action(w.approve)}
                        >
                            {t("ws.approveContinue")}
                            <ArrowRight />
                        </Button>
                    )}
                </div>
            </aside>
        </div>
    );
}

function QualitySummary({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const keys = [
        "total_rows",
        "successful_rows",
        "partial_rows",
        "failed_rows",
    ];
    if (!w.job?.metrics)
        return <p className="muted">{t("ws.metricsPending")}</p>;
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

function ModeNotice({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return w.job?.execution_mode === "offline_fixture" ? (
        <p className="notice fixture-notice">{t("workflow.fixture")}</p>
    ) : null;
}

export function ExecutionWorkspace({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const terminal = !!w.job && isTerminal(w.job.status);
    return (
        <div className="stage-layout">
            <section className="workspace-panel execution-stage">
                <div className="summary-content">
                    <div className="panel-title">
                        <Play aria-hidden="true" />
                        <h2>{t("workflow.results")}</h2>
                    </div>
                    <p
                        className={`job-status status-${w.job?.status ?? "idle"}`}
                        role="status"
                        data-testid="job-status"
                    >
                        {w.job
                            ? t(`workflow.status.${w.job.status}`)
                            : t("ws.readyToRun")}
                    </p>
                    <ModeNotice w={w} />
                    <QualitySummary w={w} />
                    {w.job?.error && (
                        <p className="validation-note" role="alert">
                            <code>{w.job.error.code}</code> ·{" "}
                            {w.job.error.message}
                        </p>
                    )}
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
                </div>
                {w.plan?.plan && <PlanGraph draft={w.plan} />}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("ws.runControls")}</h2>
                    <p>{w.plan?.instruction ?? t("ws.noPlan")}</p>
                    <p className="notice">{t("ws.staticGraph")}</p>
                    <AdvancedControls w={w} />
                    {!w.network && (
                        <p className="muted">{t("ws.executionNetwork")}</p>
                    )}
                    {w.job && (
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
                            onClick={() => void w.action(w.cancel)}
                        >
                            {t("workflow.cancel")}
                        </Button>
                    ) : (
                        <Button
                            disabled={!w.canRun}
                            onClick={() => void w.action(w.run)}
                        >
                            <Play />
                            {t(w.job ? "ws.retryPlan" : "workflow.run")}
                        </Button>
                    )}
                    {terminal && <p className="muted">{t("ws.retryHint")}</p>}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="plan" />
                    <Button
                        disabled={!terminal}
                        onClick={() =>
                            w.navigate(
                                w.job?.status === "needs_review"
                                    ? "review"
                                    : "export",
                            )
                        }
                    >
                        {t(
                            w.job?.status === "needs_review"
                                ? "ws.reviewIssues"
                                : "ws.viewOutput",
                        )}
                        <ArrowRight />
                    </Button>
                </div>
            </aside>
        </div>
    );
}

export function ReviewWorkspace({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const groups = issueGroups(w.report);
    return (
        <div className="stage-layout">
            <section className="workspace-panel panel-scroll summary-content">
                <div className="panel-title">
                    <CircleAlert aria-hidden="true" />
                    <h2>{t("ws.issueSummary")}</h2>
                </div>
                <ModeNotice w={w} />
                <QualitySummary w={w} />
                {!w.report ? (
                    <EmptyState text={t("ws.reviewPending")} />
                ) : groups.length ? (
                    <>
                        <p className="muted">{t("ws.groupingHint")}</p>
                        {groups.map((group) => (
                            <article className="issue-group" key={group.code}>
                                <div className="panel-title">
                                    <CircleAlert aria-hidden="true" />
                                    <h3>{group.code}</h3>
                                    <span className="status-label">
                                        {t(
                                            group.derived
                                                ? "ws.derivedIssue"
                                                : "ws.rootIssue",
                                        )}
                                    </span>
                                </div>
                                <p>
                                    {t("ws.affectedRecords", {
                                        count: group.records.size,
                                        operations: group.operations,
                                    })}
                                </p>
                                <p className="mono muted">
                                    {[...group.records].join(" · ")}
                                </p>
                            </article>
                        ))}
                    </>
                ) : (
                    <p className="notice">
                        {t(
                            w.report.status === "completed"
                                ? "ws.noIssues"
                                : "ws.noRowDetails",
                        )}
                    </p>
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <LockKeyhole aria-hidden="true" />
                    <h2>{t("ws.reviewSupport")}</h2>
                    <p>{t("ws.reviewUnavailable")}</p>
                    <Button disabled>{t("ws.approveCandidates")}</Button>
                    <Button disabled>{t("ws.resume")}</Button>
                    <p className="muted">{t("ws.reviewNext")}</p>
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="execution" />
                    <Button onClick={() => w.navigate("export")}>
                        {t("ws.toExport")}
                        <ArrowRight />
                    </Button>
                </div>
            </aside>
        </div>
    );
}

export function ExportWorkspace({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    return (
        <div className="stage-layout">
            <section className="workspace-panel panel-scroll summary-content">
                <div className="panel-title">
                    <Download aria-hidden="true" />
                    <h2>{t("ws.outputWorkbook")}</h2>
                </div>
                <ModeNotice w={w} />
                <QualitySummary w={w} />
                <p>{t("ws.exportDetail")}</p>
                <p className="muted">{t("ws.outputPreviewHint")}</p>
                {w.report?.source_unchanged !== undefined && (
                    <p
                        className={
                            w.report.source_unchanged
                                ? "success-text"
                                : "validation-note"
                        }
                    >
                        <CheckCircle2 aria-hidden="true" />{" "}
                        {t("workflow.sourceVerified")}:{" "}
                        {t(w.report.source_unchanged ? "ws.yes" : "ws.no")}
                    </p>
                )}
                {w.report?.rows?.length ? (
                    <div
                        className="outcome-table panel-scroll"
                        role="region"
                        aria-label={t("ws.outcomePreview")}
                        tabIndex={0}
                    >
                        <table>
                            <thead>
                                <tr>
                                    <th>{t("ws.record")}</th>
                                    <th>{t("ws.outcome")}</th>
                                    {w.plan?.plan?.outputs.map((o) => (
                                        <th key={o.target_column}>
                                            {o.target_column}
                                            {o.expected_unit
                                                ? ` (${o.expected_unit})`
                                                : ""}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {w.report.rows.map((row) => (
                                    <tr
                                        key={row.record_id}
                                        data-testid="row-outcome"
                                    >
                                        <td className="mono">
                                            {row.record_id}
                                        </td>
                                        <td>
                                            <span
                                                className={`outcome-${row.status}`}
                                            >
                                                {t(
                                                    `workflow.row.${row.status}`,
                                                )}
                                            </span>
                                        </td>
                                        {w.plan?.plan?.outputs.map((output) => {
                                            const result =
                                                w.report?.execution?.records
                                                    .find(
                                                        (record) =>
                                                            record.record_id ===
                                                            row.record_id,
                                                    )
                                                    ?.row_result.steps.find(
                                                        (step) =>
                                                            step.step_id ===
                                                            output.step_id,
                                                    )?.result;
                                            const value =
                                                result?.status === "success" &&
                                                result.value &&
                                                typeof result.value === "object"
                                                    ? (
                                                          result.value as Record<
                                                              string,
                                                              unknown
                                                          >
                                                      )[output.value_field]
                                                    : undefined;
                                            return (
                                                <td key={output.target_column}>
                                                    {typeof value ===
                                                        "number" ||
                                                    typeof value === "string" ||
                                                    typeof value === "boolean"
                                                        ? String(value)
                                                        : "—"}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    <EmptyState text={t("ws.exportPending")} />
                )}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("ws.downloads")}</h2>
                    <label>
                        {t("ws.filename")}
                        <input
                            value={w.downloadName}
                            maxLength={160}
                            onChange={(e) => w.setDownloadName(e.target.value)}
                        />
                    </label>
                    <p className="muted">{t("ws.filenameHint")}</p>
                    <Button
                        disabled={
                            w.busy ||
                            !w.job ||
                            !isTerminal(w.job.status) ||
                            !w.job.artifacts.result
                        }
                        onClick={() =>
                            void w.action(() => w.download("result"))
                        }
                    >
                        <Download />
                        {t("workflow.downloadXlsx")}
                    </Button>
                    <Button
                        variant="outline"
                        disabled={
                            w.busy ||
                            !w.job ||
                            !isTerminal(w.job.status) ||
                            !w.job.artifacts.report
                        }
                        onClick={() =>
                            void w.action(() => w.download("report"))
                        }
                    >
                        {t("workflow.downloadReport")}
                    </Button>
                    {w.job?.status === "needs_review" && (
                        <p className="notice">{t("workflow.unresolved")}</p>
                    )}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="review" />
                </div>
            </aside>
        </div>
    );
}

function Back({ w, to }: { w: Workflow; to: Workspace }) {
    const { t } = useTranslation();
    return (
        <Button variant="outline" onClick={() => w.navigate(to)}>
            <ArrowLeft aria-hidden="true" />
            {t(to)}
        </Button>
    );
}
function EmptyState({ text, icon }: { text: string; icon?: "plan" }) {
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
    const ref = useRef<HTMLDialogElement>(null);
    const [tab, setTab] = useState(section);
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
                <h2 id="diagnostics-title">{t("ws.diagnostics")}</h2>
                <button
                    className="icon-button"
                    aria-label={t("ws.close")}
                    onClick={onClose}
                >
                    <X />
                </button>
            </div>
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
