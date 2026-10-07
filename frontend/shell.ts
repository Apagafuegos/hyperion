// Shared shell primitives for the operations console: toast, status chips,
// instrument rows, and the lifted confirmation surface.

let toastTimer: number | undefined;
const toastElement = () => document.querySelector<HTMLElement>("#toast");

export function showToast(message: string): void {
  const element = toastElement();
  if (element === null) return;
  element.textContent = message;
  element.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    element.hidden = true;
  }, 2400);
}

export function statusChip(state: string, label: string | null = null): HTMLElement {
  const chip = document.createElement("span");
  const tone = ["success", "active"].includes(state) ? "reachable"
    : ["failed", "failure"].includes(state) ? "down"
    : ["warning", "denied"].includes(state) ? "degraded"
    : ["info", "not_observed", "inactive"].includes(state) ? "dormant" : state;
  chip.className = `status status-${tone}`;
  chip.textContent = label ?? stateLabel(state);
  return chip;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "reachable": return "Reachable";
    case "degraded": return "Degraded";
    case "down": return "Down";
    case "dormant": return "Dormant";
    case "active": return "Active";
    case "inactive": return "Inactive";
    case "activating": return "Starting";
    case "deactivating": return "Stopping";
    case "success": return "Successful";
    case "failure": case "failed": return "Failed";
    case "warning": return "Warning";
    case "info": return "Information";
    case "pending": return "Pending";
    case "denied": return "Denied";
    case "not_observed": return "Not observed";
    case "available": return "Available";
    case "unavailable": return "Unavailable";
    case "fresh": return "Fresh";
    case "stale": return "Stale";
    default: return "Unknown";
  }
}

export { formatBytes } from "./logic";

export function formatObserved(iso: string | null, now: number = Date.now()): string {
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

export function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function formatClock(iso: string | null): string {
  if (iso === null) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

// --- Confirmation surface ---------------------------------------------------

interface ConfirmOptions {
  title?: string;
  impact: string;
  state?: string;
  confirmLabel?: string;
  onConfirm: () => void | Promise<void>;
}

export function askConfirmation(options: ConfirmOptions): void {
  const panel = document.querySelector<HTMLElement>("#confirmation");
  const impact = document.querySelector<HTMLElement>("#confirmation-impact");
  const stateEl = document.querySelector<HTMLElement>("#confirmation-state");
  const title = document.querySelector<HTMLElement>("#confirmation-title");
  const confirm = document.querySelector<HTMLButtonElement>("#confirmation-confirm");
  const cancel = document.querySelector<HTMLButtonElement>("#confirmation-cancel");
  if (panel === null || impact === null || stateEl === null || confirm === null || cancel === null) {
    return;
  }
  if (title !== null) title.textContent = options.title ?? "Confirm operation";
  impact.textContent = options.impact;
  stateEl.textContent = options.state ?? "";
  stateEl.hidden = options.state === undefined;
  confirm.textContent = options.confirmLabel ?? "Confirm";
  panel.hidden = false;

  const onCancel = () => {
    panel.hidden = true;
    confirm.removeEventListener("click", onConfirmClick);
    cancel.removeEventListener("click", onCancel);
    document.removeEventListener("keydown", onKeydown);
  };
  const onConfirmClick = async () => {
    panel.hidden = true;
    confirm.removeEventListener("click", onConfirmClick);
    cancel.removeEventListener("click", onCancel);
    document.removeEventListener("keydown", onKeydown);
    await options.onConfirm();
  };
  const onKeydown = (event: KeyboardEvent) => {
    if (event.key === "Escape") onCancel();
  };
  confirm.addEventListener("click", onConfirmClick);
  cancel.addEventListener("click", onCancel);
  document.addEventListener("keydown", onKeydown);
  cancel.focus();
}
