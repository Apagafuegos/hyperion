import { expect, test } from "@playwright/test";

test("Overview renders current evidence while schedule inventory is still pending", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/schedules", async route => { await held; await route.continue(); });
  try {
    await page.goto("/overview");
    await expect(page.locator(".overview-identity strong")).toHaveText("meridian-vps");
    await expect(page.locator(".bearing-row")).toHaveCount(10);
    await expect(page.locator('[data-overview-region="schedules"]')).toContainText("Reading upcoming schedules");
    await expect(page.locator('.attention-row[href="/atlas#service=authentik"]')).toContainText("Degraded");
    // Current state wins over an old activity record which claimed it was down.
    await expect(page.locator('.attention-row[href="/atlas#service=mcp-observatory"]')).toContainText("Degraded");
    await expect(page.locator('.attention-row[href="#host-condition"]')).toContainText("90% full");
    await expect(page.locator(".overview-identity")).not.toContainText("Unknown");
  } finally { release(); }
  await expect(page.locator(".overview-schedule")).toHaveCount(3);
});

test("missing host readings remain unavailable rather than becoming undefined or zero", async ({ page }) => {
  await page.route("**/api/v1/host", async route => {
    const response = await route.fetch();
    const host = await response.json();
    host.cpu.utilizationPercent = null;
    host.interfaces.forEach((network: { rxBytesPerSecond: number | null; txBytesPerSecond: number | null }) => {
      network.rxBytesPerSecond = null;
      network.txBytesPerSecond = null;
    });
    await route.fulfill({ json: host });
  });
  await page.goto("/overview");
  await expect(page.locator(".overview-identity strong")).toHaveText("meridian-vps");
  const cpu = page.locator(".instrument").filter({ hasText: "CPU" });
  const network = page.locator(".instrument").filter({ hasText: "Network" });
  await expect(cpu.locator(".instrument-value")).toHaveText("—");
  await expect(cpu.locator(".instrument-state")).toHaveText("Unavailable");
  await expect(network.locator(".instrument-value")).toHaveText("—");
  await expect(network.locator(".instrument-support")).not.toContainText("0 B");
});

test("Overview search and schedule links lead directly to the selected record", async ({ page }) => {
  await page.goto("/overview");
  await expect(page.locator(".overview-schedule")).toHaveCount(3);
  await page.locator("#search").fill("backup");
  await expect(page.locator(".overview-schedule")).toHaveCount(1);
  await expect(page.locator(".bearing-row")).toHaveCount(0);
  await page.locator(".overview-schedule").click();
  await expect(page).toHaveURL(/\/schedules#schedule=backup$/);
  await expect(page.locator(".schedule-dossier")).toContainText("backup.service");
  await expect(page.locator('.schedule-row[data-schedule-id="backup"] .status')).toHaveText("Successful");
});

test("attention to service evidence to logs is one continuous path", async ({ page }) => {
  await page.goto("/overview");
  await page.locator('.attention-row[href="/atlas#service=authentik"]').click();
  await expect(page.locator('.dossier[data-dossier-for="authentik"]')).toBeVisible();
  await expect(page.locator('.dossier[data-dossier-for="authentik"]')).toContainText("1410 ms");
  await page.getByRole("button", { name: "View logs", exact: true }).click();
  await expect(page.locator("#log-drawer")).toHaveAttribute("open", "");
  await expect(page.locator(".log-row")).toHaveCount(3);
  await expect(page.locator("#log-rows")).toContainText("rate limiting burst exceeded");
});

test("closing and reopening logs reuses the pending request", async ({ page }) => {
  let requests = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/services/authentik/logs?*", async route => {
    requests++;
    await held;
    await route.continue();
  });
  try {
    await page.goto("/atlas#service=authentik");
    await page.getByRole("button", { name: "View logs", exact: true }).click();
    await expect(page.locator("#log-state")).toHaveText("Loading logs…");
    await page.locator("#log-drawer summary").click();
    await page.locator("#log-drawer summary").click();
    expect(requests).toBe(1);
  } finally { release(); }
  await expect(page.locator(".log-row")).toHaveCount(3);
});

test("search and copy remains immediate after inspecting a different service", async ({ page }) => {
  await page.goto("/atlas#service=authentik");
  await page.locator("#search").fill("vault");
  await expect(page.locator(".service-row").filter({ visible: true })).toHaveCount(1);
  await page.getByRole("link", { name: "Copy The Vault endpoint" }).click();
  await expect(page.locator("#toast")).toHaveText("Endpoint copied");
  await expect(page).toHaveURL(/#service=the-vault$/);
});

test("demo workspaces fit the viewport and show explicit result labels", async ({ page }) => {
  for (const workspace of ["overview", "atlas", "schedules", "activity"]) {
    await page.goto(`/${workspace}`);
    await expect(page.locator(workspace === "atlas" ? ".service-row" : workspace === "overview" ? ".bearing-row" : workspace === "schedules" ? ".schedule-row" : ".activity-record").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (workspace === "activity") {
      await expect(page.locator(".activity-record").filter({ hasText: "backup.timer executed" }).locator(".status")).toHaveText("Successful");
      await expect(page.locator(".activity-record").filter({ hasText: "restart requested" }).locator(".status")).toHaveText("Information");
    }
  }
});

test("Atlas polling includes catalog changes and clears a removed selection", async ({ page }) => {
  let stage = 0;
  await page.route("**/api/v1/snapshot", async route => {
    const response = await route.fetch({ headers: { ...route.request().headers(), "if-none-match": "" } });
    const snapshot = await response.json();
    if (stage === 1) {
      snapshot.services.push({ ...snapshot.services[0], id: "new-service", name: "New Service", territory: "services" });
    }
    snapshot.catalogRevision = `demo-revision-${stage}`;
    await route.fulfill({ json: snapshot, headers: { etag: `"demo-${stage}"` } });
  });
  await page.goto("/atlas");
  await expect(page.locator(".service-row")).toHaveCount(10);
  await page.locator("#search").fill("New Service");
  await expect(page.getByText("No services found")).toBeVisible();
  stage = 1;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator('.service-row[data-service-id="new-service"]')).toBeVisible();
  await expect(page).toHaveURL(/#service=new-service$/);
  stage = 2;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator('.service-row[data-service-id="new-service"]')).toHaveCount(0);
  await expect(page.getByText("No services found")).toBeVisible();
  await expect(page.locator("#log-subject")).toHaveText("Select a service to inspect its logs.");
  await expect(page).toHaveURL(/\/atlas$/);
});
