import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { copyFile, mkdir, readdir } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

// A table built from e-mails, through the real backend with the stand-ins of
// tests/browser/fixture_server.py (`mail_app`, port 8012): mail is switched on,
// and the model that reads values out of texts is a stand-in made inside the
// run worker. The ten mails are invented (samples/probe/mail). No request
// leaves the machine and no real model is asked.
const MAIL_DIRECTORY = resolve("..", "samples", "probe", "mail");
const QUESTION =
    "Payments: what was paid, to whom and when, and does the subscription renew? Advertisements and newsletters are not about it.";

async function shot(page: Page, info: TestInfo, name: string) {
    const path = info.outputPath(name);
    await page.screenshot({ path });
    const directory = process.env.SHOTS_DIR;
    if (directory) {
        await mkdir(directory, { recursive: true });
        await copyFile(path, `${directory}/${basename(name)}`);
    }
}
async function mails() {
    const names = (await readdir(MAIL_DIRECTORY))
        .filter((name) => name.endsWith(".eml"))
        .sort();
    expect(names).toHaveLength(10);
    return names.map((name) => join(MAIL_DIRECTORY, name));
}
// The Row, E-mail and Result columns are fully inside the visible part of the table,
// without scrolling sideways, and the first mail's outcome text is not cut.
async function expectOutcomeInView(page: Page) {
    const region = await page.locator(".outcome-table").boundingBox();
    expect(region).not.toBeNull();
    for (const name of ["Row", "E-mail", "Result"]) {
        const box = await page
            .locator(".outcome-table thead th", { hasText: name })
            .first()
            .boundingBox();
        expect(box, name).not.toBeNull();
        expect(box!.x, name).toBeGreaterThanOrEqual(region!.x);
        expect(box!.x + box!.width, name).toBeLessThanOrEqual(
            region!.x + region!.width + 1,
        );
    }
    const note = await page
        .locator(".outcome-table tbody tr:first-child .result-note")
        .boundingBox();
    expect(note!.x + note!.width).toBeLessThanOrEqual(
        region!.x + region!.width + 1,
    );
}
async function fillEditor(page: Page) {
    await page.getByLabel("What are these mails asked about?").fill(QUESTION);
    await page.getByLabel("Name of column 1").fill("Amount");
    await page.getByLabel("Kind of column 1").selectOption("number");
    await page.getByRole("button", { name: "Add a column" }).click();
    await page.getByLabel("Name of column 2").fill("Paid on");
    await page.getByLabel("Kind of column 2").selectOption("date");
    await page.getByRole("button", { name: "Add a column" }).click();
    await page.getByLabel("Name of column 3").fill("Renews");
    await page.getByLabel("Kind of column 3").selectOption("yes_no");
}

async function addTextColumns(page: Page, names: string[]) {
    for (const [i, name] of names.entries()) {
        const n = i + 4;
        await page.getByRole("button", { name: "Add a column" }).click();
        await page.getByLabel(`Name of column ${n}`).fill(name);
    }
}

