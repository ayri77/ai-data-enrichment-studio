import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import "./i18n";
import { AccessBoundary } from "./components/AccessBoundary";
import { ActivityBar } from "./components/ActivityBar";
import { StudioShell, type Page } from "./components/StudioShell";
import { DatasetWorkspace } from "./components/DatasetWorkspace";
import {
    Diagnostics,
    ExecutionWorkspace,
    PlanWorkspace,
} from "./components/WorkspaceViews";
import { ExportWorkspace } from "./components/ExportWorkspace";
import { OverviewWorkspace } from "./components/OverviewWorkspace";
import { ReviewWorkspace } from "./components/ReviewWorkspace";
import { Button } from "./components/ui/button";
import { CapabilitiesContext, useCapabilities } from "./capabilities";
import { Advanced, ModeContext } from "./mode";
import { initialMode, initialTheme, savePreference } from "./preferences";
import { useDataset } from "./useDataset";
import { useLimits } from "./useLimits";
import { useWorkflow, type Workspace } from "./useWorkflow";

type Opening = "overview" | "dataset";

/** Opens on the overview; `opening` lets a caller begin at the first step. */
export default function App({ opening = "overview" }: { opening?: Opening }) {
    return (
        <AccessBoundary>
            <Studio opening={opening} />
        </AccessBoundary>
    );
}

function Studio({ opening }: { opening: Opening }) {
    const { t, i18n } = useTranslation();
    const [theme, setTheme] = useState(initialTheme);
    const [mode, setMode] = useState(initialMode);
    const [diagnostics, setDiagnostics] = useState<
        "details" | "settings" | null
    >(null);
    const heading = useRef<HTMLHeadingElement>(null);
    const limits = useLimits();
    const capabilities = useCapabilities();
    // The overview stands beside the steps: opening it leaves the work as it is.
    const [overview, setOverview] = useState(opening === "overview");
    const d = useDataset({
        auto: mode === "basic",
        maxTableRows: limits.maxTableRows,
        maxTableCells: limits.maxTableCells,
    });
    const w = useWorkflow(
        d.preview,
        `${d.dataset?.dataset_id ?? ""}:${d.sheet}:${d.range}`,
        d.dataset?.filename,
        d.dataset?.worksheets.find((ws) => ws.name === d.sheet)?.columns,
        !!d.busy,
        d.sample,
        {
            limits,
            implicitConsent: mode === "basic",
            tableKey: `${d.dataset?.dataset_id ?? ""}:${d.sheet}`,
        },
    );
    useEffect(() => {
        savePreference("theme", theme);
        document.documentElement.style.colorScheme = theme;
        document.documentElement.dataset.theme = theme;
    }, [theme]);
    useEffect(() => {
        savePreference("mode", mode);
        document.documentElement.dataset.mode = mode;
    }, [mode]);
    const page: Page = overview ? "overview" : w.workspace;
    const open = (next: Page) => {
        setOverview(next === "overview");
        if (next !== "overview") w.navigate(next);
    };
    useEffect(() => {
        document.documentElement.lang = i18n.resolvedLanguage ?? "en";
        document.title = `${t(page)} · AI Data Enrichment Studio`;
    }, [i18n.resolvedLanguage, page, t]);
    useEffect(() => {
        heading.current?.focus();
    }, [page]);
    const completed: Workspace[] = [];
    if (d.preview) completed.push("dataset");
    if (w.plan?.status === "approved") completed.push("plan");
    if (w.job?.status === "completed" || w.job?.status === "needs_review")
        completed.push("execution");
    // In Basic the review step is offered only when there is something to settle.
    const unavailable: Partial<Record<Workspace, string>> =
        mode === "basic" && w.job?.status !== "needs_review"
            ? { review: t("ws.reviewUnavailable") }
            : {};
    const stranded = w.workspace in unavailable;
    // A short request ends by opening its own step; leaving meanwhile would
    // only be undone. A running job does not hold the user in place.
    if (w.busy || d.busy)
        for (const id of [
            "dataset",
            "plan",
            "execution",
            "review",
            "export",
        ] as const)
            if (id !== page) unavailable[id] = t("activity.wait");
    // Removing the workbook resets the task with it; the first step then shows
    // the examples again.
    const startOver = () => {
        d.remove();
        open("dataset");
    };
    useEffect(() => {
        // Switching to Basic on a step it does not offer leads back to the run.
        if (stranded) w.navigate(w.job ? "execution" : "dataset");
    }, [stranded]);
    return (
        <ModeContext.Provider value={mode}>
            <CapabilitiesContext.Provider value={capabilities}>
                <StudioShell
                    theme={theme}
                    onThemeChange={setTheme}
                    mode={mode}
                    onModeChange={setMode}
                    unavailable={unavailable}
                    workspace={page}
                    onNavigate={open}
                    completed={completed}
                >
                    <div className="workspace-heading">
                        <div>
                            <h1 ref={heading} tabIndex={-1}>
                                {t(page)}
                            </h1>
                            <p>
                                {t(
                                    mode === "basic" && page === "plan"
                                        ? "ws.planHintBasic"
                                        : `ws.${page}Hint`,
                                )}
                            </p>
                        </div>
                        <div className="workspace-tools">
                            <Advanced>
                                <Button
                                    variant="outline"
                                    onClick={() => setDiagnostics("settings")}
                                >
                                    <SlidersHorizontal aria-hidden="true" />
                                    {t("ws.providerSettings")}
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => setDiagnostics("details")}
                                >
                                    {t("ws.diagnostics")}
                                </Button>
                            </Advanced>
                        </div>
                    </div>
                    {(w.error || w.pollError) && (
                        <div
                            role="alert"
                            className="error-banner"
                            data-error-code={w.error || w.pollError}
                        >
                            {t(`error.${w.error || w.pollError}`, {
                                defaultValue: t("error.request_failed"),
                            })}
                            {w.pollError && (
                                <Button variant="outline" onClick={w.retryPoll}>
                                    {t("workflow.retry")}
                                </Button>
                            )}
                        </div>
                    )}
                    <ActivityBar w={w} d={d} />
                    <div
                        className="workspace-body"
                        aria-busy={w.busy || !!d.busy}
                    >
                        {page === "overview" && (
                            <OverviewWorkspace
                                maxRows={limits.maxRows}
                                capabilities={limits.capabilities}
                                samples={d.dataset ? [] : d.samples}
                                busy={w.busy || !!d.busy}
                                onStart={() => open("dataset")}
                                onSample={(sample) => {
                                    open("dataset");
                                    void d.loadSample(sample);
                                }}
                            />
                        )}
                        {page === "dataset" && (
                            <DatasetWorkspace
                                d={d}
                                workflow={w}
                                onOpenSettings={() =>
                                    setDiagnostics("settings")
                                }
                            />
                        )}
                        {page === "plan" && <PlanWorkspace w={w} />}
                        {page === "execution" && (
                            <ExecutionWorkspace w={w} onStartOver={startOver} />
                        )}
                        {page === "review" && <ReviewWorkspace w={w} />}
                        {page === "export" && (
                            <ExportWorkspace w={w} onStartOver={startOver} />
                        )}
                    </div>
                    {diagnostics && (
                        <Diagnostics
                            w={w}
                            preview={d.preview}
                            section={diagnostics}
                            onClose={() => setDiagnostics(null)}
                        />
                    )}
                </StudioShell>
            </CapabilitiesContext.Provider>
        </ModeContext.Provider>
    );
}
