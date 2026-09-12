// The application shell: router, live-poll store, theme, toasts, confirm dialog, sidebar/topbar.
// Pages themselves (pages/*.js) own no chrome — they receive a container and a ctx and render into
// it. This is the only file that touches #app-shell, #sidebar, #topbar and the singleton ws.js
// connection lifecycle (claim/release), so those concerns exist in exactly one place.

import { api } from "./api.js";
import { icon } from "./icons.js";
import { onLive, connectLive, disconnectLive } from "./ws.js";
import { statusTone, statusLabel, fmtRelative } from "./util.js";

import * as overviewPage from "./pages/overview.js";
import * as runsPage from "./pages/runs.js";
import * as interventionsPage from "./pages/interventions.js";
import * as capabilitiesPage from "./pages/capabilities.js";
import * as evidencePage from "./pages/evidence.js";
import * as settingsPage from "./pages/settings.js";
import * as aboutPage from "./pages/about.js";

// ---------------------------------------------------------------- nav + routes

const PRIMARY_NAV = [
  { path: "overview", label: "Overview", iconName: "overview" },
  { path: "runs", label: "Runs", iconName: "runs" },
  { path: "interventions", label: "Interventions", iconName: "interventions", badge: true },
  { path: "capabilities", label: "Capabilities", iconName: "capabilities" },
  { path: "evidence", label: "Evidence", iconName: "evidence" },
];

const FOOTER_NAV = [
  { path: "settings", label: "Settings", iconName: "settings" },
  { path: "about", label: "About", iconName: "about" },
];

const PAGES = {
  overview: overviewPage,
  runs: runsPage,
  interventions: interventionsPage,
  capabilities: capabilitiesPage,
  evidence: evidencePage,
  settings: settingsPage,
  about: aboutPage,
};

const TITLES = {
  overview: "Overview",
  runs: "Runs",
  interventions: "Interventions",
  capabilities: "Capabilities",
  evidence: "Evidence",
  settings: "Settings",
  about: "About",
};

// ---------------------------------------------------------------- store (state + interventions)

const store = {
  state: null,
  interventions: [],
  connected: false,
  listeners: new Set(),
};

function publish() {
  for (const fn of store.listeners) fn(store);
}

function subscribe(fn) {
  store.listeners.add(fn);
  fn(store);
  return () => store.listeners.delete(fn);
}

async function poll() {
  try {
    const [state, interventions] = await Promise.all([api.state(), api.interventions()]);
    store.state = state;
    store.interventions = interventions;
    store.connected = true;
  } catch {
    store.connected = false;
  }
  publish();
}

poll();
setInterval(poll, 2500);

// ---------------------------------------------------------------- theme

const THEME_KEY = "cua-theme";

function applyTheme(value) {
  if (value === "light" || value === "dark") {
    document.documentElement.setAttribute("data-theme", value);
    try {
      localStorage.setItem(THEME_KEY, value);
    } catch {
      /* private mode etc. — theme just won't persist */
    }
  } else {
    document.documentElement.removeAttribute("data-theme");
    try {
      localStorage.removeItem(THEME_KEY);
    } catch {
      /* ignore */
    }
  }
  updateThemeToggleIcon();
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "system";
}

function updateThemeToggleIcon() {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const explicit = document.documentElement.getAttribute("data-theme");
  const dark = explicit ? explicit === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  btn.innerHTML = icon(dark ? "sun" : "moon");
  btn.title = dark ? "Switch to light theme" : "Switch to dark theme";
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  const explicit = document.documentElement.getAttribute("data-theme");
  const dark = explicit ? explicit === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(dark ? "light" : "dark");
});

matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", updateThemeToggleIcon);
updateThemeToggleIcon();

// ---------------------------------------------------------------- toasts

