import { expect, test } from "@playwright/test";
import type { LogRecord } from "../../frontend/api";

const timestamp = "2026-10-06T12:02:45Z";
const records: LogRecord[] = [
  { timestamp, source: "server", provider: "docker", stream: "stdout", severity: "info", message: "Request completed", truncated: false },
  { timestamp, source: "worker", provider: "docker", stream: "stdout", severity: "warning", message: "Retrying request", truncated: false },
  { timestamp, source: "server", provider: "docker", stream: "stderr", severity: "error", message: "Error: address already in use\n    at Server.listen (node:net:2328:16)\n    <script>alert('unsafe')</script>", truncated: true },
  { timestamp: null, source: "worker", provider: "docker", stream: "stderr", severity: "emergency", message: "Worker unavailable", truncated: false },
];

async function openLogs(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/atlas");
  await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
  await page.locator("#log-drawer summary").click();
  await expect(page.locator(".log-row").first()).toBeVisible();
}

test("logs load on demand and the disclosure describes its current action", async ({ page }) => {
  let requests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/services/authentik/logs")) requests++;
  });
  await page.goto("/atlas");
  await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
  expect(requests).toBe(0);
  await expect(page.locator("#log-toggle-label")).toHaveText("Open logs");
  await page.locator("#log-drawer summary").click();
  await expect(page.locator(".log-row")).toHaveCount(3);
  expect(requests).toBe(1);
  await expect(page.locator("#log-toggle-label")).toHaveText("Close logs");
});

test("search and severity filters preserve multiline text and report truncation", async ({ page }) => {
  await page.route("**/services/authentik/logs?*", (route) => route.fulfill({
    json: { serviceId: "authentik", requestedAt: timestamp, records, truncated: true, source: null },
  }));
  await openLogs(page);
  await expect(page.locator(".log-row")).toHaveCount(4);
  await expect(page.locator(".log-console-footer")).toContainText("Output limit reached");
  await page.getByRole("combobox", { name: "Log severity" }).selectOption("error");
  await expect(page.locator(".log-row")).toHaveCount(2);
  await page.getByRole("searchbox", { name: "Find in service logs" }).fill("address");
  await expect(page.locator(".log-row")).toHaveCount(1);
  await expect(page.locator(".log-message")).toContainText("\n    at Server.listen");
  await expect(page.locator(".log-message")).toContainText("<script>alert('unsafe')</script>");
  await expect(page.locator(".log-message script")).toHaveCount(0);
  await expect(page.locator(".log-message")).toContainText("[record shortened]");
  await expect(page.locator(".log-console-footer")).toContainText("1 of 4 records");
  await page.getByRole("searchbox", { name: "Find in service logs" }).fill("absent");
  await expect(page.locator(".log-empty")).toContainText("No records match");
  await expect(page.getByRole("button", { name: "Copy logs" })).toBeDisabled();
});

test("copy exports the filtered records with metadata and preserved indentation", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { value: {
      writeText: async (text: string) => { document.documentElement.dataset.copied = text; },
    } });
  });
  await openLogs(page);
  await page.getByRole("combobox", { name: "Log severity" }).selectOption("error");
  await page.getByRole("button", { name: "Copy logs" }).click();
  await expect(page.locator(".log-console-footer")).toContainText("Logs copied");
  const copied = await page.locator("html").getAttribute("data-copied");
  expect(copied).toContain("ERROR [server] rate limiting burst exceeded");
  expect(copied).not.toContain("GET /api/v3/core/users/");
});

test("long lines wrap without page overflow and remain readable without wrapping", async ({ page }) => {
  await page.route("**/services/authentik/logs?*", (route) => route.fulfill({
    json: { serviceId: "authentik", requestedAt: timestamp, records: [{ ...records[0], message: `    at ${"long.stack.trace.".repeat(60)}` }], truncated: false, source: null },
  }));
  await openLogs(page);
  await expect(page.getByRole("button", { name: "Wrap lines" })).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator("#log-rows").evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Wrap lines" }).click();
  expect(await page.locator("#log-rows").evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator(".log-level").last()).toBeVisible();
  await expect(page.locator(".log-source-name")).toBeVisible();
});

test("a failed request can be retried and switching sources cannot show stale records", async ({ page }) => {
  let attempt = 0;
  await page.route("**/services/authentik/logs?*", (route) => {
    attempt++;
    return attempt === 1
      ? route.fulfill({ status: 503, json: { error: { message: "Logs unavailable", retryable: true } } })
      : route.continue();
  });
  await page.goto("/atlas");
  await page.locator('.service-row[data-service-id="authentik"] .service-select').click();
  await page.locator("#log-drawer summary").click();
  await expect(page.locator(".log-empty")).toContainText("unavailable");
  await page.locator("#log-refresh").click();
  await expect(page.locator(".log-row")).toHaveCount(3);
  await page.locator("#log-source").selectOption("worker");
  await expect(page.locator(".log-row")).toHaveCount(1);
  await expect(page.locator(".log-source-name")).toHaveText("worker");
});

test("unit journals show all fetched records and preserve filters during background refresh", async ({ page }) => {
  await page.clock.install();
  await page.route("**/units/t3code.service/logs?*", (route) => route.fulfill({
    json: { unit: "t3code.service", requestedAt: timestamp, records: Array.from({ length: 30 }, (_, i) => ({ ...records[0], message: `Journal entry ${i}` })), truncated: false },
  }));
  await page.goto("/units");
  await page.locator('.unit-row[data-unit="t3code.service"] .unit-select').click();
  await page.getByRole("button", { name: "Load journal" }).click();
  await expect(page.locator(".unit-journal .log-row")).toHaveCount(30);
  const search = page.getByRole("searchbox", { name: "Find in journal for t3code.service" });
  await search.fill("entry 29");
  await expect(page.locator(".unit-journal .log-row")).toHaveCount(1);
  const refreshed = page.waitForResponse((response) => response.url().endsWith("/api/v1/units"));
  await page.clock.fastForward(31_000);
  await refreshed;
  await expect(search).toHaveValue("entry 29");
  await expect(page.locator(".unit-journal .log-row")).toHaveCount(1);
  const journal = await page.locator(".unit-journal").boundingBox();
  const dossier = await page.locator(".unit-dossier").boundingBox();
  expect(journal!.width).toBeGreaterThan(dossier!.width * .9);
});
