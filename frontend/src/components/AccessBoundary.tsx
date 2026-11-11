import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, setAccessCode, WorkspaceApiError } from "../api";
import { HostedContext } from "../mode";
import { initialTheme } from "../preferences";
import { Button } from "./ui/button";

const STORAGE_KEY = "enrichment-access-code";
// The code survives a reload of this tab only; storage can be unavailable.
function stored() {
    try {
        return sessionStorage.getItem(STORAGE_KEY) ?? "";
    } catch {
        return "";
    }
}
function remember(code: string) {
    try {
        sessionStorage.setItem(STORAGE_KEY, code);
    } catch {
        // Kept in memory for this page only.
    }
}

function AccessGate({ onUnlock }: { onUnlock: () => void }) {
    const { t, i18n } = useTranslation();
    const [code, setCode] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        document.documentElement.dataset.theme = initialTheme();
        document.documentElement.lang = i18n.resolvedLanguage ?? "en";
        document.title = `${t("access.title")} · AI Data Enrichment Studio`;
    }, [i18n.resolvedLanguage, t]);
    async function submit(event: FormEvent) {
        event.preventDefault();
        const offered = code.trim();
        if (!offered || busy) return;
        setBusy(true);
        setError("");
        setAccessCode(offered);
        try {
            const state = await api.access();
            if (state.granted) {
                remember(offered);
                onUnlock();
                return;
            }
            setError("wrong");
        } catch (failure) {
            setError(
                failure instanceof WorkspaceApiError &&
                    failure.code === "access_throttled"
                    ? "throttled"
                    : "unreachable",
            );
        }
        setAccessCode("");
        setBusy(false);
    }
    return (
        <main className="access-gate">
            <form onSubmit={submit}>
                <LockKeyhole aria-hidden="true" />
                <h1>AI Data Enrichment Studio</h1>
                <p>{t("access.hint")}</p>
                <label>
                    {t("access.label")}
                    <input
                        type="password"
                        autoComplete="off"
                        autoFocus
                        maxLength={128}
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                    />
                </label>
                {error && <p role="alert">{t(`access.${error}`)}</p>}
                <Button type="submit" disabled={!code.trim() || busy}>
                    {t("access.submit")}
                </Button>
                <label className="access-language">
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
            </form>
        </main>
    );
}

/**
 * Shows the studio only to visitors who hold the access code of a hosted demo.
 * The server enforces the code on every request; this is the matching door.
 * Locally no code is required and the studio appears at once.
 */
export function AccessBoundary({ children }: { children: ReactNode }) {
    // The stored code is applied during the first render, before the studio's
    // own first requests leave.
    const [state, setState] = useState<"checking" | "open" | "locked">(() => {
        setAccessCode(stored());
        return "checking";
    });
    const [hosted, setHosted] = useState(false);
    useEffect(() => {
        let active = true;
        Promise.resolve()
            .then(() => api.access())
            .then((access) => {
                if (!active) return;
                setHosted(!!access?.required);
                const locked = !!access?.required && !access.granted;
                if (locked) setAccessCode("");
                setState(locked ? "locked" : "open");
            })
            // If the check itself fails, show the studio: its own requests
            // will report the problem, and the server still enforces the code.
            .catch(() => active && setState("open"));
        return () => {
            active = false;
        };
    }, []);
    if (state === "locked")
        return <AccessGate onUnlock={() => setState("open")} />;
    // Rendered while checking so a local start has no delay; hidden by CSS so a
    // hosted visitor does not see the studio flash before the gate.
    return (
        <HostedContext.Provider value={hosted}>
            <div className="access-boundary" data-access={state}>
                {children}
            </div>
        </HostedContext.Provider>
    );
}