function showToast({ title, message, tone = "info", timeout = 4200 }) {
  const root = document.getElementById("toast-root");
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.tone = tone;
  const iconName = { success: "check", danger: "alertTriangle", warning: "alertTriangle", info: "info" }[
    tone
  ];
  node.innerHTML = `${icon(iconName)}<div><strong style="display:block;font-weight:600;">${escapeTitle(
    title
  )}</strong>${message ? `<span style="color:var(--text-secondary);">${escapeTitle(message)}</span>` : ""}</div>`;
  root.appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

function escapeTitle(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ---------------------------------------------------------------- confirm dialog

function confirmDialog({ title, message, confirmLabel = "Confirm", tone = "primary" }) {
  const dialog = document.getElementById("confirm-dialog");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  const ok = document.getElementById("confirm-ok");
  ok.textContent = confirmLabel;
  ok.className = `btn btn-${tone}`;
  dialog.showModal();

  return new Promise((resolve) => {
    function cleanup(result) {
      dialog.close();
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      resolve(result);
    }
    function onOk() {
      cleanup(true);
    }
    function onCancel() {
      cleanup(false);
    }
    const cancel = document.getElementById("confirm-cancel");
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
  });
}

// ---------------------------------------------------------------- sidebar
//
// Built ONCE, then only ever patched (data-active toggled, the badge's text/visibility updated) —
// never re-rendered wholesale. Replacing .innerHTML on every ~2.5s poll tick (the original
// approach) tore down and rebuilt every nav button each time: harmless visually since the markup
// was identical, but it destroyed focus state, restarted any CSS transition, and — the thing that
// actually surfaced it — invalidated any reference an automated driver or assistive tech held to
// those buttons between one tick and the next.

function navItemHtml(item) {
  const badge = item.badge
    ? `<span class="badge nav-badge" data-path="${item.path}" hidden style="margin-left:auto;background:var(--warning-soft);color:var(--warning);border-color:transparent;"></span>`
    : "";
  return `<button class="nav-item" data-path="${item.path}" data-active="false">${icon(
    item.iconName
  )}<span class="sidebar-label">${item.label}</span>${badge}</button>`;
}

function buildSidebar() {
  const nav = document.getElementById("sidebar-nav");
  nav.innerHTML =
    `<div class="sidebar-section-label sidebar-label">Console</div>` +
    PRIMARY_NAV.map(navItemHtml).join("");

  const footer = document.getElementById("sidebar-footer");
  footer.innerHTML = FOOTER_NAV.map(navItemHtml).join("");

  for (const btn of [...nav.querySelectorAll(".nav-item"), ...footer.querySelectorAll(".nav-item")]) {
    btn.addEventListener("click", () => {
      navigate(btn.dataset.path);
      closeDrawer();
    });
  }
}

function updateSidebarActive(activePath) {
  for (const btn of document.querySelectorAll(".sidebar .nav-item")) {
    btn.dataset.active = String(btn.dataset.path === activePath);
  }
}

function updateSidebarBadges(count) {
  for (const el of document.querySelectorAll(".nav-badge")) {
    el.hidden = count === 0;
    el.textContent = String(count);
  }
}

buildSidebar();

// sidebar collapse (desktop) + drawer (mobile)
const shell = document.getElementById("app-shell");
const SIDEBAR_KEY = "cua-sidebar-collapsed";
try {
  if (localStorage.getItem(SIDEBAR_KEY) === "1") shell.dataset.sidebar = "collapsed";
} catch {
  /* ignore */
}

const sidebarToggle = document.getElementById("sidebar-toggle");
sidebarToggle.innerHTML = icon("chevronLeft");
sidebarToggle.addEventListener("click", () => {
  const collapsed = shell.dataset.sidebar === "collapsed";
  shell.dataset.sidebar = collapsed ? "expanded" : "collapsed";
  sidebarToggle.innerHTML = icon(collapsed ? "chevronLeft" : "chevronRight");
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "0" : "1");
  } catch {
    /* ignore */
  }
});
sidebarToggle.innerHTML = icon(shell.dataset.sidebar === "collapsed" ? "chevronRight" : "chevronLeft");

