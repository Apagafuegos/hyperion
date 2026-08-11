// Overview workspace: host condition, attention, upcoming schedules,
// service-state bearings, and recent activity in one continuous ruled field.

import {
  fetchActivity,
  fetchHost,
  fetchSchedules,
  fetchSnapshot,
  type ActivityRecord,
  type HostEvidence,
  type ScheduleSnapshot,
  type Snapshot,
} from "./api";
import { formatBytes, formatObserved, formatUptime, stateLabel } from "./shell";

const POLL_INTERVAL_MS = 15_000;

const workspace = () => {
  const element = document.querySelector<HTMLElement>("#workspace");
  if (element === null) throw new Error("missing #workspace");
  return element;
};

let host: HostEvidence | null = null;
let snapshot: Snapshot | null = null;
let schedules: ScheduleSnapshot[] = [];
let activity: ActivityRecord[] = [];

function section(title: string, coords: string): HTMLElement {
  const section = document.createElement("section");
  section.className = "band";
  const header = document.createElement("header");
  header.className = "band-heading";
  const h2 = document.createElement("h2");
  h2.textContent = title;
  const span = document.createElement("span");
  span.className = "band-coords";
  span.textContent = coords;
  header.append(h2, span);
  section.append(header);
  return section;
}

function instrument(
  title: string,
  value: string,
  unit: string,
  support: string,
  state: string,
  trace: number[] = [],
): HTMLElement {
  const instrument = document.createElement("div");
  instrument.className = "instrument";
  const header = document.createElement("div");
  header.className = "instrument-header";
  const titleEl = document.createElement("span");
  titleEl.className = "instrument-title";
  titleEl.textContent = title;
  const stateEl = document.createElement("span");
  stateEl.className = `instrument-state state-${state}`;
  stateEl.textContent = state === "fresh" ? "Fresh" : state === "stale" ? "Stale" : "Unavailable";
  header.append(titleEl, stateEl);
  const valueEl = document.createElement("div");
  valueEl.className = "instrument-value";
  valueEl.textContent = value;
  const unitEl = document.createElement("span");
  unitEl.className = "instrument-unit";
  unitEl.textContent = unit;
  valueEl.append(unitEl);
  const supportEl = document.createElement("div");
  supportEl.className = "instrument-support";
  supportEl.textContent = support;
  instrument.append(header, valueEl, supportEl);
  if (trace.length > 1) {
    instrument.append(sparkline(trace));
  }
  return instrument;
}

function sparkline(values: number[]): HTMLElement {
  const container = document.createElement("div");
  container.className = "instrument-trace";
  container.setAttribute("aria-hidden", "true");
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 120 26");
  svg.setAttribute("preserveAspectRatio", "none");
  const max = Math.max(...values, 1);
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * 120},${26 - (v / max) * 22 - 2}`)
    .join(" ");
  const polyline = document.createElementNS(ns, "polyline");
  polyline.setAttribute("points", points);
  svg.append(polyline);
  container.append(svg);
  return container;
}

function attentionBand(records: ActivityRecord[]): HTMLElement {
  const band = document.createElement("div");
  band.className = "attention-band";
  const heading = document.createElement("h2");
  heading.textContent = "Needs attention";
  band.append(heading);
  const attention = records.filter((r) => r.result === "failure" || r.result === "warning");
  if (attention.length === 0) {
    const quiet = document.createElement("p");
    quiet.className = "attention-empty";
    quiet.textContent = "Nothing needs attention.";
    band.append(quiet);
    return band;
  }
  for (const record of attention.slice(0, 5)) {
    const row = document.createElement("div");
    row.className = "attention-row";
    const dot = document.createElement("span");
    dot.className = `route-dot dot-${record.result === "failure" ? "down" : "degraded"}`;
    const target = document.createElement("a");
    target.className = "attention-target";
    target.textContent = record.target;
    target.href = recordTargetHref(record);
    const note = document.createElement("span");
    note.className = "attention-note";
    note.textContent = record.message;
    const time = document.createElement("time");
    time.className = "attention-time";
    time.textContent = formatObserved(record.occurredAt);
    row.append(dot, target, note, time);
    band.append(row);
  }
  return band;
}

function recordTargetHref(record: ActivityRecord): string {
  switch (record.targetType) {
    case "unit": return `/units#unit=${encodeURIComponent(record.target)}`;
    case "service": return `/atlas#service=${encodeURIComponent(record.target)}`;
    case "schedule": return `/schedules#schedule=${encodeURIComponent(record.target)}`;
    default: return "/activity";
  }
}

