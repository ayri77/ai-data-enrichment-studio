import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";

test("trusted local settings → real SDK with injected response → validated draft, separate consent and approval", async ({
    page,
}) => {
    // No tracing, screenshots, videos, attachments, or request-body capture in this project.
    const secret = randomUUID();
    const consoleMessages: string[] = [];
    page.on("console", (message) => consoleMessages.push(message.text()));
    await page.goto("/");
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await page.getByRole("button", { name: "Preview selection" }).click();
    await page
        .getByLabel("Enrichment instruction")
        .fill(
            "Calculate distances in km between literal city pairs; target distance.",
        );
    await page.getByLabel("Output column 1", { exact: true }).selectOption("E");
    const build = page.getByRole("button", { name: "Build Plan", exact: true });
    await expect(build).toBeDisabled();
    await page
        .getByRole("button", { name: "Provider / Model settings", exact: true })
        .click();
    const dialog = page.getByRole("dialog");
    const key = dialog.getByLabel("Enter / replace API key");
    await expect(key).toHaveAttribute("type", "password");
    await dialog
        .getByLabel("OpenAI planning model")
        .selectOption("gpt-4.1-mini");
    await key.fill(secret);
    const applied = page.waitForResponse(
        (r) =>
            r.url().endsWith("/api/provider-settings") &&
            r.request().method() === "PUT",
    );
    await dialog.getByRole("button", { name: "Apply settings" }).click();
    const result = await applied;
    expect(result.status()).toBe(200); // Exercises actual Vite-preserved Host/Origin.
    const status = await result.json();
    expect(status.live_ready).toBe(true);
    expect(status.planning_mode).toBe("live");
    expect(JSON.stringify(status).includes(secret)).toBe(false);
    await expect(key).toHaveValue("");
    await expect(
        dialog.getByText("Settings applied for this backend process."),
    ).toBeVisible();
    // Real modal keyboard traversal must include the newly added form controls.
    await dialog.getByLabel("OpenAI planning model").focus();
    await page.keyboard.press("Tab");
    await expect(key).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(build).toBeDisabled();
    await page
        .getByLabel(
            "Allow external network requests for planning and execution",
        )
        .check();
    const generated = page.waitForResponse(
        (r) =>
            r.url().endsWith("/api/plans") && r.request().method() === "POST",
    );
    await build.click();
    const draft = await (await generated).json();
    expect(draft.validation.valid).toBe(true);
    expect(draft.status).toBe("draft");
    expect(draft.approved_at).toBeNull();
    expect(JSON.stringify(draft).includes(secret)).toBe(false);
    await page
        .getByRole("button", { name: "Provider / Model settings", exact: true })
        .click();
    await expect(key).toHaveValue("");
    await dialog.getByRole("button", { name: "Remove API key" }).click();
    await expect(
        dialog.getByText("API key removed from this backend process."),
    ).toBeVisible();
    await expect(
        dialog.getByRole("button", { name: "Remove API key" }),
    ).toBeDisabled();
    await key.fill(secret);
    await page.keyboard.press("Escape");
    await page
        .getByRole("button", { name: "Provider / Model settings", exact: true })
        .click();
    await expect(key).toHaveValue("");
    const browserStorage = await page.evaluate(async () => ({
        local: { ...localStorage },
        session: { ...sessionStorage },
        databases: await indexedDB.databases(),
    }));
    expect(JSON.stringify(browserStorage).includes(secret)).toBe(false);
    expect(consoleMessages.some((message) => message.includes(secret))).toBe(
        false,
    );
    expect(page.url().includes(secret)).toBe(false);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("validation")).toBeVisible();
});
