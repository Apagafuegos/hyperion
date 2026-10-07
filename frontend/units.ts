// Units workspace: curated systemd inventory with expanded dossiers, journal
// disclosure, and typed lifecycle operations through the confirmed surface.

import {
  fetchUnitLogs,
  fetchUnits,
  submitOperation,
  type OperationKind,
  type UnitInventoryResponse,
  type UnitSnapshot,
} from "./api";
import { askConfirmation, formatBytes, formatObserved, showToast, stateLabel } from "./shell";
import { LogViewer } from "./logs";

const POLL_INTERVAL_MS = 30_000;

const workspace = () => {
  const element = document.querySelector<HTMLElement>("#workspace");
  if (element === null) throw new Error("missing #workspace");
  return element;
};

let inventory: UnitInventoryResponse | null = null;
let selectedName: string | null = null;
let query = "";
let filter: "default" | "all" = "default";
const journalPanels = new Map<string, HTMLElement>();

function visibleUnits(units: UnitSnapshot[]): UnitSnapshot[] {
  const q = query.trim().toLowerCase();
  return units.filter((unit) => {
    const matchesQuery =
      q === "" ||
      [unit.name, unit.description, unit.activeState, unit.subState, unit.relatedService ?? ""]
        .some((text) => text.toLowerCase().includes(q));
    if (!matchesQuery) return false;
    if (filter === "all") return true;
    return unit.activeState === "failed" || unit.curated || unit.protection !== "ordinary" || recentlyActive(unit);
  });
}

function recentlyActive(unit: UnitSnapshot): boolean {
  if (unit.activeEntered === null) return false;
  const then = Date.parse(unit.activeEntered);
  if (Number.isNaN(then)) return false;
  return Date.now() - then < 24 * 3600 * 1000;
}

function stateChip(unit: UnitSnapshot): HTMLElement {
  const chip = document.createElement("span");
  chip.className = `status status-${
    unit.activeState === "active" ? "reachable" : unit.activeState === "failed" ? "down" : "dormant"
  }`;
  chip.textContent = stateLabel(unit.activeState);
  return chip;
}

function renderFilterBar(): HTMLElement {
  const bar = document.createElement("div");
  bar.className = "filter-bar";
  const defaultButton = document.createElement("button");
  defaultButton.type = "button";
  defaultButton.className = filter === "default" ? "filter-active" : "";
  defaultButton.textContent = "Curated";
  defaultButton.addEventListener("click", () => {
    filter = "default";
    renderIndex();
  });
  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = filter === "all" ? "filter-active" : "";
  allButton.textContent = "All units";
  allButton.addEventListener("click", () => {
    filter = "all";
    renderIndex();
  });
  const count = document.createElement("span");
  count.className = "filter-count";
  count.textContent = `${inventory?.units.length ?? 0} units observed`;
  bar.append(defaultButton, allButton, count);
  return bar;
}

function renderIndex(): void {
  const root = workspace();
  root.textContent = "";

  if (inventory === null) {
    const message = document.createElement("p");
    message.className = "atlas-message";
    message.textContent = "Unit inventory is unavailable right now.";
    root.append(message);
    return;
  }

  root.append(renderFilterBar());

  const band = document.createElement("section");
  band.className = "band";
  const header = document.createElement("header");
  header.className = "band-heading";
  const h2 = document.createElement("h2");
  h2.textContent = "Units";
  const coords = document.createElement("span");
  coords.className = "band-coords";
  coords.textContent = "SYSTEMD INVENTORY";
  header.append(h2, coords);
  band.append(header);

  const visible = visibleUnits(inventory.units);
  const index = document.createElement("div");
  index.className = "band-index";
  if (visible.length === 0) {
    const empty = document.createElement("p");
    empty.className = "band-empty";
    empty.textContent = query.trim() === "" ? "No units in the curated view." : "No units match your search.";
    index.append(empty);
  } else {
    for (const unit of visible) index.append(buildRow(unit));
  }
  band.append(index);
  root.append(band);
}

function buildRow(unit: UnitSnapshot): HTMLElement {
  const row = document.createElement("div");
  row.className = `unit-row${unit.name === selectedName ? " selected" : ""}`;
  row.dataset.unit = unit.name;

  const select = document.createElement("button");
  select.type = "button";
  select.className = "unit-select";
  select.setAttribute("aria-expanded", String(unit.name === selectedName));
  select.addEventListener("click", () => selectUnit(unit.name));
  const chevron = document.createElement("span");
  chevron.className = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  const copy = document.createElement("span");
  copy.className = "route-copy";
  const name = document.createElement("strong");
  name.className = "unit-name";
  name.textContent = unit.name;
  const description = document.createElement("small");
  description.className = "unit-description";
  description.textContent = unit.description;
  copy.append(name, description);
  select.append(chevron, copy);

  const chip = stateChip(unit);
  const enabled = document.createElement("span");
  enabled.className = "unit-enabled";
  enabled.textContent = unit.enabledState;
  const since = document.createElement("code");
  since.className = "unit-since";
  since.textContent = formatObserved(unit.activeEntered);
  const restarts = document.createElement("code");
  restarts.className = "unit-restarts";
  restarts.textContent = `${unit.restartCount ?? "—"} restarts`;
  const memory = document.createElement("code");
  memory.className = "unit-mem";
  memory.textContent = formatBytes(unit.memoryBytes);

  row.append(select, chip, enabled, since, restarts, memory);
  if (unit.name === selectedName) row.append(buildDossier(unit));
  return row;
}