function renderHost(): void {
  const hostSection = section("Host condition", "HOST LAT");
  const field = document.createElement("div");
  field.className = "instrument-field";
  hostSection.append(field);
  const stale = host === null || !host.fresh;
  const state = stale ? "stale" : "fresh";

  const cpuValue = host?.cpu.utilizationPercent;
  field.append(
    instrument(
      "CPU",
      cpuValue === null ? "—" : String(cpuValue),
      cpuValue === null ? "" : "%",
      `load ${formatLoad(host?.cpu.loadAverage1m)} / ${formatLoad(host?.cpu.loadAverage5m)} / ${formatLoad(host?.cpu.loadAverage15m)}`,
      state,
      historyTrace("cpu"),
    ),
  );
  field.append(
    instrument(
      "Memory",
      host?.memory.usedPercent === null || host?.memory.usedPercent === undefined ? "—" : String(host.memory.usedPercent),
      host?.memory.usedPercent === null || host?.memory.usedPercent === undefined ? "" : "%",
      `${formatBytes(host?.memory.usedBytes ?? null)} of ${formatBytes(host?.memory.totalBytes ?? null)} · swap ${formatPercent(host?.memory.swapUsedPercent)}`,
      state,
      [],
    ),
  );
  field.append(
    instrument(
      "Storage",
      storageValue(host),
      "%",
      storageSupport(host),
      state,
      [],
    ),
  );
  field.append(
    instrument(
      "Network",
      networkValue(host),
      networkUnit(host),
      networkSupport(host),
      state,
      [],
    ),
  );
  workspace().append(hostSection);
}

function formatLoad(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(2);
}

function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function storageValue(host: HostEvidence | null): string {
  if (host === null) return "—";
  const filesystem = host.filesystems.find((fs) => fs.mountPoint === "/") ?? host.filesystems[0];
  return filesystem?.usedPercent === null || filesystem?.usedPercent === undefined ? "—" : String(filesystem.usedPercent);
}

function storageSupport(host: HostEvidence | null): string {
  if (host === null) return "—";
  const filesystem = host.filesystems.find((fs) => fs.mountPoint === "/") ?? host.filesystems[0];
  if (filesystem === undefined) return "no filesystems observed";
  return `${filesystem.mountPoint} · ${formatBytes(filesystem.freeBytes ?? null)} free · ${formatPercent(filesystem.inodeUsedPercent)} inodes`;
}

function networkValue(host: HostEvidence | null): string {
  if (host === null) return "—";
  const interface_ = host.interfaces.find((i) => i.name === "eth0") ?? host.interfaces[0];
  if (interface_ === undefined) return "—";
  const value = interface_.rxBytesPerSecond;
  return value === null || value === undefined ? "—" : formatRate(value);
}

function networkUnit(host: HostEvidence | null): string {
  return host === null ? "" : "/s in";
}

function networkSupport(host: HostEvidence | null): string {
  if (host === null) return "—";
  const interface_ = host.interfaces.find((i) => i.name === "eth0") ?? host.interfaces[0];
  if (interface_ === undefined) return "no interfaces observed";
  return `${interface_.name} · in ${formatRate(interface_.rxBytesPerSecond ?? 0)} · out ${formatRate(interface_.txBytesPerSecond ?? 0)}`;
}

function formatRate(bytesPerSecond: number): string {
  if (bytesPerSecond < 1024) return `${bytesPerSecond.toFixed(0)} B`;
  if (bytesPerSecond < 1024 * 1024) return `${(bytesPerSecond / 1024).toFixed(1)} KiB`;
  return `${(bytesPerSecond / (1024 * 1024)).toFixed(1)} MiB`;
}

function historyTrace(kind: "cpu"): number[] {
  void kind;
  return [];
}

