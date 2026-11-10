import { useRef, useState } from "react";
import { api, WorkspaceApiError, type Dataset, type Preview } from "./api";
import { columnLetter, parseRange } from "./range";

export function useDataset() {
    const [dataset, setDataset] = useState<Dataset | null>(null);
    const [sheet, setSheet] = useState("");
    const [range, setRange] = useState("");
    const [preview, setPreview] = useState<Preview | null>(null);
    const [busy, setBusy] = useState<"upload" | "preview" | null>(null);
    const [error, setError] = useState("");
    const pending = useRef(false);
    const errorCode = (err: unknown) =>
        err instanceof WorkspaceApiError ? err.code : "request_failed";
    function clearPreview() {
        setPreview(null);
        setError("");
    }
    function selectSheet(name: string, source = dataset) {
        const selected = source?.worksheets.find((ws) => ws.name === name);
        setSheet(name);
        clearPreview();
        setRange(
            selected
                ? `A1:${columnLetter(Math.max(1, selected.columns.length))}${Math.max(1, selected.row_count + 1)}`
                : "",
        );
    }
    async function upload(file?: File) {
        if (!file || pending.current) return;
        setError("");
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            setError("invalid_file_type");
            return;
        }
        if (file.size > 10 * 1024 * 1024) {
            setError("upload_too_large");
            return;
        }
        pending.current = true;
        setBusy("upload");
        try {
            const next = await api.upload(file);
            if (dataset) void api.remove(dataset.dataset_id);
            setDataset(next);
            selectSheet(next.worksheets[0]?.name ?? "", next);
        } catch (err) {
            setError(errorCode(err));
        } finally {
            pending.current = false;
            setBusy(null);
        }
    }
    async function loadPreview() {
        if (!dataset || pending.current) return;
        const parsed = parseRange(range);
        if (parsed.error) {
            setError(parsed.error);
            return;
        }
        pending.current = true;
        setBusy("preview");
        clearPreview();
        try {
            setPreview(
                await api.preview(dataset.dataset_id, sheet, parsed.canonical),
            );
        } catch (err) {
            setError(errorCode(err));
        } finally {
            pending.current = false;
            setBusy(null);
        }
    }
    function editRange(value: string) {
        setRange(value);
        clearPreview();
    }
    function remove() {
        if (dataset) void api.remove(dataset.dataset_id);
        setDataset(null);
        setSheet("");
        setRange("");
        clearPreview();
    }
    return {
        dataset,
        sheet,
        range,
        preview,
        busy,
        error,
        upload,
        selectSheet,
        editRange,
        loadPreview,
        remove,
    };
}
