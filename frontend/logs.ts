import type { LogRecord, Severity } from "./api";

const errors: Severity[] = ["error", "critical", "alert", "emergency"];

function timestamp(value: string | null): string {
  if (value === null) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString([], { hour12: false });
}

function tone(severity: Severity | null): string {
  if (severity !== null && errors.includes(severity)) return "error";
  return severity === "warning" ? "warning" : "neutral";
}

function renderRecord(record: LogRecord): HTMLElement {
  const row = document.createElement("div");
  row.className = `log-row log-tone-${tone(record.severity)}`;
  const time = document.createElement("time");
  time.textContent = timestamp(record.timestamp);
  if (record.timestamp !== null) {
    time.dateTime = record.timestamp;
    time.title = record.timestamp;
  }
  const level = document.createElement("b");
  level.className = "log-level";
  level.textContent = record.severity?.toUpperCase() ?? "—";
  const source = document.createElement("code");
  source.className = "log-source-name";
  source.textContent = record.source;
  source.title = `${record.source} · ${record.provider} · ${record.stream}`;
  const message = document.createElement("span");
  message.className = "log-message";
  message.textContent = record.message;
  if (record.truncated) {
    const note = document.createElement("span");
    note.className = "log-truncation";
    note.textContent = " … [record shortened]";
    message.append(note);
  }
  row.append(time, level, source, message);
  return row;
}

/** One continuous, selectable log surface shared by the Atlas and unit journal. */
export class LogViewer {
  readonly element = document.createElement("div");
  readonly rows = document.createElement("div");
  private records: LogRecord[] = [];
  private truncated = false;
  private readonly search = document.createElement("input");
  private readonly severity = document.createElement("select");
  private readonly copy = document.createElement("button");
  private readonly count = document.createElement("span");
  private readonly feedback = document.createElement("span");
  private copyTimer: number | undefined;

  constructor(label: string) {
    this.element.className = "log-console";
    const tools = document.createElement("div");
    tools.className = "log-console-tools";
    this.search.type = "search";
    this.search.className = "log-search";
    this.search.placeholder = "Find in logs…";
    this.search.setAttribute("aria-label", `Find in ${label.toLowerCase()}`);
    this.search.addEventListener("input", () => this.render());
    this.severity.setAttribute("aria-label", "Log severity");
    for (const [value, text] of [["all", "All levels"], ["warning", "Warnings & errors"], ["error", "Errors only"]] as const) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      this.severity.append(option);
    }
    this.severity.addEventListener("change", () => this.render());
    const wrap = document.createElement("button");
    wrap.type = "button";
    wrap.textContent = "Wrap lines";
    wrap.setAttribute("aria-pressed", "true");
    wrap.addEventListener("click", () => {
      const enabled = wrap.getAttribute("aria-pressed") !== "true";
      wrap.setAttribute("aria-pressed", String(enabled));
      this.element.classList.toggle("log-nowrap", !enabled);
    });
    this.copy.type = "button";
    this.copy.textContent = "Copy logs";
    this.copy.title = "Copy records matching the current filters";
    this.copy.disabled = true;
    this.copy.addEventListener("click", () => void this.copyRecords());
    tools.append(this.search, this.severity, wrap, this.copy);

    this.rows.className = "log-rows";
    this.rows.tabIndex = 0;
    this.rows.setAttribute("role", "region");
    this.rows.setAttribute("aria-label", `${label} output`);
    const footer = document.createElement("div");
    footer.className = "log-console-footer";
    this.count.setAttribute("role", "status");
    this.feedback.setAttribute("role", "status");
    footer.append(this.count, this.feedback);
    this.element.append(tools, this.rows, footer);
    this.setState("Select a service to inspect its logs.");
  }

  reset(): void {
    this.search.value = "";
    this.severity.value = "all";
    this.setState("Select a service to inspect its logs.");
  }

  setState(message: string, loading = false): void {
    this.records = [];
    this.rows.replaceChildren();
    this.rows.setAttribute("aria-busy", String(loading));
    this.count.textContent = loading ? "Loading records…" : "";
    this.clearFeedback();
    this.copy.disabled = true;
    this.search.disabled = true;
    this.severity.disabled = true;
    const note = document.createElement("p");
    note.className = "log-empty";
    note.textContent = message;
    this.rows.append(note);
  }

  setRecords(records: LogRecord[], truncated = false): void {
    this.records = records;
    this.truncated = truncated;
    this.rows.setAttribute("aria-busy", "false");
    this.search.disabled = records.length === 0;
    this.severity.disabled = records.length === 0;
    this.render();
  }

  private visibleRecords(): LogRecord[] {
    const query = this.search.value.trim().toLowerCase();
    return this.records.filter((record) => {
      const isError = record.severity !== null && errors.includes(record.severity);
      const matchesSeverity = this.severity.value === "all" || isError ||
        (this.severity.value === "warning" && record.severity === "warning");
      return matchesSeverity && (query === "" ||
        `${record.message}\n${record.source}`.toLowerCase().includes(query));
    });
  }

  private render(): void {
    const visible = this.visibleRecords();
    const fragment = document.createDocumentFragment();
    for (const record of visible) fragment.append(renderRecord(record));
    if (visible.length === 0) {
      const note = document.createElement("p");
      note.className = "log-empty";
      note.textContent = this.records.length === 0
        ? "No log records returned for this source."
        : "No records match. Clear the search or choose All levels.";
      fragment.append(note);
    }
    this.rows.replaceChildren(fragment);
    this.rows.scrollTop = 0;
    this.count.textContent = `${visible.length === this.records.length ? visible.length : `${visible.length} of ${this.records.length}`} ${this.records.length === 1 ? "record" : "records"}${this.truncated ? " · Output limit reached; some records were omitted" : ""}`;
    this.copy.disabled = visible.length === 0;
    this.clearFeedback();
  }

  private clearFeedback(): void {
    window.clearTimeout(this.copyTimer);
    this.feedback.textContent = "";
  }

  private async copyRecords(): Promise<void> {
    const text = this.visibleRecords().map((record) =>
      `${record.timestamp ?? "—"} ${record.severity?.toUpperCase() ?? "—"} [${record.source}] ${record.message}${record.truncated ? " … [record shortened]" : ""}`,
    ).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      this.feedback.textContent = "Logs copied";
    } catch {
      this.feedback.textContent = "Copy unavailable. Select the log text to copy it.";
    }
    this.copyTimer = window.setTimeout(() => { this.feedback.textContent = ""; }, 4000);
  }
}
