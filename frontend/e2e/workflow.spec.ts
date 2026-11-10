import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

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
            .getByLabel("Output name 1", { exact: true })
            .fill(name === "capitals" ? "distance" : "height");
        await page
            .getByLabel("Excel column 1", { exact: true })
            .fill(name === "capitals" ? "E" : "C");
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
                name: "Generate and validate plan",
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
        await expect(page.getByTestId("fingerprint")).toHaveText(
            draft.fingerprint,
        );
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
        await page.getByRole("button", { name: "Validate again" }).click();
        await expect(page.getByTestId("validation")).toHaveText(
            "Validation passed",
        );
        await expect(
            page.getByRole("button", { name: "Approve this fingerprint" }),
        ).toBeEnabled();
        await page
            .getByRole("button", { name: "Approve this fingerprint" })
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
        await page.getByRole("button", { name: "Start bounded run" }).click();
        const queued = await (await runPromise).json();
        expect(queued.status).toBe("queued");
        await expect(page.getByLabel("Upload Excel workbook")).toBeDisabled();
        await expect(page.getByTestId("job-status")).toHaveText(
            "Needs review — unresolved records remain",
            { timeout: 30_000 },
        );
        await expect(page.getByTestId("row-outcome")).toHaveCount(10);
        await expect(
            page.getByText("Offline fixture execution", { exact: false }),
        ).toBeVisible();
        const xlsxDownload = page.waitForEvent("download");
        await page
            .getByRole("button", { name: "Download enriched XLSX" })
            .click();
        const workbook = testInfo.outputPath(`${name}-result.xlsx`);
        await (await xlsxDownload).saveAs(workbook);
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
        await page.getByLabel("Excel column 1", { exact: true }).fill("F");
        await expect(page.getByTestId("fingerprint")).toHaveCount(0);
        await expect(page.getByTestId("job-status")).toHaveCount(0);
    });
}

test("invalid saved plan cannot be approved; source selection clears its draft", async ({
    page,
}) => {
    await page.goto("/");
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill("Calculate literal city distances.");
    await page.getByLabel("Output name 1", { exact: true }).fill("distance");
    await page.getByLabel("Excel column 1", { exact: true }).fill("A");
    await page
        .getByText("Use an existing plan (optional)", { exact: true })
        .click();
    await page
        .getByLabel("EnrichmentPlan JSON")
        .fill(
            await readFile(resolve("../demo/capitals_api_plan.json"), "utf8"),
        );
    await page
        .getByRole("button", { name: "Submit and validate plan" })
        .click();
    await expect(page.getByTestId("validation")).toContainText(
        "Validation failed",
    );
    await expect(
        page.getByRole("button", { name: "Approve this fingerprint" }),
    ).toBeDisabled();
    await expect(
        page.getByRole("button", { name: "Start bounded run" }),
    ).toBeDisabled();
    await page.getByLabel("Last row", { exact: true }).fill("10");
    await expect(page.getByTestId("fingerprint")).toHaveCount(0);
    await page.getByRole("button", { name: "Preview selection" }).click();
    await expect(page.getByLabel("Enrichment instruction")).toHaveValue("");
});
