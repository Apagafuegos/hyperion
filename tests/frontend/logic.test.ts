import { describe, expect, it } from "vitest";
import type { ServiceSnapshot, Snapshot } from "../../frontend/api";
import {
  filterVisible,
  formatBytes,
  formatLatency,
  formatObserved,
  formatUptime,
  matchesQuery,
  pickSelection,
  severityTone,
  stateLabel,
} from "../../frontend/logic";

function service(overrides: Partial<ServiceSnapshot>): ServiceSnapshot {
  return {
    id: "s1",
    name: "Alpha Service",
    description: "Public gateway",
    territory: "applications",
    kind: "web",
    intent: "active",
    action: { type: "open", url: "https://alpha.example.com" },
    state: "reachable",
    stateReasons: [],
    observedAt: "2026-08-04T06:00:00Z",
    route: null,
    components: [
      { key: "app", label: "app", provider: "docker", providerRef: null, role: "primary", required: true, state: "running", health: "healthy", observedAt: null, image: "ghcr.io/alpha/app:1", uptimeSeconds: 3600, restartCount: 0, cpuPercent: null, memoryBytes: null },
    ],
    dependencies: [],
    logSources: [],
    ...overrides,
  };
}

function snapshot(services: ServiceSnapshot[]): Snapshot {
  return {
    schemaVersion: 1,
    generatedAt: "2026-08-04T06:00:00Z",
    catalogRevision: "r",
    fresh: true,
    providers: [],
    summary: { total: services.length, reachable: 0, degraded: 0, down: 0, dormant: 0, unknown: 0 },
    territories: [],
    services,
    diagnostics: { unmappedRuntimes: [], warnings: [] },
  };
}

describe("matchesQuery", () => {
  it("matches name, description, endpoint, kind, and component image", () => {
    const s = service({});
    expect(matchesQuery(s, "alpha")).toBe(true);
    expect(matchesQuery(s, "gateway")).toBe(true);
    expect(matchesQuery(s, "example.com")).toBe(true);
    expect(matchesQuery(s, "ghcr.io/alpha/app")).toBe(true);
    expect(matchesQuery(s, "nonexistent")).toBe(false);
    expect(matchesQuery(s, "")).toBe(true);
  });
});

describe("filterVisible", () => {
  it("filters the visible id set", () => {
    const services = [service({ id: "a", name: "Alpha" }), service({ id: "b", name: "Beta" })];
    expect(filterVisible(services, "beta")).toEqual(new Set(["b"]));
  });
});

describe("pickSelection", () => {
  const services = [service({ id: "a" }), service({ id: "b" })];
  it("keeps the previous selection when still visible", () => {
    expect(pickSelection("a", null, services, new Set(["a", "b"]))).toBe("a");
  });
  it("falls back to the first visible when the previous is filtered out", () => {
    expect(pickSelection("a", null, services, new Set(["b"]))).toBe("b");
  });
  it("prefers the restored prior selection on clearing search", () => {
    expect(pickSelection(null, "a", services, new Set(["a", "b"]))).toBe("a");
  });
  it("returns null when nothing is visible", () => {
    expect(pickSelection("a", null, services, new Set())).toBeNull();
  });
});

describe("formatters", () => {
  it("formats uptime", () => {
    expect(formatUptime(null)).toBe("—");
    expect(formatUptime(30)).toBe("30s");
    expect(formatUptime(3600)).toBe("1h");
    expect(formatUptime(90000)).toBe("1d");
  });
  it("formats latency", () => {
    expect(formatLatency(210)).toBe("210 ms");
    expect(formatLatency(null)).toBe("—");
  });
  it("formats observed times", () => {
    const now = Date.parse("2026-08-04T06:00:05Z");
    expect(formatObserved("2026-08-04T06:00:00Z", now)).toBe("just now");
    expect(formatObserved("2026-08-04T05:59:00Z", now)).toBe("1m ago");
    expect(formatObserved(null, now)).toBe("never");
  });
  it("formats bytes", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(1024)).toBe("1.0 KiB");
    expect(formatBytes(157286400)).toBe("150.0 MiB");
  });
});

describe("state and severity labels", () => {
  it("labels states", () => {
    expect(stateLabel("reachable")).toBe("Reachable");
    expect(stateLabel("weird")).toBe("Unknown");
  });
  it("tones severities", () => {
    expect(severityTone("info")).toBe("neutral");
    expect(severityTone("warning")).toBe("warning");
    expect(severityTone("error")).toBe("critical");
    expect(severityTone(null)).toBe("neutral");
  });
});

describe("snapshot", () => {
  it("builds a minimal snapshot", () => {
    expect(snapshot([service({})]).services.length).toBe(1);
  });
});
