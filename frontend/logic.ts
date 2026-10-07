import type { ServiceSnapshot, Snapshot, TerritoryId } from "./api";

export const TERRITORY_ORDER: TerritoryId[] = ["applications", "services", "foundations"];

export function matchesQuery(service: ServiceSnapshot, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const haystacks = [
    service.name,
    service.description,
    service.kind,
    service.territory,
    service.action.type === "none" ? "" : service.action.url,
    ...service.components.flatMap((c) => [c.label, c.image ?? ""]),
  ];
  return haystacks.some((text) => text.toLowerCase().includes(q));
}

export function filterVisible(services: ServiceSnapshot[], query: string): Set<string> {
  const visible = new Set<string>();
  for (const service of services) {
    if (matchesQuery(service, query)) visible.add(service.id);
  }
  return visible;
}

export function orderedTerritories(): TerritoryId[] {
  return [...TERRITORY_ORDER];
}

export function servicesByTerritory(snapshot: Snapshot, territory: TerritoryId): ServiceSnapshot[] {
  return snapshot.services.filter((s) => s.territory === territory);
}

export function pickSelection(
  previous: string | null,
  fallback: string | null,
  services: ServiceSnapshot[],
  visible: Set<string>,
): string | null {
  if (previous !== null && visible.has(previous)) return previous;
  if (fallback !== null && visible.has(fallback)) return fallback;
  const first = services.find((s) => visible.has(s.id));
  return first ? first.id : null;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "reachable": return "Reachable";
    case "degraded": return "Degraded";
    case "down": return "Down";
    case "dormant": return "Dormant";
    default: return "Unknown";
  }
}

export function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function formatLatency(ms: number | null): string {
  return ms === null ? "—" : `${ms} ms`;
}

export function formatObserved(iso: string | null, now: number): string {
  if (iso === null) return "never";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds <= 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function severityTone(severity: string | null): "critical" | "warning" | "neutral" {
  if (severity === null || severity === "debug" || severity === "info" || severity === "notice") {
    return "neutral";
  }
  if (severity === "warning") return "warning";
  return "critical";
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  return `${(bytes / 1024 ** 4).toFixed(1)} TiB`;
}
