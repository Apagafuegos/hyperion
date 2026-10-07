// Rehearse the recommended sequence against a running fixture console.
import { chromium } from "@playwright/test";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:8792").origin;
const output = path.resolve(".portfolio-demo");
const temporary = await mkdtemp(path.join(tmpdir(), "hyperion-recording-"));
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: temporary, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
const video = page.video();
let completed = false;
try {
  await page.goto(`${origin}/overview`);
  await page.locator('.attention-row[href="/atlas#service=authentik"]').waitFor();
  await page.evaluate(() => document.fonts.ready);
  const start = Date.now();
  const holdUntil = async seconds => {
    const remaining = start + seconds * 1000 - Date.now();
    if (remaining > 0) await page.waitForTimeout(remaining);
  };
  const shot = name => page.screenshot({ path: path.join(output, `${name}.png`) });
  await shot("01-overview");
  await holdUntil(7);

  await page.locator('.attention-row[href="/atlas#service=authentik"]').click();
  await page.locator('.dossier[data-dossier-for="authentik"] button').waitFor();
  await page.locator("#search").fill("authentik");
  await page.waitForTimeout(450);
  await shot("02-evidence");
  await holdUntil(17);

  await page.getByRole("button", { name: "View logs", exact: true }).click();
  await page.locator(".log-row").first().waitFor();
  await page.getByRole("combobox", { name: "Log severity" }).selectOption("warning");
  await page.waitForTimeout(450);
  await shot("03-logs");
  await holdUntil(28);

  await page.locator("#log-drawer summary").click();
  await page.keyboard.press("Control+k");
  await page.locator("#search").fill("vault");
  await page.getByRole("link", { name: "Copy The Vault endpoint" }).click();
  await page.getByText("Endpoint copied", { exact: true }).waitFor();
  await page.waitForTimeout(450);
  await shot("04-endpoint");
  await holdUntil(38);

  await page.getByRole("link", { name: "Schedules", exact: true }).click();
  await page.locator('.schedule-row[data-schedule-id="backup"] .schedule-select').click();
  await page.waitForTimeout(450);
  await shot("05-schedule");
  await holdUntil(50);

  await page.getByRole("link", { name: "Activity", exact: true }).click();
  await page.locator(".activity-record").first().waitFor();
  await page.locator("#search").fill("restart");
  await shot("06-activity");
  await holdUntil(57);
  await page.locator("#search").fill("");
  await holdUntil(60);
  completed = true;
} finally {
  await context.close();
  if (completed && video) await video.saveAs(path.join(output, "hyperion-demo.webm"));
  await browser.close();
  await rm(temporary, { recursive: true, force: true });
}
if (completed) {
  await writeFile(path.join(output, "captions.srt"), `1
00:00:00,000 --> 00:00:07,000
One place for everything deployed on my VPS.

2
00:00:07,000 --> 00:00:17,000
From current attention to route and runtime evidence.

3
00:00:17,000 --> 00:00:28,000
Inspect logs and isolate warnings and errors.

4
00:00:28,000 --> 00:00:38,000
Find a service and copy its endpoint in seconds.

5
00:00:38,000 --> 00:00:50,000
See what runs next, its owner, and its last result.

6
00:00:50,000 --> 00:01:00,000
Search the history of changes and operator requests.
`);
  console.log(`Silent walkthrough: ${path.join(output, "hyperion-demo.webm")}`);
  console.log(`Suggested captions: ${path.join(output, "captions.srt")}`);
}
