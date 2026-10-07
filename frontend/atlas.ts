import {
  fetchLogs,
  fetchSnapshot,
  type LogsResponse,
  type ServiceSnapshot,
  type Snapshot,
} from "./api";
import {
  filterVisible,
  formatBytes,
  formatLatency,
  formatObserved,
  formatUptime,
  pickSelection,
  servicesByTerritory,
  severityTone,
  stateLabel,
} from "./logic";
import { LogViewer } from "./logs";

const POLL_INTERVAL_MS = 15_000;

type LogCacheEntry =
  | LogsResponse
  | { status: "error"; message: string; retryable: boolean }
  | "loading";

interface ClientState {
  snapshot: Snapshot | null;
  etag: string | null;
  query: string;
  selectedId: string | null;
  priorSelection: string | null;
  logs: Map<string, LogCacheEntry>;
}

const pendingLogs = new Map<string, Promise<void>>();
let refreshingSnapshot = false;

const state: ClientState = {
  snapshot: null,
  etag: null,
  query: "",
  selectedId: null,
  priorSelection: null,
  logs: new Map(),
};

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`missing element: ${selector}`);
  return element;
};

const latitudes = $("#latitudes");
const bearings = $("#bearings");
const searchInput = $("#search") as HTMLInputElement;
const logDrawer = $("#log-drawer") as HTMLDetailsElement;
const logSource = $("#log-source") as HTMLSelectElement;
const logTail = $("#log-tail") as HTMLSelectElement;
const logViewer = new LogViewer("Service logs");
logViewer.rows.id = "log-rows";
$("#log-viewer").append(logViewer.element);
const logRefresh = $<HTMLButtonElement>("#log-refresh");
const logState = $("#log-state");
const logSubject = $("#log-subject");
const toastElement = $("#toast");
const diagnosticsKeycap = $("#diagnostics-keycap");
const atlasFrame = $(".atlas-frame");

// Dedicated live region for meaningful announcements only. The template ships
// aria-live on #latitudes (useful without JS); the client removes it at boot
// because patch-in-place recency rewrites would otherwise announce on every poll.
const atlasStatus = document.createElement("div");
atlasStatus.id = "atlas-status";
atlasStatus.className = "visually-hidden";
atlasStatus.setAttribute("role", "status");
document.body.append(atlasStatus);

// Stale-data note: shown when the last poll failed or the snapshot is stale.
const staleNote = document.createElement("div");
staleNote.id = "stale-note";
staleNote.className = "stale-note";
staleNote.hidden = true;
atlasFrame.after(staleNote);

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let toastTimer: number | undefined;
let announceTimer: number | undefined;

// --- Rendering ---------------------------------------------------------------

function territoryBand(territory: string, label: string): HTMLElement {
  const band = document.createElement("section");
  band.className = "band";
  band.dataset.territory = territory;
  const header = document.createElement("header");
  header.className = "band-heading";
  const title = document.createElement("h2");
  title.textContent = label;
  const coords = document.createElement("span");
  coords.className = "band-coords";
  coords.textContent = `LAT ${territory.toUpperCase().slice(0, 4)}`;
  header.append(title, coords);
  const index = document.createElement("div");
  index.className = "band-index";
  band.append(header, index);
  return band;
}

function buildAtlas(snapshot: Snapshot): void {
  latitudes.textContent = "";
  for (const territory of snapshot.territories) {
    const band = territoryBand(territory.id, territory.label);
    const index = band.querySelector(".band-index") as HTMLElement;
    const services = servicesByTerritory(snapshot, territory.id);
    if (services.length === 0) {
      const empty = document.createElement("p");
      empty.className = "band-empty";
      empty.textContent = "Nothing deployed here yet.";
      index.append(empty);
    }
    for (const service of services) {
      index.append(buildRow(service), buildDossier(service));
    }
    latitudes.append(band);
  }
  renderBearings(snapshot);
  renderDiagnostics(snapshot);
}

