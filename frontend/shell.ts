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
  chip.className = `status status-${state}`;
  chip.textContent = label ?? stateLabel(state);
  return chip;
}

export function stateLabel(state: string): string {
  switch (state) {
    case "reachable": case "active": case "success": return "Reachable";
    case "degraded": case "warning": return "Degraded";
    case "down": case "failed": return "Down";
    case "dormant": return "Dormant";
    case "pending": return "Pending";
    case "denied": return "Denied";
    case "not_observed": return "Not observed";
    default: return "Unknown";
  }
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

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
