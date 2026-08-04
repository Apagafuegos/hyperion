export type ServiceState = "reachable" | "degraded" | "down" | "dormant" | "unknown";
export type ComponentState = "running" | "starting" | "restarting" | "paused" | "stopped" | "missing" | "unknown";
export type HealthState = "healthy" | "starting" | "unhealthy" | "unconfigured" | "unknown";
export type TerritoryId = "applications" | "services" | "foundations";
export type Severity = "debug" | "info" | "notice" | "warning" | "error" | "critical" | "alert" | "emergency";

export interface ServiceActionOpen { type: "open"; url: string }
export interface ServiceActionCopy { type: "copy"; url: string }
export interface ServiceActionNone { type: "none" }
export type ServiceAction = ServiceActionOpen | ServiceActionCopy | ServiceActionNone;

export interface StateReason {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  componentKey: string | null;
}

export interface RouteSnapshot {
  state: "reachable" | "slow" | "failed" | "unknown";
  statusCode: number | null;
  latencyMs: number | null;
  consecutiveFailures: number;
  observedAt: string | null;
  error: "dns" | "timeout" | "tls" | "connection" | "http" | "unknown" | null;
}

export interface ComponentSnapshot {
  key: string;
  label: string;
  provider: "docker" | "systemd";
  providerRef: string | null;
  role: "primary" | "worker" | "dependency" | "sidecar" | "shared";
  required: boolean;
  state: ComponentState;
  health: HealthState;
  observedAt: string | null;
  image: string | null;
  uptimeSeconds: number | null;
  restartCount: number | null;
  cpuPercent: number | null;
  memoryBytes: number | null;
}

export interface DependencySnapshot { serviceId: string; state: ServiceState }
export interface LogSource { key: string; label: string; provider: "docker" | "journald"; available: boolean }

export interface ServiceSnapshot {
  id: string;
  name: string;
  description: string;
  territory: TerritoryId;
  kind: "web" | "api" | "mcp" | "worker" | "infrastructure";
  intent: "active" | "dormant";
  action: ServiceAction;
  state: ServiceState;
  stateReasons: StateReason[];
  observedAt: string;
  route: RouteSnapshot | null;
  components: ComponentSnapshot[];
  dependencies: DependencySnapshot[];
  logSources: LogSource[];
}

export interface ProviderStatus {
  provider: "docker" | "systemd" | "probe";
  state: "available" | "degraded" | "unavailable";
  observedAt: string | null;
  message: string | null;
}

export interface StateSummary {
  total: number; reachable: number; degraded: number; down: number; dormant: number; unknown: number;
}

export interface UnmappedRuntime {
  provider: "docker" | "systemd";
  reference: string;
  project: string | null;
  component: string | null;
  state: string;
}

export interface Diagnostics { unmappedRuntimes: UnmappedRuntime[]; warnings: string[] }

export interface Snapshot {
  schemaVersion: 1;
  generatedAt: string;
  catalogRevision: string;
  fresh: boolean;
  providers: ProviderStatus[];
  summary: StateSummary;
  territories: { id: TerritoryId; label: string; order: number }[];
  services: ServiceSnapshot[];
  diagnostics: Diagnostics;
}

export interface LogRecord {
  timestamp: string | null;
  source: string;
  provider: "docker" | "journald";
  stream: "stdout" | "stderr" | "journal" | "unknown";
  severity: Severity | null;
  message: string;
  truncated: boolean;
}

export interface LogsResponse {
  serviceId: string;
  requestedAt: string;
  source: string | null;
  records: LogRecord[];
  truncated: boolean;
}

export type SnapshotResult =
  | { status: "ok"; snapshot: Snapshot; etag: string }
  | { status: "not-modified" }
  | { status: "error"; message: string };

export type LogsResult =
  | { status: "ok"; logs: LogsResponse }
  | { status: "error"; code: string; message: string; retryable: boolean };

export async function fetchSnapshot(etag: string | null): Promise<SnapshotResult> {
  try {
    const headers: Record<string, string> = {};
    if (etag !== null) headers["If-None-Match"] = etag;
    const response = await fetch("/api/v1/snapshot", { headers, cache: "no-store" });
    if (response.status === 304) return { status: "not-modified" };
    if (!response.ok) return { status: "error", message: `Snapshot request failed (${response.status}).` };
    const snapshot = (await response.json()) as Snapshot;
    return { status: "ok", snapshot, etag: response.headers.get("etag") ?? "" };
  } catch (error) {
    return { status: "error", message: error instanceof Error ? error.message : "Network failure." };
  }
}

export async function fetchLogs(
  serviceId: string,
  source: string | null,
  tail: number,
): Promise<LogsResult> {
  try {
    const params = new URLSearchParams({ tail: String(tail) });
    if (source !== null) params.set("source", source);
    const response = await fetch(
      `/api/v1/services/${encodeURIComponent(serviceId)}/logs?${params}`,
      { cache: "no-store" },
    );
    const body = (await response.json()) as
      | { error?: { code?: string; message?: string; retryable?: boolean } }
      | LogsResponse;
    if (!response.ok) {
      const err = "error" in (body as object)
        ? (body as { error: { code: string; message: string; retryable: boolean } }).error
        : null;
      return {
        status: "error",
        code: err?.code ?? "UNKNOWN",
        message: err?.message ?? "Logs request failed.",
        retryable: err?.retryable ?? false,
      };
    }
    return { status: "ok", logs: body as LogsResponse };
  } catch (error) {
    return {
      status: "error",
      code: "NETWORK",
      message: error instanceof Error ? error.message : "Network failure.",
      retryable: true,
    };
  }
}
