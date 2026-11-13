import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, WorkspaceApiError } from "../api";
import { useHosted } from "../mode";
import type { Workflow } from "../useWorkflow";
import { Button } from "./ui/button";

export function ProviderSettings({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const hosted = useHosted();
    const keyInput = useRef<HTMLInputElement>(null);
    const [model, setModel] = useState(w.provider?.model ?? "");
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState(false);
    const pending = useRef(false);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        void w.refreshProvider();
        const input = keyInput.current;
        return () => {
            mounted.current = false;
            if (input) input.value = "";
        };
    }, []);
    useEffect(() => {
        setModel(w.provider?.model ?? "");
    }, [w.provider?.model]);
    async function apply(remove = false) {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setMessage("");
        setError(false);
        // Uncontrolled password input: no key in React state or workflow state.
        const input = keyInput.current;
        const request = remove
            ? api.removeProviderKey()
            : api.configureProvider({
                  model: model || null,
                  ...(input?.value ? { api_key: input.value } : {}),
              });
        if (input) input.value = "";
        try {
            const next = await request;
            w.setProvider(next);
            if (mounted.current)
                setMessage(remove ? "provider.removed" : "provider.saved");
        } catch (err) {
            if (mounted.current) {
                setError(true);
                const code =
                    err instanceof WorkspaceApiError
                        ? err.code
                        : "request_failed";
                setMessage(
                    [
                        "configuration_forbidden",
                        "unsupported_model",
                        "invalid_api_key",
                    ].includes(code)
                        ? `error.${code}`
                        : "provider.saveFailed",
                );
            }
        } finally {
            pending.current = false;
            if (mounted.current) setBusy(false);
        }
    }
    const p = w.provider;
    return (
        <>
            <h3>{t("ws.backendOwned")}</h3>
            <p>{t("provider.ephemeral")}</p>
            <p role="status">{t(`provider.${w.planningReadiness}`)}</p>
            <dl>
                <dt>{t("provider.name")}</dt>
                <dd>OpenAI</dd>
                <dt>{t("provider.keyStatus")}</dt>
                <dd>
                    {t(
                        !p
                            ? "provider.loading"
                            : p.key_present
                              ? "provider.keyPresent"
                              : "provider.keyMissing",
                    )}
                </dd>
                <dt>{t("provider.serverGate")}</dt>
                <dd>
                    {t(
                        !p
                            ? "provider.loading"
                            : p.server_network_allowed
                              ? "ws.yes"
                              : "ws.no",
                    )}
                </dd>
            </dl>
            <form
                onSubmit={(e) => {
                    e.preventDefault();
                    void apply();
                }}
                autoComplete="off"
            >
                <fieldset
                    disabled={busy || !p?.configuration_enabled || w.locked}
                >
                    <label>
                        {t("provider.model")}
                        <select
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                        >
                            <option value="">
                                {t("provider.chooseModel")}
                            </option>
                            {p?.supported_models.map((m) => (
                                <option key={m.id} value={m.id}>
                                    {m.measured === false
                                        ? t("provider.notMeasured", {
                                              model: m.label,
                                          })
                                        : m.label}
                                </option>
                            ))}
                        </select>
                    </label>
                    {p?.supported_models.some(
                        (m) => m.id === model && m.measured === false,
                    ) && (
                        <p className="muted" data-testid="model-not-measured">
                            {t("provider.notMeasuredHint")}
                        </p>
                    )}
                    <label>
                        {t("provider.keyInput")}
                        <input
                            ref={keyInput}
                            type="password"
                            autoComplete="new-password"
                            maxLength={512}
                            spellCheck={false}
                            autoCapitalize="none"
                        />
                    </label>
                    <p>{t("provider.replaceHint")}</p>
                    <Button type="submit">{t("provider.apply")}</Button>
                    <Button
                        type="button"
                        variant="outline"
                        disabled={!p?.key_present}
                        onClick={() => void apply(true)}
                    >
                        {t("provider.remove")}
                    </Button>
                </fieldset>
            </form>
            {p && !p.configuration_enabled && (
                <p>
                    {t(hosted ? "provider.hostedOnly" : "provider.localOnly")}
                </p>
            )}
            {message && <p role={error ? "alert" : "status"}>{t(message)}</p>}
            <Button
                variant="outline"
                disabled={busy}
                onClick={() => void w.refreshProvider()}
            >
                {t("provider.refresh")}
            </Button>
            <p>{t("workflow.networkHint")}</p>
            <p>
                {t("ws.consentState")}: {t(w.network ? "ws.yes" : "ws.no")}
            </p>
            {!w.network && <p>{t("provider.consentMissing")}</p>}
        </>
    );
}
