import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

// The full-interface scenarios run in Advanced. The example scenario stays in
// Basic, the mode a visitor starts in.
async function advanced(page: Page) {
    await page.addInitScript(() =>
        localStorage.setItem("enrichment.mode", "advanced"),
    );
}
async function navigate(page: Page, name: string) {
    await page
        .getByRole("navigation")
        .getByRole("button", { name, exact: true })
        .click();
}
/**
 * The other languages have longer labels: the same screen must still fit, no
 * button may cut its text off, and no footer label may wrap.
 */
async function languagesFit(page: Page, info: TestInfo, label: string) {
    await page.setViewportSize({ width: 1366, height: 768 });
    const language = page.locator(".studio-preferences select").nth(1);
    // The tallest button of the footer: a label that wraps makes it taller
    // than one line of text with its padding.
    const footer = () =>
        page.evaluate(() =>
            Math.max(
                0,
                ...[
                    ...document.querySelectorAll(".workspace-actions button"),
                ].map((b) => b.getBoundingClientRect().height),
            ),
        );
    expect(await footer(), "en").toBeLessThanOrEqual(40);
    for (const locale of ["uk", "de"]) {
        await language.selectOption(locale);
        expect(await footer(), locale).toBeLessThanOrEqual(40);
        const fit = await page.evaluate(() => ({
            width: document.documentElement.scrollWidth,
            viewportWidth: innerWidth,
            clipped: [...document.querySelectorAll("button")]
                .filter(
                    (b) =>
                        b.getClientRects().length > 0 &&
                        b.scrollWidth > b.clientWidth + 1,
                )
                .map((b) => b.textContent),
        }));
        expect(fit.width, locale).toBeLessThanOrEqual(fit.viewportWidth);
        expect(fit.clipped, locale).toEqual([]);
        for (const action of await page.locator(".workspace-actions").all())
            await expect(action).toBeInViewport();
        await page.screenshot({
            path: info.outputPath(`${label}-${locale}.png`),
        });
    }
    await language.selectOption("en");
    await page.setViewportSize({ width: 1440, height: 900 });
}
async function screenshotWorkspace(
    page: Page,
    info: TestInfo,
    name: string,
    workspace: string,
) {
    for (const [width, height] of [
        [1366, 768],
        [1440, 900],
        [3440, 1440],
    ]) {
        await page.setViewportSize({ width, height });
        const bounds = await page.evaluate(() => ({
            width: document.documentElement.scrollWidth,
            height: document.documentElement.scrollHeight,
            viewportWidth: innerWidth,
            viewportHeight: innerHeight,
        }));
        expect(bounds.width).toBeLessThanOrEqual(bounds.viewportWidth);
        expect(bounds.height).toBeLessThanOrEqual(bounds.viewportHeight + 1);
        for (const action of await page.locator(".workspace-actions").all())
            await expect(action).toBeInViewport();
        await page.screenshot({
            path: info.outputPath(
                `${name}-${workspace}-${width}x${height}.png`,
            ),
        });
    }
    await languagesFit(page, info, `${name}-${workspace}`);
    await page.setViewportSize({ width: 1440, height: 900 });
}
for (const name of ["capitals", "mountains"]) {
    test(`${name}: browser → actual API → verified XLSX and audit (offline injected responses)`, async ({
        page,
    }, testInfo) => {
        const sourcePath = resolve(`../samples/${name}.xlsx`);
        const original = await readFile(sourcePath);
        const sourceHash = createHash("sha256").update(original).digest("hex");
        const requests: { path: string; body: unknown }[] = [];
        page.on("request", (request) => {
            if (
                request.method() === "POST" &&
                new URL(request.url()).pathname !== "/api/datasets"
            )
                requests.push({
                    path: new URL(request.url()).pathname,
                    body: request.postDataJSON(),
                });
        });
        await advanced(page);
        await page.goto("/");
        // The page opens on the overview; the work begins one click later.
        await page.getByRole("button", { name: "Start with my table" }).click();
        await page
            .getByLabel("Upload Excel workbook")
            .setInputFiles(sourcePath);
        await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
        await page.getByRole("button", { name: "Preview selection" }).click();
        await page
            .getByLabel("Enrichment instruction")
            .fill(
                name === "capitals"
                    ? "Calculate distances in km between the literal city pairs, including Istanbul; target distance."
                    : "Fill height with mountain elevations in metres; leave unresolved values empty.",
            );
        await page
            .getByLabel("Output column 1", { exact: true })
            .selectOption(name === "capitals" ? "E" : "C");
        await screenshotWorkspace(page, testInfo, name, "dataset");
        await page
            .getByLabel(
                "Allow external network requests for planning and execution",
            )
            .check();
        const createdPromise = page.waitForResponse(
            (r) =>
                r.url().endsWith("/api/plans") &&
                r.request().method() === "POST",
        );
        await page
            .getByRole("button", {
                name: "Build Plan",
                exact: true,
            })
            .click();
        const draft = await (await createdPromise).json();
        expect(draft.status).toBe("draft");
        expect(draft.source_sha256).toBe(sourceHash);
        expect(draft.range_ref).toBe(name === "capitals" ? "A1:E11" : "A1:C11");
        expect(draft.output_columns).toEqual(
            name === "capitals" ? { distance: "E" } : { height: "C" },
        );
        await screenshotWorkspace(page, testInfo, name, "plan");
        await page
            .getByRole("button", { name: "Diagnostics", exact: true })
            .click();
        await expect(page.getByTestId("fingerprint")).toHaveText(
            draft.fingerprint,
        );
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await navigate(page, "Execution");
        await expect(
            page.getByRole("button", { name: "Start bounded run" }),
        ).toBeDisabled();
        expect(requests.some((r) => r.path.endsWith("/approve"))).toBe(false);
        const unapproved = await page.request.post(
            `/api/plans/${draft.plan_id}/runs`,
            { data: { allow_network: true } },
        );
        expect(unapproved.status()).toBe(409);
        const mismatch = await page.request.post(
            `/api/plans/${draft.plan_id}/approve`,
            { data: { approved: true, fingerprint: "0".repeat(64) } },
        );
        expect((await mismatch.json()).error.code).toBe("approval_mismatch");
        await navigate(page, "Plan");
        await page.getByRole("button", { name: "Validate again" }).click();
        await expect(page.getByTestId("validation")).toHaveText(
            "Validation passed",
        );
        await expect(
            page.getByRole("button", {
                name: "Approve and continue",
            }),
        ).toBeEnabled();
        await page
            .getByRole("button", { name: "Approve and continue" })
            .click();
        await expect(
            page.getByRole("button", { name: "Start bounded run" }),
        ).toBeEnabled();
        expect(requests.find((r) => r.path.endsWith("/approve"))?.body).toEqual(
            { approved: true, fingerprint: draft.fingerprint },
        );
        const runPromise = page.waitForResponse(
            (r) => r.url().endsWith("/runs") && r.request().method() === "POST",
        );
        let releaseStatus!: () => void;
        const statusGate = new Promise<void>((done) => {
            releaseStatus = done;
        });
        await page.route(/\/api\/jobs\/[^/]+$/, async (route) => {
            const response = await route.fetch();
            await statusGate;
            await route.fulfill({ response });
        });
        await page.getByRole("button", { name: "Start bounded run" }).click();
        const queued = await (await runPromise).json();
        expect(queued.status).toBe("queued");
        await navigate(page, "Dataset");
        await expect(page.getByLabel("Upload Excel workbook")).toBeDisabled();
        await expect(page.getByLabel("Enrichment instruction")).toBeDisabled();
        await navigate(page, "Review");
        releaseStatus();
        await navigate(page, "Execution");
        await expect(page.getByTestId("job-status")).toHaveText(
            "Needs review — unresolved records remain",
            { timeout: 30_000 },
        );
        await expect(page.getByTestId("metric-total_rows")).toHaveText("10");
        await expect(page.getByTestId("metric-successful_rows")).toHaveText(
            "1",
        );
        await expect(
            page.getByText("Actual provider request counters"),
        ).toHaveCount(0);
        await screenshotWorkspace(page, testInfo, name, "execution");
        await page.getByRole("button", { name: "Review issues" }).click();
        if (name === "capitals") {
            // Kyiv is ambiguous in the offline responses: one card for two rows.
            await expect(page.getByTestId("review-Kyiv")).toContainText(
                "Rows: 2, 6",
            );
            await expect(
                page.getByRole("button", { name: "Fill in remaining rows" }),
            ).toBeDisabled();
            await expect(page.getByTestId("unresolved-Berlin")).toBeAttached();
        } else {
            await expect(
                page.getByText("There is nothing to choose in this run.", {
                    exact: false,
                }),
            ).toBeVisible();
            await expect(
                page.getByTestId("unresolved-Annapurna"),
            ).toBeAttached();
            // No candidates, but a name can be searched under another one;
            // until one is given there is nothing to fill.
            await expect(
                page
                    .getByTestId("unresolved-Annapurna")
                    .getByText("Search under another name"),
            ).toBeVisible();
            await expect(
                page.getByRole("button", { name: "Fill in remaining rows" }),
            ).toBeDisabled();
        }
        await screenshotWorkspace(page, testInfo, name, "review");
        await page.getByRole("button", { name: "Continue to results" }).click();
        await expect(page.getByTestId("row-outcome")).toHaveCount(10);
        // Rows show their source values and a readable result, not record IDs.
        await expect(page.getByTestId("row-outcome").nth(1)).toContainText(
            name === "capitals"
                ? /London.*Rome.*1,435\.48\skm/
                : /K2.*did not find “K2”/,
        );
        await expect(page.getByTestId("result-stats")).toContainText(
            name === "capitals" ? "filled 1 of 10" : "max 8,848.86",
        );
        await page.getByRole("button", { name: "Empty (9)" }).click();
        await expect(page.getByTestId("row-outcome")).toHaveCount(9);
        await page.getByRole("button", { name: "All (10)" }).click();
        await expect(page.getByTestId("row-outcome")).toHaveCount(10);
        await screenshotWorkspace(page, testInfo, name, "export");
        await page
            .getByLabel("Workbook filename")
            .fill(`${name}-submission.xlsx`);
        await expect(
            page.getByText("Offline fixture execution", { exact: false }),
        ).toBeVisible();
        const xlsxDownload = page.waitForEvent("download");
        await page
            .getByRole("button", { name: "Download enriched XLSX" })
            .click();
        const workbook = testInfo.outputPath(`${name}-result.xlsx`);
        const downloaded = await xlsxDownload;
        expect(downloaded.suggestedFilename()).toBe(`${name}-submission.xlsx`);
        await downloaded.saveAs(workbook);
        const reportDownload = page.waitForEvent("download");
        await page.getByRole("button", { name: "Download JSON audit" }).click();
        const reportPath = testInfo.outputPath(`${name}-report.json`);
        await (await reportDownload).saveAs(reportPath);
        const audit = JSON.parse(await readFile(reportPath, "utf8"));
        expect(audit.status).toBe("needs_review");
        expect(audit.execution_mode).toBe("offline_fixture");
        expect(audit.metrics.total_rows).toBe(10);
        expect(audit.metrics.successful_rows).toBe(1);
        expect(audit.approved_plan.fingerprint).toBe(draft.fingerprint);
        expect(audit.approved_plan.approved_at).toBeTruthy();
        expect(audit.source_unchanged).toBe(true);
        expect(audit.source_sha256).toBe(sourceHash);
        expect(audit.result_sha256).toBe(
            createHash("sha256")
                .update(await readFile(workbook))
                .digest("hex"),
        );
        expect(await readFile(sourcePath)).toEqual(original);
        // Validate the actual browser-downloaded workbook independently with openpyxl.
        const check = spawnSync(
            "uv",
            [
                "run",
                "--locked",
                "python",
                "tests/browser/check_download.py",
                name,
                workbook,
            ],
            { cwd: "..", encoding: "utf8" },
        );
        expect(check.status, check.stdout + check.stderr).toBe(0);
        await navigate(page, "Execution");
        for (const [locale, status] of [
            ["uk", "Потребує перевірки"],
            ["de", "Prüfung erforderlich"],
            ["en", "Needs review"],
        ]) {
            await page
                .locator(".studio-preferences select")
                .nth(1)
                .selectOption(locale);
            await expect(page.getByTestId("job-status")).toContainText(status);
        }
        await page
            .locator(".studio-preferences select")
            .nth(0)
            .selectOption("light");
        await page.screenshot({
            path: testInfo.outputPath(`${name}-results.png`),
            fullPage: true,
        });
        await writeFile(
            testInfo.outputPath("requests.json"),
            JSON.stringify(requests, null, 2),
        );
        // Editing the binding invalidates downstream artifacts and approval.
        await navigate(page, "Dataset");
        await page.getByLabel("Enrichment instruction").fill("Changed binding");
        await navigate(page, "Plan");
        await expect(page.getByTestId("fingerprint")).toHaveCount(0);
        await navigate(page, "Results");
        await expect(
            page.getByRole("button", { name: "Download enriched XLSX" }),
        ).toBeDisabled();
    });
}

