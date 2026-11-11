import { useEffect, useRef, useState } from "react";
import {
    api,
    WorkspaceApiError,
    type Dataset,
    type Preview,
    type Sample,
} from "./api";
import { columnLetter, parseRange } from "./range";

export function useDataset() {
    const [dataset, setDataset] = useState<Dataset | null>(null);
    const [sheet, setSheet] = useState("");
    const [range, setRange] = useState("");
    const [preview, setPreview] = useState<Preview | null>(null);
    const [busy, setBusy] = useState<"upload" | "preview" | null>(null);
    const [error, setError] = useState("");
    const pending = useRef(false);
    // Examples offered when there is no workbook yet, and the one in use (if any).
    const [samples, setSamples] = useState<Sample[]>([]);
    const [sample, setSample] = useState<Sample | null>(null);
    useEffect(() => {
        let active = true;
        Promise.resolve()
            .then(() => api.samples())
            .then((found) => active && setSamples(found?.samples ?? []))
            // Examples are a convenience; without them the upload still works.
            .catch(() => undefined);
        return () => {
            active = false;
        };
    }, []);
    const errorCode = (err: unknown) =>
        err instanceof WorkspaceApiError ? err.code : "request_failed";
    function clearPreview() {
        setPreview(null);
        setError("");
    }
    const fullRange = (source: Dataset | null, name: string) => {
        const selected = source?.worksheets.find((ws) => ws.name === name);
        return selected
            ? `A1:${columnLetter(Math.max(1, selected.columns.length))}${Math.max(1, selected.row_count + 1)}`
            : "";
    };
    function selectSheet(name: string, source = dataset) {
        setSheet(name);
        clearPreview();
        setRange(fullRange(source, name));
        // A selection changed by hand is no longer the prepared example.
        if (source === dataset) setSample(null);
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
            setSample(null);
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
    // One click from an empty screen to a previewed example: the workbook goes
    // through the ordinary upload, so it is checked like any other file.
    async function loadSample(chosen: Sample) {
        if (pending.current) return;
        pending.current = true;
        setBusy("upload");
        setError("");
        try {
            const blob = await api.sampleWorkbook(chosen.sample_id);
            const next = await api.upload(new File([blob], chosen.filename));
            if (dataset) void api.remove(dataset.dataset_id);
            const name = next.worksheets[0]?.name ?? "";
            const selection = fullRange(next, name);
            setSample(chosen);
            setDataset(next);
            setSheet(name);
            setRange(selection);
            setPreview(null);
            setBusy("preview");
            setPreview(await api.preview(next.dataset_id, name, selection));
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
        setSample(null);
    }
    function remove() {
        if (dataset) void api.remove(dataset.dataset_id);
        setSample(null);
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
        samples,
        sample,
        loadSample,
        upload,
        selectSheet,
        editRange,
        loadPreview,
        remove,
    };
}
