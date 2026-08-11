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

// --- Operations-console resources -------------------------------------------

export interface FilesystemEvidence {
  mountPoint: string;
  device: string;
  fstype: string;
  totalBytes: number | null;
  freeBytes: number | null;
  usedBytes: number | null;
  usedPercent: number | null;
  totalInodes: number | null;
  freeInodes: number | null;
  inodeUsedPercent: number | null;
  state: string;
}

export interface NetworkInterfaceEvidence {
  name: string;
  state: string;
  rxBytesTotal: number | null;
  txBytesTotal: number | null;
  rxBytesPerSecond: number | null;
  txBytesPerSecond: number | null;
}

export interface HostMemoryEvidence {
  totalBytes: number | null;
  availableBytes: number | null;
  usedBytes: number | null;
  usedPercent: number | null;
  swapTotalBytes: number | null;
  swapFreeBytes: number | null;
  swapUsedPercent: number | null;
}

export interface HostCpuEvidence {
  utilizationPercent: number | null;
  loadAverage1m: number | null;
  loadAverage5m: number | null;
  loadAverage15m: number | null;
}

export interface PressureEvidence {
  kind: string;
  someAvg10: number | null;
  someAvg300: number | null;
  fullAvg10: number | null;
}

export interface TemperatureEvidence { zone: string; celsius: number | null }

export interface HostEvidence {
  observedAt: string;
  fresh: boolean;
  hostname: string | null;
  uptimeSeconds: number | null;
  bootTime: string | null;
  cpu: HostCpuEvidence;
  memory: HostMemoryEvidence;
  filesystems: FilesystemEvidence[];
  interfaces: NetworkInterfaceEvidence[];
  pressure: PressureEvidence[];
  temperatures: TemperatureEvidence[];
  providerState: string;
  providerMessage: string | null;
}

export interface HostHistoryResponse {
  windowSeconds: number;
  observedAt: string;
  samples: HostEvidence[];
}

export interface UnitSnapshot {
  name: string;
  description: string;
  loadState: string;
  activeState: string;
  subState: string;
  enabledState: string;
  activeEntered: string | null;
  mainPid: number | null;
  restartCount: number | null;
  memoryBytes: number | null;
  relatedTimer: string | null;
  dependencies: string[];
  relatedService: string | null;
  protection: string;
  curated: boolean;
}

export interface UnitInventoryResponse {
  generatedAt: string;
  fresh: boolean;
  counts: Record<string, number>;
  units: UnitSnapshot[];
}

export interface UnitLogsResponse {
  unit: string;
  requestedAt: string;
  records: LogRecord[];
  truncated: boolean;
}

export interface ScheduleSnapshot {
  id: string;
  name: string;
  humanReadable: string;
  rawExpression: string;
  source: string;
  nextRun: string | null;
  lastRun: string | null;
  lastResult: string;
  owner: string | null;
  enabled: boolean;
  target: string;
  provenance: string;
  relatedService: string | null;
  managed: boolean;
}

export interface ScheduleInventoryResponse {
  generatedAt: string;
  fresh: boolean;
  schedules: ScheduleSnapshot[];
}

export interface ManagedScheduleDefinition {
  name: string;
  description: string;
  onCalendar: string;
  executable: string;
  arguments: string[];
  user: string;
  workingDirectory: string | null;
  timeoutSeconds: number;
  overlapPolicy: string;
  missedRunBehavior: string;
  relatedService: string | null;
}

export interface ManagedScheduleView {
  definition: ManagedScheduleDefinition;
  revision: number;
  enabled: boolean;
  serviceUnit: string;
  timerUnit: string;
  installed: boolean;
  lastResult: string;
  lastRun: string | null;
}

export interface ActivityRecord {
  id: string;
  occurredAt: string;
  kind: string;
  targetType: string;
  target: string;
  identity: string;
  result: string;
  message: string;
  evidence: Record<string, string>;
}

export interface ActivityResponse {
  since: string | null;
  requestedAt: string;
  records: ActivityRecord[];
}

export type OperationKind = "start" | "stop" | "restart" | "enable" | "disable" | "trigger";

export interface OperationRequest {
  operation: OperationKind;
  expectedState: string | null;
  reason: string;
}

export interface OperationResult {
  id: string;
  unit: string;
  operation: OperationKind;
  state: string;
  message: string;
  requestedAt: string;
  reconciledAt: string | null;
  evidence: Record<string, string>;
}

export async function fetchHost(): Promise<HostEvidence | null> {
  try {
    const response = await fetch("/api/v1/host", { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as HostEvidence;
  } catch {
    return null;
  }
}

export async function fetchHostHistory(window = "30m"): Promise<HostHistoryResponse | null> {
  try {
    const response = await fetch(`/api/v1/host/history?window=${encodeURIComponent(window)}`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as HostHistoryResponse;
  } catch {
    return null;
  }
}

export async function fetchUnits(): Promise<UnitInventoryResponse | null> {
  try {
    const response = await fetch("/api/v1/units", { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as UnitInventoryResponse;
  } catch {
    return null;
  }
}

export async function fetchUnitLogs(unit: string): Promise<UnitLogsResponse | null> {
  try {
    const response = await fetch(
      `/api/v1/units/${encodeURIComponent(unit)}/logs?tail=100`,
      { cache: "no-store" },
    );
    if (!response.ok) return null;
    return (await response.json()) as UnitLogsResponse;
  } catch {
    return null;
  }
}

export async function fetchSchedules(): Promise<ScheduleInventoryResponse | null> {
  try {
    const response = await fetch("/api/v1/schedules", { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as ScheduleInventoryResponse;
  } catch {
    return null;
  }
}

export async function fetchActivity(limit = 100): Promise<ActivityResponse | null> {
  try {
    const response = await fetch(`/api/v1/activity?limit=${limit}`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as ActivityResponse;
  } catch {
    return null;
  }
}

export type OperationSubmitResult =
  | { status: "ok"; result: OperationResult }
  | { status: "error"; message: string };

export async function submitOperation(
  unit: string,
  request: OperationRequest,
  idempotencyKey: string,
): Promise<OperationSubmitResult> {
  try {
    const response = await fetch(`/api/v1/units/${encodeURIComponent(unit)}/operations`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
        Origin: window.location.origin,
      },
      body: JSON.stringify(request),
      cache: "no-store",
    });
    const body = (await response.json()) as OperationResult | { error?: { message?: string } };
    if (!response.ok) {
      const message = "error" in body ? body.error?.message : undefined;
      return { status: "error", message: message ?? `Request failed (${response.status}).` };
    }
    return { status: "ok", result: body as OperationResult };
  } catch (error) {
    return { status: "error", message: error instanceof Error ? error.message : "Network failure." };
  }
}

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
