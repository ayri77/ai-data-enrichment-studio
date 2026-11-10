import type { ReactNode } from "react";
import { Database, Download, GitBranch, Play, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Workspace } from "../useWorkflow";
import type { Theme } from "../preferences";

export function StudioShell({
    theme,
    onThemeChange,
    children,
    workspace,
    onNavigate,
    completed,
}: {
    theme: Theme;
    onThemeChange: (theme: Theme) => void;
    children: ReactNode;
    workspace: Workspace;
    onNavigate: (workspace: Workspace) => void;
    completed: Workspace[];
}) {
    const { t, i18n } = useTranslation();
    const workspaces = [
        { id: "dataset", icon: Database },
        { id: "plan", icon: GitBranch },
        { id: "execution", icon: Play },
        { id: "review", icon: ShieldCheck },
        { id: "export", icon: Download },
    ];
    return (
        <div className="studio" data-theme={theme} lang={i18n.resolvedLanguage}>
            <a className="skip-link" href="#workspace">
                {t("datasetTitle")}
            </a>
            <header className="studio-header">
                <div className="studio-brand">
                    <span className="studio-mark" aria-hidden="true">
                        <span />
                        <span />
                    </span>
                    <span>AI Data Enrichment Studio</span>
                </div>
                <div className="studio-preferences">
                    <label>
                        {t("theme")}
                        <select
                            value={theme}
                            onChange={(e) =>
                                onThemeChange(e.target.value as Theme)
                            }
                        >
                            <option value="dark">{t("dark")}</option>
                            <option value="light">{t("light")}</option>
                        </select>
                    </label>
                    <label>
                        {t("language")}
                        <select
                            value={i18n.resolvedLanguage}
                            onChange={(e) =>
                                void i18n.changeLanguage(e.target.value)
                            }
                        >
                            <option value="en">English</option>
                            <option value="uk">Українська</option>
                            <option value="de">Deutsch</option>
                        </select>
                    </label>
                </div>
            </header>
            <nav className="studio-documents" aria-label={t("workspaceNav")}>
                {workspaces.map(({ id, icon: Icon }) => (
                    <button
                        key={id}
                        aria-label={t(id)}
                        aria-description={
                            completed.includes(id as Workspace)
                                ? t("ws.complete")
                                : undefined
                        }
                        type="button"
                        onClick={() => onNavigate(id as Workspace)}
                        aria-current={id === workspace ? "page" : undefined}
                    >
                        <Icon aria-hidden="true" />
                        {t(id)}
                        {completed.includes(id as Workspace) && (
                            <small aria-label={t("ws.complete")}>✓</small>
                        )}
                    </button>
                ))}
            </nav>
            <div className="studio-connection">
                <span className="source-indicator" />
                {t("localApi")}
                <span className="connection-note">{t("sourceUnchanged")}</span>
            </div>
            <main id="workspace" tabIndex={-1}>
                {children}
            </main>
            <footer className="studio-footer">
                <span>
                    <ShieldCheck aria-hidden="true" />
                    {t("sourceUnchanged")}
                </span>
                <span>{t("sessionOnly")}</span>
            </footer>
        </div>
    );
}
