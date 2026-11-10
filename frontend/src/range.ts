/** A1 syntax only. The server validates headers, table contents and resource limits. */
export interface RangeBounds {
    firstColumn: number;
    lastColumn: number;
    headerRow: number;
    lastRow: number;
}
export type RangeError = "rangeSyntax" | "rangeOrder" | "excelLimits";
export function columnNumber(value: string): number {
    return [...value.toUpperCase()].reduce(
        (sum, char) => sum * 26 + char.charCodeAt(0) - 64,
        0,
    );
}
export function columnLetter(value: number): string {
    let result = "";
    while (value > 0) {
        value--;
        result = String.fromCharCode(65 + (value % 26)) + result;
        value = Math.floor(value / 26);
    }
    return result;
}
export function parseRange(
    value: string,
):
    | { bounds: RangeBounds; canonical: string; error?: never }
    | { error: RangeError; bounds?: never; canonical?: never } {
    const match = value
        .trim()
        .toUpperCase()
        .match(
            /^\$?([A-Z]{1,3})\$?([1-9]\d*)(?::\$?([A-Z]{1,3})\$?([1-9]\d*))?$/,
        );
    if (!match) return { error: "rangeSyntax" };
    const bounds = {
        firstColumn: columnNumber(match[1]),
        headerRow: Number(match[2]),
        lastColumn: columnNumber(match[3] ?? match[1]),
        lastRow: Number(match[4] ?? match[2]),
    };
    if (
        bounds.firstColumn > 16384 ||
        bounds.lastColumn > 16384 ||
        bounds.headerRow > 1048576 ||
        bounds.lastRow > 1048576
    )
        return { error: "excelLimits" };
    if (
        bounds.firstColumn > bounds.lastColumn ||
        bounds.headerRow > bounds.lastRow
    )
        return { error: "rangeOrder" };
    return { bounds, canonical: formatRange(bounds) };
}
export function formatRange(bounds: RangeBounds): string {
    return `${columnLetter(bounds.firstColumn)}${bounds.headerRow}:${columnLetter(bounds.lastColumn)}${bounds.lastRow}`;
}
