// Activity workspace: chronological field notes grouped by day, with target,
// identity, result chip, time, and evidence.

import { fetchActivity, type ActivityRecord, type ActivityResponse } from "./api";
import { statusChip } from "./shell";

const POLL_INTERVAL_MS = 30_000;

const workspace = () => {
  const element = document.querySelector<HTMLElement>("#workspace");
  if (element === null) throw new Error("missing #workspace");
  return element;
};

let response: ActivityResponse | null = null;
let query = "";

function matches(record: ActivityRecord): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const haystacks = [record.target, record.identity, record.message, record.kind, record.result];
  return haystacks.some((text) => text.toLowerCase().includes(q));
}

function dayLabel(date: Date, now: Date): string {
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startToday.getTime() - startDay.getTime()) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function render(): void {
  const root = workspace();
  root.textContent = "";

  if (response === null) {
    const message = document.createElement("p");
    message.className = "atlas-message";
    message.textContent = "Activity is unavailable right now.";
    root.append(message);
    return;
  }

  const records = response.records.filter(matches);
  const now = new Date();
  const byDay = new Map<string, ActivityRecord[]>();
  for (const record of records) {
    const date = new Date(record.occurredAt);
    const key = date.toDateString();
    const bucket = byDay.get(key) ?? [];
    bucket.push(record);
    byDay.set(key, bucket);
  }

  if (records.length === 0) {
    const empty = document.createElement("p");
    empty.className = "atlas-message";
    empty.textContent = query.trim() === "" ? "No activity has been recorded yet." : "No activity matches your search.";
    root.append(empty);
    return;
  }

  const band = document.createElement("section");
  band.className = "band";
  const header = document.createElement("header");
  header.className = "band-heading";
  const h2 = document.createElement("h2");
  h2.textContent = "Activity";
  const coords = document.createElement("span");
  coords.className = "band-coords";
  coords.textContent = "FIELD NOTES";
  header.append(h2, coords);
  band.append(header);

  for (const [key, dayRecords] of byDay) {
    const day = document.createElement("h3");
    day.className = "activity-day";
    day.textContent = dayLabel(new Date(key), now);
    band.append(day);
    for (const record of dayRecords) {
      band.append(buildRecord(record));
    }
  }
  root.append(band);
}

function buildRecord(record: ActivityRecord): HTMLElement {
  const row = document.createElement("div");
  row.className = "activity-record";
  const time = document.createElement("time");
  time.className = "activity-time";
  time.textContent = new Date(record.occurredAt).toLocaleTimeString();
  const target = document.createElement("code");
  target.className = "activity-target";
  target.textContent = record.target;
  target.title = record.target;
  const result = statusChip(record.result);
  const note = document.createElement("span");
  note.className = "activity-note";
  note.textContent = record.message;
  const identity = document.createElement("code");
  identity.className = "activity-identity";
  identity.textContent = record.identity;
  row.append(time, target, result, note, identity);
  return row;
}

export function initActivity(): void {
  const search = document.querySelector<HTMLInputElement>("#search");
  search?.addEventListener("input", () => {
    query = search.value;
    render();
  });
  void refresh();
  window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
}

async function refresh(): Promise<void> {
  const next = await fetchActivity(200);
  if (next !== null) {
    response = next;
    render();
  } else if (response === null) {
    render();
  }
}