function renderSchedules(): void {
  const scheduleSection = section("Upcoming schedules", "SCHEDULES");
  const index = document.createElement("div");
  index.className = "band-index";
  if (schedules.length === 0) {
    const empty = document.createElement("p");
    empty.className = "band-empty";
    empty.textContent = "No schedules observed.";
    index.append(empty);
  } else {
    for (const schedule of schedules.slice(0, 5)) {
      const row = document.createElement("div");
      row.className = "schedule-row";
      const main = document.createElement("div");
      main.className = "schedule-main";
      const name = document.createElement("strong");
      name.className = "schedule-name";
      name.textContent = schedule.name;
      const expr = document.createElement("code");
      expr.className = "schedule-expr";
      expr.textContent = schedule.rawExpression;
      const provenance = document.createElement("span");
      provenance.className = `provenance provenance-${schedule.source}`;
      provenance.textContent = `${schedule.source} timer`;
      main.append(name, expr, provenance);
      const next = document.createElement("span");
      next.className = "schedule-next";
      next.textContent = schedule.nextRun === null ? "not observed" : new Date(schedule.nextRun).toLocaleString();
      const result = document.createElement("span");
      result.className = `status status-${schedule.lastResult === "success" ? "reachable" : schedule.lastResult === "failed" ? "down" : "dormant"}`;
      result.textContent = schedule.lastResult === "not_observed" ? "Not observed" : stateLabel(schedule.lastResult);
      row.append(main, next, result);
      index.append(row);
    }
  }
  scheduleSection.append(index);
  workspace().append(scheduleSection);
}

function renderBearings(): void {
  const bearingsSection = section("Service bearings", "FLEET");
  const index = document.createElement("div");
  index.className = "band-index";
  if (snapshot === null) {
    const empty = document.createElement("p");
    empty.className = "band-empty";
    empty.textContent = "No service snapshot yet.";
    index.append(empty);
  } else {
    for (const service of snapshot.services) {
      const row = document.createElement("a");
      row.className = "bearing-row";
      row.href = `/atlas#service=${encodeURIComponent(service.id)}`;
      const dot = document.createElement("span");
      dot.className = `route-dot dot-${service.state}`;
      const name = document.createElement("strong");
      name.textContent = service.name;
      const chip = document.createElement("span");
      chip.className = `status status-${service.state}`;
      chip.textContent = stateLabel(service.state);
      row.append(dot, name, chip);
      index.append(row);
    }
  }
  bearingsSection.append(index);
  workspace().append(bearingsSection);
}

function renderActivity(): void {
  const activitySection = section("Recent activity", "FIELD NOTES");
  const index = document.createElement("div");
  index.className = "band-index";
  if (activity.length === 0) {
    const empty = document.createElement("p");
    empty.className = "band-empty";
    empty.textContent = "No activity observed yet.";
    index.append(empty);
  } else {
    for (const record of activity.slice(0, 8)) {
      const row = document.createElement("div");
      row.className = "activity-record";
      const time = document.createElement("time");
      time.className = "activity-time";
      time.textContent = new Date(record.occurredAt).toLocaleTimeString();
      const target = document.createElement("code");
      target.className = "activity-target";
      target.textContent = record.target;
      const result = document.createElement("span");
      result.className = `status status-${record.result === "success" ? "reachable" : record.result === "failure" ? "down" : "dormant"}`;
      result.textContent = stateLabel(record.result);
      const note = document.createElement("span");
      note.className = "activity-note";
      note.textContent = record.message;
      row.append(time, target, result, note);
      index.append(row);
    }
  }
  activitySection.append(index);
  workspace().append(activitySection);
}

function renderHeader(): void {
  const banner = document.createElement("div");
  banner.className = "overview-identity";
  if (host !== null && host.hostname !== null) {
    banner.innerHTML = "";
    const name = document.createElement("strong");
    name.textContent = host.hostname;
    const detail = document.createElement("span");
    detail.textContent =
      `up ${formatUptime(host.uptimeSeconds)} · booted ${formatObserved(host.bootTime)} · ${stateLabel(host.providerState)}`;
    banner.append(name, detail);
  }
  workspace().prepend(banner);
}

function render(): void {
  const root = workspace();
  root.textContent = "";
  renderHeader();
  root.append(attentionBand(activity));
  renderHost();
  renderSchedules();
  renderBearings();
  renderActivity();
}

async function refresh(): Promise<void> {
  const [nextHost, nextSchedules, nextActivity, nextSnapshot] = await Promise.all([
    fetchHost(),
    fetchSchedules(),
    fetchActivity(100),
    fetchSnapshot(null),
  ]);
  if (nextHost !== null) host = nextHost;
  if (nextSchedules !== null) schedules = nextSchedules.schedules;
  if (nextActivity !== null) activity = nextActivity.records;
  if (nextSnapshot?.status === "ok") snapshot = nextSnapshot.snapshot;
  render();
}

export function initOverview(): void {
  void refresh();
  window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refresh();
  });
}