test("a table longer than one run accepts is shown whole, and the run takes its first rows", async ({
    page,
}, testInfo) => {
    // Sixty data rows: three times what one run accepts.
    const source = testInfo.outputPath("long.xlsx");
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
book = load_workbook('samples/capitals.xlsx')
sheet = book['Sheet1']
rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2, max_row=11)]
for index in range(50):
    sheet.append(rows[index % 10])
book.save(sys.argv[1])
`,
            source,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(made.status, made.stdout + made.stderr).toBe(0);

    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await page.getByRole("button", { name: "Preview selection" }).click();
    // The whole table is there to look through …
    await expect(page.getByTestId("row-count")).toHaveText("60 rows");
    await expect(page.locator(".source-reference")).toContainText("A1:E61");
    await page
        .getByRole("searchbox", { name: "Filter rows by text" })
        .fill("kyiv");
    await expect(page.getByTestId("row-count")).toHaveText(
        /^1[0-9] of 60 rows match$/,
    );
    await page.getByRole("searchbox", { name: "Filter rows by text" }).fill("");
    await page.getByRole("combobox", { name: "Show" }).selectOption("all");
    const body = page.locator(".table-scroll tbody");
    await expect(body.getByRole("row")).toHaveCount(60);
    // … and the run says which of its rows it takes.
    const run = page.getByTestId("run-rows");
    await expect(run).toContainText(
        "The first 20 of 60 rows will be filled (worksheet rows 2–21).",
    );
    await expect(run).toContainText("One run fills at most 20 rows.");
    await expect(body.getByRole("row").nth(19)).not.toHaveAttribute(
        "data-out-of-run",
    );
    await expect(body.getByRole("row").nth(20)).toHaveAttribute(
        "data-out-of-run",
        "true",
    );
    await page.screenshot({ path: testInfo.outputPath("long-table.png") });
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate distances in km between the city pairs.");
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    await run.getByLabel("Rows to fill, from the top of the table").fill("3");
    const planned = page.waitForResponse(
        (r) =>
            r.url().endsWith("/api/plans") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Build Plan", exact: true }).click();
    const draft = await (await planned).json();
    // The plan is made for the run's rows, not for the table that is shown.
    expect(draft.range_ref).toBe("A1:E4");
    await page.getByRole("button", { name: "Approve and continue" }).click();
    await page.getByRole("button", { name: "Start bounded run" }).click();
    await expect(page.getByTestId("metric-total_rows")).toHaveText("3", {
        timeout: 30_000,
    });
});

test("dataset: the whole table can be looked through, filtered and sorted", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await page.getByRole("button", { name: "Preview selection" }).click();
    const body = page.locator(".table-scroll tbody");
    // Every row of the selection, numbered as in the worksheet.
    await expect(page.getByText("10 rows", { exact: true })).toBeVisible();
    await expect(body.getByRole("row")).toHaveCount(10);
    await expect(body.getByRole("rowheader").first()).toHaveText("2");
    await page.getByRole("button", { name: "Sort by Capital_From" }).click();
    const first = await body.getByRole("row").first().innerText();
    await page.getByRole("button", { name: "Sort by Capital_From" }).click();
    expect(await body.getByRole("row").first().innerText()).not.toBe(first);
    await page
        .getByRole("searchbox", { name: "Filter rows by text" })
        .fill("kyiv");
    await expect(page.getByText(/of 10 rows match/)).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("dataset-table.png") });
    // Looking through the table does not change what will be run.
    await expect(page.locator(".source-reference")).toContainText("A1:E11");
});

test("existing values in the output column are announced, kept by default and replaced on request", async ({
    page,
}, testInfo) => {
    // Kyiv–Paris cannot be resolved offline; London–Rome can. Both cells are occupied.
    const source = testInfo.outputPath("occupied.xlsx");
    const python = (code: string, ...args: string[]) =>
        spawnSync("uv", ["run", "--locked", "python", "-c", code, ...args], {
            cwd: "..",
            encoding: "utf8",
        });
    const made = python(
        `
