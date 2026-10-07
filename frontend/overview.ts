// Overview publishes each evidence source independently, keeping slow inventory
// reads from blocking the host and service snapshot or replacing focused links.
import {
  fetchActivity, fetchHost, fetchSchedules, fetchSnapshot,
  type ActivityRecord, type HostEvidence, type ScheduleSnapshot, type Snapshot,
} from "./api";
import { matchesQuery } from "./logic";
import { formatBytes, formatObserved, formatUptime, stateLabel, statusChip } from "./shell";

const POLL_INTERVAL_MS = 15_000;
let host: HostEvidence | null = null;
let snapshot: Snapshot | null = null;
let schedules: ScheduleSnapshot[] | null = null;
let activity: ActivityRecord[] | null = null;
let query = "";
let refreshing = false;
const regions = new Map<string, HTMLElement>();
const pending = new Set(["host", "services", "schedules", "activity"]);
const failed = new Set<string>();

function publish(name: string, content: HTMLElement): void {
  regions.get(name)?.replaceChildren(content);
}

function section(title: string, coords: string): HTMLElement {
  const band = document.createElement("section");
  band.className = "band";
  const header = document.createElement("header");
  header.className = "band-heading";
  const h2 = document.createElement("h2");
  h2.textContent = title;
  const span = document.createElement("span");
  span.className = "band-coords";
  span.textContent = coords;
  header.append(h2, span);
  band.append(header);
  return band;
}

function note(text: string): HTMLElement {
  const p = document.createElement("p");
  p.className = "band-empty";
  p.textContent = text;
  return p;
}

function freshness(name: string, fresh: boolean): string {
  return failed.has(name) || !fresh ? "stale" : "fresh";
}

function instrument(title: string, value: string, unit: string, support: string, state: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "instrument";
  const header = document.createElement("div");
  header.className = "instrument-header";
  const label = document.createElement("span");
  label.className = "instrument-title";
  label.textContent = title;
  const status = document.createElement("span");
  status.className = `instrument-state state-${state}`;
  status.textContent = stateLabel(state);
  header.append(label, status);
  const reading = document.createElement("div");
  reading.className = "instrument-value";
  reading.textContent = value;
  const suffix = document.createElement("span");
  suffix.className = "instrument-unit";
  suffix.textContent = unit;
  reading.append(suffix);
  const detail = document.createElement("div");
  detail.className = "instrument-support";
  detail.textContent = support;
  el.append(header, reading, detail);
  return el;
}

function numeric(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(1).replace(/\.0$/, "");
}

function rate(value: number | null | undefined): string {
  return value == null ? "—" : formatBytes(value);
}

function renderHost(): void {
  const identity = document.createElement("div");
  identity.className = "overview-identity";
  const name = document.createElement("strong");
  name.textContent = host?.hostname ?? (pending.has("host") ? "Reading host…" : "Host unavailable");
  const detail = document.createElement("span");
  if (host !== null) {
    detail.textContent = `up ${formatUptime(host.uptimeSeconds)} · observed ${formatObserved(host.observedAt)} · ${stateLabel(freshness("host", host.fresh))}`;
  } else {
    detail.textContent = "Waiting for host evidence.";
  }
  identity.append(name, detail);
  publish("identity", identity);

  const band = section("Host condition", "HOST LAT");
  const field = document.createElement("div");
  field.className = "instrument-field";
  const available = host !== null && host.providerState !== "unavailable";
  const state = available ? freshness("host", host!.fresh) : pending.has("host") ? "pending" : "unavailable";
  const cpu = host?.cpu.utilizationPercent;
  const memory = host?.memory.usedPercent;
  // Show the filesystem closest to capacity, rather than concealing a full
  // Docker volume behind a healthy root filesystem.
  const filesystem = host?.filesystems.filter(fs => fs.usedPercent !== null)
    .sort((a, b) => (b.usedPercent ?? 0) - (a.usedPercent ?? 0))[0];
  const network = host?.interfaces.find(i => i.name === "eth0") ?? host?.interfaces.find(i => i.name !== "lo");
  field.append(
    instrument("CPU", numeric(cpu), cpu == null ? "" : "%", `load ${numeric(host?.cpu.loadAverage1m)} / ${numeric(host?.cpu.loadAverage5m)} / ${numeric(host?.cpu.loadAverage15m)}`, cpu == null && available ? "unavailable" : state),
    instrument("Memory", numeric(memory), memory == null ? "" : "%", `${formatBytes(host?.memory.usedBytes ?? null)} of ${formatBytes(host?.memory.totalBytes ?? null)} · swap ${numeric(host?.memory.swapUsedPercent)}%`, memory == null && available ? "unavailable" : state),
    instrument("Storage", numeric(filesystem?.usedPercent), filesystem?.usedPercent == null ? "" : "%", filesystem === undefined ? "No filesystems observed." : `${filesystem.mountPoint} · ${formatBytes(filesystem.freeBytes)} free · ${numeric(filesystem.inodeUsedPercent)}% inodes`, filesystem === undefined && available ? "unavailable" : state),
    instrument("Network", rate(network?.rxBytesPerSecond), network?.rxBytesPerSecond == null ? "" : "/s in", network === undefined ? "No interfaces observed." : `${network.name} · out ${rate(network.txBytesPerSecond)}/s`, network?.rxBytesPerSecond == null && available ? "unavailable" : state),
  );
  band.append(field);
  publish("host", band);
}