function buildRow(service: ServiceSnapshot): HTMLElement {
  const row = document.createElement("div");
  row.className = "service-row";
  row.dataset.serviceId = service.id;

  const select = document.createElement("button");
  select.type = "button";
  select.className = "service-select";
  select.setAttribute("aria-expanded", "false");
  const chevron = document.createElement("span");
  chevron.className = "chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  const dot = document.createElement("i");
  dot.className = "route-dot";
  const copy = document.createElement("span");
  copy.className = "route-copy";
  const name = document.createElement("strong");
  name.className = "service-name";
  const description = document.createElement("small");
  description.className = "service-description";
  copy.append(name, description);
  select.append(chevron, dot, copy);

  const endpoint = document.createElement("code");
  endpoint.className = "cell endpoint";
  const status = document.createElement("span");
  status.className = "status";
  const recency = document.createElement("span");
  recency.className = "cell recency";

  const action = document.createElement("a");
  action.className = "open-action";
  action.target = "_blank";
  action.rel = "noopener noreferrer";

  row.append(select, endpoint, status, recency, action);
  patchRow(row, service);
  return row;
}

function serviceFacts(service: ServiceSnapshot): [string, string][] {
  return [
    ["State", stateLabel(service.state)],
    ["Observed", formatObserved(service.observedAt, Date.now())],
    ...((service.route
      ? [
          ["Status code", service.route.statusCode === null ? "—" : String(service.route.statusCode)],
          ["Latency", formatLatency(service.route.latencyMs)],
          ["Failures", String(service.route.consecutiveFailures)],
        ]
      : []) as [string, string][]),
  ];
}

function routeEvidence(service: ServiceSnapshot): HTMLElement {
  const section = dossierSection("Route evidence", serviceFacts(service));
  const logs = document.createElement("button");
  logs.type = "button";
  logs.className = "button-route dossier-logs";
  logs.dataset.inspectLogs = service.id;
  logs.textContent = "View logs";
  logs.disabled = !service.logSources.some(source => source.available);
  if (logs.disabled) logs.title = "No log sources are available.";
  section.append(logs);
  return section;
}

function buildDossier(service: ServiceSnapshot): HTMLElement {
  const dossier = document.createElement("div");
  dossier.className = "dossier";
  dossier.hidden = true;
  dossier.dataset.dossierFor = service.id;
  dossier.append(
    routeEvidence(service),
    dossierComponents(service),
    dossierDependencies(service),
    dossierReasons(service),
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

function dossierComponents(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Components";
  section.append(heading);
  const list = document.createElement("ul");
  for (const component of service.components) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "dossier-component-label";
    label.textContent = component.label;
    const meta = document.createElement("code");
    meta.className = "dossier-component-meta";
    const bits: string[] = [component.provider, component.state, component.health];
    if (component.uptimeSeconds !== null) bits.push(`up ${formatUptime(component.uptimeSeconds)}`);
    if (component.memoryBytes !== null) bits.push(formatBytes(component.memoryBytes));
    if (component.image !== null) bits.push(component.image);
    meta.textContent = bits.join(" · ");
    item.append(label, meta);
    list.append(item);
  }
  section.append(list);
  return section;
}

function dossierDependencies(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Dependencies";
  section.append(heading);
  const list = document.createElement("ul");
  for (const dependency of service.dependencies) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = dependency.serviceId;
    const chip = document.createElement("span");
    chip.className = `status status-${dependency.state}`;
    chip.textContent = stateLabel(dependency.state);
    item.append(name, chip);
    list.append(item);
  }
  if (service.dependencies.length === 0) {
    const none = document.createElement("li");
    none.className = "dossier-none";
    none.textContent = "No declared dependencies.";
    list.append(none);
  }
  section.append(list);
  return section;
}

function dossierReasons(service: ServiceSnapshot): HTMLElement {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Recent events";
  section.append(heading);
  const list = document.createElement("ul");
  for (const reason of service.stateReasons) {
    const item = document.createElement("li");
    const chip = document.createElement("span");
    chip.className = `log-level severity-${severityTone(reason.severity)}`;
    chip.textContent = reason.code;
    const text = document.createElement("span");
    text.textContent = reason.message;
    item.append(chip, text);
    list.append(item);
  }
  if (service.stateReasons.length === 0) {
    const none = document.createElement("li");
    none.className = "dossier-none";
    none.textContent = "No recent events.";
    list.append(none);
  }
  section.append(list);
  return section;
}

function parseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

type ActionIcon = "open" | "copy" | "unavailable";

function actionIcon(kind: ActionIcon): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.classList.add("action-icon");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");

  const paths = kind === "open"
    ? ["M6 3.5h6.5V10", "M12.25 3.75 5.5 10.5", "M10 8.5v3H3.5v-6h3"]
    : kind === "copy"
      ? ["M5.5 5.5h7v7h-7z", "M3.5 10.5h-1v-7h7v1"]
      : ["M3.5 8h9"];
  for (const value of paths) {
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", value);
    svg.append(path);
  }
  return svg;
}

