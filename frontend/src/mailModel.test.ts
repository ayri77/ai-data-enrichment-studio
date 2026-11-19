import { describe, expect, it } from "vitest";
import { WorkspaceApiError } from "./api";
import {
    extractionDraft,
    extractionJob,
    extractionPlan,
    extractionReport,
    mailPreview,
    plainDataset,
} from "./extractionFixtures";
import {
    MAX_MAILS,
    blankColumn,
    buildExtractionRequest,
    columnProblem,
    extractionCounts,
    extractionProblems,
    isExtractionDraft,
    isExtractionRun,
    isMailTable,
    mailIdentities,
    oneLine,
    packMails,
    parseOptions,
    readBytes,
    readExtractionTask,
} from "./mailModel";

const eml = (name: string, text = "Subject: x\r\n\r\nbody") =>
    new File([text], name, { type: "message/rfc822" });
const col = (id: number, name: string, kind = "text", options = "") => ({
    ...blankColumn(id),
    name,
    kind: kind as never,
    options,
});

/** The names stored in a zip, read from its central directory. */
const entryNames = (bytes: Uint8Array) => {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
    const count = view.getUint16(bytes.length - 22 + 10, true);
    let at = view.getUint32(bytes.length - 22 + 16, true);
    const names: string[] = [];
    for (let i = 0; i < count; i++) {
        const n = view.getUint16(at + 28, true);
        const extra = view.getUint16(at + 30, true);
        const comment = view.getUint16(at + 32, true);
        names.push(String.fromCharCode(...bytes.slice(at + 46, at + 46 + n)));
        at += 46 + n + extra + comment;
    }
    return names;
};
const bytesOf = readBytes;

describe("packMails", () => {
    it("packs .eml files under neutral names, never the person's own", async () => {
        const blob = await packMails([
            eml("secret-bank.eml"),
            eml("Holiday plans.EML"),
        ]);
        const bytes = await bytesOf(blob);
        expect(entryNames(bytes)).toEqual(["0001.eml", "0002.eml"]);
        const text = new TextDecoder("latin1").decode(bytes);
        expect(text).not.toContain("secret");
        expect(text).not.toContain("Holiday");
    });

    it("sends a single .zip as it is", async () => {
        const zip = new File(["PK"], "mine.zip");
        expect(await packMails([zip])).toBe(zip);
    });

    it("refuses other types and mixed selections with the server's code", async () => {
        for (const files of [
            [],
            [new File(["x"], "a.txt")],
            [eml("a.eml"), new File(["x"], "b.zip")],
            [new File(["x"], "a.zip"), new File(["x"], "b.zip")],
        ])
            await expect(packMails(files)).rejects.toMatchObject({
                code: "invalid_mail_file_type",
            });
    });

    it("refuses more than 500 files before reading any", async () => {
        const files = Array.from({ length: MAX_MAILS + 1 }, (_, i) =>
            eml(`${i}.eml`),
        );
        const error = await packMails(files).catch((e) => e);
        expect(error).toBeInstanceOf(WorkspaceApiError);
        expect(error.code).toBe("mail_too_many");
    });

    it("refuses a mail over 10 MiB and an upload over 32 MiB", async () => {
        const big = (size: number, name = "a.eml") => {
            const file = eml(name);
            Object.defineProperty(file, "size", { value: size });
            return file;
        };
        await expect(
            packMails([big(10 * 1024 * 1024 + 1)]),
        ).rejects.toMatchObject({ code: "mail_entry_too_large" });
        await expect(
            packMails(
                [1, 2, 3, 4].map((i) => big(9 * 1024 * 1024, `${i}.eml`)),
            ),
        ).rejects.toMatchObject({ code: "mail_upload_too_large" });
        const zip = new File(["x"], "a.zip");
        Object.defineProperty(zip, "size", { value: 33 * 1024 * 1024 });
        await expect(packMails([zip])).rejects.toMatchObject({
            code: "mail_upload_too_large",
        });
    });
});

