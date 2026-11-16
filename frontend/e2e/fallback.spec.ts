import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { copyFile, mkdir } from "node:fs/promises";
import { basename } from "node:path";
import { spawnSync } from "node:child_process";

// The universal search, through the real backend with the stand-in for the
// model client (tests/browser/fixture_server.py): no request leaves the
// machine. The screenshots are kept with the test; with SHOTS_DIR set they are
// copied there as well.
async function shot(page: Page, info: TestInfo, name: string) {
    const path = info.outputPath(name);
    await page.screenshot({ path });
    const directory = process.env.SHOTS_DIR;
    if (directory) {
        await mkdir(directory, { recursive: true });
        await copyFile(path, `${directory}/${basename(name)}`);
    }
}
async function navigate(page: Page, name: string) {
    await page
        .getByRole("navigation")
        .getByRole("button", { name, exact: true })
        .click();
}

// Three mountains: the stand-in finds everything for Everest (one value
// concluded), no height for K2, and nothing at all for "Nowhere".
function peaksWorkbook(testInfo: TestInfo) {
    const source = testInfo.outputPath("peaks.xlsx");
    const made = spawnSync(
        "uv",
        [
            "run",
            "--locked",
            "python",
            "-c",
            `
import sys
from openpyxl import Workbook
book = Workbook()
sheet = book.active
sheet.title = 'Sheet1'
sheet.append(['Mountain', 'Country', 'Height'])
sheet.append(['Everest', 'Nepal', None])
sheet.append(['K2', 'Pakistan', None])
sheet.append(['Nowhere', 'Nowhere', None])
book.save(sys.argv[1])
`,
            source,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(made.status, made.stdout + made.stderr).toBe(0);
    return source;
}

test("universal search: the planner refuses, the person sets up the search, and the values come with their basis", async ({
    page,
}, testInfo) => {
    const source = peaksWorkbook(testInfo);

    const fallbackRequests: unknown[] = [];
    const planRequests: unknown[] = [];
    page.on("request", (request) => {
        const path = new URL(request.url()).pathname;
        if (request.method() !== "POST") return;
        if (path === "/api/fallback-plans")
            fallbackRequests.push(request.postDataJSON());
        if (path === "/api/plans") planRequests.push(request.postDataJSON());
    });
    await page.setViewportSize({ width: 1440, height: 1500 });
    await page.goto("/");
    // The server has the search switched on.
    const limits = await (await page.request.get("/api/limits")).json();
    expect(limits.web_lookup).toBe(true);
    await expect(
        page.getByText("this server can search the web with AI instead", {
            exact: false,
        }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
    await page
        .getByLabel("Enrichment instruction")
        .fill(
            "Find the height, the first ascent and whether each mountain was climbed in winter.",
        );
    // Two more columns to fill, both new.
    await page.getByRole("button", { name: "Fill one more column" }).click();
    await page
        .getByLabel("One more column to fill")
        .selectOption({ label: "A new column…" });
    await page.getByLabel("Name of the new column").fill("First ascent");
    await page.getByRole("button", { name: "Fill one more column" }).click();
    await page
        .getByLabel("One more column to fill")
        .nth(1)
        .selectOption({ label: "A new column…" });
    await page
        .getByLabel("Name of the new column")
        .nth(1)
        .fill("Climbed in winter");
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner refuses; the view says why, and offers the search below.
    const refusal = page.getByTestId("plan-refusal");
    await expect(refusal).toBeVisible();
    const offer = page.getByTestId("fallback-offer");
    await expect(offer).toBeVisible();
    await expect(
        offer.getByRole("heading", { name: "Search the web with AI instead" }),
    ).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });
    await shot(page, testInfo, "fallback-offer.png");
    await page.setViewportSize({ width: 1440, height: 1500 });

    // The form: nothing is chosen for the person.
    await offer.getByRole("button", { name: "Set up the search" }).click();
    const form = page.getByTestId("fallback-form");
    await expect(form.getByLabel("Question for each row")).toHaveValue(
        /first ascent/,
    );
    const context = page.getByTestId("fallback-context");
    for (const box of await context.getByRole("checkbox").all())
        await expect(box).not.toBeChecked();
    // The columns to fill cannot identify a row.
    await expect(
        context.getByRole("checkbox", { name: /^Height/ }),
    ).toBeDisabled();
    // A new column is not in the table, so it is not offered at all.
    await expect(context.getByRole("checkbox")).toHaveCount(3);
    const build = form.locator("button[type=submit]");
    await expect(build).toBeDisabled();
    await context.getByRole("checkbox", { name: /^Mountain/ }).check();
    await context.getByRole("checkbox", { name: /^Country/ }).check();
    await expect(build).toBeEnabled();
    const targets = page.getByTestId("fallback-targets");
    // Height holds nothing yet, so it starts as text; it is a number in metres.
    await targets.getByLabel("Kind of value in Height").selectOption("number");
    await targets.getByLabel("Unit for Height (optional)").fill("m");
    await targets
        .getByLabel("Kind of value in Climbed in winter")
        .selectOption("yes_no");
    await expect(
        targets.getByLabel("Kind of value in First ascent"),
    ).toHaveValue("text");
    await expect(form.getByLabel("Sources in the result workbook")).toHaveValue(
        "notes",
    );
    const notice = page.getByTestId("fallback-notice");
    await expect(notice).toContainText("one request to an OpenAI model");
    await expect(notice).toContainText(
        "At most 3 rows are searched in one run",
    );
    await build.scrollIntoViewIfNeeded();
    await shot(page, testInfo, "fallback-form.png");

    // The same form in Ukrainian.
    await page.locator(".studio-preferences select").nth(1).selectOption("uk");
    await expect(
        offer.getByRole("heading", {
            name: "Натомість шукати в інтернеті за допомогою ШІ",
        }),
    ).toBeVisible();
    await build.scrollIntoViewIfNeeded();
    await shot(page, testInfo, "fallback-form-uk.png");
    await page.locator(".studio-preferences select").nth(1).selectOption("en");

    // Build the plan: the request has the table, the range and the columns of the
    // failed request, and the plan replaces the refusal on the same view.
    await build.click();
    const plan = page.getByTestId("fallback-plan");
    await expect(plan).toBeVisible();
    expect(planRequests).toHaveLength(1);
    expect(fallbackRequests).toHaveLength(1);
    expect(fallbackRequests[0]).toMatchObject({
        sheet_name: "Sheet1",
        range_ref: "A1:C4",
        question:
            "Find the height, the first ascent and whether each mountain was climbed in winter.",
        context_columns: ["Mountain", "Country"],
        targets: [
            { column: "Height", kind: "number", unit: "m" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
        output_columns: {
            Height: "C",
            "First ascent": "D",
            "Climbed in winter": "E",
        },
        existing_outputs: "keep",
        sources_in_workbook: "notes",
    });
    await expect(page.getByTestId("fallback-offer")).toHaveCount(0);
    await expect(plan).toContainText("Search the web with AI (not verified)");
    await expect(plan).toContainText("“Height” (C): a number in m");
    await expect(plan).toContainText("“Climbed in winter” (E): yes or no");
    await expect(plan).not.toContainText(/value_\d|ctx_\d/);
    await expect(page.getByTestId("fallback-banner")).toContainText(
        "Values will be found by an AI web search and are not verified.",
    );
    await shot(page, testInfo, "fallback-plan.png");

    // Approve and run; the run says what the notes are for.
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("fallback-sources")).toContainText(
        "links are written next to it",
    );
    // One row has no value that a page backs, so the run ends in review.
    await expect(page.getByTestId("job-status")).toContainText(
        /Rows filled: \d+ of 3\./,
        { timeout: 60_000 },
    );
    await page.getByRole("button", { name: "Review issues" }).click();
    const nothing = page.getByTestId("fallback-review");
    await expect(nothing).toContainText("There is nothing to choose here");
    await shot(page, testInfo, "fallback-review-nothing.png");
    await nothing.getByRole("button", { name: "Continue to results" }).click();

    // The results: every value with its basis; one concluded.
    await expect(page.getByTestId("fallback-summary")).toContainText(
        "Rows asked: 3. Values found: 5, of them concluded by the model: 1.",
    );
    const rows = page.getByTestId("row-outcome");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).getByTestId("basis-mark")).toHaveText([
        "stated on the page",
        "concluded by the model",
        "stated on the page",
    ]);
    await expect(rows.nth(0)).toContainText("8,849 m");
    await expect(rows.nth(0)).toHaveAttribute("data-concluded", "true");
    await expect(rows.nth(1)).toContainText(
        "No page could be named for “Height”",
    );
    await expect(rows.nth(2)).toContainText(
        "No page could be named for any value of this row",
    );
    await expect(page.getByTestId("result-provenance").first()).toHaveText(
        "Found by an AI web search, not verified",
    );
    await page.getByRole("button", { name: "Concluded by AI (1)" }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Everest");
    await page.getByRole("button", { name: /^All \(3\)/ }).click();
    await shot(page, testInfo, "fallback-results.png");

    // The download carries the values and a note with the links on each cell.
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download enriched XLSX" }).click();
    const output = testInfo.outputPath("peaks-result.xlsx");
    await (await download).saveAs(output);
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
assert sheet['C2'].value == 8849, sheet['C2'].value
assert sheet['D2'].value == '1953', sheet['D2'].value
assert sheet['E2'].value == 'No', sheet['E2'].value
assert sheet['C3'].value is None
assert sheet['D3'].value == 'Italian expedition', sheet['D3'].value
assert sheet['C4'].value is None and sheet['D4'].value is None
note = sheet['D2'].comment
assert note is not None and 'example.org' in note.text, note
`,
            output,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);
    // Nothing was sent anywhere but to the stand-in: the planner was asked once.
    expect(planRequests).toHaveLength(1);
});

test("universal search: not offered where the planner built a plan", async ({
    page,
}) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles("../samples/mountains.xlsx");
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
    await page
        .getByLabel("Enrichment instruction")
        .fill("Fill height with mountain elevations in metres.");
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.getByTestId("validation")).toBeVisible();
    await expect(page.getByTestId("fallback-offer")).toHaveCount(0);
    await expect(page.getByTestId("fallback-banner")).toHaveCount(0);
    await navigate(page, "Plan");
});

test("universal search: offered also when the planner's plan fails the check", async ({
    page,
}, testInfo) => {
    const source = peaksWorkbook(testInfo);
    const fallbackRequests: unknown[] = [];
    page.on("request", (request) => {
        if (
            request.method() === "POST" &&
            new URL(request.url()).pathname === "/api/fallback-plans"
        )
            fallbackRequests.push(request.postDataJSON());
    });
    await page.setViewportSize({ width: 1440, height: 1500 });
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
    await page
        .getByLabel("Enrichment instruction")
        .fill("Find the phone characteristics of each mountain.");
    // Two more columns to fill, both new (the stand-in answers for three).
    await page.getByRole("button", { name: "Fill one more column" }).click();
    await page
        .getByLabel("One more column to fill")
        .selectOption({ label: "A new column…" });
    await page.getByLabel("Name of the new column").fill("First ascent");
    await page.getByRole("button", { name: "Fill one more column" }).click();
    await page
        .getByLabel("One more column to fill")
        .nth(1)
        .selectOption({ label: "A new column…" });
    await page
        .getByLabel("Name of the new column")
        .nth(1)
        .fill("Climbed in winter");
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner said "ready" with a plan that has no steps; the check refuses
    // it, the messages stay, and the search is offered below them.
    const invalid = page.getByTestId("plan-invalid");
    await expect(invalid).toBeVisible();
    await expect(invalid).toContainText("This plan cannot be run");
    await expect(invalid.locator("details")).toBeVisible();
    await expect(page.getByTestId("plan-refusal")).toHaveCount(0);
    const offer = page.getByTestId("fallback-offer");
    await expect(offer).toBeVisible();
    await expect(page.getByTestId("fallback-lead")).toContainText(
        "The planner did not produce a plan that can run.",
    );
    expect(
        await invalid.evaluate(
            (node, other) =>
                !!(
                    node.compareDocumentPosition(other as Node) &
                    Node.DOCUMENT_POSITION_FOLLOWING
                ),
            await offer.elementHandle(),
        ),
    ).toBe(true);
    await offer.getByRole("button", { name: "Set up the search" }).click();
    const form = page.getByTestId("fallback-form");
    await expect(form.getByLabel("Question for each row")).toHaveValue(
        /phone characteristics/,
    );
    const context = page.getByTestId("fallback-context");
    for (const box of await context.getByRole("checkbox").all())
        await expect(box).not.toBeChecked();
    await page.setViewportSize({ width: 1440, height: 1100 });
    await shot(page, testInfo, "fallback-after-invalid.png");
    await page.setViewportSize({ width: 1440, height: 1500 });

    await context.getByRole("checkbox", { name: /^Mountain/ }).check();
    await context.getByRole("checkbox", { name: /^Country/ }).check();
    const targets = page.getByTestId("fallback-targets");
    await targets.getByLabel("Kind of value in Height").selectOption("number");
    await targets.getByLabel("Unit for Height (optional)").fill("m");
    await targets
        .getByLabel("Kind of value in Climbed in winter")
        .selectOption("yes_no");
    await form.locator("button[type=submit]").click();
    await expect(page.getByTestId("fallback-plan")).toBeVisible();
    expect(fallbackRequests).toHaveLength(1);
    expect(fallbackRequests[0]).toMatchObject({
        sheet_name: "Sheet1",
        range_ref: "A1:C4",
        context_columns: ["Mountain", "Country"],
        targets: [
            { column: "Height", kind: "number", unit: "m" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
        output_columns: {
            Height: "C",
            "First ascent": "D",
            "Climbed in winter": "E",
        },
    });
    await expect(page.getByTestId("plan-invalid")).toHaveCount(0);
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toContainText(
        /Rows filled: \d+ of 3\./,
        { timeout: 60_000 },
    );
    const review = page.getByRole("button", { name: "Review issues" });
    if (await review.isVisible()) {
        await review.click();
        await page
            .getByTestId("fallback-review")
            .getByRole("button", { name: "Continue to results" })
            .click();
    }
    await expect(page.getByTestId("fallback-summary")).toBeVisible();
});