function setActionContent(action: HTMLAnchorElement, label: string, icon: ActionIcon): void {
  const text = document.createElement("span");
  text.className = "action-label";
  text.textContent = label;
  action.replaceChildren(text, actionIcon(icon));
}

function patchRow(row: HTMLElement, service: ServiceSnapshot): void {
  const name = row.querySelector(".service-name") as HTMLElement;
  const description = row.querySelector(".service-description") as HTMLElement;
  const endpoint = row.querySelector(".endpoint") as HTMLElement;
  const status = row.querySelector(".status") as HTMLElement;
  const recency = row.querySelector(".recency") as HTMLElement;
  const dot = row.querySelector(".route-dot") as HTMLElement;
  const action = row.querySelector(".open-action") as HTMLAnchorElement;

  name.textContent = service.name;
  description.textContent = service.description;
  const url = service.action.type === "none" ? null : parseUrl(service.action.url);
  endpoint.textContent = url === null ? "—" : url.host + url.pathname;
  status.textContent = stateLabel(service.state);
  status.className = `status status-${service.state}`;
  recency.textContent = formatObserved(service.observedAt, Date.now());
  dot.className = `route-dot dot-${service.state}`;

  if (service.action.type === "open") {
    action.href = service.action.url;
    setActionContent(action, "Open", "open");
    action.setAttribute("aria-label", `Open ${service.name}`);
    action.removeAttribute("aria-disabled");
    action.removeAttribute("tabindex");
    action.classList.remove("copy-action", "no-action");
  } else if (service.action.type === "copy") {
    action.href = "#";
    setActionContent(action, "Copy", "copy");
    action.setAttribute("aria-label", `Copy ${service.name} endpoint`);
    action.removeAttribute("aria-disabled");
    action.removeAttribute("tabindex");
    action.classList.add("copy-action");
    action.classList.remove("no-action");
  } else {
    action.href = "#";
    setActionContent(action, "No route", "unavailable");
    action.setAttribute("aria-label", `${service.name} has no launch route`);
    action.setAttribute("aria-disabled", "true");
    action.setAttribute("tabindex", "-1");
    action.classList.add("no-action");
    action.classList.remove("copy-action");
  }
}

function patchDossier(dossier: HTMLElement, service: ServiceSnapshot): void {
  const sections = dossier.querySelectorAll<HTMLElement>(":scope > section");
  if (sections[0]) sections[0].replaceWith(routeEvidence(service));
  if (sections[1]) sections[1].replaceWith(dossierComponents(service));
  if (sections[2]) sections[2].replaceWith(dossierDependencies(service));
  if (sections[3]) sections[3].replaceWith(dossierReasons(service));
}

function renderBearings(snapshot: Snapshot): void {
  bearings.textContent = "";
  const entries: [string, number, string][] = [
    ["reachable", snapshot.summary.reachable, "Reachable"],
    ["degraded", snapshot.summary.degraded, "Degraded"],
    ["down", snapshot.summary.down, "Down"],
    ["dormant", snapshot.summary.dormant, "Dormant"],
    ["unknown", snapshot.summary.unknown, "Unknown"],
  ];
  for (const [key, count, label] of entries) {
    const instrument = document.createElement("div");
    instrument.className = `bearing bearing-${key}`;
    const ring = document.createElement("span");
    ring.className = "bearing-ring";
    const value = document.createElement("strong");
    value.className = "bearing-value";
    value.textContent = String(count);
    const name = document.createElement("span");
    name.className = "bearing-label";
    name.textContent = label;
    instrument.append(ring, value, name);
    bearings.append(instrument);
  }
}

function renderDiagnostics(snapshot: Snapshot): void {
  const count = snapshot.diagnostics.unmappedRuntimes.length;
  if (count === 0) {
    diagnosticsKeycap.hidden = true;
    return;
  }
  diagnosticsKeycap.hidden = false;
  diagnosticsKeycap.textContent = `${count} unmapped runtime${count === 1 ? "" : "s"}`;
  diagnosticsKeycap.title = snapshot.diagnostics.unmappedRuntimes.map((u) => u.reference).join(", ");
}

// --- Selection and dossier ---------------------------------------------------