function buildDossier(unit: UnitSnapshot): HTMLElement {
  const dossier = document.createElement("div");
  dossier.className = "dossier unit-dossier";
  dossier.dataset.dossierFor = unit.name;
  dossier.append(
    dossierSection("Unit evidence", [
      ["Active state", stateLabel(unit.activeState)],
      ["Sub state", unit.subState],
      ["Load state", unit.loadState],
      ["Enabled", unit.enabledState],
      ["Main PID", unit.mainPid === null ? "—" : String(unit.mainPid)],
      ["Active since", unit.activeEntered === null ? "—" : formatObserved(unit.activeEntered)],
      ["Restarts", String(unit.restartCount ?? "—")],
      ["Memory", formatBytes(unit.memoryBytes)],
      ["Related timer", unit.relatedTimer ?? "—"],
    ]),
    dossierSection("Protection", [
      ["Classification", unit.protection],
      ["Related service", unit.relatedService ?? "—"],
    ]),
    dossierActions(unit),
    dossierLogs(unit),
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

function dossierLogs(unit: UnitSnapshot): HTMLElement {
  const existing = journalPanels.get(unit.name);
  if (existing) return existing;
  const section = document.createElement("section");
  section.className = "unit-journal";
  const heading = document.createElement("h3");
  heading.textContent = "Journal";
  const header = document.createElement("div");
  header.className = "unit-journal-header";
  const viewer = new LogViewer(`Journal for ${unit.name}`);
  viewer.setState("Load the last 100 journal records for this unit.");
  const load = document.createElement("button");
  load.type = "button";
  load.className = "button-route";
  load.textContent = "Load journal";
  load.addEventListener("click", async () => {
    load.disabled = true;
    load.textContent = "Loading…";
    viewer.setState("Loading recent journal records…", true);
    const logs = await fetchUnitLogs(unit.name);
    load.disabled = false;
    if (logs === null) {
      load.textContent = "Retry journal";
      viewer.setState("Journal is unavailable for this unit. Use Retry journal to try again.");
      return;
    }
    load.textContent = "Refresh journal";
    viewer.setRecords(logs.records, logs.truncated);
  });
  header.append(heading, load);
  section.append(header, viewer.element);
  journalPanels.set(unit.name, section);
  return section;
}

function dossierActions(unit: UnitSnapshot): HTMLElement {
  const section = document.createElement("section");
  section.className = "unit-actions";
  const heading = document.createElement("h3");
  heading.textContent = "Operations";
  section.append(heading);
  if (unit.protection === "protected") {
    const note = document.createElement("p");
    note.className = "operation-note denied";
    note.textContent = "Protected by Hyperion policy — operations are not available.";
    section.append(note);
    return section;
  }
  const actions = document.createElement("div");
  actions.className = "operation-actions";
  const operations: OperationKind[] = ["start", "stop", "restart"];
  for (const operation of operations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "op-action op-secondary";
    button.textContent = titleCase(operation);
    button.addEventListener("click", () => requestOperation(unit, operation));
    actions.append(button);
  }
  section.append(actions);
  const note = document.createElement("p");
  note.className = "operation-note";
  note.textContent = "Operations cross the privileged boundary and are recorded in Activity.";
  section.append(note);
  return section;
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function requestOperation(unit: UnitSnapshot, operation: OperationKind): void {
  askConfirmation({
    title: `${titleCase(operation)} ${unit.name}`,
    impact: `${operation} will be applied to ${unit.name}.`,
    state: `Current state: ${stateLabel(unit.activeState)}.`,
    confirmLabel: titleCase(operation),
    onConfirm: () => runOperation(unit, operation),
  });
}

async function runOperation(unit: UnitSnapshot, operation: OperationKind): Promise<void> {
  const idempotencyKey = `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  const result = await submitOperation(
    unit.name,
    { operation, expectedState: unit.activeState, reason: "operator request" },
    idempotencyKey,
  );
  if (result.status === "ok") {
    showToast(`${operation} ${result.result.state === "success" ? "completed" : result.result.state}`);
    void refresh();
  } else {
    showToast(`Operation failed: ${result.message}`);
  }
}

function selectUnit(name: string): void {
  selectedName = selectedName === name ? null : name;
  const fromHash = selectedName === null ? "" : `#unit=${encodeURIComponent(selectedName)}`;
  history.replaceState(null, "", window.location.pathname + fromHash);
  renderIndex();
}

function initSelection(): void {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("unit");
  if (fromHash !== null) selectedName = fromHash;
}

export function initUnits(): void {
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
  const next = await fetchUnits();
  if (next !== null) {
    inventory = next;
    renderIndex();
  } else if (inventory === null) {
    renderIndex();
  }
}
