import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { copyFile, mkdir, readdir } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

// New rows from a source, through the real backend of tests/browser/fixture_server.py
// (`mail_app`, port 8012: mail is switched on, so the source is offered). The ten mails are
// invented (samples/probe/mail); the registers are made by scripts/make_rows_register.py and
// hold three of them. No model is asked and no request leaves the machine.
const SAMPLES = resolve("..", "samples", "probe");
const MAIL_DIRECTORY = join(SAMPLES, "mail");
const REGISTER = join(SAMPLES, "rows-register.xlsx");
const TEXT_DATES = join(SAMPLES, "rows-register-text-dates.xlsx");

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
async function openRegister(page: Page, file: string) {
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(file);
    await expect(page.getByTestId("row-count")).toContainText("3");
}
async function chooseTenMails(page: Page) {
    await page.getByLabel("Add new rows from a source").check();
    await page.getByLabel("Files of the source").setInputFiles(await mails());
    await expect(page.getByTestId("rows-read")).toContainText(
        "Read: 10. Skipped (not readable): 0.",
    );
}

test("rows: from ten mails into a register that holds three of them", async ({
    page,
}, testInfo) => {
    const requests: { path: string; query: string; body: unknown }[] = [];
    page.on("request", (request) => {
        if (request.method() === "POST")
            requests.push({
                path: new URL(request.url()).pathname,
                query: new URL(request.url()).search,
                body: request.postData(),
            });
    });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await openRegister(page, REGISTER);

    // The first step: two parts, the first one on, the second one off.
    await expect(page.getByRole("heading", { name: "Rows" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Columns" })).toBeVisible();
    await expect(
        page.getByLabel("Fill the rows that are in the table"),
    ).toBeChecked();
    await expect(
        page.getByLabel("Add new rows from a source"),
    ).not.toBeChecked();
    await expect(page.getByTestId("rows-source")).toHaveCount(0);

    // Both parts: no plan yet, and the page says what to do.
    await page.getByLabel("Add new rows from a source").check();
    await expect(page.getByTestId("rows-both")).toContainText(
        "add the rows first, then continue with the result",
    );
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeDisabled();

    // Only the second part.
    await page.getByLabel("Fill the rows that are in the table").uncheck();
    await expect(page.getByTestId("rows-both")).toHaveCount(0);
    await expect(page.getByTestId("rows-no-columns")).toContainText(
        "No column is filled in this task.",
    );
    await expect(page.getByTestId("rows-privacy")).toContainText(
        "No model is asked",
    );
    await expect(page.getByTestId("rows-fields")).toContainText("Sender");
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeDisabled();

    // Ten files; their names are not sent.
    await page.getByLabel("Files of the source").setInputFiles(await mails());
    await expect(page.getByTestId("rows-read")).toContainText(
        "Read: 10. Skipped (not readable): 0.",
    );
    const upload = requests.find((r) =>
        r.path.startsWith("/api/row-sources/mail_files/candidates"),
    );
    expect(upload?.query).toBe("?filename=mails.zip");
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeEnabled();

    // A condition is a row with its own controls; the screenshot shows one.
    await page.getByRole("button", { name: "Add a condition" }).click();
    await expect(page.getByLabel("Value of condition 1")).toHaveValue("30");
    await page
        .getByLabel("Condition 1", { exact: true })
        .selectOption("on_or_after");
    await expect(page.getByLabel("Value of condition 1")).toHaveAttribute(
        "type",
        "date",
    );
    await page.getByLabel("Value of condition 1").fill("2026-11-01");
    await shot(page, testInfo, "rows-dataset.png");
    await page.getByRole("button", { name: "Remove condition 1" }).click();
    await expect(page.getByTestId("rows-condition")).toHaveCount(0);

    // The plan: what the server proposed, and the counts.
    await page.getByRole("button", { name: "Build Plan" }).click();
    const card = page.getByTestId("rows-plan-rows");
    await expect(card).toBeVisible();
    const preview = requests.find((r) => r.path.endsWith("/rows/preview"));
    expect(JSON.parse(preview!.body as string)).toMatchObject({
        sheet_name: "Register",
        range_ref: "A1:D4",
        mapping: null,
        key: null,
        on_duplicate: "skip",
        conditions: [],
    });
    await expect(card).toContainText("everything in the source");
    await expect(page.getByLabel("Column for Received")).toHaveValue("A");
    await expect(card.getByTestId("proposed").first()).toBeVisible();
    await expect(page.getByTestId("rows-counts")).toContainText(
        "Of 10 in the source: 3 already in the table, 7 will be added.",
    );
    await expect(page.getByTestId("rows-no-match")).toHaveCount(0);
    await expect(page.getByTestId("rows-plan-columns")).toContainText(
        "No column is filled in this task.",
    );
    await expect(page.getByTestId("rows-plan-check")).toContainText(
        "A second run on the same files adds no row.",
    );
    await shot(page, testInfo, "rows-plan.png");

    // A change asks again with the explicit plan.
    const before = requests.length;
    await page.getByLabel("Column for Subject").selectOption("");
    await expect(page.getByTestId("rows-counts")).toContainText(
        "7 will be added",
    );
    const again = requests
        .slice(before)
        .find((r) => r.path.endsWith("/rows/preview"));
    expect(JSON.parse(again!.body as string).mapping).toEqual({
        received: "A",
        sender: "B",
    });
    await page.getByLabel("Column for Subject").selectOption("C");
    await expect(page.getByTestId("rows-counts")).toContainText(
        "7 will be added",
    );

    // Run.
    await page.getByRole("button", { name: "Approve and run" }).click();
    const tiles = page.getByTestId("rows-tiles");
    await expect(tiles).toBeVisible();
    await expect(tiles.locator("[data-tile=in_source] strong")).toHaveText(
        "10",
    );
    await expect(tiles.locator("[data-tile=added] strong")).toHaveText("7");
    await expect(tiles.locator("[data-tile=duplicates] strong")).toHaveText(
        "3",
    );
    await expect(tiles.locator("li")).toHaveCount(3);
    await expect(page.getByTestId("rows-sum")).toHaveText(
        "7 added + 3 duplicates = 10 in the source",
    );
    await expect(page.getByTestId("rows-check")).toContainText(
        "The check holds",
    );
    await expect(page.getByTestId("rows-kept")).toContainText(
        "compared cell by cell: 3. None has changed.",
    );
    await expect(page.getByTestId("rows-second")).toContainText(
        "a second run on the same files would add 0 rows",
        { ignoreCase: true },
    );
    await expect(page.getByTestId("rows-end-row").last()).toHaveAttribute(
        "data-added",
        "true",
    );
    await expect(page.locator("[data-added=true]")).toHaveCount(7);
    // Execution and Review are not steps of this task.
    await expect(
        page.getByRole("button", { name: "Execution" }),
    ).toBeDisabled();
    await expect(page.getByRole("button", { name: "Review" })).toBeDisabled();
    await shot(page, testInfo, "rows-results.png");

    // The workbook: the three rows of the register, then the seven new ones.
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download workbook" }).click();
    const download = await waiting;
    expect(download.suggestedFilename()).toBe("rows-register-rows-added.xlsx");
    const output = testInfo.outputPath("rows-result.xlsx");
    await download.saveAs(output);
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
sheet = load_workbook(sys.argv[1])["Register"]
assert [c.value for c in sheet[1]] == ['Received', 'From', 'Subject', 'Amount']
assert sheet.max_row == 11, sheet.max_row
assert [sheet.cell(r, 4).value for r in range(2, 5)] == [10, 20, 30]
assert all(sheet.cell(r, 4).value is None for r in range(5, 12))
assert all(sheet.cell(r, c).value for r in range(5, 12) for c in (1, 2, 3))
`,
            output,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);

    // Continue with the new table: ten rows, a fresh task.
    await page
        .getByRole("button", { name: "Continue with this table" })
        .click();
    await expect(page.getByTestId("row-count")).toContainText("10");
    await expect(page.locator(".upload-description strong")).toHaveText(
        "rows-register-rows-added.xlsx",
    );
    await expect(
        page.getByLabel("Fill the rows that are in the table"),
    ).toBeChecked();
    await expect(
        page.getByLabel("Add new rows from a source"),
    ).not.toBeChecked();
});

test("rows: a register whose dates are text is refused, with the row", async ({
    page,
}, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await openRegister(page, TEXT_DATES);
    await page.getByLabel("Fill the rows that are in the table").uncheck();
    await chooseTenMails(page);
    await page.getByRole("button", { name: "Build Plan" }).click();
    const alert = page.getByTestId("rows-plan-rows").getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toHaveAttribute(
        "data-error-code",
        "rows_key_not_comparable",
    );
    await expect(alert).toContainText(
        "A date in a key column is not a date cell",
    );
    await expect(alert).toContainText("Cells concerned: 3. First row: 2.");
    await expect(
        page.getByRole("button", { name: "Approve and run" }),
    ).toBeDisabled();
    await expect(page.getByTestId("rows-cannot-run")).toContainText(
        "the plan is refused",
        { ignoreCase: true },
    );
    await shot(page, testInfo, "rows-plan-refused.png");
});