function selectService(serviceId: string | null): void {
  if (state.selectedId === serviceId) return;
  state.selectedId = serviceId;
  for (const dossier of document.querySelectorAll<HTMLElement>(".dossier")) {
    dossier.hidden = dossier.dataset.dossierFor !== serviceId;
  }
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    const isSelected = row.dataset.serviceId === serviceId;
    row.classList.toggle("selected", isSelected);
    const button = row.querySelector<HTMLButtonElement>(".service-select");
    if (button) button.setAttribute("aria-expanded", String(isSelected));
  }
  if (serviceId === null) {
    history.replaceState(null, "", window.location.pathname + window.location.search);
  } else {
    history.replaceState(null, "", `#service=${encodeURIComponent(serviceId)}`);
  }
  updateLogsDrawer();
  if (serviceId !== null) {
    const selectedRow = document.querySelector<HTMLElement>(
      `.service-row[data-service-id="${serviceId}"]`,
    );
    selectedRow?.scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
    const service = state.snapshot?.services.find((s) => s.id === serviceId);
    if (service) announce(`Selected ${service.name}`);
  }
}

function applyFilter(): void {
  if (state.snapshot === null) return;
  atlasFrame.classList.toggle("is-filtered", state.query.trim() !== "");
  const visible = filterVisible(state.snapshot.services, state.query);
  const fallback =
    state.priorSelection !== null && visible.has(state.priorSelection) ? state.priorSelection : null;
  const next = pickSelection(state.selectedId, fallback, state.snapshot.services, visible);
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    row.hidden = !visible.has(row.dataset.serviceId ?? "");
  }
  for (const dossier of document.querySelectorAll<HTMLElement>(".dossier")) {
    if (!dossier.hidden && dossier.dataset.dossierFor) {
      dossier.hidden = !visible.has(dossier.dataset.dossierFor);
    }
  }
  document.querySelector(".filter-empty")?.remove();
  for (const band of document.querySelectorAll<HTMLElement>(".band")) {
    const hasVisibleService = Array.from(band.querySelectorAll<HTMLElement>(".service-row"))
      .some((row) => !row.hidden);
    band.hidden = state.query.trim() !== "" && !hasVisibleService;
  }
  if (visible.size === 0 && state.query.trim() !== "") {
    const note = document.createElement("div");
    note.className = "atlas-message filter-empty";
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = "No services found";
    const detail = document.createElement("small");
    detail.textContent = "Try a service name, endpoint, environment, or component.";
    copy.append(title, detail);
    note.append(copy);
    latitudes.append(note);
    announce("No services match your search");
  }
  selectService(next);
}

// --- Logs --------------------------------------------------------------------

function updateLogsDrawer(): void {
  const service = state.snapshot?.services.find((s) => s.id === state.selectedId) ?? null;
  const sameService = logSubject.dataset.serviceId === service?.id;
  if (!sameService) logViewer.reset();
  logSource.disabled = service === null;
  logTail.disabled = service === null;
  logRefresh.disabled = service === null;
  logRefresh.textContent = "Refresh";
  logState.textContent = "";
  const previousSource = logSource.value;
  logSubject.dataset.serviceId = service?.id ?? "";
  logSource.textContent = "";
  if (service === null) {
    logSubject.textContent = "Select a service to inspect its logs.";
    logState.textContent = "";
    return;
  }
  logSubject.textContent = `Logs for ${service.name}`;
  const optionAll = document.createElement("option");
  optionAll.value = "";
  optionAll.textContent = "All sources";
  logSource.append(optionAll);
  for (const source of service.logSources) {
    const option = document.createElement("option");
    option.value = source.key;
    option.textContent = `${source.label} (${source.provider})`;
    if (!source.available) option.disabled = true;
    logSource.append(option);
  }
  if (sameService && service.logSources.some(source => source.key === previousSource && source.available)) {
    logSource.value = previousSource;
  }
  if (logDrawer.open) void loadLogs(service, false);
  else logViewer.setState("Open logs to load recent records.");
}

function currentLogKey(): string {
  return logCacheKey(state.selectedId ?? "", logSource.value, Number(logTail.value));
}