document.getElementById("topbar-menu-btn").innerHTML = icon("menu");
document.getElementById("topbar-menu-btn").addEventListener("click", () => {
  shell.dataset.drawer = shell.dataset.drawer === "open" ? "" : "open";
});
document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
function closeDrawer() {
  shell.dataset.drawer = "";
}

// ---------------------------------------------------------------- topbar status + global release

const STATE_COPY = {
  running: { tone: "running", text: (s) => `RUNNING · automation owns the session · epoch ${s.epoch}` },
  paused: { tone: "warning", text: (s) => `PAUSED · awaiting an operator · epoch ${s.epoch}` },
  human_control: {
    tone: "human",
    text: (s) => `HUMAN CONTROL · ${s.operator || "an operator"} owns the session · epoch ${s.epoch}`,
  },
  resuming: { tone: "warning", text: (s) => `RESUMING · reconciling before continuing · epoch ${s.epoch}` },
};

function renderTopbarStatus() {
  const pill = document.getElementById("topbar-status");
  const text = document.getElementById("topbar-status-text");
  const releaseBtn = document.getElementById("topbar-release-btn");

  if (!store.connected) {
    pill.dataset.tone = "neutral";
    text.textContent = "console offline";
    releaseBtn.hidden = true;
    return;
  }
  const s = store.state;
  const copy = STATE_COPY[s?.state] || STATE_COPY.running;
  pill.dataset.tone = copy.tone;
  text.textContent = copy.text(s);
  pill.querySelector(".status-dot").dataset.pulse = s.state === "human_control" ? "true" : "false";

  releaseBtn.hidden = s.state !== "human_control";
}

document.getElementById("topbar-release-btn").addEventListener("click", async () => {
  const ok = await confirmDialog({
    title: "Hand back to automation?",
    message: "The executor will reconcile the session and resume at the right step.",
    confirmLabel: "Hand back",
    tone: "human",
  });
  if (!ok) return;
  try {
    const r = await api.release();
    disconnectLive();
    showToast({ title: "Handed back to automation", message: r.human_delta, tone: "success" });
    await poll();
  } catch (e) {
    showToast({ title: "Release failed", message: String(e.message || e), tone: "danger" });
  }
});

subscribe((s) => {
  renderTopbarStatus();
  updateSidebarBadges(s.interventions.length);
});

// ---------------------------------------------------------------- router

function currentRoute() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [page, ...rest] = hash.split("/").filter(Boolean);
  return { page: page || "overview", params: rest };
}

let cleanupCurrent = null;

async function renderRoute() {
  const { page, params } = currentRoute();
  const mod = PAGES[page] || PAGES.overview;
  const resolvedPage = PAGES[page] ? page : "overview";

  document.getElementById("topbar-title").textContent = TITLES[resolvedPage];
  updateSidebarActive(resolvedPage);

  if (typeof cleanupCurrent === "function") {
    try {
      cleanupCurrent();
    } catch {
      /* page cleanup should not be able to break navigation */
    }
  }

  const container = document.getElementById("view");
  container.innerHTML = `<div class="skeleton" style="height:200px;"></div>`;

  const ctx = {
    api,
    params,
    subscribe,
    getStore: () => store,
    navigate,
    showToast,
    confirmDialog,
    icon,
    live: { onLive, connectLive, disconnectLive },
    statusTone,
    statusLabel,
    fmtRelative,
  };

  try {
    cleanupCurrent = (await mod.render(container, ctx)) || null;
  } catch (e) {
    container.innerHTML = `<div class="empty-state">${icon("alertTriangle")}<h3>This page hit an error</h3><p>${
      e && e.message ? escapeTitle(e.message) : "See the console for details."
    }</p></div>`;
    console.error(e);
  }
}

function navigate(path) {
  location.hash = `#/${path}`;
}

window.addEventListener("hashchange", renderRoute);
renderRoute();
