import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("column inspector is an Advanced tool; neither mode overflows the document", async ({
    page,
}) => {
    await page.goto("/");
    // The page opens on the overview; the work begins one click later.
    await page.getByRole("button", { name: "Start with my table" }).click();
    await page
        .getByLabel("Upload Excel workbook")
        .setInputFiles(resolve("../samples/capitals.xlsx"));
    // Basic previews the table on upload; there are no boundaries to set.
    await expect(
        page.getByRole("button", { name: "Preview selection" }),
    ).toHaveCount(0);
    const inspector = page.getByRole("complementary", {
        name: "Column inspector",
    });
    const grid = page.locator(".source-grid");
    const fits = async (width: number, height?: number) => {
        expect(
            await page.evaluate(() => document.documentElement.scrollWidth),
        ).toBeLessThanOrEqual(width);
        if (height)
            expect(
                await page.evaluate(
                    () => document.documentElement.scrollHeight,
                ),
            ).toBeLessThanOrEqual(height);
    };

    // Basic: the table takes the whole width, with no inspector beside it.
    await expect(page.getByRole("columnheader").nth(1)).toBeVisible();
    await expect(inspector).toHaveCount(0);
    // Sorting is offered in both modes; choosing a column to inspect is not.
    await expect(
        page.locator(".table-scroll th button.column-head"),
    ).toHaveCount(0);
    await expect(
        page.locator(".table-scroll th button.sort-button"),
    ).toHaveCount(5);
    for (const [width, height] of [
        [1366, 768],
        [1440, 900],
        [3440, 1440],
    ]) {
        await page.setViewportSize({ width, height });
        await fits(width, height);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    const basicWidth = (await grid.boundingBox())!.width;

    await page.getByLabel("Mode").selectOption("advanced");
    await expect(inspector).toBeVisible();
    // Include the intermediate breakpoint that previously forced a fixed width.
    for (const [width, height] of [
        [1366, 768],
        [1440, 900],
        [3440, 1440],
        [1100, 768],
    ]) {
        await page.setViewportSize({ width, height });
        await expect
            .poll(async () => (await inspector.boundingBox())?.width)
            .toBeCloseTo(300, 0);
        await fits(width, height);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect
        .poll(async () => basicWidth - (await grid.boundingBox())!.width)
        .toBeCloseTo(300, 0);

    // Compact and stacked layouts keep the inspector within the viewport.
    for (const [width, height] of [
        [900, 768],
        [390, 844],
        [1366, 500],
    ]) {
        await page.setViewportSize({ width, height });
        await expect(inspector).toBeVisible();
        if (width === 900)
            expect((await inspector.boundingBox())!.width).toBeCloseTo(180, 0);
        await fits(width);
    }
});
