import { useRef, useState } from "react";
import {
    FileSpreadsheet,
    LoaderCircle,
    UploadCloud,
    X,
    Info,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { RangeSelection } from "./RangeSelection";
import { DatasetPreview } from "./DatasetPreview";
import { DatasetConfiguration } from "./WorkspaceViews";
import { Button } from "./ui/button";
import type { useDataset } from "../useDataset";
import type { Workflow } from "../useWorkflow";
export function DatasetWorkspace({
    d,
    workflow,
    onOpenSettings,
}: {
    d: ReturnType<typeof useDataset>;
    workflow: Workflow;
    onOpenSettings?: () => void;
}) {
    const { t } = useTranslation();
    const [dragging, setDragging] = useState(false);
    const fileInput = useRef<HTMLInputElement>(null);
    const workflowLocked = workflow.locked;
    return (
        <div className="dataset-layout">
            <div className="dataset-source">
                <div className="source-controls panel-scroll">
                    <section
                        className={`source-upload ${dragging ? "is-dragging" : ""}`}
                        aria-label={t("sourceWorkbook")}
                        onDragOver={(e) => {
                            e.preventDefault();
                            if (!d.busy && !workflowLocked) setDragging(true);
                        }}
                        onDragLeave={() => setDragging(false)}
                        onDrop={(e) => {
                            e.preventDefault();
                            setDragging(false);
                            if (!workflowLocked)
                                void d.upload(e.dataTransfer.files[0]);
                        }}
                    >
                        <input
                            ref={fileInput}
                            type="file"
                            accept=".xlsx"
                            className="sr-only"
                            aria-label={t("uploadWorkbook")}
                            disabled={!!d.busy || workflowLocked}
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
                                disabled={!!d.busy || workflowLocked}
                                onClick={() => fileInput.current?.click()}
                            >
                                {d.dataset ? t("replace") : t("browse")}
                            </Button>
                            {d.dataset && (
                                <button
                                    className="icon-button"
                                    type="button"
                                    disabled={!!d.busy || workflowLocked}
                                    aria-label={t("remove")}
                                    onClick={d.remove}
                                >
                                    <X />
                                </button>
                            )}
                        </div>
                    </section>
                    <p
                        className="async-status"
                        role="status"
                        aria-live="polite"
                    >
                        {d.busy === "upload"
                            ? t("inspecting")
                            : d.busy === "preview"
                              ? t("reading")
                              : d.preview
                                ? t("sampleCount", {
                                      shown: d.preview.profile.sample_records
                                          .length,
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
                        disabled={!d.dataset || !!d.busy || workflowLocked}
                        pending={d.busy === "preview"}
                        onSheet={(name) => d.selectSheet(name)}
                        onRange={d.editRange}
                        onPreview={() => void d.loadPreview()}
                    />
                </div>
                <DatasetPreview
                    preview={d.preview}
                    filename={d.dataset?.filename}
                    busy={d.busy === "preview"}
                    hasWorkbook={!!d.dataset}
                />
            </div>
            <DatasetConfiguration
                workflow={workflow}
                preview={d.preview}
                onOpenSettings={onOpenSettings}
            />
        </div>
    );
}
