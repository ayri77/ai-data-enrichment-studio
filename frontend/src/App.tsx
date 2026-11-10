import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal, Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import "./i18n";
import { StudioShell } from "./components/StudioShell";
import { DatasetWorkspace } from "./components/DatasetWorkspace";
import {
    Diagnostics,
    ExecutionWorkspace,
    ExportWorkspace,
    PlanWorkspace,
    ReviewWorkspace,
} from "./components/WorkspaceViews";
import { Button } from "./components/ui/button";
import { initialTheme, savePreference } from "./preferences";
import { useDataset } from "./useDataset";
import { useWorkflow, type Workspace } from "./useWorkflow";

export default function App() {
    const { t, i18n } = useTranslation();
    const [theme, setTheme] = useState(initialTheme);
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
    );
    useEffect(() => {
        savePreference("theme", theme);
        document.documentElement.style.colorScheme = theme;
        document.documentElement.dataset.theme = theme;
    }, [theme]);
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
    return (
        <StudioShell
            theme={theme}
            onThemeChange={setTheme}
            workspace={w.workspace}
            onNavigate={w.navigate}
            completed={completed}
        >
            <div className="workspace-heading">
                <div>
                    <span className="eyebrow">{t("ws.studio")}</span>
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
                    <DatasetWorkspace d={d} workflow={w} />
                )}
                {w.workspace === "plan" && <PlanWorkspace w={w} />}
                {w.workspace === "execution" && <ExecutionWorkspace w={w} />}
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
    );
}
