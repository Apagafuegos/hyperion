// Schedules workspace: unified systemd-timer and cron index with an inline
// schedule dossier and a restrained next-24-hours timeline.

import {
  fetchSchedules,
  type ScheduleInventoryResponse,
  type ScheduleSnapshot,
} from "./api";
import { stateLabel } from "./shell";

const POLL_INTERVAL_MS = 30_000;

const workspace = () => {
  const element = document.querySelector<HTMLElement>("#workspace");
  if (element === null) throw new Error("missing #workspace");
  return element;
};

let inventory: ScheduleInventoryResponse | null = null;
let selectedId: string | null = null;
let query = "";

function applyFilter(schedule: ScheduleSnapshot): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const haystacks = [schedule.name, schedule.humanReadable, schedule.rawExpression, schedule.target, schedule.provenance, schedule.owner ?? "", schedule.relatedService ?? ""];
  return haystacks.some((text) => text.toLowerCase().includes(q));
}

function provenanceLabel(source: string): string {
  return source === "systemd" ? "systemd timer" : source === "cron" ? "cron" : "managed";
}

function renderIndex(): void {
  const root = workspace();
  root.textContent = "";

  if (inventory === null) {
    const message = document.createElement("p");
    message.className = "atlas-message";
    message.textContent = "Schedules are unavailable right now.";
    root.append(message);
    return;
  }

  const band = document.createElement("section");
  band.className = "band";
  const header = document.createElement("header");
  header.className = "band-heading";
  const h2 = document.createElement("h2");
  h2.textContent = "Schedules";
  const coords = document.createElement("span");
  coords.className = "band-coords";
  coords.textContent = "NEXT RUN TIMELINE";
  header.append(h2, coords);
  band.append(header);

  const visible = inventory.schedules.filter(applyFilter);
  const timeline = buildTimeline(visible);
  if (timeline !== null) band.append(timeline);

  const index = document.createElement("div");
  index.className = "band-index";
  if (visible.length === 0) {
    const empty = document.createElement("p");
    empty.className = "band-empty";
    empty.textContent = query.trim() === "" ? "No schedules observed." : "No schedules match your search.";
    index.append(empty);
  } else {
    for (const schedule of visible) {
      index.append(buildRow(schedule));
    }
  }
  band.append(index);
  root.append(band);
}

function buildTimeline(schedules: ScheduleSnapshot[]): HTMLElement | null {
  const now = Date.now();
  const horizon = now + 24 * 3600 * 1000;
  const withNext = schedules
    .map((schedule) => ({ schedule, next: schedule.nextRun === null ? null : Date.parse(schedule.nextRun) }))
    .filter((entry): entry is { schedule: ScheduleSnapshot; next: number } => entry.next !== null && !Number.isNaN(entry.next) && entry.next >= now && entry.next <= horizon);
  if (withNext.length === 0) return null;
  withNext.sort((a, b) => a.next - b.next);

  const timeline = document.createElement("div");
  timeline.className = "next-run-timeline";
  const label = document.createElement("h3");
  label.textContent = "Next 24 hours";
  timeline.append(label);
  for (const { schedule, next } of withNext.slice(0, 12)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "timeline-row";
    row.dataset.scheduleId = schedule.id;
    row.addEventListener("click", () => select(schedule.id));
    const time = document.createElement("time");
    time.textContent = new Date(next).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const name = document.createElement("strong");
    name.textContent = schedule.name;
    const marker = document.createElement("span");
    marker.className = "timeline-marker";
    row.append(time, name, marker);
    timeline.append(row);
  }
  return timeline;
}

function buildRow(schedule: ScheduleSnapshot): HTMLElement {
  const row = document.createElement("div");
  row.className = `schedule-row${schedule.id === selectedId ? " selected" : ""}`;
  row.dataset.scheduleId = schedule.id;

  const main = document.createElement("div");
  main.className = "schedule-main";
  const name = document.createElement("button");
  name.type = "button";
  name.className = "schedule-select";
  name.addEventListener("click", () => select(schedule.id));
  const strong = document.createElement("strong");
  strong.className = "schedule-name";
  strong.textContent = schedule.name;
  const expr = document.createElement("code");
  expr.className = "schedule-expr";
  expr.textContent = schedule.rawExpression || schedule.humanReadable;
  const provenance = document.createElement("span");
  provenance.className = `provenance provenance-${schedule.source}`;
  provenance.textContent = provenanceLabel(schedule.source);
  name.append(strong, expr, provenance);
  main.append(name);

  const next = document.createElement("span");
  next.className = "schedule-next";
  next.textContent = schedule.nextRun === null ? "Not observed" : new Date(schedule.nextRun).toLocaleString();

  const result = document.createElement("span");
  result.className = `status status-${schedule.lastResult === "success" ? "reachable" : schedule.lastResult === "failed" ? "down" : "dormant"}`;
  result.textContent = stateLabel(schedule.lastResult);

  const owner = document.createElement("span");
  owner.className = "schedule-owner";
  owner.textContent = schedule.owner ?? "—";

  const enabled = document.createElement("span");
  enabled.className = "schedule-enabled";
  enabled.textContent = schedule.enabled ? "Enabled" : "Disabled";

  row.append(main, next, result, owner, enabled);
  if (schedule.id === selectedId) row.append(buildDossier(schedule));
  return row;
}

function buildDossier(schedule: ScheduleSnapshot): HTMLElement {
  const dossier = document.createElement("div");
  dossier.className = "dossier schedule-dossier";
  dossier.dataset.dossierFor = schedule.id;
  dossier.append(
    dossierSection("Schedule", [
      ["Name", schedule.name],
      ["Human schedule", schedule.humanReadable],
      ["Raw expression", schedule.rawExpression],
      ["Source", provenanceLabel(schedule.source)],
      ["Provenance", schedule.provenance],
    ]),
    dossierSection("Execution", [
      ["Next run", schedule.nextRun === null ? "Not observed" : new Date(schedule.nextRun).toLocaleString()],
      ["Last run", schedule.lastRun === null ? "Not observed" : new Date(schedule.lastRun).toLocaleString()],
      ["Last result", schedule.lastResult === "not_observed" ? "Not observed" : stateLabel(schedule.lastResult)],
    ]),
    dossierSection("Ownership", [
      ["Owner", schedule.owner ?? "—"],
      ["Target", schedule.target],
      ["Related service", schedule.relatedService ?? "—"],
    ]),
  );
  return dossier;
}

function dossierSection(title: string, facts: [string, string][]): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  const list = document.createElement("dl");
  for (const [term, detail] of facts) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = detail;
    list.append(dt, dd);
  }
  section.append(list);
  return section;
}

function select(id: string): void {
  selectedId = selectedId === id ? null : id;
  const fromHash = selectedId === null ? "" : `#schedule=${encodeURIComponent(selectedId)}`;
  history.replaceState(null, "", window.location.pathname + fromHash);
  renderIndex();
}

function initSelection(): void {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("schedule");
  if (fromHash !== null) selectedId = fromHash;
}

export function initSchedules(): void {
  initSelection();
  const search = document.querySelector<HTMLInputElement>("#search");
  search?.addEventListener("input", () => {
    query = search.value;
    renderIndex();
  });
  void refresh();
  window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
}

async function refresh(): Promise<void> {
  const next = await fetchSchedules();
  if (next !== null) {
    inventory = next;
    renderIndex();
  } else if (inventory === null) {
    renderIndex();
  }
}
