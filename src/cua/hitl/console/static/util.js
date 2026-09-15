// Shared formatting and status-mapping helpers. Split out from app.js so page modules can import
// it without a circular dependency (app.js imports the pages; the pages must not import app.js).

import { icon } from "./icons.js";

/** Map every status vocabulary in the system (ControlState, RunStatus, ObservationClass,
 * InterventionState, CatalogEntry.state) onto the 8-tone status-pill system consistently. */
const TONE_MAP = {
  // ControlState
  running: "running",
  paused: "needs_human",
  human_control: "human_control",
  resuming: "resuming",
  // RunStatus / RunResult
  success: "success",
  business_outcome: "business_outcome",
  needs_human: "needs_human",
  failed: "failed",
  // InterventionState
  open: "needs_human",
  claimed: "human_control",
  released: "resuming",
  resolved: "success",
  abandoned: "failed",
  // CatalogEntry.state
  draft: "draft",
  sealed: "sealed",
  tampered: "tampered",
  TAMPERED: "tampered",
};

const LABEL_MAP = {
  running: "Running",
  paused: "Paused",
  human_control: "Human control",
  resuming: "Resuming",
  success: "Success",
  business_outcome: "Business outcome",
  needs_human: "Needs human",
  failed: "Failed",
  open: "Open",
  claimed: "Claimed",
  released: "Released",
  resolved: "Resolved",
  abandoned: "Abandoned",
  draft: "Draft",
  sealed: "Sealed",
  tampered: "Tampered",
  TAMPERED: "Tampered",
};

export function statusTone(value) {
  return TONE_MAP[value] || "paused";
}

export function statusLabel(value) {
  return LABEL_MAP[value] || value || "Unknown";
}

export function statusPill(value, label) {
  const tone = statusTone(value);
  return `<span class="status-pill" data-status="${tone}"><span class="status-dot"></span>${escapeHtml(
    label || statusLabel(value)
  )}</span>`;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtRelative(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return String(iso);
  const diff = Date.now() - then;
  const s = Math.round(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function fmtDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

export function fmtPercent(fraction) {
  if (fraction == null) return "—";
  return `${Math.round(fraction * 100)}%`;
}

export function truncate(value, max = 64) {
  const s = String(value ?? "");
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

/** A small syntax-highlighted, indented JSON renderer — no library, keeps arbitrary evidence
 * readable instead of a dense unformatted dump. Returns an HTML string for .json-viewer. */
export function jsonView(value, indent = 0) {
  const pad = "  ".repeat(indent);
  const pad1 = "  ".repeat(indent + 1);
  if (value === null) return `<span class="json-null">null</span>`;
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    const items = value.map((v) => pad1 + jsonView(v, indent + 1)).join(",\n");
    return `[\n${items}\n${pad}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 0) return "{}";
    const items = keys
      .map(
        (k) =>
          `${pad1}<span class="json-key">"${escapeHtml(k)}"</span>: ${jsonView(value[k], indent + 1)}`
      )
      .join(",\n");
    return `{\n${items}\n${pad}}`;
  }
  if (typeof value === "string") return `<span class="json-string">"${escapeHtml(value)}"</span>`;
  if (typeof value === "number") return `<span class="json-number">${value}</span>`;
  if (typeof value === "boolean") return `<span class="json-bool">${value}</span>`;
  return escapeHtml(String(value));
}

export function emptyState({ iconName = "info", title, body }) {
  return `<div class="empty-state">${icon(iconName)}<h3>${escapeHtml(title)}</h3>${
    body ? `<p>${escapeHtml(body)}</p>` : ""
  }</div>`;
}

export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

/**
 * Write markup into an element only when it actually differs from what is already there.
 *
 * The console polls every 2.5s and every tick repainted whole regions by assigning `.innerHTML`.
 * On the overwhelmingly common tick where nothing changed, that tore down and rebuilt identical
 * markup — which looks like nothing at all and is not: it drops focus, restarts CSS transitions,
 * and invalidates any element reference held between ticks, by an assistive technology or by a
 * driver. The sidebar already avoided this by building once and patching; this is the same idea
 * for regions that genuinely are rebuilt from a template.
 *
 * Compares the generated string against the last one written rather than reading `.innerHTML`
 * back, because the browser normalises what it returns and the two would never match.
 *
 * Returns true when it wrote, so a caller can re-bind listeners only when there is new DOM.
 */
const lastWritten = new WeakMap();

export function setHtml(element, html) {
  if (!element) return false;
  if (lastWritten.get(element) === html) return false;
  lastWritten.set(element, html);
  element.innerHTML = html;
  return true;
}
