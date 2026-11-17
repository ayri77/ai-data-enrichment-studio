import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { copyFile, mkdir } from "node:fs/promises";
import { basename } from "node:path";
import { spawnSync } from "node:child_process";

// A mixed plan, through the real backend with the stand-ins of
// tests/browser/fixture_server.py: the planner answers a task about the height
// of a mountain and its geological age with a plan for the height (it has a
// source) and names "age" as having none; the stand-in for the model answers
// the search for "age". No request leaves the machine.
const SWITCH_LABEL =
    "If the system has no source for the task or for some of its columns, search the web with AI for them";
const TASK =
    "Find the elevation in metres of each mountain and its geological age in millions of years.";

async function shot(page: Page, info: TestInfo, name: string) {
    const path = info.outputPath(name);
    await page.screenshot({ path });
    const directory = process.env.SHOTS_DIR;
    if (directory) {
        await mkdir(directory, { recursive: true });
        await copyFile(path, `${directory}/${basename(name)}`);
    }
}

// The mountains sample with one more empty column, "age": two columns to fill.
function mountainsWithAge(testInfo: TestInfo) {
    const source = testInfo.outputPath("mountains-age.xlsx");
    const made = spawnSync(
        "uv",
        [
            "run",
            "--locked",
            "python",
            "-c",
            `
import sys
from openpyxl import load_workbook
book = load_workbook('samples/mountains.xlsx')
book['Sheet1']['D1'] = 'age'
book.save(sys.argv[1])
`,
            source,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(made.status, made.stdout + made.stderr).toBe(0);
    return source;
}
async function openTable(page: Page, source: string) {
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await expect(page.getByLabel("Which column to fill?")).toBeVisible();
}
async function twoColumns(page: Page) {
    await page.getByLabel("Enrichment instruction").fill(TASK);
    await page.getByLabel("Which column to fill?").selectOption("C");
    await page.getByRole("button", { name: "Fill one more column" }).click();
    await page.getByLabel("One more column to fill").selectOption("D");
}
function recordRequests(page: Page) {
    const requests: Record<string, unknown>[] = [];
    page.on("request", (request) => {
        if (
            request.method() === "POST" &&
            new URL(request.url()).pathname === "/api/fallback-plans"
        )
            requests.push(request.postDataJSON());
    });
    return requests;
}
function cells(output: string, expression: string) {
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
sheet = load_workbook(sys.argv[1])['Sheet1']
${expression}
`,
            output,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);
}
async function download(page: Page, testInfo: TestInfo, name: string) {
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download enriched XLSX" }).click();
    const output = testInfo.outputPath(name);
    await (await waiting).saveAs(output);
    return output;
}

test("mixed plan: with the switch on, the plan gets a web search for the column without a source, and the results mark each column", async ({
    page,
}, testInfo) => {
    const source = mountainsWithAge(testInfo);
    const requests = recordRequests(page);
    await page.setViewportSize({ width: 1440, height: 1200 });
    await openTable(page, source);
    await twoColumns(page);
    // One switch, covering a task and single columns.
    const box = page.getByLabel(SWITCH_LABEL);
    await expect(box).not.toBeChecked();
    await box.check();
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner's plan stands and the search part is added at once.
    const notice = page.getByTestId("mixed-notice");
    await expect(notice).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({
        plan_id: expect.any(String),
        question: TASK,
        context_columns: ["Mountain", "Country"],
        targets: [{ column: "age", kind: "text" }],
        sources_in_workbook: "notes",
    });
    await expect(page.getByTestId("mixed-filled")).toContainText(
        "“height” (C)",
    );
    await expect(page.getByTestId("mixed-searched")).toContainText("“age” (D)");
    await expect(notice).toContainText("not verified");
    await expect(page.getByTestId("fallback-banner")).toContainText(
        "Some values (“age” (D)) are found by an AI web search and are not verified.",
    );
    // The picture has the search as a node of its own, marked; the list is in words.
    const diagram = page.getByTestId("plan-diagram");
    await expect(diagram.locator("button")).toHaveCount(3);
    await expect(diagram.getByTestId("node-ai-search")).toHaveText(
        "AI search, not verified",
    );
    const steps = page.locator(".plan-steps > li");
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(2)).toContainText("Search the web with AI");
    await expect(steps.nth(2)).toContainText("“age” (D): text");
    await expect(page.locator(".plan-words")).not.toContainText(
        /value_\d|ctx_\d|"question"/,
    );
    await expect(page.getByTestId("fallback-sent")).toContainText(
        "for every row: Mountain, Country.",
    );
    await expect(
        page.getByRole("button", { name: "Use only the system's sources" }),
    ).toBeVisible();
    await shot(page, testInfo, "mixed-plan.png");

    // The same view in Ukrainian.
    await page.locator(".studio-preferences select").nth(1).selectOption("uk");
    await expect(page.getByTestId("mixed-searched")).toContainText(
        "Для цих стовпців у системи немає джерела",
    );
    await shot(page, testInfo, "mixed-plan-uk.png");
    await page.locator(".studio-preferences select").nth(1).selectOption("en");

    // Approve and run. Only Everest has a record in the offline source, so the
    // run ends in review, but the search answered every row.
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("fallback-banner")).toContainText(
        "Some values (“age” (D))",
    );
    await expect(page.getByTestId("fallback-sources")).toContainText(
        "links are written next to it",
    );
    await expect(page.getByTestId("job-status")).toContainText(
        /Rows filled: \d+ of 10\./,
        { timeout: 90_000 },
    );
    // Not the panel of a run with nothing to choose: this is a review of names.
    await page.getByRole("button", { name: "Review issues" }).click();
    await expect(page.getByTestId("fallback-review")).toHaveCount(0);
    await page.getByRole("button", { name: "Continue to results" }).click();

    const summary = page.getByTestId("fallback-summary");
    await expect(summary).toContainText(
        "Rows asked: 10. Values found: 9, of them concluded by the model: 8.",
    );
    await expect(page.getByTestId("lookup-columns")).toContainText("“age” (D)");
    // Each column has its own kind of trust.
    const trust = page.getByTestId("result-provenance");
    await expect(trust).toHaveCount(2);
    await expect(trust.nth(0)).not.toHaveText(
        "Found by an AI web search, not verified",
    );
    await expect(trust.nth(1)).toHaveText(
        "Found by an AI web search, not verified",
    );
    const rows = page.getByTestId("row-outcome");
    await expect(rows).toHaveCount(10);
    // Everest: a height from the source and an age a page states.
    await expect(rows.nth(0)).toContainText("Mount Everest");
    await expect(rows.nth(0)).toContainText("about 60 million years");
    await expect(rows.nth(0).getByTestId("basis-mark")).toHaveText(
        "stated on the page",
    );
    await expect(rows.nth(0)).toHaveAttribute("data-concluded", "false");
    // K2: no mountain record was confirmed and no page could be named.
    await expect(rows.nth(1)).toContainText("K2");
    await expect(rows.nth(1).getByTestId("basis-mark")).toHaveCount(0);
    await expect(rows.nth(2).getByTestId("basis-mark")).toHaveText(
        "concluded by the model",
    );
    await page.getByRole("button", { name: "Concluded by AI (8)" }).click();
    await expect(rows).toHaveCount(8);
    await page.getByRole("button", { name: /^All \(10\)/ }).click();
    await shot(page, testInfo, "mixed-results.png");

    // The download: the height from the source, the age with its note.
    const output = await download(page, testInfo, "mountains-age-result.xlsx");
    cells(
        output,
        `
assert abs(sheet['C2'].value - 8849) < 5, sheet['C2'].value
assert sheet['D2'].value == 'about 60 million years', sheet['D2'].value
assert sheet['D3'].value is None, sheet['D3'].value
assert sheet['D4'].value == 'about 50 million years', sheet['D4'].value
assert sheet['C4'].value is None, sheet['C4'].value
note = sheet['D2'].comment
assert note is not None and 'example.org' in note.text, note
`,
    );
});

test("mixed plan: with the switch off, the partial plan offers the search, the mixed plan can be left for the system's sources, and the unfilled column stays out of the result", async ({
    page,
}, testInfo) => {
    const source = mountainsWithAge(testInfo);
    const requests = recordRequests(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openTable(page, source);
    await expect(page.getByLabel(SWITCH_LABEL)).not.toBeChecked();
    await twoColumns(page);
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner's plan for the height, and a notice for "age" with the offer.
    const notice = page.getByTestId("unfilled-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("“age” (D)");
    await expect(
        notice.getByRole("button", {
            name: "Search the web with AI for these columns",
        }),
    ).toBeVisible();
    await expect(
        notice.getByLabel("Do this automatically from now on"),
    ).not.toBeChecked();
    await expect(page.getByTestId("mixed-notice")).toHaveCount(0);
    await expect(
        page.getByRole("button", { name: "Approve and run" }),
    ).toBeEnabled();
    expect(requests).toHaveLength(0);
    await shot(page, testInfo, "mixed-partial-offer.png");

    // The button builds the mixed plan, with the same request the switch sends.
    await notice
        .getByRole("button", {
            name: "Search the web with AI for these columns",
        })
        .click();
    await expect(page.getByTestId("mixed-notice")).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
        question: TASK,
        context_columns: ["Mountain", "Country"],
        targets: [{ column: "age", kind: "text" }],
    });
    // Pressing it did not switch anything on.
    await expect(page.getByTestId("fallback-banner")).toBeVisible();

    // Back to the system's sources only: the partial plan, with the way to return.
    await page
        .getByRole("button", { name: "Use only the system's sources" })
        .click();
    await expect(page.getByTestId("unfilled-notice")).toBeVisible();
    await expect(page.getByTestId("mixed-notice")).toHaveCount(0);
    expect(requests).toHaveLength(1);

    // Approve and run it: the search is not part of it, and "age" stays as it was.
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toContainText(
        /Rows filled: \d+ of 10\./,
        { timeout: 90_000 },
    );
    await page.getByRole("button", { name: "Review issues" }).click();
    await page.getByRole("button", { name: "Continue to results" }).click();
    await expect(page.getByTestId("fallback-summary")).toHaveCount(0);
    await expect(page.getByTestId("unfilled-summary")).toContainText(
        "“age” (D)",
    );
    const headers = page.locator(".outcome-table thead th");
    await expect(headers.filter({ hasText: "height" })).toHaveCount(1);
    await expect(headers.filter({ hasText: "age" })).toHaveCount(0);
    const output = await download(page, testInfo, "mountains-partial.xlsx");
    cells(
        output,
        `
assert abs(sheet['C2'].value - 8849) < 5, sheet['C2'].value
assert sheet['D1'].value == 'age'
assert all(sheet.cell(row=row, column=4).value is None for row in range(2, 12))
`,
    );
    expect(requests).toHaveLength(1);
});