describe("the form", () => {
    it("splits options at commas and drops empty ones", () => {
        expect(parseOptions(" a, b ,, c ,")).toEqual(["a", "b", "c"]);
        expect(parseOptions("")).toEqual([]);
    });

    it("tells what is wrong with a column", () => {
        const long = col(1, "x".repeat(101));
        expect(columnProblem(col(1, "  "), [])).toBe("blank");
        expect(columnProblem(long, [long])).toBe("long");
        expect(columnProblem(col(1, "a\u0007b"), [])).toBe("control");
        expect(columnProblem(col(1, "text"), [])).toBe("existing");
        const a = col(1, "Amount");
        const b = col(2, "amount");
        expect(columnProblem(b, [a, b])).toBe("taken");
        expect(columnProblem(a, [a])).toBeNull();
    });

    it("checks the options of a choice", () => {
        const check = (options: string) => {
            const c = col(1, "Kind", "one_of", options);
            return columnProblem(c, [c]);
        };
        expect(check("one")).toBe("optionCount");
        expect(check("a, A")).toBe("optionsSame");
        expect(check(`a, ${"b".repeat(61)}`)).toBe("optionLong");
        expect(check("a, b\u0001")).toBe("optionControl");
        expect(
            check(Array.from({ length: 21 }, (_, i) => `o${i}`).join(",")),
        ).toBe("optionCount");
        expect(check("payment, renewal")).toBeNull();
    });

    it("finds what stops the form", () => {
        const ok = [col(1, "Amount", "number")];
        expect(extractionProblems("What was paid?", ok)).toEqual([]);
        expect(extractionProblems("  ", ok)).toEqual(["question"]);
        expect(extractionProblems("q".repeat(1001), ok)).toEqual(["question"]);
        expect(extractionProblems("q", [])).toEqual(["columns"]);
        expect(
            extractionProblems(
                "q",
                Array.from({ length: 21 }, (_, i) => col(i, `c${i}`)),
            ),
        ).toEqual(["columns"]);
    });

    it("builds the request: text column Text, notes, options only for a choice", () => {
        const body = buildExtractionRequest({
            preview: mailPreview,
            question: "  What was paid? ",
            columns: [
                col(1, " Amount ", "number", "ignored, here"),
                col(2, "Kind", "one_of", "payment, renewal"),
            ],
        });
        expect(body).toEqual({
            dataset_id: "mail-table",
            question: "What was paid?",
            text_column: "Text",
            targets: [
                { column: "Amount", kind: "number" },
                {
                    column: "Kind",
                    kind: "one_of",
                    options: ["payment", "renewal"],
                },
            ],
            sheet_name: "Mail",
            range_ref: "A1:D6",
            sources_in_workbook: "notes",
        });
        expect(
            buildExtractionRequest({
                preview: mailPreview,
                range: "A1:D3",
                question: "q",
                columns: [],
            }).range_ref,
        ).toBe("A1:D3");
    });
});

describe("what is a mail table", () => {
    it("is told by the server, not by column names", () => {
        expect(isMailTable(mailPreview)).toBe(true);
        expect(isMailTable({ ...mailPreview, mail: undefined })).toBe(false);
        expect(isMailTable(null)).toBe(false);
        expect(plainDataset.worksheets[0].name).toBe("Sheet1");
    });
    it("tells extraction drafts and runs", () => {
        expect(isExtractionDraft(extractionDraft)).toBe(true);
        expect(isExtractionDraft({ kind: "mixed" })).toBe(false);
        expect(isExtractionRun(extractionJob, extractionDraft)).toBe(true);
        expect(isExtractionRun({ kind: "mixed" }, { kind: "mixed" })).toBe(
            false,
        );
    });
});

describe("the plan, read back", () => {
    it("reads question and columns, with the letters of the draft", () => {
        const view = readExtractionTask(
            extractionPlan,
            extractionDraft.output_columns,
        );
        expect(view?.question).toMatch(/^Payments/);
        expect(view?.columns).toEqual([
            { header: "Amount", kind: "number", options: [], letter: "E" },
            { header: "Paid on", kind: "date", options: [], letter: "F" },
            {
                header: "Kind",
                kind: "one_of",
                options: ["payment", "renewal"],
                letter: "G",
            },
        ]);
    });
    it("gives null for anything else", () => {
        expect(readExtractionTask(null)).toBeNull();
        const step = extractionPlan.steps[0];
        const withTask = (value: string) => ({
            ...extractionPlan,
            steps: [{ ...step, inputs: { task: { kind: "literal", value } } }],
        });
        expect(readExtractionTask(withTask("not json"))).toBeNull();
        expect(readExtractionTask(withTask('{"question":1}'))).toBeNull();
        expect(
            readExtractionTask(
                withTask(
                    '{"question":"q","columns":[{"header":"a","kind":"nope"}]}',
                ),
            ),
        ).toBeNull();
    });
});

describe("the numbers of a run", () => {
    it("tell the mail that is not about the task from the rest, from the report", () => {
        expect(extractionCounts(extractionJob, extractionReport)).toEqual({
            total: 5,
            found: 2,
            notApplicable: 1,
            unread: 2,
            nothingFound: 2,
            failed: 0,
        });
    });
    it("fall back to the job until the report has arrived", () => {
        expect(extractionCounts(extractionJob, null)).toEqual({
            total: 5,
            found: 2,
            notApplicable: 1,
            unread: 2,
            nothingFound: null,
            failed: null,
        });
    });
});

describe("which mail a row is", () => {
    it("takes the day, sender and subject from the rows of the preview, by worksheet row", () => {
        const found = mailIdentities(mailPreview);
        expect(found.size).toBe(5);
        expect(found.get(2)).toEqual({
            Received: "2026-11-01",
            From: "Shop Example",
            Subject: "Receipt one",
        });
        expect(found.get(2)).not.toHaveProperty("Text");
        expect(found.get(7)).toBeUndefined();
    });
    it("holds nothing for another table or a preview without rows", () => {
        expect(mailIdentities({ ...mailPreview, mail: undefined }).size).toBe(
            0,
        );
        expect(mailIdentities({ ...mailPreview, rows: undefined }).size).toBe(
            0,
        );
        expect(mailIdentities(null).size).toBe(0);
    });
});

describe("the question on one line", () => {
    it("turns line breaks into one space, here and in the request", () => {
        expect(oneLine("What was paid,\r\n  and when?\n\nAnd more")).toBe(
            "What was paid, and when? And more",
        );
        expect(
            buildExtractionRequest({
                preview: mailPreview,
                question: " a\nb ",
                columns: [],
            }).question,
        ).toBe("a b");
        expect(extractionProblems("a\nb", [col(1, "Amount")])).toEqual([]);
    });
});