async function loadLogs(service: ServiceSnapshot, force: boolean): Promise<void> {
  const source = logSource.value === "" ? null : logSource.value;
  const tail = Number(logTail.value);
  const key = logCacheKey(service.id, source, tail);
  const cached = state.logs.get(key);
  const renderCached = () => {
    if (key !== currentLogKey()) return;
    const entry = state.logs.get(key);
    if (entry === undefined || entry === "loading") return;
    if ("status" in entry) renderLogsError(entry.message, entry.retryable);
    else renderLogs(entry);
  };
  if (!force && cached !== undefined && cached !== "loading") {
    renderCached();
    return;
  }
  logState.textContent = "Loading logs…";
  logRefresh.disabled = true;
  logRefresh.textContent = "Loading…";
  logViewer.setState("Loading recent log records…", true);
  const inFlight = pendingLogs.get(key);
  if (inFlight !== undefined) {
    await inFlight;
    renderCached();
    return;
  }
  state.logs.set(key, "loading");
  const request = (async () => {
    const result = await fetchLogs(service.id, source, tail);
    // Cache even when selection changed. Only the current key may render.
    state.logs.set(key, result.status === "ok" ? result.logs
      : { status: "error", message: result.message, retryable: result.retryable });
  })();
  pendingLogs.set(key, request);
  try {
    await request;
    renderCached();
  } finally {
    pendingLogs.delete(key);
  }
}

function logCacheKey(serviceId: string, source: string | null, tail: number): string {
  return `${serviceId}::${source ?? ""}::${tail}`;
}

function renderLogs(logs: LogsResponse): void {
  logRefresh.disabled = false;
  logRefresh.textContent = "Refresh";
  const loaded = new Date(logs.requestedAt);
  logState.textContent = Number.isNaN(loaded.getTime()) ? "Snapshot loaded" :
    `Loaded at ${loaded.toLocaleTimeString([], { hour12: false })}`;
  logViewer.setRecords(logs.records, logs.truncated);
}

function renderLogsError(message: string, retryable: boolean): void {
  logState.textContent = message;
  logRefresh.disabled = false;
  logRefresh.textContent = "Refresh";
  logViewer.setState(retryable
    ? "Logs are temporarily unavailable. Use Refresh to try again."
    : "Logs are unavailable for this source. Choose another source.");
}

// --- Copy action and toast ---------------------------------------------------

async function copyEndpoint(url: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(url);
    showToast("Endpoint copied");
    return;
  } catch {
    // fall through to the legacy execCommand path
  }
  const textarea = document.createElement("textarea");
  textarea.value = url;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  showToast(copied ? "Endpoint copied" : "Copy failed — select the endpoint manually");
}

function showToast(message: string): void {
  toastElement.textContent = message;
  toastElement.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastElement.hidden = true;
  }, 2400);
}

// --- Polling ----------------------------------------------------------------

function announce(message: string): void {
  atlasStatus.textContent = message;
  window.clearTimeout(announceTimer);
  announceTimer = window.setTimeout(() => {
    atlasStatus.textContent = "";
  }, 1000);
}

function setStaleNote(show: boolean, message: string): void {
  if (show) {
    staleNote.textContent = message;
    staleNote.hidden = false;
    announce(message);
  } else {
    staleNote.textContent = "";
    staleNote.hidden = true;
  }
}

async function refreshSnapshot(): Promise<void> {
  if (document.hidden || refreshingSnapshot) return;
  refreshingSnapshot = true;
  let result;
  try {
    result = await fetchSnapshot(state.etag);
  } finally {
    refreshingSnapshot = false;
  }
  if (result.status === "error") {
    setStaleNote(true, "Live data is stale — refresh failed.");
    return;
  }
  if (result.status === "not-modified") {
    setStaleNote(false, "");
    return;
  }
  state.etag = result.etag;
  setStaleNote(!result.snapshot.fresh, result.snapshot.fresh ? "" : "Live data is stale.");
  if (state.snapshot === null) {
    state.snapshot = result.snapshot;
    buildAtlas(result.snapshot);
    initSelection();
    if (state.query !== "") applyFilter();
    return;
  }
  const topologyChanged = state.snapshot.catalogRevision !== result.snapshot.catalogRevision
    || state.snapshot.services.map(s => s.id).join() !== result.snapshot.services.map(s => s.id).join();
  state.snapshot = result.snapshot;
  if (topologyChanged) {
    const selected = state.selectedId;
    state.selectedId = null;
    buildAtlas(result.snapshot);
    if (selected !== null && result.snapshot.services.some(s => s.id === selected)) {
      selectService(selected);
    } else {
      history.replaceState(null, "", window.location.pathname + window.location.search);
      updateLogsDrawer();
    }
    applyFilter();
  } else {
    patchAll(result.snapshot);
  }
}

