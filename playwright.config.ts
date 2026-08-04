import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/browser",
  fullyParallel: true,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8787",
    extraHTTPHeaders: { "X-Authentik-Username": "test-owner" },
  },
  webServer: {
    command:
      "HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1",
    url: "http://127.0.0.1:8787/healthz",
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
