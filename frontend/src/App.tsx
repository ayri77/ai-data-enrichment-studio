import { useEffect, useRef, useState } from "react";
import {
    FileSpreadsheet,
    LoaderCircle,
    ShieldCheck,
    UploadCloud,
    X,
    Info,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import "./i18n";
import { StudioShell } from "./components/StudioShell";
import { RangeSelection } from "./components/RangeSelection";
import { DatasetPreview } from "./components/DatasetPreview";
import { Button } from "./components/ui/button";
import { initialTheme, savePreference } from "./preferences";
import { useDataset } from "./useDataset";

export default function App() {
    const { t, i18n } = useTranslation();
    const [theme, setTheme] = useState(initialTheme);
    const [dragging, setDragging] = useState(false);
    const fileInput = useRef<HTMLInputElement>(null);
    const d = useDataset();
    useEffect(() => {
        savePreference("theme", theme);
        document.documentElement.style.colorScheme = theme;
        document.documentElement.dataset.theme = theme;
    }, [theme]);
    useEffect(() => {
        document.documentElement.lang = i18n.resolvedLanguage ?? "en";
        document.title = `${t("datasetTitle")} · AI Data Enrichment Studio`;
    }, [i18n.resolvedLanguage]);
    return (
        <StudioShell theme={theme} onThemeChange={setTheme}>
            <div className="workspace-heading">
                <div>
                    <h1>
                        {t("datasetTitle")}
                        {d.dataset && (
                            <span className="workspace-file mono">
                                {" "}
                                / {d.dataset.filename}
                            </span>
                        )}
                    </h1>
                    <p>{t("subtitle")}</p>
                </div>
                <span className="read-only">
                    <ShieldCheck aria-hidden="true" />
                    {t("readOnly")}
                </span>
            </div>
            <section
                className={`source-upload ${dragging ? "is-dragging" : ""}`}
                aria-label={t("sourceWorkbook")}
                onDragOver={(e) => {
                    e.preventDefault();
                    if (!d.busy) setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    void d.upload(e.dataTransfer.files[0]);
                }}
            >
                <input
                    ref={fileInput}
                    type="file"
                    accept=".xlsx"
                    className="sr-only"
                    aria-label={t("uploadWorkbook")}
                    disabled={!!d.busy}
                    onChange={(e) => {
                        void d.upload(e.target.files?.[0]);
                        e.target.value = "";
                    }}
                />
                <span className="upload-glyph">
                    {d.busy === "upload" ? (
                        <LoaderCircle className="spin" />
                    ) : d.dataset ? (
                        <FileSpreadsheet />
                    ) : (
                        <UploadCloud />
                    )}
                </span>
                <div className="upload-description">
                    <strong>
                        {d.busy === "upload"
                            ? t("inspecting")
                            : d.dataset
                              ? d.dataset.filename
                              : t("dropWorkbook")}
                    </strong>
                    <span>
                        {d.dataset
                            ? t("worksheets", {
                                  count: d.dataset.worksheets.length,
                              })
                            : t("uploadHint")}
                    </span>
                </div>
                <div className="upload-actions">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={!!d.busy}
                        onClick={() => fileInput.current?.click()}
                    >
                        {d.dataset ? t("replace") : t("browse")}
                    </Button>
                    {d.dataset && (
                        <button
                            className="icon-button"
                            type="button"
                            disabled={!!d.busy}
                            aria-label={t("remove")}
                            onClick={d.remove}
                        >
                            <X />
                        </button>
                    )}
                </div>
            </section>
            <p className="async-status" role="status" aria-live="polite">
                {d.busy === "upload"
                    ? t("inspecting")
                    : d.busy === "preview"
                      ? t("reading")
                      : d.preview
                        ? t("sampleCount", {
                              shown: d.preview.profile.sample_records.length,
                              total: d.preview.profile.row_count,
                          })
                        : ""}
            </p>
            {d.error && (
                <div
                    className="error-banner"
                    role="alert"
                    data-error-code={d.error}
                >
                    <Info aria-hidden="true" />
                    <span>
                        {t(`error.${d.error}`, {
                            defaultValue: t("error.request_failed"),
                        })}
                    </span>
                </div>
            )}
            <RangeSelection
                key={`${d.dataset?.dataset_id ?? "empty"}:${d.sheet}`}
                sheets={d.dataset?.worksheets ?? []}
                sheet={d.sheet}
                range={d.range}
                disabled={!d.dataset || !!d.busy}
                pending={d.busy === "preview"}
                onSheet={(name) => d.selectSheet(name)}
                onRange={d.editRange}
                onPreview={() => void d.loadPreview()}
            />
            <DatasetPreview
                preview={d.preview}
                filename={d.dataset?.filename}
                busy={d.busy === "preview"}
                hasWorkbook={!!d.dataset}
            />
        </StudioShell>
    );
}
