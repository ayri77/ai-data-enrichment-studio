// A register, a source of rows and what the server answers about adding rows to
// it, for the tests of the model and of the interface. Invented data only.
import type {
    Dataset,
    Preview,
    RowSource,
    RowsCount,
    RowsPlan,
    RowsPreview,
    RowsRun,
} from "./api";

export const mailSource: RowSource = {
    source_id: "mail_files",
    label: "E-mail files (.eml)",
    connect: "files",
    file_suffixes: [".eml", ".zip"],
    fields: [
        { field_id: "received", kind: "moment", label: "Received" },
        { field_id: "sender", kind: "text", label: "Sender" },
        { field_id: "subject", kind: "text", label: "Subject" },
    ],
    default_key: ["received", "sender"],
    listing: "given",
    available: true,
};

export const register: Dataset = {
    dataset_id: "register",
    filename: "register.xlsx",
    worksheets: [
        {
            name: "Register",
            columns: ["Received", "From", "Subject", "Amount"],
            row_count: 3,
            table: {
                first_row: 1,
                first_column: 1,
                last_row: 4,
                last_column: 4,
            },
        },
    ],
};

const column = (name: string, inferred_type: string) => ({
    name,
    inferred_type,
    null_count: 0,
    warnings: [],
});
const rows = [
    ["2026-10-30T16:30:00", "Streamly", "Subscription ended", 10],
    ["2026-11-03T14:21:00", "PayRelay", "Your receipt", 20],
    ["2026-11-11T07:30:00", "AppMarket", "Promo prices", 30],
];
export const registerPreview: Preview = {
    dataset_id: "register",
    sheet_name: "Register",
    range_ref: "A1:D4",
    sample_limit: 5,
    profile: {
        row_count: 3,
        columns: [
            column("Received", "DateTime"),
            column("From", "Text"),
            column("Subject", "Text"),
            column("Amount", "Integer"),
        ],
        sample_records: rows.map(([a, b, c, d]) => ({
            Received: a,
            From: b,
            Subject: c,
            Amount: d,
        })),
        schema_warnings: [],
    },
    rows: { row_numbers: [2, 3, 4], values: rows },
};

/** The register after the run: the three rows and seven new ones. */
export const grownPreview: Preview = {
    ...registerPreview,
    dataset_id: "grown",
    range_ref: "A1:D11",
    profile: { ...registerPreview.profile, row_count: 10 },
    rows: {
        row_numbers: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        values: [
            ...rows,
            ...[5, 6, 7, 8, 9, 10, 11].map((n) => [
                `2026-11-${10 + n}T10:00:00`,
                "Shop Example",
                `New mail ${n}`,
                null,
            ]),
        ],
    },
};

export const candidates = {
    candidates_id: "set-1",
    source_id: "mail_files",
    count: 10,
    skipped: 0,
};

export const count = (over: Partial<RowsCount> = {}): RowsCount => ({
    in_source: 10,
    left_out: 0,
    without_key: 0,
    duplicates_in_table: 3,
    duplicates_in_source: 0,
    added: 7,
    written: 7,
    table_rows_without_key: 0,
    holds: true,
    ...over,
});

export const plan = (over: Partial<RowsPlan> = {}): RowsPlan => ({
    source_id: "mail_files",
    sheet_name: "Register",
    range_ref: "A1:D4",
    mapping: { received: "A", sender: "B", subject: "C" },
    key: ["received", "sender"],
    on_duplicate: "skip",
    conditions: [],
    ...over,
});

export const answer = (over: Partial<RowsPreview> = {}): RowsPreview => ({
    plan: plan(),
    proposed: { mapping: true, key: true },
    count: count(),
    refused: null,
    ...over,
});

export const run = (over: Partial<RowsRun["rows"]> = {}): RowsRun => ({
    dataset_id: "grown",
    worksheets: [
        {
            name: "Register",
            columns: ["Received", "From", "Subject", "Amount"],
            row_count: 10,
            table: {
                first_row: 1,
                first_column: 1,
                last_row: 11,
                last_column: 4,
            },
        },
    ],
    rows: {
        count: count(),
        first_row: 5,
        last_row: 11,
        sheet_name: "Register",
        range_ref: "A1:D11",
        ...over,
    },
});
