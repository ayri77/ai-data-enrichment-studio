import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    api,
    isTerminal,
    WorkspaceApiError,
    type AuditReport,
    type EnrichmentPlan,
    type Job,
    type PlanDraft,
    type Preview,
} from "../api";
import { Button } from "./ui/button";
import { columnNumber, parseRange } from "../range";

export function PlanRunWorkspace({
    preview,
    onLock,
}: {
    preview: Preview;
    onLock: (locked: boolean) => void;
}) {
    const { t } = useTranslation();
    const [instruction, setInstruction] = useState("");
    const [mapping, setMapping] = useState([{ name: "", column: "" }]);
    const [savedPlan, setSavedPlan] = useState("");
    const [network, setNetwork] = useState(false);
    const [policy, setPolicy] = useState<"strict" | "verified_only">("strict");
    const [timeout, setTimeout] = useState("180");
    const [plan, setPlan] = useState<PlanDraft | null>(null);
    const [job, setJob] = useState<Job | null>(null);
    const [report, setReport] = useState<AuditReport | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [pollError, setPollError] = useState("");
    const [retry, setRetry] = useState(0);
    const mounted = useRef(false);
    const pending = useRef(false);
    const active = !!job && !isTerminal(job.status);
    const locked = busy || active;
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);
    useEffect(() => {
        onLock(locked);
        return () => onLock(false);
    }, [locked, onLock]);

    const code = (err: unknown) =>
        err instanceof WorkspaceApiError ? err.code : "request_failed";
    async function action(work: () => Promise<void>) {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setError("");
        try {
            await work();
        } catch (err) {
            if (mounted.current) setError(code(err));
        } finally {
            pending.current = false;
            if (mounted.current) setBusy(false);
        }
    }
    function invalidate() {
        setPlan(null);
        setJob(null);
        setReport(null);
        setError("");
        setPollError("");
    }
    const selection = parseRange(preview.range_ref);
    const withinBounds =
        !!selection.bounds &&
        selection.bounds.lastRow - selection.bounds.headerRow >= 1 &&
        selection.bounds.lastRow - selection.bounds.headerRow <= 20 &&
        selection.bounds.lastColumn <= 100;
    const mappingValid =
        mapping.every(
            ({ name, column }) =>
                name.trim() &&
                /^[A-Z]{1,2}$/.test(column) &&
                columnNumber(column) <= 100,
        ) &&
        new Set(mapping.map((x) => x.name.trim())).size === mapping.length &&
        new Set(mapping.map((x) => x.column)).size === mapping.length;
    const timeoutValid =
        /^\d+$/.test(timeout) && Number(timeout) >= 1 && Number(timeout) <= 300;
    async function create() {
        let supplied: EnrichmentPlan | undefined;
        if (savedPlan.trim()) {
            try {
                supplied = JSON.parse(savedPlan);
            } catch {
                throw new WorkspaceApiError("plan_json", "Invalid plan JSON");
            }
            if (
                !supplied ||
                typeof supplied !== "object" ||
                Array.isArray(supplied)
            )
                throw new WorkspaceApiError("plan_json", "Use a JSON object");
        }
        setPlan(null);
        setJob(null);
        setReport(null);
        setPollError("");
        const draft = await api.createPlan({
            dataset_id: preview.dataset_id,
            sheet_name: preview.sheet_name,
            range_ref: preview.range_ref,
            instruction,
            output_columns: Object.fromEntries(
                mapping.map((x) => [x.name.trim(), x.column]),
            ),
            allow_network: network,
            mountain_resolution_policy: policy,
            ...(supplied === undefined ? {} : { plan: supplied }),
        });
        if (mounted.current) setPlan(draft);
    }

    const jobId = job?.job_id;
    useEffect(() => {
        if (!jobId) return;
        const controller = new AbortController();
        let timer: number | undefined;
        async function poll() {
            try {
                const next = await api.getJob(jobId!, controller.signal);
                if (controller.signal.aborted) return;
                setJob(next);
                if (isTerminal(next.status)) {
                    if (next.artifacts.report) {
                        const audit = await api.getReport(
                            jobId!,
                            controller.signal,
                        );
                        if (!controller.signal.aborted) setReport(audit);
                    }
                } else {
                    timer = window.setTimeout(() => void poll(), 1000);
                }
            } catch (err) {
                if (!controller.signal.aborted) setPollError(code(err));
            }
        }
        setPollError("");
        void poll();
        return () => {
            controller.abort();
            window.clearTimeout(timer);
        };
    }, [jobId, retry]);

    async function download(kind: "result" | "report") {
        if (!job) return;
        const blob = await api.artifact(job.job_id, kind);
        if (!mounted.current) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${job.job_id}-${kind === "result" ? "result.xlsx" : "report.json"}`;
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    return (
        <section className="plan-run" aria-label={t("workflow.title")}>
            <h2>{t("workflow.title")}</h2>
            <p>
                {preview.sheet_name} ·{" "}
                <span className="mono">{preview.range_ref}</span> ·{" "}
                {t("workflow.bounds")}
            </p>
            {!withinBounds && (
                <p role="alert">{t("workflow.selectionLimit")}</p>
            )}
            <fieldset disabled={locked}>
                <label>
                    {t("workflow.instruction")}
                    <textarea
                        maxLength={4000}
                        value={instruction}
                        onChange={(e) => {
                            invalidate();
                            setInstruction(e.target.value);
                        }}
                    />
                </label>
                <p>{t("workflow.mappingHint")}</p>
                {mapping.map((entry, index) => (
                    <div className="mapping-row" key={index}>
                        <label>
                            {t("workflow.outputName")} {index + 1}
                            <input
                                value={entry.name}
                                onChange={(e) => {
                                    invalidate();
                                    setMapping(
                                        mapping.map((x, i) =>
                                            i === index
                                                ? { ...x, name: e.target.value }
                                                : x,
                                        ),
                                    );
                                }}
                            />
                        </label>
                        <label>
                            {t("workflow.excelColumn")} {index + 1}
                            <input
                                value={entry.column}
                                maxLength={2}
                                onChange={(e) => {
                                    invalidate();
                                    setMapping(
                                        mapping.map((x, i) =>
                                            i === index
                                                ? {
                                                      ...x,
                                                      column: e.target.value
                                                          .toUpperCase()
                                                          .trim(),
                                                  }
                                                : x,
                                        ),
                                    );
                                }}
                            />
                        </label>
                        {mapping.length > 1 && (
                            <Button
                                variant="outline"
                                onClick={() => {
                                    invalidate();
                                    setMapping(
                                        mapping.filter((_, i) => i !== index),
                                    );
                                }}
                            >
                                {t("workflow.removeMapping")} {index + 1}
                            </Button>
                        )}
                    </div>
                ))}
                <Button
                    variant="outline"
                    disabled={mapping.length >= 20}
                    onClick={() => {
                        invalidate();
                        setMapping([...mapping, { name: "", column: "" }]);
                    }}
                >
                    {t("workflow.addMapping")}
                </Button>
                <label className="network-opt-in">
                    <input
                        type="checkbox"
                        checked={network}
                        onChange={(e) => {
                            invalidate();
                            setNetwork(e.target.checked);
                        }}
                    />
                    {t("workflow.network")}
                </label>
                <p>{t("workflow.networkHint")}</p>
                <label>
                    {t("workflow.policy")}
                    <select
                        value={policy}
                        onChange={(e) => {
                            invalidate();
                            setPolicy(e.target.value as typeof policy);
                        }}
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
                        type="number"
                        min={1}
                        max={300}
                        value={timeout}
                        onChange={(e) => setTimeout(e.target.value)}
                    />
                </label>
                <details>
                    <summary>{t("workflow.savedPlan")}</summary>
                    <label>
                        {t("workflow.planJson")}
                        <textarea
                            className="mono"
                            value={savedPlan}
                            onChange={(e) => {
                                invalidate();
                                setSavedPlan(e.target.value);
                            }}
                        />
                    </label>
                </details>
                <Button
                    disabled={
                        !withinBounds ||
                        !mappingValid ||
                        !instruction.trim() ||
                        (!network && !savedPlan.trim())
                    }
                    onClick={() => void action(create)}
                >
                    {savedPlan.trim()
                        ? t("workflow.submitPlan")
                        : t("workflow.generate")}
                </Button>
            </fieldset>
            {busy && <p role="status">{t("workflow.requesting")}</p>}
            {error && (
                <p
                    className="error-banner"
                    role="alert"
                    data-error-code={error}
                >
                    {t(`error.${error}`, {
                        defaultValue: t("error.request_failed"),
                    })}{" "}
                    <code>{error}</code>
                </p>
            )}
            {plan && (
                <section aria-label={t("workflow.planSummary")}>
                    <h3>{t("workflow.planSummary")}</h3>
                    <p>
                        {t("workflow.decision")}:{" "}
                        <code>{plan.planning_status}</code>
                        {plan.reason && ` · ${plan.reason}`}
                    </p>
                    <p>
                        {t("workflow.binding")}: {plan.dataset_id} ·{" "}
                        {plan.sheet_name} · {plan.range_ref}
                    </p>
                    <p>{plan.instruction}</p>
                    <p>
                        {t("workflow.policy")}:{" "}
                        <code>{plan.mountain_resolution_policy}</code>
                    </p>
                    {plan.plan && (
                        <>
                            <ol>
                                {plan.plan.steps.map((step) => (
                                    <li key={step.id}>
                                        <strong>{step.id}</strong> ·{" "}
                                        <code>{step.capability}</code>
                                        <div>
                                            {t("workflow.inputs")}:{" "}
                                            <code>
                                                {JSON.stringify(step.inputs)}
                                            </code>
                                        </div>
                                        <div>
                                            {t("workflow.parameters")}:{" "}
                                            <code>
                                                {JSON.stringify(
                                                    step.parameters,
                                                )}
                                            </code>
                                        </div>
                                    </li>
                                ))}
                            </ol>
                            <ul>
                                {plan.plan.outputs.map((output, i) => (
                                    <li key={i}>
                                        <code>
                                            {output.step_id}.
                                            {output.value_field}
                                        </code>{" "}
                                        → {output.target_column} →{" "}
                                        <strong>
                                            {
                                                plan.output_columns[
                                                    output.target_column
                                                ]
                                            }
                                        </strong>
                                        {output.expected_unit &&
                                            ` (${output.expected_unit})`}
                                    </li>
                                ))}
                            </ul>
                            {plan.plan.assumptions.length > 0 && (
                                <>
                                    <h3>{t("workflow.assumptions")}</h3>
                                    <ul>
                                        {plan.plan.assumptions.map(
                                            (value, i) => (
                                                <li key={i}>{value}</li>
                                            ),
                                        )}
                                    </ul>
                                </>
                            )}
                        </>
                    )}
                    <p data-testid="validation">
                        {t(
                            plan.validation.valid
                                ? "workflow.valid"
                                : "workflow.invalid",
                        )}
                    </p>
                    <ul>
                        {plan.validation.errors.map((err, i) => (
                            <li key={i}>
                                <code>{err.code}</code> {err.step_id} ·{" "}
                                {err.message}
                            </li>
                        ))}
                    </ul>
                    <details>
                        <summary>{t("workflow.fullPlan")}</summary>
                        <pre>{JSON.stringify(plan, null, 2)}</pre>
                    </details>
                    <p>
                        {t("workflow.fingerprint")}:{" "}
                        <code className="fingerprint" data-testid="fingerprint">
                            {plan.fingerprint}
                        </code>
                    </p>
                    <p>
                        {t("workflow.sourceHash")}:{" "}
                        <code className="fingerprint">
                            {plan.source_sha256}
                        </code>
                    </p>
                    {plan.approved_at && (
                        <p>
                            {t("workflow.approved")}: {plan.approved_at}
                        </p>
                    )}
                    <div className="workflow-actions">
                        <Button
                            variant="outline"
                            disabled={locked || !!job}
                            onClick={() =>
                                void action(async () => {
                                    const validation = await api.validatePlan(
                                        plan.plan_id,
                                    );
                                    const current = await api.getPlan(
                                        plan.plan_id,
                                    );
                                    if (mounted.current)
                                        setPlan({ ...current, validation });
                                })
                            }
                        >
                            {t("workflow.validate")}
                        </Button>
                        <Button
                            disabled={
                                locked ||
                                !!job ||
                                !plan.plan ||
                                !plan.validation.valid ||
                                plan.status === "approved"
                            }
                            onClick={() =>
                                void action(async () => {
                                    const approved = await api.approvePlan(
                                        plan.plan_id,
                                        plan.fingerprint,
                                    );
                                    if (mounted.current) setPlan(approved);
                                })
                            }
                        >
                            {t("workflow.approve")}
                        </Button>
                        <Button
                            disabled={
                                locked ||
                                !!job ||
                                !plan.plan ||
                                !plan.validation.valid ||
                                plan.status !== "approved" ||
                                !network ||
                                !timeoutValid
                            }
                            onClick={() =>
                                void action(async () => {
                                    const started = await api.startRun(
                                        plan.plan_id,
                                        network,
                                        Number(timeout),
                                    );
                                    if (mounted.current) setJob(started);
                                })
                            }
                        >
                            {t("workflow.run")}
                        </Button>
                    </div>
                </section>
            )}
            {job && (
                <section aria-label={t("workflow.results")}>
                    <h3>{t("workflow.results")}</h3>
                    <p role="status" data-testid="job-status">
                        {t(`workflow.status.${job.status}`)}
                    </p>
                    <p>
                        {t("workflow.job")}: <code>{job.job_id}</code> ·{" "}
                        {t("workflow.mode")}:{" "}
                        <strong>{job.execution_mode}</strong>
                    </p>
                    {job.execution_mode === "offline_fixture" && (
                        <p className="error-banner">{t("workflow.fixture")}</p>
                    )}
                    {job.status === "needs_review" && (
                        <p>{t("workflow.unresolved")}</p>
                    )}
                    <p>
                        {t("workflow.created")}: {job.created_at}
                    </p>
                    {job.started_at && (
                        <p>
                            {t("workflow.started")}: {job.started_at}
                        </p>
                    )}
                    {job.finished_at && (
                        <p>
                            {t("workflow.finished")}: {job.finished_at}
                        </p>
                    )}
                    {job.error && (
                        <p role="alert">
                            <code>{job.error.code}</code> · {job.error.message}
                        </p>
                    )}
                    {active && (
                        <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                                void action(async () => {
                                    await api.cancelJob(job.job_id);
                                    setRetry((x) => x + 1);
                                })
                            }
                        >
                            {t("workflow.cancel")}
                        </Button>
                    )}
                    {pollError && (
                        <div role="alert">
                            {t(`error.${pollError}`, {
                                defaultValue: t("error.request_failed"),
                            })}{" "}
                            <code>{pollError}</code>
                            <Button
                                variant="outline"
                                onClick={() => setRetry((x) => x + 1)}
                            >
                                {t("workflow.retry")}
                            </Button>
                        </div>
                    )}
                    {job.metrics && (
                        <>
                            <h3>{t("workflow.metrics")}</h3>
                            <dl className="workflow-metrics">
                                {Object.entries(job.metrics).map(
                                    ([key, value]) => (
                                        <div key={key}>
                                            <dt>
                                                {t(`workflow.metric.${key}`, {
                                                    defaultValue: key,
                                                })}
                                            </dt>
                                            <dd>{value}</dd>
                                        </div>
                                    ),
                                )}
                            </dl>
                        </>
                    )}
                    {report?.execution && (
                        <>
                            <h3>{t("workflow.providerRequests")}</h3>
                            <pre>
                                {JSON.stringify(
                                    report.execution.provider_requests,
                                    null,
                                    2,
                                )}
                            </pre>
                            <h3>{t("workflow.outcomes")}</h3>
                            {report.rows?.map((row) => (
                                <details
                                    key={row.record_id}
                                    data-testid="row-outcome"
                                >
                                    <summary>
                                        {row.record_id} ·{" "}
                                        {t(`workflow.row.${row.status}`)}
                                    </summary>
                                    <ul>
                                        {report
                                            .execution!.records.find(
                                                (x) =>
                                                    x.record_id ===
                                                    row.record_id,
                                            )
                                            ?.row_result.steps.map((step) => (
                                                <li key={step.step_id}>
                                                    <code>{step.step_id}</code>{" "}
                                                    ·{" "}
                                                    {t(
                                                        `workflow.row.${step.result.status}`,
                                                    )}
                                                    {step.result.status ===
                                                    "success" ? (
                                                        <pre>
                                                            {JSON.stringify(
                                                                step.result
                                                                    .value,
                                                                null,
                                                                2,
                                                            )}
                                                        </pre>
                                                    ) : (
                                                        <p>
                                                            <code>
                                                                {
                                                                    step.result
                                                                        .error_code
                                                                }
                                                            </code>{" "}
                                                            ·{" "}
                                                            {
                                                                step.result
                                                                    .message
                                                            }
                                                        </p>
                                                    )}
                                                    {step.result.evidence.map(
                                                        (evidence, i) => (
                                                            <p key={i}>
                                                                {
                                                                    evidence.source
                                                                }{" "}
                                                                ·{" "}
                                                                <code>
                                                                    {
                                                                        evidence.reference
                                                                    }
                                                                </code>
                                                            </p>
                                                        ),
                                                    )}
                                                </li>
                                            ))}
                                    </ul>
                                </details>
                            ))}
                            <h3>{t("workflow.issues")}</h3>
                            <ul>
                                {report.execution.records.flatMap((record) =>
                                    record.row_result.steps
                                        .filter(
                                            (step) =>
                                                step.result.status ===
                                                "failure",
                                        )
                                        .map((step) => (
                                            <li
                                                key={`${record.record_id}:${step.step_id}`}
                                            >
                                                {record.record_id} ·{" "}
                                                {step.step_id} ·{" "}
                                                <code>
                                                    {step.result.error_code}
                                                </code>{" "}
                                                · {step.result.message}
                                            </li>
                                        )),
                                )}
                            </ul>
                            {report.source_unchanged !== undefined && (
                                <p>
                                    {t("workflow.sourceVerified")}:{" "}
                                    {String(report.source_unchanged)}
                                </p>
                            )}
                            {report.preserved_cells && (
                                <p>
                                    {t("workflow.preserved")}:{" "}
                                    {JSON.stringify(report.preserved_cells)}
                                </p>
                            )}
                        </>
                    )}
                    <div className="workflow-actions">
                        <Button
                            disabled={
                                busy ||
                                !isTerminal(job.status) ||
                                !job.artifacts.result
                            }
                            onClick={() =>
                                void action(() => download("result"))
                            }
                        >
                            {t("workflow.downloadXlsx")}
                        </Button>
                        <Button
                            variant="outline"
                            disabled={
                                busy ||
                                !isTerminal(job.status) ||
                                !job.artifacts.report
                            }
                            onClick={() =>
                                void action(() => download("report"))
                            }
                        >
                            {t("workflow.downloadReport")}
                        </Button>
                    </div>
                    <p>{t("workflow.reviewUnavailable")}</p>
                </section>
            )}
        </section>
    );
}
