import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { copyFile, mkdir } from "node:fs/promises";
import { basename } from "node:path";

// The catalog on the overview, through the real backend with the stand-ins of
// tests/browser/fixture_server.py: tiles filter, cards open in place. With
// SHOTS_DIR set the screenshots are copied there as well.
async function shot(
    page: Page,
    info: TestInfo,
    name: string,
    scrollTo = "#overview-can",
) {
    // The page scrolls inside its panel: bring the catalog to the top of it.
    await page.locator(scrollTo).evaluate((el) => {
        el.scrollIntoView();
        el.closest(".panel-scroll")?.scrollBy(0, -16);
    });
    const path = info.outputPath(name);
    await page.screenshot({ path, fullPage: name.includes("narrow") });
    const directory = process.env.SHOTS_DIR;
    if (directory) {
        await mkdir(directory, { recursive: true });
        await copyFile(path, `${directory}/${basename(name)}`);
    }
}

async function openOverview(page: Page, mode: "basic" | "advanced") {
    await page.addInitScript((value) => {
        localStorage.setItem("enrichment.mode", value);
        localStorage.setItem("enrichment.theme", "light");
    }, mode);
    await page.goto("/");
    await expect(page.getByTestId("category-tiles")).toBeVisible();
}

test("filters the catalog by a category and opens a card in place", async ({
    page,
}, testInfo) => {
    await openOverview(page, "basic");
    const tiles = page.getByTestId("category-tiles");
    const groups = page.locator(".capability-group");
    await expect(groups.first()).toBeVisible();
    const everything = await groups.count();
    expect(everything).toBeGreaterThan(2);
    await shot(page, testInfo, "catalog-basic.png");

    // Choose one category: only its group stays.
    const distances = tiles.getByRole("button", { name: /^Distances/ });
    await distances.click();
    await expect(distances).toHaveAttribute("aria-pressed", "true");
    await expect(groups).toHaveCount(1);
    await expect(groups.first()).toHaveAttribute("data-category", "distance");

    // Open a card: it stays on the page.
    const url = page.url();
    const card = page.locator('[data-capability="geodesic_distance"]');
    const toggle = card.getByRole("button", { name: /^Details/ });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(card.getByText("What it needs")).toBeVisible();
    await expect(card.getByText("What it gives")).toBeVisible();
    await expect(card.getByTestId("capability-contract")).toHaveCount(0);
    expect(page.url()).toBe(url);
    await shot(page, testInfo, "catalog-filtered-open.png");

    // Pressing the tile again shows everything.
    await distances.click();
    await expect(groups).toHaveCount(everything);
});

test("shows the contract in Advanced", async ({ page }, testInfo) => {
    await openOverview(page, "advanced");
    const card = page.locator('[data-capability="geocode"]');
    await card.getByRole("button", { name: /^Details/ }).click();
    await expect(card.getByTestId("capability-contract")).toBeVisible();
    await shot(
        page,
        testInfo,
        "catalog-advanced.png",
        '[data-capability="geocode"]',
    );
});

test("reads in Ukrainian", async ({ page }, testInfo) => {
    await openOverview(page, "basic");
    await page.getByLabel("Language").selectOption("uk");
    await expect(page.locator("html")).toHaveAttribute("lang", "uk");
    await page
        .locator('[data-capability="geocode"]')
        .getByRole("button", { name: /^Докладніше/ })
        .click();
    await expect(page.getByText("Що потрібно").first()).toBeVisible();
    await shot(page, testInfo, "catalog-uk.png");
});

test("fits a narrow screen", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openOverview(page, "basic");
    await page
        .locator('[data-capability="geocode"]')
        .getByRole("button", { name: /^Details/ })
        .click();
    const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await shot(page, testInfo, "catalog-narrow.png");
});
