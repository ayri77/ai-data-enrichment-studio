import { useRef, useState, type FormEvent } from "react";
import {
    ArrowDownToLine,
    ArrowRight,
    Check,
    ChevronRight,
    CircleHelp,
    Database,
    FileSpreadsheet,
    Layers3,
    LoaderCircle,
    LockKeyhole,
    ScanLine,
    ShieldCheck,
    Sparkles,
    Table2,
    UploadCloud,
    X,
} from "lucide-react";
import { Button } from "./components/ui/button";
import { api, type Dataset, type Preview, type Worksheet } from "./api";

function suggestedRange(sheet: Worksheet) {
    let column = "",
        n = Math.max(1, sheet.columns.length);
    while (n > 0) {
        n--;
        column = String.fromCharCode(65 + (n % 26)) + column;
        n = Math.floor(n / 26);
    }
    return `A1:${column}${Math.max(1, sheet.row_count + 1)}`;
}
const message = (error: unknown) =>
    error instanceof Error
        ? error.message
        : "Something went wrong. Please try again.";

export default function App() {
    const [dataset, setDataset] = useState<Dataset | null>(null);
    const [sheet, setSheet] = useState("");
    const [range, setRange] = useState("");
    const [preview, setPreview] = useState<Preview | null>(null);
    const [busy, setBusy] = useState<"upload" | "preview" | null>(null);
    const [error, setError] = useState("");
    const [dragging, setDragging] = useState(false);
    const input = useRef<HTMLInputElement>(null);
    const inFlight = useRef(false);

    async function upload(file?: File) {
        if (!file || inFlight.current) return;
        setError("");
        setDragging(false);
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            setError("Only .xlsx workbooks are supported.");
            return;
        }
        if (file.size > 10 * 1024 * 1024) {
            setError("Upload a file up to 10 MiB.");
            return;
        }
        inFlight.current = true;
        setBusy("upload");
        try {
            const next = await api.upload(file);
            if (dataset) void api.remove(dataset.dataset_id);
            setDataset(next);
            setPreview(null);
            setSheet(next.worksheets[0]?.name ?? "");
            setRange(
                next.worksheets[0] ? suggestedRange(next.worksheets[0]) : "",
            );
        } catch (err) {
            setError(message(err));
        } finally {
            inFlight.current = false;
            setBusy(null);
            if (input.current) input.current.value = "";
        }
    }
    async function loadPreview(event: FormEvent) {
        event.preventDefault();
        if (!dataset || inFlight.current) return;
        inFlight.current = true;
        setBusy("preview");
        setError("");
        setPreview(null);
        try {
            setPreview(
                await api.preview(
                    dataset.dataset_id,
                    sheet,
                    range.trim().toUpperCase(),
                ),
            );
        } catch (err) {
            setError(message(err));
        } finally {
            inFlight.current = false;
            setBusy(null);
        }
    }
    function selectSheet(name: string) {
        setSheet(name);
        setPreview(null);
        setError("");
        const selected = dataset?.worksheets.find((item) => item.name === name);
        setRange(selected ? suggestedRange(selected) : "");
    }
    function reset() {
        if (dataset) void api.remove(dataset.dataset_id);
        setDataset(null);
        setPreview(null);
        setSheet("");
        setRange("");
        setError("");
    }
    const selectedSheet = dataset?.worksheets.find(
        (item) => item.name === sheet,
    );

    return (
        <div className="workspace">
            <aside className="sidebar">
                <a
                    className="brand"
                    href="#dataset"
                    aria-label="Enrichment Studio home"
                >
                    <span className="brand-mark">
                        <Layers3 size={20} />
                    </span>
                    <span>
                        Enrichment
                        <span className="brand-secondary">Studio</span>
                    </span>
                </a>
                <div className="workspace-label">
                    WORKSPACE <span>LOCAL</span>
                </div>
                <nav aria-label="Workflow">
                    <a href="#dataset" className="nav-item active">
                        <Database size={17} /> Dataset{" "}
                        <span className="nav-number">01</span>
                    </a>
                    <button className="nav-item" disabled>
                        <Sparkles size={17} /> Enrichment plan{" "}
                        <LockKeyhole size={13} />
                    </button>
                    <button className="nav-item" disabled>
                        <ScanLine size={17} /> Execution{" "}
                        <LockKeyhole size={13} />
                    </button>
                    <button className="nav-item" disabled>
                        <ShieldCheck size={17} /> Review{" "}
                        <LockKeyhole size={13} />
                    </button>
                    <button className="nav-item" disabled>
                        <ArrowDownToLine size={17} /> Export{" "}
                        <LockKeyhole size={13} />
                    </button>
                </nav>
                <div className="sidebar-note">
                    <span className="small-caps">A CLEAR START</span>
                    <p>Good enrichment starts with understanding your data.</p>
                    <div className="note-line" />
                </div>
                <div className="local-status">
                    <span className="status-dot" />
                    <div>
                        Local workspace
                        <small>Single user · session storage</small>
                    </div>
                </div>
            </aside>
            <div className="main-column">
                <header className="topbar">
                    <div>
                        Workspace <ChevronRight size={13} />
                        <span>Dataset</span>
                    </div>
                    <span className="version">M5 / DATASET WORKSPACE</span>
                </header>
                <main id="dataset">
                    <div className="heading-row">
                        <div>
                            <div className="eyebrow">
                                <span /> STEP 01 / DATASET
                            </div>
                            <h1>Get to know your data.</h1>
                            <p className="subtitle">
                                Upload a workbook, select your table, and see
                                what’s inside.
                            </p>
                        </div>
                        <span className="read-only">
                            <ShieldCheck size={14} /> Read-only inspection
                        </span>
                    </div>
                    <div className="stepper" aria-label="Dataset steps">
                        <span className={dataset ? "complete" : "current"}>
                            {dataset ? <Check size={13} /> : "1"}
                        </span>
                        <b>Upload workbook</b>
                        <div />
                        <span className={dataset ? "current" : ""}>2</span>
                        <b>Select table</b>
                        <div />
                        <span className={preview ? "complete" : ""}>
                            {preview ? <Check size={13} /> : "3"}
                        </span>
                        <b>Preview data</b>
                    </div>
                    {error && (
                        <div className="error-banner" role="alert">
                            <CircleHelp size={18} />
                            <span>{error}</span>
                            <button
                                aria-label="Dismiss error"
                                onClick={() => setError("")}
                            >
                                <X size={16} />
                            </button>
                        </div>
                    )}
                    <section
                        className="configuration-grid"
                        aria-label="Dataset configuration"
                    >
                        <div className="panel upload-panel">
                            <div className="panel-title">
                                <span className="section-number">01</span>
                                <h2>Source workbook</h2>
                                <span className="file-tag">XLSX</span>
                            </div>
                            <input
                                ref={input}
                                type="file"
                                accept=".xlsx"
                                aria-label="Upload Excel workbook"
                                className="sr-only"
                                disabled={!!busy}
                                onChange={(e) =>
                                    void upload(e.target.files?.[0])
                                }
                            />
                            <div
                                className={`drop-zone ${dragging ? "dragging" : ""} ${dataset ? "has-file" : ""}`}
                                onDragOver={(e) => {
                                    e.preventDefault();
                                    if (!busy) setDragging(true);
                                }}
                                onDragLeave={() => setDragging(false)}
                                onDrop={(e) => {
                                    e.preventDefault();
                                    void upload(e.dataTransfer.files[0]);
                                }}
                            >
                                <div className="upload-icon">
                                    {busy === "upload" ? (
                                        <LoaderCircle
                                            className="animate-spin"
                                            size={25}
                                        />
                                    ) : dataset ? (
                                        <FileSpreadsheet size={26} />
                                    ) : (
                                        <UploadCloud size={26} />
                                    )}
                                </div>
                                <h3>
                                    {busy === "upload"
                                        ? "Inspecting workbook…"
                                        : dataset
                                          ? dataset.filename
                                          : "Drop your Excel workbook here"}
                                </h3>
                                <p>
                                    {dataset
                                        ? `${dataset.worksheets.length} worksheet${dataset.worksheets.length === 1 ? "" : "s"} available · original file preserved`
                                        : "A fresh perspective on your spreadsheet."}
                                </p>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={!!busy}
                                    onClick={() => input.current?.click()}
                                >
                                    {dataset
                                        ? "Replace workbook"
                                        : "Browse files"}
                                    <ArrowRight />
                                </Button>
                                <small>
                                    .xlsx only <span>·</span> Up to 10 MiB
                                </small>
                            </div>
                            <div className="preservation-note">
                                <ShieldCheck size={14} />
                                <span>
                                    Your source workbook is never modified.
                                </span>
                                {dataset && (
                                    <button
                                        disabled={!!busy}
                                        onClick={reset}
                                        aria-label="Remove workbook"
                                    >
                                        <X size={14} />
                                    </button>
                                )}
                            </div>
                        </div>
                        <form
                            className="panel selection-panel"
                            onSubmit={loadPreview}
                        >
                            <div className="panel-title">
                                <span className="section-number">02</span>
                                <h2>Select your table</h2>
                                <Table2 size={16} className="muted" />
                            </div>
                            <fieldset disabled={!dataset || !!busy}>
                                <label htmlFor="worksheet">
                                    Worksheet{" "}
                                    <span>
                                        {dataset
                                            ? `${dataset.worksheets.length} available`
                                            : "Upload to select"}
                                    </span>
                                </label>
                                <select
                                    id="worksheet"
                                    value={sheet}
                                    onChange={(e) =>
                                        selectSheet(e.target.value)
                                    }
                                >
                                    {!dataset && (
                                        <option value="">
                                            Choose a worksheet
                                        </option>
                                    )}
                                    {dataset?.worksheets.map((item) => (
                                        <option
                                            key={item.name}
                                            value={item.name}
                                        >
                                            {item.name}
                                        </option>
                                    ))}
                                </select>
                                <label htmlFor="range">
                                    Table range <span>A1 notation</span>
                                </label>
                                <input
                                    id="range"
                                    value={range}
                                    placeholder="e.g. A1:E11"
                                    maxLength={32}
                                    required
                                    onChange={(e) => {
                                        setRange(e.target.value);
                                        setPreview(null);
                                        setError("");
                                    }}
                                />
                                <p className="field-help">
                                    Include the header row. Adjust the range to
                                    match your table.
                                </p>
                                <Button
                                    className="w-full"
                                    type="submit"
                                    disabled={!sheet || !range.trim()}
                                >
                                    {busy === "preview" ? (
                                        <>
                                            <LoaderCircle className="animate-spin" />{" "}
                                            Reading selection…
                                        </>
                                    ) : (
                                        <>
                                            Preview selection <ArrowRight />
                                        </>
                                    )}
                                </Button>
                            </fieldset>
                        </form>
                    </section>
                    <section
                        className="panel preview-panel"
                        aria-label="Data preview"
                        aria-busy={busy === "preview"}
                    >
                        <div className="preview-heading">
                            <div className="preview-title">
                                <Table2 size={17} />
                                <h2>Data preview</h2>
                                {preview && (
                                    <span className="preview-badge">
                                        {preview.sheet_name}
                                    </span>
                                )}
                            </div>
                            <span className="preview-caption">
                                {preview
                                    ? `${preview.range_ref} · ${preview.profile.sample_records.length} of ${preview.profile.row_count} records`
                                    : "Your selected table, at a glance"}
                            </span>
                        </div>
                        {preview ? (
                            <>
                                <div className="table-scroll">
                                    <table>
                                        <thead>
                                            <tr>
                                                <th
                                                    className="row-index"
                                                    scope="col"
                                                >
                                                    #
                                                </th>
                                                {preview.profile.columns.map(
                                                    (column) => (
                                                        <th
                                                            key={column.name}
                                                            scope="col"
                                                        >
                                                            <span>
                                                                {column.name}
                                                            </span>
                                                            <small>
                                                                {
                                                                    column.inferred_type
                                                                }
                                                            </small>
                                                        </th>
                                                    ),
                                                )}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {preview.profile.sample_records.map(
                                                (record, index) => (
                                                    <tr key={index}>
                                                        <td className="row-index">
                                                            {index + 1}
                                                        </td>
                                                        {preview.profile.columns.map(
                                                            (column) => (
                                                                <td
                                                                    key={
                                                                        column.name
                                                                    }
                                                                >
                                                                    {record[
                                                                        column
                                                                            .name
                                                                    ] ==
                                                                    null ? (
                                                                        <span className="null-value">
                                                                            null
                                                                        </span>
                                                                    ) : (
                                                                        String(
                                                                            record[
                                                                                column
                                                                                    .name
                                                                            ],
                                                                        )
                                                                    )}
                                                                </td>
                                                            ),
                                                        )}
                                                    </tr>
                                                ),
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                                {preview.profile.row_count === 0 && (
                                    <p className="no-records">
                                        Headers found. This selection contains
                                        no non-empty data rows.
                                    </p>
                                )}
                                <div className="table-footer">
                                    <span>
                                        <span className="status-dot" />{" "}
                                        {preview.profile.columns.length} columns{" "}
                                        <i /> {preview.profile.row_count}{" "}
                                        records in selection
                                    </span>
                                    <span>
                                        Sample limited to {preview.sample_limit}{" "}
                                        rows · values up to 200 characters
                                    </span>
                                </div>
                                {[
                                    ...preview.profile.schema_warnings,
                                    ...preview.profile.columns.flatMap((c) =>
                                        c.warnings.map(
                                            (w) => `${c.name}: ${w}`,
                                        ),
                                    ),
                                ].map((warning, index) => (
                                    <p className="profile-warning" key={index}>
                                        {warning}
                                    </p>
                                ))}
                            </>
                        ) : (
                            <div className="empty-preview" role="status">
                                <span className="empty-icon">
                                    {busy === "preview" ? (
                                        <LoaderCircle
                                            className="animate-spin"
                                            size={24}
                                        />
                                    ) : (
                                        <Table2 size={25} />
                                    )}
                                </span>
                                <h3>
                                    {busy === "preview"
                                        ? "Reading your selected table…"
                                        : dataset
                                          ? "Your workbook is ready to explore"
                                          : "A little context goes a long way"}
                                </h3>
                                <p>
                                    {dataset
                                        ? "Choose a worksheet and range, then preview your selection."
                                        : "Upload an Excel workbook to explore its worksheets and preview real data."}
                                </p>
                                {selectedSheet && (
                                    <small>
                                        {selectedSheet.row_count} non-empty rows
                                        detected after the first worksheet row
                                    </small>
                                )}
                            </div>
                        )}
                    </section>
                    <footer className="page-footer">
                        <span>
                            <LockKeyhole size={12} /> Local session. No external
                            providers called.
                        </span>
                        <span>Inspect first. Enrich next.</span>
                    </footer>
                </main>
            </div>
        </div>
    );
}
