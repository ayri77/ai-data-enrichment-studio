import type { ReactNode } from "react";
import {
    Database,
    Download,
    GitBranch,
    Info,
    Play,
    ShieldCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Workspace } from "../useWorkflow";
import { Advanced, useHosted } from "../mode";
import type { Mode, Theme } from "../preferences";

/** What the navigation opens: the overview, or one step of the work. */
export type Page = Workspace | "overview";

export function StudioShell({
    theme,
    onThemeChange,
    mode,
    onModeChange,
    unavailable,
    children,
    workspace,
    onNavigate,
    completed,
}: {
    theme: Theme;
    onThemeChange: (theme: Theme) => void;
    mode: Mode;
    onModeChange: (mode: Mode) => void;
    /** Steps that cannot be opened now, each with the reason shown as a tooltip. */
    unavailable: Partial<Record<Workspace, string>>;
    children: ReactNode;
    workspace: Page;
    onNavigate: (page: Page) => void;
    completed: Workspace[];
}) {
    const { t, i18n } = useTranslation();
    const hosted = useHosted();
    const workspaces: { id: Page; icon: typeof Info }[] = [
        { id: "overview", icon: Info },
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
                    <label>
                        {t("mode.label")}
                        <select
                            value={mode}
                            onChange={(e) =>
                                onModeChange(e.target.value as Mode)
                            }
                        >
                            <option value="basic">{t("mode.basic")}</option>
                            <option value="advanced">
                                {t("mode.advanced")}
                            </option>
                        </select>
                    </label>
                </div>
            </header>
            <nav className="studio-documents" aria-label={t("workspaceNav")}>
                {workspaces.map(({ id, icon: Icon }) => {
                    const done = id !== "overview" && completed.includes(id);
                    return (
                        <button
                            key={id}
                            aria-label={t(id)}
                            aria-description={
                                done ? t("ws.complete") : undefined
                            }
                            type="button"
                            disabled={id in unavailable}
                            title={
                                id === "overview" ? undefined : unavailable[id]
                            }
                            onClick={() => onNavigate(id)}
                            aria-current={id === workspace ? "page" : undefined}
                        >
                            <Icon aria-hidden="true" />
                            {t(id)}
                            {done && (
                                <small aria-label={t("ws.complete")}>✓</small>
                            )}
                        </button>
                    );
                })}
            </nav>
            <Advanced>
                <div className="studio-connection">
                    <span className="source-indicator" />
                    {t(hosted ? "hostedApi" : "localApi")}
                    <span className="connection-note">
                        {t("sourceUnchanged")}
                    </span>
                </div>
            </Advanced>
            <main id="workspace" tabIndex={-1}>
                {children}
            </main>
            <footer className="studio-footer">
                <span>
                    <ShieldCheck aria-hidden="true" />
                    {t("sourceUnchanged")}
                </span>
                <span>
                    {t(
                        hosted
                            ? "hostedSession"
                            : mode === "basic"
                              ? "sessionLocal"
                              : "sessionOnly",
                    )}
                </span>
            </footer>
        </div>
    );
}