function renderAttention(): void {
  const band = document.createElement("section");
  band.className = "attention-band";
  const title = document.createElement("h2");
  title.textContent = "Needs attention";
  band.append(title);
  let count = 0;
  let critical = false;
  const add = (target: string, message: string, state: string, href: string, observedAt: string) => {
    if (![target, message].some(text => text.toLowerCase().includes(query))) return;
    const row = document.createElement("a");
    row.className = "attention-row";
    row.href = href;
    const dot = document.createElement("span");
    dot.className = `route-dot dot-${state}`;
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("strong");
    name.className = "attention-target";
    name.textContent = target;
    const note = document.createElement("span");
    note.className = "attention-note";
    note.textContent = message;
    const time = document.createElement("time");
    time.className = "attention-time";
    time.dateTime = observedAt;
    time.textContent = formatObserved(observedAt);
    row.append(dot, name, statusChip(state), note, time);
    band.append(row);
    count++;
    critical ||= state === "down";
  };
  const services = snapshot?.services.filter(s => s.state === "down" || s.state === "degraded" || s.state === "unknown") ?? [];
  services.sort((a, b) => Number(b.state === "down") - Number(a.state === "down"));
  for (const service of services) {
    const reason = service.stateReasons.find(r => r.severity === "critical") ?? service.stateReasons[0];
    add(service.name, reason?.message ?? "Service state could not be established.", service.state,
      `/atlas#service=${encodeURIComponent(service.id)}`, service.observedAt);
  }
  for (const fs of host?.filesystems ?? []) {
    if (fs.usedPercent !== null && fs.usedPercent >= 85) {
      add(fs.mountPoint, `Storage is ${numeric(fs.usedPercent)}% full · ${formatBytes(fs.freeBytes)} free.`, "degraded", "#host-condition", host!.observedAt);
    }
  }
  if (count > 0 && !critical) band.classList.add("attention-caution");
  if (count === 0) {
    band.classList.add("attention-quiet");
    const empty = note(query !== "" ? "No attention items match your search." : pending.has("services") || pending.has("host") ? "Checking current evidence…" : snapshot === null || host === null ? "Current attention is unavailable." : "Nothing needs attention.");
    empty.className = "attention-empty";
    band.append(empty);
  }
  if (failed.has("services") || failed.has("host") || snapshot?.fresh === false || host?.fresh === false) {
    band.append(note("Some evidence is stale or unavailable. Retrying automatically."));
  }
  publish("attention", band);
}

function renderServices(): void {
  const band = section("Service bearings", "FLEET");
  const index = document.createElement("div");
  index.className = "band-index overview-services";
  const visible = snapshot?.services.filter(service => matchesQuery(service, query)) ?? [];
  if (snapshot === null) {
    index.append(note(pending.has("services") ? "Reading service states…" : "Service states are unavailable. Retrying automatically."));
  } else if (visible.length === 0) {
    index.append(note(query === "" ? "No services observed." : "No services match your search."));
  }
  for (const service of visible) {
    const row = document.createElement("a");
    row.className = "bearing-row";
    row.href = `/atlas#service=${encodeURIComponent(service.id)}`;
    const dot = document.createElement("span");
    dot.className = `route-dot dot-${service.state}`;
    const name = document.createElement("strong");
    name.textContent = service.name;
    row.append(dot, name, statusChip(service.state));
    index.append(row);
  }
  band.append(index);
  if (snapshot !== null && freshness("services", snapshot.fresh) === "stale") band.append(note("Service evidence is stale. Retrying automatically."));
  publish("services", band);
}