function patchAll(snapshot: Snapshot): void {
  const byId = new Map(snapshot.services.map((s) => [s.id, s]));
  for (const row of document.querySelectorAll<HTMLElement>(".service-row")) {
    const service = byId.get(row.dataset.serviceId ?? "");
    if (service) patchRow(row, service);
  }
  if (state.selectedId !== null) {
    const dossier = document.querySelector<HTMLElement>(
      `.dossier[data-dossier-for="${state.selectedId}"]`,
    );
    const service = byId.get(state.selectedId);
    if (dossier && service) patchDossier(dossier, service);
  }
  renderBearings(snapshot);
  renderDiagnostics(snapshot);
}

function initSelection(): void {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("service");
  const services = state.snapshot?.services ?? [];
  const next = pickSelection(fromHash, null, services, new Set(services.map((s) => s.id)));
  if (next !== null) selectService(next);
  state.priorSelection = next;
}

// --- Event wiring ------------------------------------------------------------

function rowFromEvent(event: Event, selector: string): HTMLElement | null {
  const target = event.target;
  if (!(target instanceof Element)) return null;
  return target.closest<HTMLElement>(selector);
}

function wireEvents(): void {
  searchInput.addEventListener("input", () => {
    if (state.priorSelection === null && state.selectedId !== null) state.priorSelection = state.selectedId;
    state.query = searchInput.value;
    applyFilter();
  });

  latitudes.addEventListener("click", (event) => {
    const inspect = rowFromEvent(event, "[data-inspect-logs]");
    if (inspect) {
      selectService(inspect.dataset.inspectLogs ?? null);
      logDrawer.open = true;
      logDrawer.scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
      logDrawer.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
      return;
    }
    const button = rowFromEvent(event, ".service-select");
    if (button) {
      const row = button.closest<HTMLElement>(".service-row");
      if (row) {
        state.priorSelection = state.selectedId;
        selectService(row.dataset.serviceId ?? "");
      }
      return;
    }
    const copyAction = rowFromEvent(event, ".copy-action");
    if (copyAction) {
      event.preventDefault();
      const row = copyAction.closest<HTMLElement>(".service-row");
      const service = state.snapshot?.services.find((s) => s.id === row?.dataset.serviceId);
      if (service && service.action.type === "copy") void copyEndpoint(service.action.url);
      return;
    }
    const noAction = rowFromEvent(event, ".no-action");
    if (noAction) event.preventDefault();
  });

  logDrawer.addEventListener("toggle", () => {
    $("#log-toggle-label").textContent = logDrawer.open ? "Close logs" : "Open logs";
    if (!logDrawer.open) return;
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) void loadLogs(service, false);
  });

  logSource.addEventListener("change", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) void loadLogs(service, false);
  });
  logTail.addEventListener("change", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) void loadLogs(service, false);
  });
  logRefresh.addEventListener("click", () => {
    const service = state.snapshot?.services.find((s) => s.id === state.selectedId);
    if (service) void loadLogs(service, true);
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      searchInput.focus();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refreshSnapshot();
  });
  window.addEventListener("focus", () => void refreshSnapshot());

  for (const link of document.querySelectorAll<HTMLAnchorElement>("[data-territory-link]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const territory = link.dataset.territoryLink ?? "applications";
      const band = document.querySelector<HTMLElement>(`.band[data-territory="${territory}"]`);
      band?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" });
    });
  }
}

// --- Boot --------------------------------------------------------------------

async function boot(): Promise<void> {
  // The template keeps aria-live="polite" on #latitudes for no-JS semantics;
  // with the client active, patch-in-place recency rewrites every 15s would
  // announce constantly, so the live region is removed and meaningful changes
  // go through the dedicated #atlas-status region instead.
  latitudes.removeAttribute("aria-live");
  wireEvents();
  const result = await fetchSnapshot(null);
  if (result.status === "error") {
    latitudes.textContent = "";
    const note = document.createElement("p");
    note.className = "atlas-message";
    note.textContent = `The atlas could not be loaded: ${result.message}`;
    latitudes.append(note);
  } else if (result.status === "ok") {
    state.etag = result.etag;
    state.snapshot = result.snapshot;
    buildAtlas(result.snapshot);
    initSelection();
    if (state.query !== "") applyFilter();
  }
  window.setInterval(() => void refreshSnapshot(), POLL_INTERVAL_MS);
}

export function initAtlas(): void {
  void boot();
}
