import { defineConfig } from "@playwright/test";

// The hardened systemd unit binds 8787 on the VPS, so the browser suite's
// fixture server must be able to move. Run it elsewhere with:
//   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8790 npx playwright test
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:8787";
const port = new URL(baseURL).port;

export default defineConfig({
  testDir: "tests/browser",
  fullyParallel: true,
  timeout: 30_000,
  use: {
    baseURL,
    extraHTTPHeaders: { "X-Authentik-Username": "test-owner" },
  },
  webServer: {
    command: `HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml uv run uvicorn hyperion.main:app --host 127.0.0.1 --port ${port} --workers 1`,
    url: `${baseURL}/healthz`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, hasTouch: true } },
    {
      name: "reduced-motion",
      use: { viewport: { width: 1440, height: 900 }, contextOptions: { reducedMotion: "reduce" } },
    },
  ],
});
