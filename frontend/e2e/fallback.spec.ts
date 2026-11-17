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

const SWITCH_LABEL =
    "If the system has no source for the task or for some of its columns, search the web with AI for them";

// Two more columns to fill, both new (the stand-in answers for three).
async function threeColumns(page: Page, task: string) {
    await page.getByLabel("Enrichment instruction").fill(task);
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
}
async function openTable(page: Page, source: string) {
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
}
function recordFallbackRequests(page: Page) {
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

test("universal search: with the switch on, a task the planner refuses gets a search plan at once, and the values come with their basis", async ({
    page,
}, testInfo) => {
    const source = peaksWorkbook(testInfo);
    const fallbackRequests = recordFallbackRequests(page);
    const planRequests: unknown[] = [];
    page.on("request", (request) => {
        if (
            request.method() === "POST" &&
            new URL(request.url()).pathname === "/api/plans"
        )
            planRequests.push(request.postDataJSON());
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    // The server has the search switched on.
    await page.goto("/");
    const limits = await (await page.request.get("/api/limits")).json();
    expect(limits.web_lookup).toBe(true);
    await expect(
        page.getByText("this server can search the web with AI instead", {
            exact: false,
        }),
    ).toBeVisible();
    await openTable(page, source);
    await threeColumns(
        page,
        "Find the height, the first ascent and whether each mountain was climbed in winter.",
    );

    // One switch, off to begin with, under the task and the columns to fill.
    const box = page.getByLabel(SWITCH_LABEL);
    await expect(box).not.toBeChecked();
    await expect(page.getByTestId("fallback-switch")).toContainText(
        "Values found this way are not verified.",
    );
    await box.check();
    await box.scrollIntoViewIfNeeded();
    await shot(page, testInfo, "fallback-switch.png");
    // It is remembered in this browser.
    await openTable(page, source);
    await expect(page.getByLabel(SWITCH_LABEL)).toBeChecked();
    await threeColumns(
        page,
        "Find the height, the first ascent and whether each mountain was climbed in winter.",
    );
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner refuses, and nothing more is pressed: the search plan is the plan.
    const plan = page.getByTestId("fallback-plan");
    await expect(plan).toBeVisible();
    expect(planRequests).toHaveLength(1);
    expect(fallbackRequests).toHaveLength(1);
    expect(fallbackRequests[0]).toEqual({
        dataset_id: expect.any(String),
        sheet_name: "Sheet1",
        range_ref: "A1:C4",
        question:
            "Find the height, the first ascent and whether each mountain was climbed in winter.",
        context_columns: ["Mountain", "Country"],
        targets: [
            { column: "Height", kind: "text" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "text" },
        ],
        output_columns: {
            Height: "C",
            "First ascent": "D",
            "Climbed in winter": "E",
        },
        existing_outputs: "keep",
        sources_in_workbook: "notes",
    });
    const notice = page.getByTestId("fallback-auto-notice");
    await expect(notice).toContainText(
        "The planner had no way to do this task from the system's sources.",
    );
    await expect(notice).toContainText(
        "The first ascent of a mountain is not in the sources it can read.",
    );
    await expect(notice).toContainText(
        "Because you switched on the web search",
    );
    await expect(page.getByTestId("fallback-banner")).toContainText(
        "Values will be found by an AI web search and are not verified.",
    );
    await expect(plan).toContainText("“Height” (C): text");
    await expect(page.getByTestId("fallback-sent")).toContainText(
        "The values of these columns are sent to OpenAI for every row: Mountain, Country.",
    );
    await expect(page.getByTestId("fallback-offer")).toHaveCount(0);
    await expect(page.getByTestId("plan-refusal")).toBeHidden();
    await expect(
        page.getByRole("button", { name: "Approve and run" }),
    ).toBeEnabled();
    await shot(page, testInfo, "fallback-auto-plan.png");

    // The same view in Ukrainian.
    await page.locator(".studio-preferences select").nth(1).selectOption("uk");
    await expect(page.getByTestId("fallback-auto-notice")).toContainText(
        "Оскільки ви ввімкнули пошук в інтернеті",
    );
    await shot(page, testInfo, "fallback-auto-plan-uk.png");
    await page.locator(".studio-preferences select").nth(1).selectOption("en");

    // What the planner answered can be looked at again.
    await page.getByText("Show what the planner answered").click();
    await expect(page.getByTestId("plan-refusal")).toBeVisible();
    await page.getByText("Show what the planner answered").click();

    // Change what is sent and the kinds: the form has this plan's choices.
    await page
        .getByRole("button", {
            name: "Change what is sent and the kinds of values",
        })
        .click();
    const form = page.getByTestId("fallback-form");
    await expect(form.getByLabel("Question for each row")).toHaveValue(
        /first ascent/,
    );
    const context = page.getByTestId("fallback-context");
    await expect(
        context.getByRole("checkbox", { name: /^Mountain/ }),
    ).toBeChecked();
    await expect(
        context.getByRole("checkbox", { name: /^Country/ }),
    ).toBeChecked();
    // The columns to fill cannot identify a row.
    await expect(
        context.getByRole("checkbox", { name: /^Height/ }),
    ).toBeDisabled();
    const targets = page.getByTestId("fallback-targets");
    await targets.getByLabel("Kind of value in Height").selectOption("number");
    await targets.getByLabel("Unit for Height (optional)").fill("m");
    await targets
        .getByLabel("Kind of value in Climbed in winter")
        .selectOption("yes_no");
    await expect(form.getByLabel("Sources in the result workbook")).toHaveValue(
        "notes",
    );
    await expect(page.getByTestId("fallback-notice")).toContainText(
        "At most 3 rows are searched in one run",
    );
    await page
        .getByTestId("fallback-change")
        .evaluate((node) => node.scrollIntoView({ block: "start" }));
    await shot(page, testInfo, "fallback-change.png");
    await form.locator("button[type=submit]").click();
    await expect(page.getByTestId("fallback-form")).toHaveCount(0);
    await expect(plan).toContainText("“Height” (C): a number in m");
    await expect(plan).toContainText("“Climbed in winter” (E): yes or no");
    await expect(plan).not.toContainText(/value_\d|ctx_\d/);
    expect(planRequests).toHaveLength(1);
    expect(fallbackRequests).toHaveLength(2);
    expect(fallbackRequests[1]).toMatchObject({
        context_columns: ["Mountain", "Country"],
        targets: [
            { column: "Height", kind: "number", unit: "m" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
        sources_in_workbook: "notes",
    });
    // The planner's answer and the notice outlive the rebuilt plan.
    await expect(page.getByTestId("fallback-auto-notice")).toContainText(
        "The planner had no way",
    );

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

test("universal search: not used where the planner built a plan, even with the switch on", async ({
    page,
}) => {
    const fallbackRequests = recordFallbackRequests(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles("../samples/mountains.xlsx");
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
    await page
        .getByLabel("Enrichment instruction")
        .fill("Fill height with mountain elevations in metres.");
    await page.getByLabel(SWITCH_LABEL).check();
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.getByTestId("validation")).toBeVisible();
    await expect(page.getByTestId("fallback-offer")).toHaveCount(0);
    // Even with the switch on, a plan the planner built is the plan.
    await expect(page.getByTestId("fallback-auto-notice")).toHaveCount(0);
    await expect(page.getByTestId("fallback-banner")).toHaveCount(0);
    expect(fallbackRequests).toHaveLength(0);
    await navigate(page, "Plan");
});

test("universal search: with the switch off, the refusal gets one short block", async ({
    page,
}, testInfo) => {
    const source = peaksWorkbook(testInfo);
    const fallbackRequests = recordFallbackRequests(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await openTable(page, source);
    await expect(page.getByLabel(SWITCH_LABEL)).not.toBeChecked();
    await threeColumns(
        page,
        "Find the height, the first ascent and whether each mountain was climbed in winter.",
    );
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.getByTestId("plan-refusal")).toBeVisible();
    const offer = page.getByTestId("fallback-offer");
    await expect(offer).toBeVisible();
    await expect(offer.getByRole("button")).toHaveCount(1);
    await expect(
        offer.getByRole("button", { name: "Search the web with AI instead" }),
    ).toBeVisible();
    await expect(
        offer.getByLabel("Do this automatically from now on"),
    ).not.toBeChecked();
    await expect(page.getByTestId("fallback-form")).toHaveCount(0);
    expect(fallbackRequests).toHaveLength(0);
    await shot(page, testInfo, "fallback-offer-short.png");
});

test("universal search: with the switch off, a plan that fails the check gets one button, and the plan can be changed", async ({
    page,
}, testInfo) => {
    const source = peaksWorkbook(testInfo);
    const fallbackRequests = recordFallbackRequests(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openTable(page, source);
    await threeColumns(
        page,
        "Find the phone characteristics of each mountain.",
    );
    await page.getByRole("button", { name: "Build Plan" }).click();

    // The planner said "ready" with a plan that has no steps; the check refuses
    // it, the messages stay, and below them is the short block.
    const invalid = page.getByTestId("plan-invalid");
    await expect(invalid).toBeVisible();
    await expect(invalid).toContainText("This plan cannot be run");
    await expect(invalid.locator("details")).toBeVisible();
    await expect(page.getByTestId("plan-refusal")).toHaveCount(0);
    const offer = page.getByTestId("fallback-offer");
    await expect(offer).toBeVisible();
    await expect(page.getByTestId("fallback-lead")).toContainText(
        "building the plan again or rephrasing the task is the better way",
    );
    await expect(offer.getByRole("button")).toHaveCount(1);
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
    expect(fallbackRequests).toHaveLength(0);

    // One button: the plan is built with the defaults and shown as the plan.
    await offer
        .getByRole("button", { name: "Search the web with AI instead" })
        .click();
    const plan = page.getByTestId("fallback-plan");
    await expect(plan).toBeVisible();
    expect(fallbackRequests).toHaveLength(1);
    await expect(page.getByTestId("fallback-auto-notice")).toContainText(
        "The planner's plan did not pass the check",
    );
    await expect(page.getByTestId("fallback-auto-notice")).toContainText(
        "You chose to search the web with AI",
    );
    await expect(plan).toContainText(
        "The row is identified by: Mountain, Country",
    );
    await expect(plan).toContainText("“Climbed in winter” (E): text");
    await shot(page, testInfo, "fallback-offer-invalid.png");

    // Change a kind to Yes/No and take a column out of what is sent.
    await page
        .getByRole("button", {
            name: "Change what is sent and the kinds of values",
        })
        .click();
    const form = page.getByTestId("fallback-form");
    await expect(form.getByLabel("Question for each row")).toHaveValue(
        /phone characteristics/,
    );
    await page
        .getByTestId("fallback-targets")
        .getByLabel("Kind of value in Climbed in winter")
        .selectOption("yes_no");
    await page
        .getByTestId("fallback-context")
        .getByRole("checkbox", { name: /^Country/ })
        .uncheck();
    await form.locator("button[type=submit]").click();
    await expect(page.getByTestId("fallback-form")).toHaveCount(0);
    expect(fallbackRequests).toHaveLength(2);
    expect(fallbackRequests[1]).toMatchObject({
        context_columns: ["Mountain"],
        targets: [
            { column: "Height", kind: "text" },
            { column: "First ascent", kind: "text" },
            { column: "Climbed in winter", kind: "yes_no" },
        ],
    });
    await expect(plan).toContainText("The row is identified by: Mountain");
    await expect(plan).not.toContainText("Mountain, Country");
    await expect(plan).toContainText("“Climbed in winter” (E): yes or no");
    await expect(page.getByTestId("fallback-sent")).toContainText(
        "for every row: Mountain.",
    );
    await expect(page.getByTestId("fallback-auto-notice")).toContainText(
        "The planner's plan did not pass the check",
    );

    // Nothing was run: the plan is still waiting for the person's approval.
    await expect(
        page.getByRole("button", { name: "Approve and run" }),
    ).toBeEnabled();
    await expect(page.getByTestId("job-status")).toHaveCount(0);
});
