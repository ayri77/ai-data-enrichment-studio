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
    type Review,
    type Sample,
} from "./api";
import { columnLetter, parseRange } from "./range";
import { DEMO_LIMITS, type Limits } from "./useLimits";
import { validMappings, type OutputTarget } from "./workflowModel";
import {
    decisions,
    hasCandidateChoice,
    isSearched,
    validChoices,
    withRecommendations,
    type Choice,
    type Choices,
    type Searches,
} from "./reviewModel";

export type Requirement =
    | "preview"
    | "selection"
    | "instruction"
    | "mapping"
    | "consent"
    | "provider";
export type Workspace = "dataset" | "plan" | "execution" | "review" | "export";
/** A request the interface waits for; each has its own wording. */
export type Operation =
    | "plan"
    | "check"
    | "prepared"
    | "validate"
    | "approve"
    | "start"
    | "resume"
    | "search"
    | "suggest"
    | "cancel"
    | "download";
export function useWorkflow(
    preview: Preview | null,
    sourceKey: string,
    filename?: string,
    sheetColumns: string[] = [],
    sourceBusy = false,
    sample: Sample | null = null,
    options: {
        limits?: Limits;
        /** Basic mode: pressing the button is the consent, with no checkbox. */
        implicitConsent?: boolean;
        /**
         * Workbook and sheet, without the range. The task is reset when this
         * changes; when it is absent, on every change of the selection.
         */
        tableKey?: string;
    } = {},
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
    const { maxRows, maxTimeout } = options.limits ?? DEMO_LIMITS;
    const [workspace, setWorkspace] = useState<Workspace>("dataset");
    const [instruction, setInstruction] = useState("");
    const [mapping, setMapping] = useState<OutputTarget[]>([
        { mode: "existing", name: "", column: "" },
    ]);
    const [savedPlan, setSavedPlan] = useState("");
    const [network, setNetwork] = useState(false);
    // Whether external requests are agreed to: ticked, or implied by the mode.
    const consent = !!options.implicitConsent || network;
    // What to do when the source confirms exactly one match but could not confirm
    // the others: accept it (default, marked in the results) or ask the user.
    const [policy, setPolicy] = useState<"strict" | "verified_only">(
        "verified_only",
    );
    // An output cell that already holds a value is kept unless the person
    // asks for it to be replaced. A new workbook starts from "keep" again.
    const [existingOutputs, setExistingOutputs] = useState<"keep" | "replace">(
        "keep",
    );
    const [timeout, setTimeout] = useState("180");
    const [plan, setPlan] = useState<PlanDraft | null>(null);
    const [job, setJob] = useState<Job | null>(null);
    const [report, setReport] = useState<AuditReport | null>(null);
    // Candidates a person can choose between, and the choices made so far.
    const [review, setReview] = useState<Review | null>(null);
    const [choices, setChoices] = useState<Choices>({});
    // What the latest search under another name found for each name.
    const [searches, setSearches] = useState<Searches>({});
    // Other names the AI proposed for each name; they are only text to search.
    const [suggestions, setSuggestions] = useState<Record<string, string[]>>(
        {},
    );
    const [history, setHistory] = useState<
        { job: Job; report: AuditReport | null; plan: PlanDraft | null }[]
    >([]);
    const [busy, setBusy] = useState(false);
    // What the interface is waiting for, and since when (browser clock).
    const [operation, setOperation] = useState<{
        kind: Operation;
        since: number;
    } | null>(null);
    const [jobSince, setJobSince] = useState<number | null>(null);
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
    // Any change of the selection invalidates the plan and the run made for it.
    useEffect(() => {
        invalidate();
    }, [sourceKey]);
    // The task itself belongs to the table, not to its boundaries: adjusting
    // the range keeps the instruction, the output column and a pasted plan.
    // Another workbook or sheet is another table and starts empty; a bundled
    // example arrives with its instruction and output column.
    useEffect(() => {
        setInstruction(sample?.instruction ?? "");
        setMapping([
            {
                mode: "existing",
                name: sample?.output.name ?? "",
                column: sample?.output.column ?? "",
            },
        ]);
        setSavedPlan("");
        setExistingOutputs("keep");
    }, [options.tableKey ?? sourceKey]);
    // A table with exactly one empty column most likely wants that column
    // filled: propose it, as long as nothing was chosen yet. The user sees the
    // proposal and can change it.
    useEffect(() => {
        if (!preview || sample) return;
        const first = parseRange(preview.range_ref).bounds?.firstColumn ?? 1;
        const empty = preview.profile.columns
            .map((column, index) => ({ column, index }))
            .filter(
                ({ column }) => column.null_count >= preview.profile.row_count,
            );
        if (empty.length !== 1) return;
        setMapping((current) =>
            current.length === 1 && !current[0].name && !current[0].column
                ? [
                      {
                          mode: "existing",
                          name: empty[0].column.name,
                          column: columnLetter(first + empty[0].index),
                      },
                  ]
                : current,
        );
    }, [preview]);
    useEffect(() => {
        setDownloadName(
            `${(filename ?? "dataset").replace(/\.xlsx$/i, "")}-enriched.xlsx`,
        );
    }, [filename]);
    const code = (err: unknown) =>
        err instanceof WorkspaceApiError ? err.code : "request_failed";
    async function action(work: () => Promise<void>, kind: Operation) {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setOperation({ kind, since: Date.now() });
        setError("");
        try {
            await work();
        } catch (err) {
            if (mounted.current) setError(code(err));
        } finally {
            pending.current = false;
            if (mounted.current) {
                setBusy(false);
                setOperation(null);
            }
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
        setReview(null);
        setChoices({});
        setSearches({});
        setSuggestions({});
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
        bounds.lastRow - bounds.headerRow <= maxRows &&
        bounds.lastColumn <= 100;
    const mappingValid =
        !!preview && validMappings(mapping, preview, sheetColumns);
    const timeoutValid =
        /^\d+$/.test(timeout) &&
        Number(timeout) >= 1 &&
        Number(timeout) <= maxTimeout;
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
    // One list drives both the button and the on-screen explanation of what is
    // still missing, so the two can never disagree.
    const requirements: { key: Requirement; met: boolean }[] = [
        { key: "preview", met: !!preview },
        ...(preview ? [{ key: "selection" as const, met: withinBounds }] : []),
        { key: "instruction", met: !!instruction.trim() },
        { key: "mapping", met: mappingValid },
        // A saved plan is validated offline and needs neither consent nor a model.
        ...(savedPlan.trim()
            ? []
            : [
                  { key: "consent" as const, met: consent },
                  {
                      key: "provider" as const,
                      met:
                          planningReadiness === "ready" ||
                          planningReadiness === "offline",
                  },
              ]),
    ];
    const missing = requirements.filter((r) => !r.met).map((r) => r.key);
    const canBuild = missing.length === 0 && !locked;
    // Insurance for a demo: the example's prepared plan needs no AI request.
    async function loadPreparedPlan() {
        if (!sample?.has_plan || locked) return;
        const prepared = await api.samplePlan(sample.sample_id);
        if (mounted.current) {
            invalidate();
            setSavedPlan(JSON.stringify(prepared, null, 4));
        }
    }
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
            allow_network: consent,
            mountain_resolution_policy: policy,
            existing_outputs: existingOutputs,
            ...(supplied === undefined ? {} : { plan: supplied }),
        });
        if (mounted.current && revision === generation.current) {
            // Consent given by pressing the button stays given if the user
            // switches to Advanced, where it is a ticked checkbox.
            if (options.implicitConsent) setNetwork(true);
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
        consent &&
        timeoutValid &&
        !locked;
    function begin(started: Job) {
        archive();
        setReport(null);
        setReview(null);
        setChoices({});
        setSearches({});
        setSuggestions({});
        setPollError("");
        setJob(started);
        setJobSince(Date.now());
        setWorkspace("execution");
    }
    async function run() {
        if (!canRun || !plan) return;
        const started = await api.startRun(
            plan.plan_id,
            consent,
            Number(timeout),
        );
        if (mounted.current) begin(started);
    }
    // Basic: one press approves the displayed plan and starts it. The server
    // still requires the approval; the two requests are made one after another.
    // If the start is refused, the plan stays approved and Execution offers
    // the start again.
    async function approveAndRun() {
        if (!plan?.plan || !plan.validation.valid || locked) return;
        let current = plan;
        if (current.status !== "approved") {
            current = await api.approvePlan(plan.plan_id, plan.fingerprint);
            if (!mounted.current) return;
            setPlan(current);
            setWorkspace("execution");
        }
        if (!consent || !timeoutValid) return;
        setOperation({ kind: "start", since: Date.now() });
        const started = await api.startRun(
            current.plan_id,
            consent,
            Number(timeout),
        );
        if (mounted.current) begin(started);
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
                    if (next.status === "needs_review") {
                        const found =
                            (await api.getReview(jobId!, controller.signal)) ??
                            null;
                        if (!controller.signal.aborted) {
                            setReview(found);
                            setChoices((old) => validChoices(found, old));
                        }
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
    const canResume =
        job?.status === "needs_review" &&
        review?.job_id === job.job_id &&
        hasCandidateChoice(review, choices) &&
        consent &&
        timeoutValid &&
        !locked;
    function choose(operationId: string, choice: Choice) {
        if (!locked) setChoices((old) => ({ ...old, [operationId]: choice }));
    }
    function unchoose(operationId: string) {
        if (!locked)
            setChoices((old) =>
                Object.fromEntries(
                    Object.entries(old).filter(([id]) => id !== operationId),
                ),
            );
    }
    // The source is asked at once; nothing is written until a found candidate
    // is chosen and the remaining rows are filled.
    async function searchOtherName(operationId: string, name: string) {
        if (!job || !consent) return;
        const found = await api.searchOtherName(
            job.job_id,
            operationId,
            name,
            consent,
        );
        if (!mounted.current) return;
        setSearches((old) => ({ ...old, [operationId]: found }));
        // A choice from the search this one replaces has nothing behind it now.
        setChoices((old) =>
            isSearched(old[operationId])
                ? Object.fromEntries(
                      Object.entries(old).filter(([id]) => id !== operationId),
                  )
                : old,
        );
    }
    async function suggestNames(operationId: string) {
        if (!job || !consent) return;
        const { names } = await api.suggestNames(
            job.job_id,
            operationId,
            consent,
        );
        if (mounted.current)
            setSuggestions((old) => ({ ...old, [operationId]: names }));
    }
    function acceptRecommendations() {
        if (review && !locked)
            setChoices((old) => withRecommendations(review, old));
    }
    // A resume is a new job on the same approved plan; the earlier one stays in history.
    async function resume() {
        if (!canResume || !job || !review) return;
        const started = await api.resumeJob(
            job.job_id,
            decisions(review, choices),
            consent,
            Number(timeout),
        );
        if (mounted.current) {
            archive();
            setReport(null);
            setReview(null);
            setChoices({});
            setSearches({});
            setSuggestions({});
            setPollError("");
            setJob(started);
            setJobSince(Date.now());
            setWorkspace("execution");
        }
    }
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
        sample,
        loadPreparedPlan,
        network,
        consent,
        setNetwork,
        policy,
        setPolicy,
        existingOutputs,
        setExistingOutputs,
        timeout,
        setTimeout,
        plan,
        job,
        report,
        review,
        choices,
        choose,
        unchoose,
        searches,
        searchOtherName,
        suggestions,
        suggestNames,
        acceptRecommendations,
        canResume,
        resume,
        history,
        busy,
        operation,
        jobSince,
        error,
        pollError,
        active,
        locked,
        edit,
        withinBounds,
        mappingValid,
        maxRows,
        // Where a new column would go: right after everything the sheet holds.
        freeColumn: bounds
            ? columnLetter(Math.max(bounds.lastColumn, sheetColumns.length) + 1)
            : "",
        timeoutValid,
        maxTimeout,
        canBuild,
        missing,
        canRun,
        action,
        create,
        validate,
        approve,
        run,
        approveAndRun,
        cancel,
        retryPoll: () => setRetry((x) => x + 1),
        download,
        downloadName,
        setDownloadName,
    };
}
export type Workflow = ReturnType<typeof useWorkflow>;