function renderSchedules(): void {
  const band = section("Upcoming schedules", "SCHEDULES");
  const index = document.createElement("div");
  index.className = "band-index";
  const visible = (schedules ?? []).filter(s => [s.name, s.target, s.rawExpression, s.owner ?? ""].some(text => text.toLowerCase().includes(query)))
    .sort((a, b) => (a.nextRun === null ? Infinity : Date.parse(a.nextRun)) - (b.nextRun === null ? Infinity : Date.parse(b.nextRun)));
  if (visible.length === 0) index.append(note(schedules === null ? pending.has("schedules") ? "Reading upcoming schedules…" : "Schedules are unavailable. Retrying automatically." : query === "" ? "No schedules observed." : "No schedules match your search."));
  for (const schedule of visible.slice(0, 5)) {
    const row = document.createElement("a");
    row.className = "schedule-row overview-schedule";
    row.href = `/schedules#schedule=${encodeURIComponent(schedule.id)}`;
    const main = document.createElement("div");
    main.className = "schedule-main";
    const name = document.createElement("strong");
    name.className = "schedule-name";
    name.textContent = schedule.name;
    const expression = document.createElement("code");
    expression.className = "schedule-expr";
    expression.textContent = schedule.rawExpression || schedule.humanReadable;
    const source = document.createElement("span");
    source.className = `provenance provenance-${schedule.source}`;
    source.textContent = schedule.source === "systemd" ? "systemd timer" : schedule.source;
    main.append(name, expression, source);
    const next = document.createElement("time");
    next.className = "schedule-next";
    if (schedule.nextRun !== null) next.dateTime = schedule.nextRun;
    next.textContent = schedule.nextRun === null ? "Not observed" : new Date(schedule.nextRun).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    row.append(main, next, statusChip(schedule.lastResult));
    index.append(row);
  }
  band.append(index);
  if (failed.has("schedules") && schedules !== null) band.append(note("Schedule evidence is stale. Retrying automatically."));
  publish("schedules", band);
}

function recordHref(record: ActivityRecord): string {
  if (record.targetType === "service") return `/atlas#service=${encodeURIComponent(record.target)}`;
  if (record.targetType === "unit") return `/units#unit=${encodeURIComponent(record.target)}`;
  if (record.targetType === "schedule") return `/schedules#schedule=${encodeURIComponent(record.target)}`;
  return "/activity";
}

function renderActivity(): void {
  const band = section("Recent activity", "FIELD NOTES");
  const records = (activity ?? []).filter(r => [r.target, r.message, r.identity, r.kind, r.result].some(text => text.toLowerCase().includes(query)));
  if (records.length === 0) band.append(note(activity === null ? pending.has("activity") ? "Reading recent activity…" : "Activity is unavailable. Retrying automatically." : query === "" ? "No activity observed yet." : "No activity matches your search."));
  for (const record of records.slice(0, 4)) {
    const row = document.createElement("a");
    row.className = "activity-record";
    row.href = recordHref(record);
    const time = document.createElement("time");
    time.className = "activity-time";
    time.dateTime = record.occurredAt;
    time.textContent = new Date(record.occurredAt).toLocaleTimeString();
    const target = document.createElement("code");
    target.className = "activity-target";
    target.textContent = record.target;
    const message = document.createElement("span");
    message.className = "activity-note";
    message.textContent = record.message;
    const identity = document.createElement("code");
    identity.className = "activity-identity";
    identity.textContent = record.identity;
    row.append(time, target, statusChip(record.result), message, identity);
    band.append(row);
  }
  publish("activity", band);
}

async function refresh(): Promise<void> {
  if (refreshing || document.hidden) return;
  refreshing = true;
  const settle = (name: string, ok: boolean) => {
    pending.delete(name);
    if (ok) failed.delete(name); else failed.add(name);
  };
  try {
    await Promise.all([
      fetchHost().then(next => { settle("host", next !== null); if (next !== null) host = next; renderHost(); renderAttention(); }),
      fetchSnapshot(null).then(next => { settle("services", next.status === "ok"); if (next.status === "ok") snapshot = next.snapshot; renderServices(); renderAttention(); }),
      fetchSchedules().then(next => { settle("schedules", next !== null); if (next !== null) schedules = next.schedules; renderSchedules(); }),
      fetchActivity(100).then(next => { settle("activity", next !== null); if (next !== null) activity = next.records; renderActivity(); }),
    ]);
  } finally {
    refreshing = false;
  }
}

export function initOverview(): void {
  const root = document.querySelector<HTMLElement>("#workspace");
  if (root === null) return;
  root.removeAttribute("aria-live");
  root.classList.add("overview-workspace");
  root.textContent = "";
  for (const name of ["identity", "attention", "host", "services", "schedules", "activity"]) {
    const slot = document.createElement("div");
    slot.dataset.overviewRegion = name;
    if (name === "host") slot.id = "host-condition";
    regions.set(name, slot);
    root.append(slot);
  }
  renderHost(); renderAttention(); renderServices(); renderSchedules(); renderActivity();
  document.querySelector<HTMLInputElement>("#search")?.addEventListener("input", event => {
    query = (event.target as HTMLInputElement).value.trim().toLowerCase();
    renderAttention(); renderServices(); renderSchedules(); renderActivity();
  });
  void refresh();
  window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
}
