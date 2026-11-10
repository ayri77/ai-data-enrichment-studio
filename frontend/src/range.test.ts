import { describe, expect, it } from "vitest";
import { columnLetter, columnNumber, parseRange } from "./range";

describe("A1 syntax boundary", () => {
    it.each([
        ["A1:E11", "A1:E11"],
        [" $b$2:$d$14 ", "B2:D14"],
        ["A1", "A1:A1"],
        ["AA20:AZ99", "AA20:AZ99"],
        ["XFD1048576", "XFD1048576:XFD1048576"],
    ])("normalizes %s", (input, canonical) =>
        expect(parseRange(input).canonical).toBe(canonical),
    );
    it.each([
        "",
        "0",
        "A0:B1",
        "A1:",
        "Sheet1!A1:B4",
        "A:A",
        "1:10",
        "A1:B2:C3",
        "A1.5:B4",
        "A1:B0",
        "A 1:B2",
    ])("rejects malformed syntax %s", (input) =>
        expect(parseRange(input).error).toBe("rangeSyntax"),
    );
    it.each(["B1:A2", "A4:B2"])("rejects reversed bounds %s", (input) =>
        expect(parseRange(input).error).toBe("rangeOrder"),
    );
    it.each(["XFE1:XFE2", "A1:A1048577"])(
        "enforces Excel syntax bounds %s",
        (input) => expect(parseRange(input).error).toBe("excelLimits"),
    );
    it("does not pretend to validate table content or application resource limits", () => {
        expect(parseRange("A2:E11").error).toBeUndefined();
        expect(parseRange("A1:A5002").error).toBeUndefined();
    });
    it.each([1, 26, 27, 100, 16384])("roundtrips Excel column %s", (n) =>
        expect(columnNumber(columnLetter(n))).toBe(n),
    );
});
