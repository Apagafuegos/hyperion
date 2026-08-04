import { expect, test } from "@playwright/test";

test("atlas renders all territories and service states", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Applications" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Services" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Foundations" })).toBeVisible();
  await expect(page.locator(".service-row")).toHaveCount(10);
  await expect(page.locator(".service-row .status-down")).toHaveCount(2);
  await expect(page.locator(".service-row .status-degraded")).toHaveCount(3);
  await expect(page.locator(".service-row .status-dormant")).toHaveCount(1);
  await expect(page.locator(".service-row .status-reachable")).toHaveCount(4);
});

test("search filters services and restores selection", async ({ page }) => {
  await page.goto("/");
  await page.locator("#search").fill("langfuse");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(1);
  await expect(page.locator(".service-row").filter({ visible: true }).locator(".service-name")).toHaveText("Langfuse");
  await page.locator("#search").fill("zzyzx");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(0);
  await page.locator("#search").fill("");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(10);
});

test("selecting a row opens the dossier inline and preserves atlas topology", async ({ page }) => {
  await page.goto("/");
  const row = page.locator('.service-row[data-service-id="langfuse"]');
  await row.locator(".service-select").click();
  await expect(page.locator('.dossier[data-dossier-for="langfuse"]')).toBeVisible();
  await expect(page).toHaveURL(/#service=langfuse/);
  await expect(page.locator(".dossier h3").first()).toHaveText("Route evidence");
});

test("dossier patches in place and marks the row selected", async ({ page }) => {
  await page.goto("/");
  await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
  const dossier = page.locator('.dossier[data-dossier-for="langfuse"]');
  await expect(dossier).toBeVisible();
  await expect(dossier.locator("h3").first()).toHaveText("Route evidence");
  await expect(page.locator('.service-row[data-service-id="langfuse"]')).toHaveClass(/selected/);
});

test("logs drawer loads bounded records with controls", async ({ page }) => {
  await page.goto("/");
  await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
  await page.locator("#log-drawer summary").click();
  await expect(page.locator(".log-row").first()).toBeVisible();
  await expect(page.locator(".log-row").first()).toContainText("GET /api/v3/core/users/");
  await page.locator("#log-source").selectOption("worker");
  await expect(page.locator(".log-row code").first()).toHaveText("worker");
});

test("copy action shows toast and writes to clipboard", async ({ page }) => {
  await page.goto("/");
  const vault = page.locator('.service-row[data-service-id="the-vault"] .copy-action');
  await expect(vault).toBeVisible();
  await vault.click();
  await expect(page.locator("#toast")).toBeVisible();
  await expect(page.locator("#toast")).toHaveText("Endpoint copied");
});

test("bearings count service states and unmapped runtimes are diagnostic only", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".bearing-reachable .bearing-value")).toHaveText("4");
  await expect(page.locator(".bearing-degraded .bearing-value")).toHaveText("3");
  await expect(page.locator(".bearing-down .bearing-value")).toHaveText("2");
  await expect(page.locator(".bearing-dormant .bearing-value")).toHaveText("1");
  await expect(page.locator("#diagnostics-keycap")).toHaveText("3 unmapped runtimes");
});

test("keyboard focus is visible and Ctrl/Cmd+K focuses search", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");
  await expect(page.locator("#search")).toBeFocused();
});

test("mobile: bearings become a strip and dossier stacks", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile layout project");
  await page.goto("/");
  await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
  await expect(page.locator('.dossier[data-dossier-for="langfuse"] h3').first()).toBeVisible();
  const grid = await page.locator(".atlas-frame").evaluate((el) => getComputedStyle(el).gridTemplateColumns);
  expect(grid.split(" ").length).toBe(1);
});

test("reduced motion: dossier reveal is effectively instant", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "reduced-motion", "reduced motion project");
  await page.goto("/");
  await page.locator('.service-row[data-service-id="langfuse"] .service-select').click();
  const duration = await page
    .locator('.dossier[data-dossier-for="langfuse"]')
    .evaluate((el) => getComputedStyle(el).animationDuration);
  expect(parseFloat(duration)).toBeLessThan(0.01);
});
