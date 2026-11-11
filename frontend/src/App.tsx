import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal, Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import "./i18n";
import { AccessBoundary } from "./components/AccessBoundary";
import { StudioShell } from "./components/StudioShell";
import { DatasetWorkspace } from "./components/DatasetWorkspace";
import {
    Diagnostics,
    ExecutionWorkspace,
    PlanWorkspace,
} from "./components/WorkspaceViews";
import { ExportWorkspace } from "./components/ExportWorkspace";
import { ReviewWorkspace } from "./components/ReviewWorkspace";
import { Button } from "./components/ui/button";
import { Advanced, ModeContext } from "./mode";
import { initialMode, initialTheme, savePreference } from "./preferences";
import { useDataset } from "./useDataset";
import { useWorkflow, type Workspace } from "./useWorkflow";

export default function App() {
    return (
        <AccessBoundary>
            <Studio />
        </AccessBoundary>
    );
}

function Studio() {
    const { t, i18n } = useTranslation();
    const [theme, setTheme] = useState(initialTheme);
    const [mode, setMode] = useState(initialMode);
    const [diagnostics, setDiagnostics] = useState<
        "details" | "settings" | null
    >(null);
    const heading = useRef<HTMLHeadingElement>(null);
    const d = useDataset();
    const w = useWorkflow(
        d.preview,
        `${d.dataset?.dataset_id ?? ""}:${d.sheet}:${d.range}`,
        d.dataset?.filename,
        d.dataset?.worksheets.find((ws) => ws.name === d.sheet)?.columns,
        !!d.busy,
        d.sample,
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
    useEffect(() => {
        document.documentElement.lang = i18n.resolvedLanguage ?? "en";
        document.title = `${t(w.workspace)} · AI Data Enrichment Studio`;
    }, [i18n.resolvedLanguage, w.workspace, t]);
    useEffect(() => {
        heading.current?.focus();
    }, [w.workspace]);
    const completed: Workspace[] = [];
    if (d.preview) completed.push("dataset");
    if (w.plan?.status === "approved") completed.push("plan");
    if (w.job?.status === "completed" || w.job?.status === "needs_review")
        completed.push("execution");
    // In Basic the review step is offered only when there is something to settle.
    const unavailable: Workspace[] =
        mode === "basic" && w.job?.status !== "needs_review" ? ["review"] : [];
    const stranded = unavailable.includes(w.workspace);
    useEffect(() => {
        // Switching to Basic on a step it does not offer leads back to the run.
        if (stranded) w.navigate(w.job ? "execution" : "dataset");
    }, [stranded]);
    return (
        <ModeContext.Provider value={mode}>
            <StudioShell
                theme={theme}
                onThemeChange={setTheme}
                mode={mode}
                onModeChange={setMode}
                unavailable={unavailable}
                workspace={w.workspace}
                onNavigate={w.navigate}
                completed={completed}
            >
                <div className="workspace-heading">
                    <div>
                        <h1 ref={heading} tabIndex={-1}>
                            {t(w.workspace)}
                        </h1>
                        <p>{t(`ws.${w.workspace}Hint`)}</p>
                    </div>
                    <div className="workspace-tools">
                        {w.active && (
                            <button
                                className="active-job"
                                onClick={() => w.navigate("execution")}
                            >
                                <Activity aria-hidden="true" />
                                {t(`workflow.status.${w.job!.status}`)}
                            </button>
                        )}
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
                {w.busy && (
                    <p className="request-status" role="status">
                        {t("workflow.requesting")}
                    </p>
                )}
                <div className="workspace-body" aria-busy={w.busy}>
                    {w.workspace === "dataset" && (
                        <DatasetWorkspace
                            d={d}
                            workflow={w}
                            onOpenSettings={() => setDiagnostics("settings")}
                        />
                    )}
                    {w.workspace === "plan" && <PlanWorkspace w={w} />}
                    {w.workspace === "execution" && (
                        <ExecutionWorkspace w={w} />
                    )}
                    {w.workspace === "review" && <ReviewWorkspace w={w} />}
                    {w.workspace === "export" && <ExportWorkspace w={w} />}
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
        </ModeContext.Provider>
    );
}
