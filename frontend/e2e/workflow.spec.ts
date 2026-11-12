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
                name: "Approve / Continue to Execution",
            }),
        ).toBeEnabled();
        await page
            .getByRole("button", { name: "Approve / Continue to Execution" })
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
            await expect(
                page.getByRole("button", { name: "Fill in remaining rows" }),
            ).toHaveCount(0);
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
            ["uk", "Потребує перегляду"],
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

test("capitals: a chosen candidate resumes as a new audited job and fills the row", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
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
    await page
        .getByRole("button", { name: "Approve / Continue to Execution" })
        .click();
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
        .check();
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
}) => {
    const planning: string[] = [];
    page.on("request", (r) => {
        if (r.url().endsWith("/api/plans") && r.method() === "POST")
            planning.push(r.postData() ?? "");
    });
    await page.goto("/");
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
    await expect(steps.nth(2)).toBeDisabled();
    await expect(steps.nth(2)).toHaveAttribute(
        "title",
        "Wait for the current operation to finish.",
    );
    await page
        .getByRole("button", { name: "Approve / Continue to Execution" })
        .click();
    await expect(activity).toHaveCount(0);
    // The submitted request carried the prepared plan itself.
    expect(planning).toHaveLength(1);
    expect(JSON.parse(planning[0]).plan.steps.length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Start bounded run" }).click();
    await expect(page.getByTestId("metric-total_rows")).toHaveText("10", {
        timeout: 30_000,
    });
    // Removing the workbook brings the examples back.
    await navigate(page, "Dataset");
    await page.getByRole("button", { name: "Remove workbook" }).click();
    await expect(
        page.getByRole("button", {
            name: "Capitals: distances between cities",
        }),
    ).toBeVisible();
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
    // What can be asked for is stated before any file is chosen.
    await expect(
        page.getByText("calculate the distance between two cities"),
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
    await page
        .getByRole("button", { name: "Approve / Continue to Execution" })
        .click();
    expect(JSON.parse(planning[0])).toMatchObject({
        range_ref: "A1:E11",
        output_columns: { distance: "E" },
        allow_network: true,
    });
    await page.getByRole("button", { name: "Start bounded run" }).click();
    await expect(page.getByTestId("metric-total_rows")).toContainText("10");
    // The boundaries are still one click away.
    await navigate(page, "Dataset");
    await page.getByRole("button", { name: "Change its boundaries" }).click();
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
});

test("invalid saved plan cannot be approved; source selection clears its draft", async ({
    page,
}) => {
    await advanced(page);
    await page.goto("/");
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
        page.getByRole("button", { name: "Approve / Continue to Execution" }),
    ).toBeDisabled();
    await navigate(page, "Execution");
    await expect(
        page.getByRole("button", { name: "Start bounded run" }),
    ).toBeDisabled();
    await navigate(page, "Dataset");
    await page.getByLabel("Last row", { exact: true }).fill("10");
    await expect(page.getByTestId("fingerprint")).toHaveCount(0);
    await page.getByRole("button", { name: "Preview selection" }).click();
    await expect(page.getByLabel("Enrichment instruction")).toHaveValue("");
});

test("new column outside selection: collision protection, offline validation, real output and keyboard/mobile access", async ({
    page,
}, testInfo) => {
    await advanced(page);
    await page.goto("/");
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
    await page
        .getByRole("button", { name: "Approve / Continue to Execution" })
        .click();
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
    await page
        .getByRole("button", { name: "Approve / Continue to Execution" })
        .click();
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
