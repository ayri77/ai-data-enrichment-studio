import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowRight, LoaderCircle } from "lucide-react";
import { columnLetter, parseRange } from "../range";
import type { Worksheet } from "../api";
import { Button } from "./ui/button";

interface Props {
    sheets: Worksheet[];
    sheet: string;
    range: string;
    disabled: boolean;
    pending: boolean;
    onSheet: (name: string) => void;
    onRange: (value: string) => void;
    onPreview: () => void;
}
const defaults = { first: "A", last: "A", header: "1", end: "1" };
function boundaries(value: string) {
    const parsed = parseRange(value);
    return parsed.bounds
        ? {
              first: columnLetter(parsed.bounds.firstColumn),
              last: columnLetter(parsed.bounds.lastColumn),
              header: String(parsed.bounds.headerRow),
              end: String(parsed.bounds.lastRow),
          }
        : null;
}
export function RangeSelection(props: Props) {
    const { t } = useTranslation();
    const [mode, setMode] = useState<"guided" | "advanced">("guided");
    const [guide, setGuide] = useState(
        () => boundaries(props.range) ?? defaults,
    );
    const parsed = parseRange(props.range);
    useEffect(() => {
        const next = boundaries(props.range);
        if (next) setGuide(next);
    }, [props.range]);
    function edit(key: keyof typeof guide, value: string) {
        const next = { ...guide, [key]: value };
        setGuide(next);
        props.onRange(`${next.first}${next.header}:${next.last}${next.end}`);
    }
    const worksheet = props.sheets.find((ws) => ws.name === props.sheet);
    const columns = [
        ...new Set([
            ...Array.from({ length: 100 }, (_, i) => columnLetter(i + 1)),
            guide.first,
            guide.last,
        ]),
    ];
    return (
        <form
            className="selection-toolbar"
            onSubmit={(event) => {
                event.preventDefault();
                props.onPreview();
            }}
        >
            <div className="toolbar-title">
                <h2>{t("tableSelection")}</h2>
                <div
                    className="range-mode"
                    role="group"
                    aria-label={t("rangeMode")}
                >
                    <button
                        type="button"
                        aria-pressed={mode === "guided"}
                        onClick={() => {
                            if (mode === "advanced" && parsed.error)
                                props.onRange(
                                    `${guide.first}${guide.header}:${guide.last}${guide.end}`,
                                );
                            setMode("guided");
                        }}
                        disabled={props.disabled}
                    >
                        {t("guided")}
                    </button>
                    <button
                        type="button"
                        aria-pressed={mode === "advanced"}
                        onClick={() => setMode("advanced")}
                        disabled={props.disabled}
                    >
                        {t("advanced")}
                    </button>
                </div>
            </div>
            <fieldset disabled={props.disabled} className="range-fields">
                <label className="sheet-control">
                    {t("worksheet")}
                    <select
                        aria-label={t("worksheet")}
                        value={props.sheet}
                        onChange={(e) => props.onSheet(e.target.value)}
                    >
                        {!props.sheets.length && (
                            <option value="">{t("chooseSheet")}</option>
                        )}
                        {props.sheets.map((ws) => (
                            <option key={ws.name} value={ws.name}>
                                {ws.name}
                            </option>
                        ))}
                    </select>
                </label>
                {mode === "guided" ? (
                    <>
                        <label>
                            {t("firstColumn")}
                            <select
                                aria-label={t("firstColumn")}
                                value={guide.first}
                                onChange={(e) => edit("first", e.target.value)}
                            >
                                {columns.map((letter, i) => (
                                    <option key={letter} value={letter}>
                                        {letter}
                                        {worksheet?.columns[i]
                                            ? ` · ${worksheet.columns[i]}`
                                            : ""}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label>
                            {t("lastColumn")}
                            <select
                                aria-label={t("lastColumn")}
                                value={guide.last}
                                onChange={(e) => edit("last", e.target.value)}
                            >
                                {columns.map((letter, i) => (
                                    <option key={letter} value={letter}>
                                        {letter}
                                        {worksheet?.columns[i]
                                            ? ` · ${worksheet.columns[i]}`
                                            : ""}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label>
                            {t("headerRow")}
                            <input
                                type="number"
                                min="1"
                                max="1048576"
                                value={guide.header}
                                required
                                onChange={(e) => edit("header", e.target.value)}
                            />
                        </label>
                        <label>
                            {t("lastRow")}
                            <input
                                type="number"
                                min="1"
                                max="1048576"
                                value={guide.end}
                                required
                                onChange={(e) => edit("end", e.target.value)}
                            />
                        </label>
                    </>
                ) : (
                    <label className="advanced-control">
                        {t("tableRange")}
                        <input
                            className="mono"
                            value={props.range}
                            maxLength={32}
                            required
                            placeholder="A1:E11"
                            aria-invalid={!!parsed.error}
                            aria-describedby="range-guidance"
                            onChange={(e) => props.onRange(e.target.value)}
                        />
                    </label>
                )}
                <Button
                    type="submit"
                    disabled={!props.sheet || !!parsed.error}
                    className="preview-action"
                >
                    {props.pending ? (
                        <>
                            <LoaderCircle className="spin" />
                            {t("reading")}
                        </>
                    ) : (
                        <>
                            {t("previewAction")}
                            <ArrowRight />
                        </>
                    )}
                </Button>
            </fieldset>
            {props.range && parsed.error && (
                <p className="range-error" role="alert">
                    {t(`error.${parsed.error}`)}
                </p>
            )}
            <div className="range-guidance" id="range-guidance">
                <span className="range-reference mono">
                    {parsed.canonical ?? (props.range || "A1:…")}
                </span>
                <p>
                    {t("rangeHint")} <span>{t("suggestion")}</span>
                </p>
            </div>
        </form>
    );
}