import sys
from openpyxl import load_workbook
book = load_workbook('samples/capitals.xlsx')
book['Sheet1']['E2'] = 99999
book['Sheet1']['E3'] = 5
book.save(sys.argv[1])
`,
        source,
    );
    expect(made.status, made.stdout + made.stderr).toBe(0);

    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page.getByLabel("Upload Excel workbook").setInputFiles(source);
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate distances in km between the city pairs.");
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    const notice = page.getByTestId("existing-outputs");
    await expect(notice).toContainText(
        "2 of 10 rows already have a value (rows 2, 3)",
    );
    await expect(notice.getByLabel(/Keep them/)).toBeChecked();
    await notice.getByLabel(/Replace them/).check();
    await page.screenshot({
        path: testInfo.outputPath("existing-outputs.png"),
    });
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    await page.getByRole("button", { name: "Build Plan", exact: true }).click();
    await page.getByRole("button", { name: "Approve and continue" }).click();
    await page.getByRole("button", { name: "Start bounded run" }).click();
    await expect(page.getByTestId("metric-successful_rows")).toHaveText("1", {
        timeout: 30_000,
    });
    await page.getByRole("button", { name: "Review issues" }).click();
    await page.getByRole("button", { name: "Continue to results" }).click();
    // Row 3 was found and replaced; row 2 was not found and keeps its value.
    await expect(page.getByTestId("result-replaced")).toHaveText(
        "Replaced the earlier value: 5",
    );
    await expect(page.getByTestId("result-kept")).toHaveCount(1);
    await page.screenshot({
        path: testInfo.outputPath("existing-results.png"),
    });
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download enriched XLSX" }).click();
    const output = testInfo.outputPath("occupied-result.xlsx");
    await (await download).saveAs(output);
    const check = python(
        `
