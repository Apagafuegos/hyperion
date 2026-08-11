import { expect, test } from "@playwright/test";

// Operations-console workspace visual verification: capture each workspace at
// desktop and mobile widths into .impeccable/screenshots/ for the design pass.

const SHOT = ".impeccable/screenshots/workspaces";

test.describe("operations console visual capture", () => {
  for (const workspace of ["overview", "schedules", "units", "activity"]) {
    test(`desktop ${workspace}`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "desktop capture");
      await page.goto(`/${workspace}`);
      await expect(page.locator(".workspace-statement")).toBeVisible();
      await expect(page.locator(".band, .attention-band, .filter-bar").first()).toBeVisible();
      await page.waitForTimeout(200);
      await page.screenshot({ path: `${SHOT}/${workspace}-desktop.png`, fullPage: true });
    });

    test(`phone ${workspace}`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "mobile", "phone capture");
      await page.goto(`/${workspace}`);
      await expect(page.locator(".workspace-statement")).toBeVisible();
      await page.waitForTimeout(200);
      await page.screenshot({ path: `${SHOT}/${workspace}-phone.png`, fullPage: true });
    });
  }
});
