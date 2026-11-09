import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function contrast(first: string, second: string) {
    function luminance(hex: string) {
        const values = hex
            .slice(1)
            .match(/../g)!
            .map((x) => parseInt(x, 16) / 255)
            .map((x) =>
                x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4,
            );
        return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
    }
    const a = luminance(first),
        b = luminance(second);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
describe("semantic theme contrast", () => {
    const css = readFileSync("src/tokens.css", "utf8");
    for (const theme of ["dark", "light"]) {
        const block = css.match(
            new RegExp(`\\.studio\\[data-theme="${theme}"\\]\\s*\\{([^}]+)`),
        )![1];
        const colors = Object.fromEntries(
            [...block.matchAll(/--studio-([\w-]+):\s*(#[0-9a-f]{6});/g)].map(
                (match) => [match[1], match[2]],
            ),
        );
        for (const [foreground, background] of [
            ["text", "surface0"],
            ["text", "surface1"],
            ["text", "surface2"],
            ["muted", "surface0"],
            ["muted", "surface1"],
            ["muted", "surface2"],
            ["text", "selection"],
            ["muted", "selection"],
            ["accent-text", "accent"],
            ["warning", "warning-fill"],
            ["danger", "danger-fill"],
            ["source", "source-fill"],
        ]) {
            it(`${theme}: ${foreground} on ${background} meets AA normal-text contrast`, () =>
                expect(
                    contrast(colors[foreground], colors[background]),
                ).toBeGreaterThanOrEqual(4.5));
        }
    }
});