import sys
from openpyxl import load_workbook
sheet = load_workbook(sys.argv[1])['Sheet1']
assert sheet['E2'].value == 99999, sheet['E2'].value
assert isinstance(sheet['E3'].value, float) and sheet['E3'].value > 1000, sheet['E3'].value
`,
        output,
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);
});

test("capitals: a name the source does not know is found under another name", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate distances in km between the city pairs.");
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    await page.getByRole("button", { name: "Build Plan", exact: true }).click();
    await page.getByRole("button", { name: "Approve and continue" }).click();
    const firstRun = page.waitForResponse(
        (r) => r.url().endsWith("/runs") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start bounded run" }).click();
    const first = await (await firstRun).json();
    await expect(page.getByTestId("metric-successful_rows")).toHaveText("1", {
        timeout: 30_000,
    });
    await page.getByRole("button", { name: "Review issues" }).click();
    // A name typed by hand that the source does not confirm either.
    const vienna = page.getByTestId("unresolved-Vienna");
    await vienna.getByText("Search under another name").click();
    await vienna
        .getByLabel("Name to search for instead of “Vienna”")
        .fill("Wien");
    await vienna.getByRole("button", { name: "Search" }).click();
    await expect(vienna.getByTestId("other-name-found")).toContainText(
        "no confirmed match for “Wien”",
    );
    // The offline source knows Warsaw under its Polish name only; the AI
    // proposes it, and the proposal is searched like a typed name.
    const warsaw = page.getByTestId("unresolved-Warsaw");
    await warsaw.getByText("Search under another name").click();
    await warsaw
        .getByRole("button", { name: "Ask the AI for other names" })
        .click();
    await page.screenshot({
        path: testInfo.outputPath("review-ai-names.png"),
    });
    await warsaw
        .getByTestId("ai-names")
        .getByRole("button", { name: "Warszawa" })
        .click();
    // The source is asked at once and the confirmed match is offered.
    const match = warsaw.getByTestId("found-open-meteo:756135");
    await expect(match).toContainText("Warszawa");
    await page.screenshot({
        path: testInfo.outputPath("review-other-name-found.png"),
    });
    await match.getByRole("radio").click();
    await expect(warsaw.getByTestId("review-chosen")).toContainText(
        "found under “Warszawa”",
    );
    // The options of Kyiv are compared and chosen on the map, in place. The
    // map itself comes from another site; the test supplies a blank page.
    await page.route("https://www.openstreetmap.org/**", (route) =>
        route.fulfill({ contentType: "text/html", body: "<html></html>" }),
    );
    await page
        .getByTestId("review-Kyiv")
        .getByTestId("candidate-open-meteo:703448")
        .getByRole("button", { name: "Map" })
        .click();
    const map = page.getByTestId("candidate-map");
    await expect(map.getByTestId("map-position")).toHaveText("Option 1 of 2");
    await expect(map.locator("iframe")).toHaveAttribute(
        "src",
        /openstreetmap\.org\/export\/embed\.html\?bbox=.*marker=/,
    );
    await page.screenshot({ path: testInfo.outputPath("review-map.png") });
    await map.getByRole("button", { name: "Choose this one" }).click();
    await expect(map).toHaveCount(0);
    await expect(
        page.getByTestId("review-Kyiv").getByTestId("review-chosen"),
    ).toBeVisible();
    // The footer keeps its rows in every language: the way back and the way
    // on stay side by side under the fill button.
    for (const [locale, back] of [
        ["uk", "Виконання"],
        ["de", "Ausführung"],
        ["en", "Execution"],
    ]) {
        await page
            .locator(".studio-preferences select")
            .nth(1)
            .selectOption(locale);
        const actions = page.locator(".review-actions");
        const backBox = await actions
            .getByRole("button", { name: back, exact: true })
            .boundingBox();
        const onBox = await actions.getByRole("button").last().boundingBox();
        const fillBox = await actions.locator(".review-fill").boundingBox();
        expect(backBox!.y, locale).toBeCloseTo(onBox!.y, 0);
        expect(backBox!.y, locale).toBeGreaterThan(fillBox!.y);
        await page.screenshot({
            path: testInfo.outputPath(`review-footer-${locale}.png`),
        });
    }
    await warsaw.scrollIntoViewIfNeeded();
    await page.screenshot({
        path: testInfo.outputPath("review-other-name.png"),
    });
    const resumeRequest = page.waitForRequest((r) =>
        r.url().endsWith(`/api/jobs/${first.job_id}/resume`),
    );
    await page.getByRole("button", { name: "Fill in remaining rows" }).click();
    const sent = (await resumeRequest).postDataJSON();
    expect(sent.decisions).toHaveLength(2);
    expect(sent.decisions[1]).toMatchObject({
        source: "open-meteo",
        entity_id: "756135",
        name: "Warszawa",
    });
    // Kyiv–Paris and Warsaw–Kyiv are now complete.
    await expect(page.getByTestId("metric-successful_rows")).toHaveText("3", {
        timeout: 30_000,
    });
    await page.getByRole("button", { name: "Review issues" }).click();
    await expect(page.getByTestId("unresolved-Warsaw")).toHaveCount(0);
    await page.getByRole("button", { name: "Continue to results" }).click();
    // A value found under the given name is marked as the user's decision.
    const chosen = page.locator(
        '[data-testid="row-outcome"][data-chosen="true"]',
    );
    await expect(chosen.nth(1)).toContainText("After your choice: Kyiv");
    // The row says under which name its value was found, and who gave it.
    await expect(chosen.nth(1).getByTestId("result-searched")).toHaveText(
        "“Warsaw” was found under a name the AI proposed: “Warszawa”",
    );
    await page.screenshot({
        path: testInfo.outputPath("results-searched-as.png"),
    });
    const reportDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download JSON audit" }).click();
    const reportPath = testInfo.outputPath("other-name-report.json");
    await (await reportDownload).saveAs(reportPath);
    const audit = JSON.parse(await readFile(reportPath, "utf8"));
    const queries = audit.execution.resolution_report.operations
        .map((operation: any) => operation.report)
        .filter((report: any) => report.candidate_set.query.name === "Warsaw")
        .map((report: any) => [
            report.candidate_set.query.searched_as,
            report.decision.basis,
            report.decision.selected.entity_id,
        ]);
    // The audit keeps the row's value, the name searched and whose choice it was.
    expect(queries).toEqual([["Warszawa", "user_override", "756135"]]);
    // It also says who gave the name, and what the model was asked for it.
    expect(audit.review_assistance.other_names).toMatchObject([
        { name: "Warsaw", searched_as: "Warszawa", origin: "model" },
    ]);
    expect(audit.review_assistance.model_requests).toMatchObject([
        {
            name: "Warsaw",
            outcome: "answered",
            proposed: ["Warszawa", "Varsovie"],
        },
    ]);
});

test("capitals: a chosen candidate resumes as a new audited job and fills the row", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate distances in km between the city pairs.");
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    await page.getByRole("button", { name: "Build Plan", exact: true }).click();
    await page.getByRole("button", { name: "Approve and continue" }).click();
    const firstRun = page.waitForResponse(
        (r) => r.url().endsWith("/runs") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start bounded run" }).click();
    const first = await (await firstRun).json();
    await expect(page.getByTestId("metric-successful_rows")).toHaveText("1", {
        timeout: 30_000,
    });
    await page.getByRole("button", { name: "Review issues" }).click();
    const kyiv = page.getByTestId("review-Kyiv");
    await expect(kyiv.getByRole("radio")).toHaveCount(3);
    await expect(page.getByTestId("review-progress")).toHaveText(
        "Decided: 0 of 1",
    );
    // The two offline candidates are equal, so nothing is recommended.
    await expect(
        page.getByRole("button", { name: "Accept recommendations" }),
    ).toBeDisabled();
    await page
        .getByTestId("candidate-open-meteo:703448")
        .getByRole("radio")
        .click();
    // The decided card folds to the choice, with a way back to the list.
    await expect(page.getByTestId("review-chosen")).toContainText("Kyiv");
    await expect(page.getByRole("button", { name: "Change" })).toBeFocused();
    await expect(page.getByTestId("review-progress")).toHaveText(
        "Decided: 1 of 1",
    );
    // Choices are kept across languages; identifiers stay in the second layer.
    for (const [locale, label] of [
        ["uk", "Заповнити решту рядків"],
        ["de", "Übrige Zeilen ausfüllen"],
        ["en", "Fill in remaining rows"],
    ]) {
        await page
            .locator(".studio-preferences select")
            .nth(1)
            .selectOption(locale);
        await expect(page.getByRole("button", { name: label })).toBeEnabled();
    }
    // The technical layer belongs to the open list.
    await kyiv.getByRole("button", { name: "Change" }).click();
    await expect(kyiv.getByText("geonames:703448")).toBeHidden();
    await kyiv.getByText("Technical details").click();
    await expect(kyiv.getByText("geonames:703448")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("review-choice.png") });
    const resumeRequest = page.waitForRequest((r) =>
        r.url().endsWith(`/api/jobs/${first.job_id}/resume`),
    );
    await page.getByRole("button", { name: "Fill in remaining rows" }).click();
    const sent = (await resumeRequest).postDataJSON();
    expect(sent.decisions).toHaveLength(1);
    expect(sent.decisions[0]).toMatchObject({
        source: "open-meteo",
        entity_id: "703448",
    });
    // Kyiv–Paris is now complete; Warsaw–Kyiv still lacks Warsaw.
    await expect(page.getByTestId("metric-successful_rows")).toHaveText("2", {
        timeout: 30_000,
    });
    await expect(
        page.getByText("This run continues an earlier one after review."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Review issues" }).click();
    await expect(page.getByTestId("review-Kyiv")).toHaveCount(0);
    await expect(
        page.getByText("There is nothing to choose in this run.", {
            exact: false,
        }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Continue to results" }).click();
    // The value that exists only because of the choice is marked as such.
    const chosen = page.locator(
        '[data-testid="row-outcome"][data-chosen="true"]',
    );
    await expect(chosen).toHaveCount(2);
    await expect(chosen.first()).toContainText(/Kyiv.*Paris.*2,023\.79\skm/);
    await expect(chosen.first()).toContainText("After your choice: Kyiv");
    // Warsaw–Kyiv used the choice too, but still lacks Warsaw.
    await expect(chosen.nth(1)).toContainText("did not find “Warsaw”");
    await page.getByRole("button", { name: "After your choice (2)" }).click();
    await expect(page.getByTestId("row-outcome")).toHaveCount(2);
    await page.getByRole("button", { name: "Details for row 2" }).click();
    await expect(page.getByText("open-meteo geonames:703448")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("results.png") });
    const reportDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download JSON audit" }).click();
    const reportPath = testInfo.outputPath("resumed-report.json");
    await (await reportDownload).saveAs(reportPath);
    const audit = JSON.parse(await readFile(reportPath, "utf8"));
    expect(audit.previous_job_id).toBe(first.job_id);
    expect(audit.review_decisions).toEqual(sent.decisions);
    expect(audit.metrics.successful_rows).toBe(2);
    const decisions = audit.execution.resolution_report.operations
        .map((operation: any) => operation.report)
        .filter((report: any) => report.candidate_set.query.name === "Kyiv")
        .map((report: any) => report.decision);
    expect(decisions.length).toBeGreaterThan(0);
    for (const decision of decisions) {
        expect(decision.basis).toBe("user_override");
        expect(decision.selected.entity_id).toBe("703448");
    }
    // The first job is untouched and still downloadable.
    const earlier = await page.request.get(
        `/api/jobs/${first.job_id}/artifacts/report`,
    );
    expect((await earlier.json()).metrics.successful_rows).toBe(1);
});

test("example: one click fills the task; the prepared plan runs without an AI request", async ({
    page,
}, testInfo) => {
    const planning: string[] = [];
    page.on("request", (r) => {
        if (r.url().endsWith("/api/plans") && r.method() === "POST")
            planning.push(r.postData() ?? "");
    });
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await expect(page.getByLabel("Mode")).toHaveValue("basic");
    await expect(page.getByRole("button", { name: "Diagnostics" })).toHaveCount(
        0,
    );
    await page.getByRole("button", { name: "Mountains: heights" }).click();
    // Uploaded, previewed and filled in without touching a file dialog.
    await expect(page.getByLabel("Enrichment instruction")).toHaveValue(
        /elevation in metres of each mountain/,
    );
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("C");
    await expect(page.locator(".upload-description strong")).toHaveText(
        "mountains.xlsx",
    );
    // Basic has no consent checkbox: pressing the button is the consent.
    await expect(
        page.getByLabel(
            "Allow external network requests for planning and execution",
        ),
    ).toHaveCount(0);
    await languagesFit(page, testInfo, "basic-dataset");
    await page
        .getByRole("button", { name: "Use it instead of asking the AI" })
        .click();
    // Hold the answer back so the wait itself can be observed.
    await page.route("**/api/plans", async (route) => {
        await new Promise((done) => setTimeout(done, 1500));
        await route.continue();
    });
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    const activity = page.locator(".activity-bar");
    await expect(activity.getByRole("status")).toContainText(
        "Checking the plan",
    );
    await expect(activity.getByRole("timer")).toHaveText("0:01");
    const steps = page.getByRole("navigation").getByRole("button");
    await expect(steps.nth(3)).toBeDisabled();
    await expect(steps.nth(3)).toHaveAttribute(
        "title",
        "Wait for the current operation to finish.",
    );
    // The plan is put into words; the graph belongs to Advanced.
    await expect(page.locator(".plan-steps li")).toHaveText([
        /Find the mountain.*Name: column “Mountain” · Country: column “Country”/,
        /Look up the elevation.*For: result of step 1.*Written to column “height” \(C\), m/,
    ]);
    await expect(
        page.getByRole("region", { name: "Planned workflow" }),
    ).toHaveCount(0);
    await expect(activity).toHaveCount(0);
    // The submitted request carried the prepared plan itself.
    expect(planning).toHaveLength(1);
    expect(JSON.parse(planning[0]).plan.steps.length).toBeGreaterThan(0);
    await languagesFit(page, testInfo, "basic-plan");
    // One press approves the plan and starts the run.
    await page.getByRole("button", { name: "Approve and run" }).click();
    // The run is summed up in a sentence; the counters belong to Advanced.
    await expect(page.getByTestId("job-status")).toHaveText(
        /Rows filled: \d+ of 10\.|All rows are filled: 10 of 10\./,
        { timeout: 30_000 },
    );
    await expect(page.getByTestId("metric-total_rows")).toHaveCount(0);
    await languagesFit(page, testInfo, "basic-execution");
    await page.getByRole("button", { name: "Review", exact: true }).click();
    await languagesFit(page, testInfo, "basic-review");
    await page.getByRole("button", { name: "Results", exact: true }).click();
    await languagesFit(page, testInfo, "basic-results");
    await page.getByRole("button", { name: "Execution", exact: true }).click();
    // Starting over warns that the filled table goes with the workbook, and
    // brings the examples back.
    await page
        .getByRole("button", { name: "Start over with another table" })
        .click();
    await expect(
        page.getByText("Download the filled table first if you need it."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Remove and start over" }).click();
    await expect(
        page.getByRole("button", {
            name: "Capitals: distances between cities",
        }),
    ).toBeVisible();
});

test("example: a plan that branches fills three columns, in words and as a graph", async ({
    page,
}, testInfo) => {
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await expect(page.getByLabel("Mode")).toHaveValue("basic");
    // Offered because this server has a routing source.
    await expect(
        page.getByText("calculate the driving distance by road"),
    ).toBeVisible();
    await page
        .getByRole("button", {
            name: "Capitals: straight line, by road, and how much longer",
        })
        .click();
    // The example arrives with all three of its columns chosen.
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("E");
    const more = page.getByLabel("One more column to fill");
    await expect(more).toHaveCount(2);
    await expect(more.nth(0)).toHaveValue("F");
    await expect(more.nth(1)).toHaveValue("G");
    await languagesFit(page, testInfo, "roads-dataset");
    await page.screenshot({ path: testInfo.outputPath("roads-dataset.png") });
    await page
        .getByRole("button", { name: "Use it instead of asking the AI" })
        .click();
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    // Five steps: two cities, two distances from the same two points, and
    // the ratio of those two distances.
    await expect(page.locator(".plan-steps li")).toHaveText([
        /Find the coordinates of the city.*City: column “Capital_From”/,
        /Find the coordinates of the city.*City: column “Capital_To”/,
        /Calculate the straight-line distance in km.*From: result of step 1 · To: result of step 2.*Written to column “distance” \(E\), km/,
        /Calculate the driving distance by road in km.*From: result of step 1 · To: result of step 2.*Written to column “road” \(F\), km/,
        /Calculate how many times the first value is larger than the second.*Divide: result of step 4 · By: result of step 3.*Written to column “detour” \(G\)/,
    ]);
    // A road distance says what computed it and that no record backs it.
    await expect(page.locator(".plan-steps li").nth(3)).toContainText(
        "openrouteservice routing service",
    );
    // Basic draws the same plan above the list, in the list's own words and
    // numbers: two cities in the first column, the two distances in the
    // second, their ratio in the third.
    const diagram = page.getByTestId("plan-diagram");
    const node = (id: string) => diagram.locator(`[data-step="${id}"]`);
    await expect(diagram.getByRole("button")).toHaveCount(5);
    await expect(node("road")).toContainText(
        "Calculate the driving distance by road in km",
    );
    await expect(node("road")).toContainText("→ “road” (F)");
    await expect(node("detour")).toContainText("5");
    const box = async (id: string) => (await node(id).boundingBox())!;
    expect((await box("from_city")).x).toBe((await box("to_city")).x);
    expect((await box("distance")).x).toBeGreaterThan(
        (await box("from_city")).x,
    );
    expect((await box("road")).x).toBe((await box("distance")).x);
    expect((await box("detour")).x).toBeGreaterThan((await box("road")).x);
    // The joining step stands between the two it joins.
    expect((await box("detour")).y).toBeGreaterThan((await box("distance")).y);
    expect((await box("detour")).y).toBeLessThan((await box("road")).y);
    // Nothing technical: no capability ids, no inspector.
    await expect(diagram).not.toContainText("road_distance");
    await expect(
        page.getByRole("complementary", { name: "Node inspector" }),
    ).toHaveCount(0);
    // Selecting a step points at its line in the list.
    await node("road").click();
    await expect(page.locator(".plan-steps li").nth(3)).toHaveAttribute(
        "aria-current",
        "true",
    );
    await page.screenshot({ path: testInfo.outputPath("roads-plan.png") });
    await node("road").click();
    await expect(
        page.locator('.plan-steps li[aria-current="true"]'),
    ).toHaveCount(0);
    await languagesFit(page, testInfo, "roads-plan");
    // The same plan as a graph: two branches from the two cities meet again.
    await page.getByLabel("Mode").selectOption("advanced");
    await expect(
        page.getByRole("region", { name: "Planned workflow" }),
    ).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("roads-graph.png") });
    await page.getByLabel("Mode").selectOption("basic");
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toHaveText(
        /Rows filled: \d+ of 10\./,
        { timeout: 30_000 },
    );
    // What each step did, from the audit of this run: the injected answers
    // know three of the first cities and two of the second, and one pair.
    const did = page.getByTestId("step-outcomes").locator(".plan-steps li");
    await expect(did).toHaveText([
        /Capital_From.*Rows with a value: 3 of 10 · without a value: 7/,
        /Capital_To.*Rows with a value: 2 of 10 · without a value: 8/,
        /straight-line distance.*Rows with a value: 1 of 10 · not reached: 9/,
        /driving distance by road.*Rows with a value: 1 of 10 · not reached: 9/,
        /how many times.*Rows with a value: 1 of 10 · not reached: 9/,
    ]);
    // The picture carries the same counts.
    const ran = page.getByTestId("step-outcomes").getByTestId("plan-diagram");
    await expect(ran.locator('[data-step="from_city"]')).toContainText(
        "Rows: 3 of 10",
    );
    await expect(ran.locator('[data-step="detour"]')).toContainText(
        "Rows: 1 of 10",
    );
    await languagesFit(page, testInfo, "roads-execution");
    await page.screenshot({ path: testInfo.outputPath("roads-steps.png") });
    // The same counts on the graph, with the reasons for the selected step.
    await page.getByLabel("Mode").selectOption("advanced");
    const graph = page.getByRole("region", { name: "Planned workflow" });
    await expect(graph.locator('[data-step="from_city"]')).toContainText(
        "Rows: 3 of 10",
    );
    await expect(graph.locator('[data-step="detour"]')).toContainText(
        "Rows: 1 of 10",
    );
    await graph.locator('[data-step="road"]').click();
    await expect(page.getByTestId("node-run")).toContainText(
        "Rows with a value: 1 of 10 · not reached: 9",
    );
    await expect(page.getByTestId("node-run")).toContainText(
        "Values from: openrouteservice",
    );
    await graph.locator('[data-step="to_city"]').click();
    await expect(page.getByTestId("node-run")).toContainText("NOT_FOUND × 7");
    await expect(page.getByTestId("node-run")).toContainText("AMBIGUOUS × 1");
    await page.screenshot({
        path: testInfo.outputPath("roads-graph-run.png"),
    });
    await page.getByLabel("Mode").selectOption("basic");
    await page.getByRole("button", { name: "Results", exact: true }).click();
    // The injected answers know London and Rome: all three cells are filled.
    const london = page.locator("tbody tr", { hasText: "London" }).first();
    await expect(london).toContainText("1,435.48");
    await expect(london).toContainText("1,870");
    await expect(london).toContainText("1.3");
    await page.screenshot({ path: testInfo.outputPath("roads-results.png") });
    await languagesFit(page, testInfo, "roads-results");
});

test("usage: the owner sees what was used, as numbers, after entering the admin code", async ({
    page,
}, testInfo) => {
    // Something to count: the Capitals example, run from its prepared plan.
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByRole("button", { name: "Capitals: distances between cities" })
        .click();
    await page
        .getByRole("button", { name: "Use it instead of asking the AI" })
        .click();
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toHaveText(
        /Rows filled: \d+ of 10\./,
        { timeout: 30_000 },
    );
    await page.goto("/#usage");
    await expect(page.getByTestId("usage-visitors")).toHaveCount(0);
    await page.getByLabel("Admin code").fill("not-the-admin-code");
    await page.getByRole("button", { name: "Show" }).click();
    await expect(page.getByRole("alert")).toHaveText(
        "That admin code is not correct.",
    );
    await page.getByLabel("Admin code").fill("fixture-admin-code");
    await page.getByRole("button", { name: "Show" }).click();
    // Locally there are no visitor codes: everything counts as "local".
    const local = page
        .getByTestId("usage-visitors")
        .getByRole("row", { name: /^local/ });
    await expect(local).toContainText("open-meteo");
    const recent = page.getByTestId("usage-recent");
    await expect(recent).toContainText("run_finished");
    await expect(recent).toContainText("example");
    await expect(recent).toContainText("sample=capitals");
    // Numbers and fixed words: no city, no column, no file of the table.
    for (const content of ["Kyiv", "London", "Capital_From", ".xlsx"])
        await expect(page.locator("main")).not.toContainText(content);
    await page.screenshot({ path: testInfo.outputPath("usage.png") });
    await page.getByRole("link", { name: "To the studio" }).click();
    await expect(page.getByLabel("Mode")).toBeVisible();
});

test("Basic: an uploaded workbook needs an instruction and nothing else", async ({
    page,
}) => {
    const planning: string[] = [];
    page.on("request", (r) => {
        if (r.url().endsWith("/api/plans") && r.method() === "POST")
            planning.push(r.postData() ?? "");
    });
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    // What can be asked for is stated before any file is chosen.
    await expect(
        page.getByText(
            "Calculate the straight-line distance between two places",
        ),
    ).toBeVisible();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    // The table is read at once, and its only empty column is proposed.
    await expect(page.getByRole("columnheader").nth(1)).toBeVisible();
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("E");
    await expect(
        page.getByRole("button", { name: "Preview selection" }),
    ).toHaveCount(0);
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate distances between the cities.");
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.locator(".plan-steps li").last()).toContainText(
        "Written to column “distance” (E), km",
    );
    expect(JSON.parse(planning[0])).toMatchObject({
        range_ref: "A1:E11",
        output_columns: { distance: "E" },
        allow_network: true,
    });
    await page.getByRole("button", { name: "Approve and run" }).click();
    await expect(page.getByTestId("job-status")).toHaveText(
        /Rows filled: \d+ of 10\./,
        { timeout: 30_000 },
    );
    // The boundaries are still one click away.
    await navigate(page, "Dataset");
    await page.getByRole("button", { name: "Change its boundaries" }).click();
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
});

test("Basic: the AI proposes a task for the table, in the language of the interface", async ({
    page,
}, testInfo) => {
    const asked: { language: string; range_ref: string }[] = [];
    page.on("request", (r) => {
        if (r.url().endsWith("/api/task-ideas"))
            asked.push(JSON.parse(r.postData() ?? "{}"));
    });
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Which column to fill?")).toHaveValue("E");
    const field = page.getByLabel("Enrichment instruction");
    // What leaves the browser is said next to the button.
    await expect(page.getByText(/five sample rows to the AI/)).toBeVisible();
    await page.getByRole("button", { name: "Suggest a task" }).click();
    const idea = page.getByTestId("task-ideas").getByRole("button");
    await expect(idea).toHaveText(
        "[English] Calculate the distance in km between Capital_From and Capital_To and write it to distance.",
    );
    // A proposal is not an instruction until it is chosen.
    await expect(field).toHaveValue("");
    await expect(
        page.getByRole("button", { name: "Build Plan" }),
    ).toBeDisabled();
    await idea.click();
    await expect(field).toHaveValue(/^\[English\] Calculate the distance/);
    await page.screenshot({ path: testInfo.outputPath("task-ideas.png") });
    // The chosen text is planned and checked like a typed one.
    await page.getByRole("button", { name: "Build Plan" }).click();
    await expect(page.locator(".plan-steps li").last()).toContainText(
        "Written to column “distance” (E), km",
    );
    // Another interface language asks again, in that language.
    await navigate(page, "Dataset");
    await page.locator(".studio-preferences select").nth(1).selectOption("uk");
    await page.getByRole("button", { name: "Запропонувати завдання" }).click();
    await expect(page.getByTestId("task-ideas").getByRole("button")).toHaveText(
        /^\[Ukrainian\]/,
    );
    expect(asked.map((x) => x.language)).toEqual(["en", "uk"]);
    expect(asked[0].range_ref).toBe("A1:E11");
    await page.screenshot({ path: testInfo.outputPath("task-ideas-uk.png") });
});

test("invalid saved plan cannot be approved; source selection clears its draft", async ({
    page,
}) => {
    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate literal city distances.");
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    await page
        .getByText("Use an existing plan (optional)", { exact: true })
        .click();
    await page
        .getByLabel("EnrichmentPlan JSON")
        .fill(
            (
                await readFile(
                    resolve("../demo/capitals_api_plan.json"),
                    "utf8",
                )
            ).replace('"geodesic_distance"', '"unknown_capability"'),
        );
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    await expect(page.getByTestId("validation")).toContainText(
        "Validation failed",
    );
    await expect(
        page.getByRole("button", { name: "Approve and continue" }),
    ).toBeDisabled();
    await navigate(page, "Execution");
    await expect(
        page.getByRole("button", { name: "Start bounded run" }),
    ).toBeDisabled();
    await navigate(page, "Dataset");
    await page.getByLabel("Last row", { exact: true }).fill("10");
    await expect(page.getByTestId("fingerprint")).toHaveCount(0);
    await page.getByRole("button", { name: "Preview selection" }).click();
    // The draft made for the old boundaries is gone; the task written for the
    // table is not.
    await expect(page.getByLabel("Enrichment instruction")).toHaveValue(
        "Calculate literal city distances.",
    );
});

test("new column outside selection: collision protection, offline validation, real output and keyboard/mobile access", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByLabel("Last column", { exact: true }).selectOption("D");
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Write verified literal city distances in km to distance_km.");
    await page
        .getByLabel("Destination type 1", { exact: true })
        .selectOption("new");
    await page.getByLabel("Letter or index 1", { exact: true }).fill("5");
    await page
        .getByLabel("New column header 1", { exact: true })
        .fill("distance_km");
    const supplied = JSON.parse(
        await readFile(resolve("../demo/capitals_api_plan.json"), "utf8"),
    );
    supplied.outputs[0].target_column = "distance_km";
    await page
        .getByText("Use an existing plan (optional)", { exact: true })
        .click();
    await page.getByLabel("EnrichmentPlan JSON").fill(JSON.stringify(supplied));
    const submit = page.getByRole("button", {
        name: "Submit and validate plan",
    });
    await expect(submit).toBeDisabled(); // E exists outside the selected A:D table.
    await page.getByLabel("Letter or index 1", { exact: true }).fill("6");
    await expect(
        page.getByLabel("Letter or index 1", { exact: true }),
    ).toHaveValue("F");
    await page
        .getByLabel("New column header 1", { exact: true })
        .fill("distance");
    await expect(submit).toBeDisabled(); // Header collision outside the selected range.
    await page
        .getByLabel("New column header 1", { exact: true })
        .fill("distance_km");
    await expect(submit).toBeEnabled(); // Supplied plan validation requires no network.
    await submit.click();
    await expect(page.getByTestId("validation")).toHaveText(
        "Validation passed",
    );
    await page.getByRole("button", { name: "Approve and continue" }).click();
    await expect(
        page.getByRole("button", { name: "Start bounded run" }),
    ).toBeDisabled();
    await navigate(page, "Dataset");
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    await expect(page.getByTestId("validation")).toHaveText(
        "Validation passed",
    );
    await page.getByRole("button", { name: "Approve and continue" }).click();
    await page.getByRole("button", { name: "Start bounded run" }).click();
    await expect(page.getByTestId("job-status")).toContainText("Needs review", {
        timeout: 30_000,
    });
    await navigate(page, "Results");
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download enriched XLSX" }).click();
    const workbook = testInfo.outputPath("new-column.xlsx");
    await (await downloadPromise).saveAs(workbook);
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
source = load_workbook('samples/capitals.xlsx').active
result = load_workbook(sys.argv[1]).active
for row in source:
    for cell in row:
        assert result[cell.coordinate].value == cell.value, cell.coordinate
        assert result[cell.coordinate].number_format == cell.number_format
assert result['F1'].value == 'distance_km'
assert 1400 < result['F3'].value < 1500
assert result['F2'].value is None
`,
            workbook,
        ],
        { cwd: "..", encoding: "utf8" },
    );
    expect(check.status, check.stdout + check.stderr).toBe(0);
    await expect(
        page.getByRole("columnheader", { name: "distance_km" }),
    ).toBeVisible();
    await expect(page.getByTestId("row-outcome").nth(1)).toContainText(
        /1,435\.48\skm/,
    );

    const trigger = page.getByRole("button", {
        name: "Diagnostics",
        exact: true,
    });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    for (let i = 0; i < 12; i++) {
        await page.keyboard.press("Tab");
        expect(
            await page.evaluate(() =>
                document
                    .querySelector("dialog")
                    ?.contains(document.activeElement),
            ),
        ).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await page.setViewportSize({ width: 390, height: 844 });
    for (const workspace of [
        "Dataset",
        "Plan",
        "Execution",
        "Review",
        "Results",
    ]) {
        await navigate(page, workspace);
        await expect(
            page.getByRole("heading", { name: workspace, exact: true }),
        ).toBeVisible();
        const documentWidth = await page.evaluate(
            () => document.documentElement.scrollWidth,
        );
        expect(documentWidth).toBeLessThanOrEqual(390);
    }
    await page
        .getByRole("button", { name: "Download enriched XLSX" })
        .scrollIntoViewIfNeeded();
    await expect(
        page.getByRole("button", { name: "Download enriched XLSX" }),
    ).toBeInViewport();
});

