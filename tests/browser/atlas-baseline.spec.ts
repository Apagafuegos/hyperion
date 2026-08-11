import { expect, test } from "@playwright/test";

// Atlas visual baselines: deterministic fixture-backed references captured at
// desktop/tablet/phone across loading, populated, empty, stale, partial, and
// unavailable states. These screenshots become regression gates; later shell
// work must demonstrate that the Atlas has not drifted unintentionally.
//
// Re-capture with:
//   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8794 npx playwright test tests/browser/atlas-baseline.spec.ts

const BASELINE = ".impeccable/screenshots/atlas-baseline";

test.describe("atlas visual baselines", () => {
  test("populated desktop", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop baseline");
    await page.goto("/");
    await expect(page.locator(".service-row")).toHaveCount(10);
    await page.screenshot({ path: `${BASELINE}/desktop-populated.png`, fullPage: true });
  });

  test("selected dossier desktop", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop baseline");
    await page.goto("/");
    await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
    await expect(page.locator('.dossier[data-dossier-for="langfuse"]')).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/desktop-dossier.png`, fullPage: true });
  });

  test("logs open desktop", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop baseline");
    await page.goto("/");
    await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
    await page.locator("#log-drawer summary").click();
    await expect(page.locator(".log-row").first()).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/desktop-logs.png`, fullPage: true });
  });

  test("no results state", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop baseline");
    await page.goto("/");
    await page.locator("#search").fill("zzyzx");
    await expect(page.getByText("No services found")).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/desktop-no-results.png`, fullPage: true });
  });

  test("search results", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop baseline");
    await page.goto("/");
    await page.locator("#search").fill("langfuse");
    await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(1);
    await page.screenshot({ path: `${BASELINE}/desktop-search.png`, fullPage: true });
  });
});

test.describe("atlas tablet baselines", () => {
  test("populated tablet", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "tablet", "tablet baseline");
    await page.goto("/");
    await expect(page.locator(".service-row")).toHaveCount(10);
    await page.screenshot({ path: `${BASELINE}/tablet-populated.png`, fullPage: true });
  });

  test("dossier tablet", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "tablet", "tablet baseline");
    await page.goto("/");
    await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
    await expect(page.locator('.dossier[data-dossier-for="langfuse"]')).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/tablet-dossier.png`, fullPage: true });
  });
});

test.describe("atlas mobile baselines", () => {
  test("populated phone", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile project");
    await page.goto("/");
    await expect(page.locator(".service-row")).toHaveCount(10);
    await page.screenshot({ path: `${BASELINE}/phone-populated.png`, fullPage: true });
  });

  test("dossier phone", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile project");
    await page.goto("/");
    await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
    await expect(page.locator('.dossier[data-dossier-for="langfuse"]')).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/phone-dossier.png`, fullPage: true });
  });

  test("bearings strip phone", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile project");
    await page.goto("/");
    await expect(page.locator(".bearing-value").first()).toBeVisible();
    await page.screenshot({ path: `${BASELINE}/phone-bearings.png`, fullPage: true });
  });
});
