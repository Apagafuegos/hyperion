import { expect, test } from "@playwright/test";

test.describe("workspace shell navigation", () => {
  test("global nav exposes all five workspaces", async ({ page }) => {
    await page.goto("/overview");
    await expect(page.locator(".global-nav .nav-destination")).toHaveCount(5);
    await expect(page.locator('.global-nav a[href="/overview"]')).toHaveClass(/active/);
  });

  test("each workspace route renders its statement", async ({ page }) => {
    const routes: [string, RegExp][] = [
      ["/overview", /One glance at the VPS/],
      ["/atlas", /mapped to one calm surface/],
      ["/schedules", /What is scheduled to run/],
      ["/units", /systemd inventory/],
      ["/activity", /What changed/],
    ];
    for (const [path, statement] of routes) {
      await page.goto(path);
      await expect(page.locator(".workspace-statement")).toContainText(statement);
    }
  });

  test("Ctrl+K focuses the workspace search", async ({ page }) => {
    await page.goto("/schedules");
    await page.keyboard.press("Control+k");
    await expect(page.locator("#search")).toBeFocused();
  });
});

test.describe("overview workspace", () => {
  test("renders host instruments and attention", async ({ page }) => {
    await page.goto("/overview");
    await expect(page.locator(".overview-identity strong")).toHaveText("meridian-vps");
    await expect(page.locator(".instrument")).toHaveCount(4);
    await expect(page.locator(".attention-band")).toBeVisible();
    await expect(page.locator(".attention-row").first()).toBeVisible();
  });

  test("overview links service bearings to the atlas", async ({ page }) => {
    await page.goto("/overview");
    const bearing = page.locator(".bearing-row").first();
    await expect(bearing).toBeVisible();
    await expect(bearing).toHaveAttribute("href", /\/atlas#service=/);
  });
});

test.describe("schedules workspace", () => {
  test("renders unified schedule index with provenance", async ({ page }) => {
    await page.goto("/schedules");
    await expect(page.locator(".schedule-row")).toHaveCount(3);
    await expect(page.locator(".provenance-systemd")).toHaveCount(1);
    await expect(page.locator(".provenance-cron")).toHaveCount(2);
    await expect(page.locator(".next-run-timeline")).toBeVisible();
  });

  test("selecting a schedule opens its dossier and updates the URL", async ({ page }) => {
    await page.goto("/schedules");
    const row = page.locator(".schedule-row").first();
    await row.locator(".schedule-select").click();
    const dossier = row.locator(".schedule-dossier");
    await expect(dossier).toBeVisible();
    await expect(page).toHaveURL(/#schedule=/);

    const [rowBox, dossierBox] = await Promise.all([row.boundingBox(), dossier.boundingBox()]);
    expect(rowBox).not.toBeNull();
    expect(dossierBox).not.toBeNull();
    expect(dossierBox!.width).toBeGreaterThan(rowBox!.width * 0.65);
  });
});

test.describe("units workspace", () => {
  test("renders curated inventory with dossiers and operations", async ({ page }) => {
    await page.goto("/units");
    await expect(page.locator(".unit-row")).toHaveCount(6);
    await expect(page.locator(".filter-bar")).toBeVisible();
  });

  test("selecting a unit opens its dossier with protected classification", async ({ page }) => {
    await page.goto("/units");
    await page.locator('.unit-row[data-unit="caddy.service"] .unit-select').click();
    await expect(page.locator(".unit-dossier")).toBeVisible();
    await expect(page.locator(".unit-dossier")).toContainText("protected");
    await expect(page.locator(".operation-note.denied")).toBeVisible();
  });

  test("all units filter reveals the full inventory", async ({ page }) => {
    await page.goto("/units");
    await page.getByRole("button", { name: "All units" }).click();
    await expect(page.locator(".unit-row")).toHaveCount(7);
  });

  test("operation request opens the confirmation surface", async ({ page }) => {
    await page.goto("/units");
    await page.locator('.unit-row[data-unit="t3code.service"] .unit-select').click();
    await page.getByRole("button", { name: "Restart", exact: true }).click();
    await expect(page.locator("#confirmation")).toBeVisible();
    await expect(page.locator("#confirmation-title")).toContainText("Restart t3code.service");
    await page.locator("#confirmation-cancel").click();
    await expect(page.locator("#confirmation")).toBeHidden();
  });
});

test.describe("activity workspace", () => {
  test("renders day-grouped activity", async ({ page }) => {
    await page.goto("/activity");
    await expect(page.locator(".activity-record").first()).toBeVisible();
    await expect(page.locator(".activity-day").first()).toBeVisible();
  });
});