test("overview: the page a visitor opens says what the system is and lists what the server offers", async ({
    page,
}, testInfo) => {
    const listed = page.waitForResponse((r) =>
        r.url().endsWith("/api/capabilities"),
    );
    await page.goto("/");
    await expect(
        page.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeVisible();
    // The list on the page is the server's own, in its order.
    const offered: { id: string; source: string }[] = (
        await (await listed).json()
    ).capabilities;
    expect(offered.map((item) => item.id)).toEqual([
        "geocode",
        "resolve_entity",
        "lookup_property",
        "geodesic_distance",
        "ratio",
        "road_distance",
    ]);
    const cards = page.getByTestId("capability-list").locator("> li");
    await expect(cards).toHaveCount(offered.length);
    expect(
        await cards.evaluateAll((items) =>
            items.map((item) => item.getAttribute("data-capability")),
        ),
    ).toEqual(offered.map((item) => item.id));
    const roads = page.locator('[data-capability="road_distance"]');
    await expect(roads).toContainText("Calculate the driving distance by road");
    await expect(roads).toContainText(
        "Source: openrouteservice (OpenStreetMap data)",
    );
    await expect(page.locator('[data-capability="ratio"]')).toContainText(
        "Calculated here, without an outside source.",
    );
    // Basic keeps to plain words: no contract, no identifiers.
    await expect(page.getByTestId("capability-contract")).toHaveCount(0);
    await expect(page.getByText("One run fills up to 20 rows.")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("overview.png") });
    await languagesFit(page, testInfo, "overview");

    // Advanced adds each contract as the planning model is shown it.
    await page
        .locator(".studio-preferences select")
        .nth(2)
        .selectOption("advanced");
    await expect(page.getByTestId("capability-contract")).toHaveCount(
        offered.length,
    );
    await expect(roads).toContainText(
        "road_distance · v1.0 · openrouteservice",
    );
    await expect(
        roads.getByRole("rowheader", { name: "point_a" }),
    ).toBeVisible();
    await expect(roads).toContainText("km · required");
    await roads.scrollIntoViewIfNeeded();
    await page.screenshot({
        path: testInfo.outputPath("overview-advanced.png"),
    });
    await page
        .locator(".studio-preferences select")
        .nth(2)
        .selectOption("basic");

    // A narrow screen still fits the page.
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.setViewportSize({ width: 1440, height: 900 });

    // An example opens straight from here, on the first step.
    await page
        .getByRole("button", { name: "Capitals: distances between cities" })
        .click();
    await expect(
        page.getByRole("heading", { level: 1, name: "Dataset" }),
    ).toBeVisible();
    await expect(page.getByLabel("Enrichment instruction")).not.toHaveValue("");
    // And the overview stays one click away without losing the table.
    await navigate(page, "Overview");
    await expect(page.getByTestId("capability-list")).toBeVisible();
    await navigate(page, "Dataset");
    await expect(page.getByLabel("Enrichment instruction")).not.toHaveValue("");
});