test("e-mails: from the files to the workbook, with a mail that is not about the task", async ({
    page,
}, testInfo) => {
    const requests: { path: string; query: string; body: unknown }[] = [];
    page.on("request", (request) => {
        if (request.method() === "POST")
            requests.push({
                path: new URL(request.url()).pathname,
                query: new URL(request.url()).search,
                body: request.postDataBuffer(),
            });
    });
    // The browser does not hand over the bytes of a Blob it uploads, so the page
    // keeps a copy of what it sends to the mail route, for the check below.
    await page.addInitScript(() => {
        const send = window.fetch.bind(window);
        window.fetch = async (input, init) => {
            if (String(input).includes("/api/mail-datasets") && init?.body) {
                const bytes = new Uint8Array(
                    await new Response(init.body as BodyInit).arrayBuffer(),
                );
                (window as unknown as Record<string, unknown>).__mailUpload =
                    Array.from(bytes, (b) => String.fromCharCode(b)).join("");
            }
            return send(input, init);
        };
    });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();

    // The first step: the second way in, with what happens to the files.
    const entry = page.getByTestId("mail-import");
    await expect(entry).toBeVisible();
    await expect(entry).toContainText("Build a table from e-mails");
    await expect(page.getByTestId("mail-privacy")).toContainText(
        "Nothing is sent to an AI model at this point",
    );
    await shot(page, testInfo, "mail-first-step.png");

    // Ten files, through the file dialog's input.
    await page.getByLabel("Choose e-mail files").setInputFiles(await mails());
    const counts = page.getByTestId("mail-counts");
    await expect(counts).toBeVisible();
    await expect(counts.locator("[data-count=read]")).toContainText("10");
    await expect(counts.locator("[data-count=skipped]")).toContainText("0");
    await expect(counts.locator("[data-count=masked]")).toContainText(
        "card numbers 3, phone numbers 1, your own address 1",
    );
    // What was sent: one zip, a name for the file that is not the person's,
    // neutral names inside, none of the ten file names.
    const upload = requests.find((r) => r.path === "/api/mail-datasets");
    expect(upload?.query).toBe("?filename=mails.zip");
    const sent = await page.evaluate(
        () => (window as unknown as { __mailUpload: string }).__mailUpload,
    );
    expect(sent).toContain("0001.eml");
    expect(sent).toContain("0010.eml");
    expect(sent).not.toContain("cloudbox_rechnung");
    // The table.
    const table = page.locator(".table-scroll");
    await expect(table).toContainText("Received");
    await expect(table).toContainText("Subject");
    await expect(table).toContainText("Text");
    await expect(table).toContainText("Ihre Rechnung von Cloudbox");
    await shot(page, testInfo, "mail-imported.png");

    // The task step is the editor, with nothing of the planner.
    await expect(page.getByTestId("extraction-form")).toBeVisible();
    await expect(page.getByLabel("Enrichment instruction")).toHaveCount(0);
    await expect(page.getByTestId("fallback-planner")).toHaveCount(0);
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeDisabled();
    await fillEditor(page);
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeEnabled();
    await expect(page.getByTestId("extraction-notice")).toContainText(
        "the text of every e-mail in the run is sent to OpenAI",
    );
    // A line break in the question is turned into a space.
    await page
        .getByLabel("What are these mails asked about?")
        .fill(QUESTION.replace(" to whom", "\nto whom"));
    await expect(
        page.getByLabel("What are these mails asked about?"),
    ).toHaveValue(QUESTION);
    await shot(page, testInfo, "mail-editor.png");

    // A column that is one of a list: one option is not enough, and says so.
    await page.getByRole("button", { name: "Add a column" }).click();
    await page.getByLabel("Name of column 4").fill("Kind");
    await page.getByLabel("Kind of column 4").selectOption("one_of");
    await page
        .getByLabel("Options of column 4, with commas between them")
        .fill("payment");
    const problem = page.locator('[data-problem="optionCount"]');
    await expect(problem).toBeVisible();
    // The panel scrolls: the whole message can be brought into view.
    await problem.scrollIntoViewIfNeeded();
    const within = async () => {
        const message = (await problem.boundingBox())!;
        const bottom = (await page
            .getByRole("button", { name: "Build Plan" })
            .boundingBox())!;
        return message.y + message.height <= bottom.y;
    };
    expect(await within()).toBe(true);
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeDisabled();
    await shot(page, testInfo, "mail-editor-choice.png");
    await page
        .getByLabel("Options of column 4, with commas between them")
        .fill("payment, renewal");
    await expect(page.locator("[data-problem]")).toHaveCount(0);
    await page.getByRole("button", { name: "Remove column 4" }).click();

    // The same editor in Ukrainian.
    await page.locator(".studio-preferences select").nth(1).selectOption("uk");
    await expect(page.getByTestId("extraction-form")).toBeVisible();
    await shot(page, testInfo, "mail-editor-uk.png");
    await page.locator(".studio-preferences select").nth(1).selectOption("en");
    // What was typed is still there.
    await expect(page.getByLabel("Name of column 2")).toHaveValue("Paid on");

    // The plan.
    await page.getByRole("button", { name: "Build Plan" }).click();
    const plan = page.getByTestId("extraction-plan");
    await expect(plan).toBeVisible();
    const built = requests.find((r) => r.path === "/api/extraction-plans");
    expect(JSON.parse((built?.body as Buffer).toString("utf8"))).toMatchObject({
        question: QUESTION,
        text_column: "Text",
        targets: [
            { column: "Amount", kind: "number" },
            { column: "Paid on", kind: "date" },
            { column: "Renews", kind: "yes_no" },
        ],
        sources_in_workbook: "notes",
    });
    await expect(plan).toContainText("“Amount” (E): a number");
    await expect(plan).toContainText("“Paid on” (F): a date");
    await expect(page.getByTestId("extraction-banner")).toContainText(
        "The text of every e-mail in this run is sent to OpenAI",
    );
    await expect(page.locator(".plan-words")).not.toContainText(
        /value_\d|"question"|timeout/,
    );
    await shot(page, testInfo, "mail-plan.png");

    // Run: the time limit is left to the server.
    const starting = page.waitForRequest(
        (r) => r.method() === "POST" && r.url().endsWith("/runs"),
    );
    await page.getByRole("button", { name: "Approve and run" }).click();
    const started = await starting;
    expect(started.postDataJSON()).not.toHaveProperty("timeout_seconds");
    await expect(page.getByTestId("job-status")).toContainText(
        /Mails with values: 4 of 10/,
        { timeout: 90_000 },
    );
    await expect(page.getByTestId("extraction-notes")).toBeVisible();
    await expect(page.locator("main")).toContainText(
        "Not about the task: 2. Nothing is written for them, and they are not failures.",
    );
    await shot(page, testInfo, "mail-execution.png");

    // Results.
    await page.getByRole("navigation").getByRole("button").last().click();
    const summary = page.getByTestId("extraction-summary");
    await expect(summary).toContainText(
        "Mails asked: 10. With values: 4. Not about the task: 2.",
    );
    const rows = page.getByTestId("row-outcome");
    await expect(rows).toHaveCount(10);
    // The text of a mail is not in the results; only the marker and the quotes are.
    await expect(page.locator("main")).not.toContainText("Hallo Sam");
    await expect(page.locator("[data-not-applicable=true]")).toHaveCount(2);
    await expect(
        page.locator("[data-not-applicable=true]").first(),
    ).toContainText("not about the task");
    const found = rows.filter({ hasText: "Gesamt: 10,00 EUR" });
    await expect(found).toHaveCount(1);
    // A row says which mail it is: the day, the sender and the subject, not the text.
    await expect(found).toContainText("Cloudbox Billing");
    await expect(found).toContainText("Ihre Rechnung von Cloudbox");
    await expect(found).toContainText("2026-11-01");
    // One column for the mail, and the outcome right after it.
    await expect(page.locator(".outcome-table thead th").nth(1)).toHaveText(
        "E-mail",
    );
    await expect(page.locator(".outcome-table thead th").nth(2)).toContainText(
        "Result",
    );
    await expect(
        page.locator(".outcome-table thead th", { hasText: /^From/ }),
    ).toHaveCount(0);
    await expect(rows.first()).not.toContainText("[text not kept");
    await expect(found.getByTestId("basis-mark").first()).toBeVisible();
    await expect(page.getByTestId("extraction-download-note")).toContainText(
        "the column “Text” is empty",
    );
    await expectOutcomeInView(page);
    // Tall enough that a found row and the mails that are not about the task are both in view.
    await page.setViewportSize({ width: 1440, height: 1900 });
    await shot(page, testInfo, "mail-results.png");
    await page.setViewportSize({ width: 1440, height: 1100 });

    // The workbook: the column of texts is empty, the quotes are notes.
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download enriched XLSX" }).click();
    const output = testInfo.outputPath("mail-result.xlsx");
    await (await waiting).saveAs(output);
    const check = spawnSync(
        "uv",
        [
            "run",
            "--locked",
            "python",
            "-c",
            `
import sys
from openpyxl import load_workbook
sheet = load_workbook(sys.argv[1]).active
head = [c.value for c in sheet[1]]
assert head == ['Received', 'From', 'Subject', 'Text', 'Amount', 'Paid on', 'Renews'], head
assert all(sheet.cell(r, 4).value in (None, '') for r in range(2, 12)), 'text is kept'
by_subject = {sheet.cell(r, 3).value: r for r in range(2, 12)}
r = by_subject['Ihre Rechnung von Cloudbox – November 2026']
assert sheet.cell(r, 5).value == 10, sheet.cell(r, 5).value
assert 'Gesamt: 10,00 EUR' in sheet.cell(r, 5).comment.text, sheet.cell(r, 5).comment
for subject in ('Black Friday: Notes App Pro for $14.99', 'New this week on Streamly'):
    row = by_subject[subject]
    assert all(sheet.cell(row, c).value is None for c in (5, 6, 7)), subject
`,
            output,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);
});

test("e-mails: with eight columns the mail and its outcome stay in view", async ({
    page,
}, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1900 });
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Choose e-mail files").setInputFiles(await mails());
    await expect(page.getByTestId("mail-counts")).toBeVisible();
    await fillEditor(page);
    await addTextColumns(page, [
        "Service",
        "Period",
        "Reference",
        "VAT",
        "Seller",
    ]);
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.getByTestId("extraction-plan")).toBeVisible();
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toContainText(
        /Mails with values: 4 of 10/,
        { timeout: 90_000 },
    );
    await page.getByRole("navigation").getByRole("button").last().click();
    await expect(page.getByTestId("row-outcome")).toHaveCount(10);
    await expect(page.locator(".outcome-table thead th")).toHaveCount(
        2 + 1 + 8,
    );
    await expectOutcomeInView(page);
    await expect(
        page.getByTestId("row-outcome").filter({ hasText: "CB-2026-110417" }),
    ).toHaveCount(1);
    await shot(page, testInfo, "mail-results-wide.png");
});

test("e-mails: a server with mail switched off shows no trace of it", async ({
    page,
}) => {
    // The server of the other scenarios (port 8011) has mail off, as a hosted one does.
    const mailRequests: string[] = [];
    page.on("request", (request) => {
        if (/^\/api\/.*(mail|extraction)/.test(new URL(request.url()).pathname))
            mailRequests.push(request.url());
    });
    await page.goto("http://127.0.0.1:5174/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await expect(page.getByLabel("Upload Excel workbook")).toBeVisible();
    await expect(page.getByTestId("mail-import")).toHaveCount(0);
    await expect(page.getByLabel("Choose e-mail files")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(/e-mail/i);
    const limits = await page.request.get("http://127.0.0.1:5174/api/limits");
    expect((await limits.json()).mail).toBe(false);
    expect(mailRequests).toEqual([]);
});
