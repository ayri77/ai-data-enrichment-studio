import { useEffect, useRef, useState } from "react";
import {
    api,
    isTerminal,
    WorkspaceApiError,
    type AuditReport,
    type EnrichmentPlan,
    type Job,
    type PlanDraft,
    type Preview,
    type ProviderReadiness,
} from "./api";
import { parseRange } from "./range";
import { validMappings, type OutputTarget } from "./workflowModel";

export type Workspace = "dataset" | "plan" | "execution" | "review" | "export";
export function useWorkflow(
    preview: Preview | null,
    sourceKey: string,
    filename?: string,
    sheetColumns: string[] = [],
    sourceBusy = false,
) {
    const [provider, setProviderState] = useState<ProviderReadiness | null>(
        null,
    );
    const [providerError, setProviderError] = useState(false);
    const providerRevision = useRef(0);
    function setProvider(next: ProviderReadiness) {
        providerRevision.current++;
        if (mounted.current) {
            setProviderState(next);
            setProviderError(false);
        }
    }
    async function refreshProvider() {
        const revision = ++providerRevision.current;
        try {
            const next = await api.providerSettings();
            if (mounted.current && revision === providerRevision.current) {
                setProviderState(next);
                setProviderError(false);
            }
        } catch {
            if (mounted.current && revision === providerRevision.current) {
                setProviderState(null);
                setProviderError(true);
            }
        }
    }
    const [workspace, setWorkspace] = useState<Workspace>("dataset");
    const [instruction, setInstruction] = useState("");
    const [mapping, setMapping] = useState<OutputTarget[]>([
        { mode: "existing", name: "", column: "" },
    ]);
    const [savedPlan, setSavedPlan] = useState("");
    const [network, setNetwork] = useState(false);
    const [policy, setPolicy] = useState<"strict" | "verified_only">("strict");
    const [timeout, setTimeout] = useState("180");
    const [plan, setPlan] = useState<PlanDraft | null>(null);
    const [job, setJob] = useState<Job | null>(null);
    const [report, setReport] = useState<AuditReport | null>(null);
    const [history, setHistory] = useState<
        { job: Job; report: AuditReport | null; plan: PlanDraft | null }[]
    >([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [pollError, setPollError] = useState("");
    const [retry, setRetry] = useState(0);
    const [downloadName, setDownloadName] = useState("");
    const mounted = useRef(false);
    const pending = useRef(false);
    const generation = useRef(0);
    const active = !!job && !isTerminal(job.status);
    const locked = busy || active || sourceBusy;
    useEffect(() => {
        mounted.current = true;
        void refreshProvider();
        return () => {
            mounted.current = false;
        };
    }, []);
    // The hook lives above all workspace bodies. Navigation never owns job lifetime.
    useEffect(() => {
        invalidate();
        setInstruction("");
        setMapping([{ mode: "existing", name: "", column: "" }]);
        setSavedPlan("");
    }, [sourceKey]);
    useEffect(() => {
        setDownloadName(
            `${(filename ?? "dataset").replace(/\.xlsx$/i, "")}-enriched.xlsx`,
        );
    }, [filename]);
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
    function archive() {
        if (job)
            setHistory((old) =>
                [
                    { job, report, plan },
                    ...old.filter((x) => x.job.job_id !== job.job_id),
                ].slice(0, 8),
            );
    }
    function invalidate() {
        generation.current++;
        archive();
        setPlan(null);
        setJob(null);
        setReport(null);
        setError("");
        setPollError("");
    }
    function edit(change: () => void) {
        if (locked) return;
        invalidate();
        change();
    }
    const bounds = preview ? parseRange(preview.range_ref).bounds : undefined;
    const withinBounds =
        !!bounds &&
        bounds.lastRow - bounds.headerRow >= 1 &&
        bounds.lastRow - bounds.headerRow <= 20 &&
        bounds.lastColumn <= 100;
    const mappingValid =
        !!preview && validMappings(mapping, preview, sheetColumns);
    const timeoutValid =
        /^\d+$/.test(timeout) && Number(timeout) >= 1 && Number(timeout) <= 300;
    const planningReadiness = !provider
        ? providerError
            ? "unavailable"
            : "loading"
        : provider.planning_mode === "offline_injected"
          ? "offline"
          : !provider.server_network_allowed
            ? "networkDisabled"
            : !provider.model
              ? "modelMissing"
              : !provider.key_present
                ? "keyMissing"
                : "ready";
    const canBuild =
        !!preview &&
        withinBounds &&
        mappingValid &&
        !!instruction.trim() &&
        (!!savedPlan.trim() ||
            (network &&
                (planningReadiness === "ready" ||
                    planningReadiness === "offline"))) &&
        !locked;
    async function create() {
        if (!canBuild || !preview) return;
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
        invalidate();
        const revision = generation.current;
        const draft = await api.createPlan({
            dataset_id: preview.dataset_id,
            sheet_name: preview.sheet_name,
            range_ref: preview.range_ref,
            instruction,
            output_columns: Object.fromEntries(
                mapping.map((x) => [
                    x.mode === "existing" ? x.name : x.name.trim(),
                    x.column,
                ]),
            ),
            allow_network: network,
            mountain_resolution_policy: policy,
            ...(supplied === undefined ? {} : { plan: supplied }),
        });
        if (mounted.current && revision === generation.current) {
            setPlan(draft);
            setWorkspace("plan");
        }
    }
    async function validate() {
        if (!plan || locked) return;
        const validation = await api.validatePlan(plan.plan_id);
        const current = await api.getPlan(plan.plan_id);
        if (mounted.current) setPlan({ ...current, validation });
    }
    async function approve() {
        if (!plan || !plan.plan || !plan.validation.valid || locked) return;
        const approved = await api.approvePlan(plan.plan_id, plan.fingerprint);
        if (mounted.current) {
            setPlan(approved);
            setWorkspace("execution");
        }
    }
    const canRun =
        !!plan?.plan &&
        plan.validation.valid &&
        plan.status === "approved" &&
        network &&
        timeoutValid &&
        !locked;
    async function run() {
        if (!canRun || !plan) return;
        const started = await api.startRun(
            plan.plan_id,
            network,
            Number(timeout),
        );
        if (mounted.current) {
            archive();
            setReport(null);
            setPollError("");
            setJob(started);
            setWorkspace("execution");
        }
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
                } else timer = window.setTimeout(() => void poll(), 1000);
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
    async function cancel() {
        if (!job || !active) return;
        await api.cancelJob(job.job_id);
        if (mounted.current) setRetry((x) => x + 1);
    }
    async function download(kind: "result" | "report") {
        if (!job || !isTerminal(job.status) || !job.artifacts[kind]) return;
        const blob = await api.artifact(job.job_id, kind);
        if (!mounted.current) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        const base =
            downloadName
                .trim()
                .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
                .replace(/\.(xlsx|json)$/i, "") || "dataset-enriched";
        link.download = `${base}${kind === "result" ? ".xlsx" : "-audit.json"}`;
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    return {
        provider,
        setProvider,
        refreshProvider,
        planningReadiness,
        workspace,
        navigate: setWorkspace,
        instruction,
        setInstruction,
        mapping,
        setMapping,
        savedPlan,
        setSavedPlan,
        network,
        setNetwork,
        policy,
        setPolicy,
        timeout,
        setTimeout,
        plan,
        job,
        report,
        history,
        busy,
        error,
        pollError,
        active,
        locked,
        edit,
        withinBounds,
        mappingValid,
        timeoutValid,
        canBuild,
        canRun,
        action,
        create,
        validate,
        approve,
        run,
        cancel,
        retryPoll: () => setRetry((x) => x + 1),
        download,
        downloadName,
        setDownloadName,
    };
}
export type Workflow = ReturnType<typeof useWorkflow>;
