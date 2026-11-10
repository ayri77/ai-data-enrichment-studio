import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("column inspector visibly resizes without document overflow; compact layouts hide the slider", async ({
    page,
}) => {
    await page.goto("/");
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    await expect(page.getByLabel("Worksheet")).toHaveValue("Sheet1");
    await page.getByRole("button", { name: "Preview selection" }).click();
    const inspector = page.getByRole("complementary", {
        name: "Column inspector",
    });
    const slider = inspector.locator('input[type="range"]');
    const grid = page.locator(".source-grid");

    // Include the intermediate breakpoint that previously forced a fixed width.
    for (const [width, height] of [
        [1366, 768],
        [1440, 900],
        [3440, 1440],
        [1100, 768],
    ]) {
        await page.setViewportSize({ width, height });
        await expect(slider).toBeVisible();
        await slider.focus();
        await page.keyboard.press("Home");
        await expect(slider).toHaveValue("260");
        await expect
            .poll(async () => (await inspector.boundingBox())?.width)
            .toBeCloseTo(260, 0);
        const originalGridWidth = (await grid.boundingBox())!.width;
        await page.keyboard.press("End");
        await expect(slider).toHaveValue("420");
        await expect
            .poll(async () => (await inspector.boundingBox())?.width)
            .toBeCloseTo(420, 0);
        await expect
            .poll(
                async () =>
                    originalGridWidth - (await grid.boundingBox())!.width,
            )
            .toBeCloseTo(160, 0);
        if (width === 1100) {
            expect(
                await page
                    .locator(".table-scroll")
                    .evaluate(
                        (element) => element.scrollWidth > element.clientWidth,
                    ),
            ).toBe(true);
        }
        expect(
            await page.evaluate(() => document.documentElement.scrollWidth),
        ).toBeLessThanOrEqual(width);
        expect(
            await page.evaluate(() => document.documentElement.scrollHeight),
        ).toBeLessThanOrEqual(height);
    }

    // A fixed compact inspector / stacked mobile inspector must not expose an
    // inactive resize control, including short viewports that use stacked panes.
    for (const [width, height] of [
        [900, 768],
        [390, 844],
        [1366, 500],
    ]) {
        await page.setViewportSize({ width, height });
        await expect(inspector).toBeVisible();
        await expect(slider).toBeHidden();
        if (width === 900)
            expect((await inspector.boundingBox())!.width).toBeCloseTo(180, 0);
        expect(
            await page.evaluate(() => document.documentElement.scrollWidth),
        ).toBeLessThanOrEqual(width);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(slider).toBeVisible();
    expect((await inspector.boundingBox())!.width).toBeCloseTo(420, 0);
});
