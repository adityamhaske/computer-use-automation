// Settings: five tabs. General is the only genuinely interactive one (theme, compact mode,
// reduce-motion — all client-side, cosmetic, persisted to localStorage). Automation, Safety, LLM
// and Session are read-only mirrors of `GET /api/settings` — real config, rendered as static
// text/chips, never as a form control that would silently do nothing when changed.

import { api } from "../api.js";
import { icon } from "../icons.js";
import { escapeHtml, fmtDuration, statusPill, emptyState } from "../util.js";

const THEME_KEY = "cua-theme"; // same key app.js's topbar toggle already reads/writes
const COMPACT_KEY = "cua-compact";
const NO_MOTION_KEY = "cua-no-motion";

const TABS = [
  { id: "general", label: "General" },
  { id: "automation", label: "Automation" },
  { id: "safety", label: "Safety" },
  { id: "llm", label: "LLM" },
  { id: "session", label: "Session" },
];

const READONLY_NOTE = {
  automation: "Read-only — set in config/policy.yaml and environment variables, enforced by PolicyEngine.",
  safety: "Read-only — set in config/policy.yaml, enforced by PolicyEngine.",
  llm: "Read-only — set via environment variables. The API key itself is never sent to the frontend.",
  session: "Read-only — session identity and hold duration are set at process startup.",
};

export async function render(container, ctx) {
  let activeTab = "general";
  let settings = null;
  let loadError = null;
  let loading = true;

  // Re-sync the two cosmetic classes to whatever was last persisted — app.js/index.html don't
  // know about these (only the theme toggle does), so a hard page reload lands here without them
  // applied until Settings is mounted again. Idempotent either way.
  document.body.classList.toggle("compact", readBool(COMPACT_KEY));
  document.documentElement.classList.toggle("no-motion", readBool(NO_MOTION_KEY));

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Settings</h1>
        <p>General is a real, working, per-browser preference panel. Automation, Safety, LLM and
        Session mirror the console's actual configuration for visibility — change those in
        config/policy.yaml or the environment, not here.</p>
      </div>
    </div>
    <div class="tabs" id="set-tabs" role="tablist" aria-label="Settings sections">
      ${TABS.map(
        (t) => `<button type="button" class="tab" role="tab" id="set-tab-${t.id}"
          aria-selected="${t.id === activeTab}" aria-controls="set-body"
          data-tab="${t.id}" data-active="${t.id === activeTab}">${escapeHtml(t.label)}</button>`
      ).join("")}
    </div>
    <div id="set-body" role="tabpanel" style="margin-top:var(--space-5);"></div>`;

  function paintTabs() {
    container.querySelectorAll("#set-tabs .tab").forEach((btn) => {
      const active = btn.dataset.tab === activeTab;
      btn.dataset.active = String(active);
      btn.setAttribute("aria-selected", String(active));
    });
  }

  function renderBody() {
    const body = container.querySelector("#set-body");
    if (activeTab === "general") {
      body.innerHTML = renderGeneral();
      wireGeneral(container);
      return;
    }
    if (loading) {
      body.innerHTML = skeletonHtml();
      return;
    }
    if (loadError) {
      body.innerHTML = emptyState({
        iconName: "alertTriangle",
        title: "Could not load settings",
        body: String(loadError.message || loadError),
      });
      return;
    }
    if (activeTab === "automation") body.innerHTML = renderAutomation(settings);
    else if (activeTab === "safety") body.innerHTML = renderSafety(settings);
    else if (activeTab === "llm") body.innerHTML = renderLLM(settings);
    else if (activeTab === "session") body.innerHTML = renderSession(settings);
  }

  container.querySelectorAll("#set-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === activeTab) return;
      activeTab = btn.dataset.tab;
      paintTabs();
      renderBody();
    });
  });

  renderBody(); // General shows immediately; the other tabs show a skeleton until the fetch below lands

  try {
    settings = await api.settings();
  } catch (e) {
    loadError = e;
  } finally {
    loading = false;
  }
  renderBody();
}

// ---------------------------------------------------------------- General (interactive)

function renderGeneral() {
  const theme = document.documentElement.getAttribute("data-theme") || "system";
  const compactOn = document.body.classList.contains("compact");
  const motionOn = document.documentElement.classList.contains("no-motion");

  return `
    <div class="settings-grid">
      <div class="card">
        <div class="card-header"><span class="card-title">Appearance</span></div>
        <div class="card-pad">
          <div class="form-section">
            <div class="form-row">
              <div>
                <div class="form-row-label">Theme</div>
                <div class="form-row-desc">System follows your OS setting; Light/Dark pin it explicitly for this browser.</div>
              </div>
              <div class="segmented" id="set-theme-segmented" role="group" aria-label="Theme">
                <button type="button" data-theme-choice="system" data-active="${theme === "system"}">System</button>
                <button type="button" data-theme-choice="light" data-active="${theme === "light"}">Light</button>
                <button type="button" data-theme-choice="dark" data-active="${theme === "dark"}">Dark</button>
              </div>
            </div>
            <div class="form-row">
              <div>
                <div class="form-row-label">Compact mode</div>
                <div class="form-row-desc">Tightens spacing across the console. Purely cosmetic — a hook for
                future density styling, no visual effect yet, but the class is really applied and saved.</div>
              </div>
              <button type="button" class="switch" id="set-compact-switch" role="switch"
                aria-label="Compact mode" aria-checked="${compactOn}" data-on="${compactOn}"></button>
            </div>
            <div class="form-row">
              <div>
                <div class="form-row-label">Reduce motion</div>
                <div class="form-row-desc">A manual override on top of your OS's prefers-reduced-motion,
                which this console already respects everywhere via CSS.</div>
              </div>
              <button type="button" class="switch" id="set-motion-switch" role="switch"
                aria-label="Reduce motion" aria-checked="${motionOn}" data-on="${motionOn}"></button>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}

function wireGeneral(container) {
  const segButtons = Array.from(container.querySelectorAll("#set-theme-segmented [data-theme-choice]"));
  segButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const choice = btn.dataset.themeChoice;
      applyTheme(choice);
      segButtons.forEach((b) => (b.dataset.active = String(b.dataset.themeChoice === choice)));
    });
  });

  const compactSwitch = container.querySelector("#set-compact-switch");
  compactSwitch.addEventListener("click", () => {
    const next = compactSwitch.dataset.on !== "true";
    applyCompact(next);
    compactSwitch.dataset.on = String(next);
    compactSwitch.setAttribute("aria-checked", String(next));
  });

  const motionSwitch = container.querySelector("#set-motion-switch");
  motionSwitch.addEventListener("click", () => {
    const next = motionSwitch.dataset.on !== "true";
    applyNoMotion(next);
    motionSwitch.dataset.on = String(next);
    motionSwitch.setAttribute("aria-checked", String(next));
  });
}

function applyTheme(choice) {
  if (choice === "light" || choice === "dark") {
    document.documentElement.setAttribute("data-theme", choice);
    try {
      localStorage.setItem(THEME_KEY, choice);
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
}

function applyCompact(on) {
  document.body.classList.toggle("compact", on);
  try {
    if (on) localStorage.setItem(COMPACT_KEY, "true");
    else localStorage.removeItem(COMPACT_KEY);
  } catch {
    /* ignore */
  }
}

function applyNoMotion(on) {
  document.documentElement.classList.toggle("no-motion", on);
  try {
    if (on) localStorage.setItem(NO_MOTION_KEY, "true");
    else localStorage.removeItem(NO_MOTION_KEY);
  } catch {
    /* ignore */
  }
}

function readBool(key) {
  try {
    return localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- Automation (read-only)

function renderAutomation(settings) {
  const policy = settings.policy;
  const budgets = policy?.budgets;
  const llm = settings.llm || {};

  return `
    <div class="settings-grid">
      ${readonlyHeader("automation", settings)}
      ${!policy ? policyMissingNote(settings) : ""}
      <div class="card">
        <div class="card-header"><span class="card-title">Policy budgets</span></div>
        <div class="card-pad">
          <dl class="kv-grid">
            ${kv("Max steps", budgets ? budgets.max_steps : null)}
            ${kv("Max duration", budgets ? fmtDuration(budgets.max_duration_ms) : null)}
            ${kv("Max recovery attempts (total)", budgets ? budgets.max_recovery_attempts_total : null)}
          </dl>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">LLM step budget</span></div>
        <div class="card-pad">
          <dl class="kv-grid">
            ${kv("Max steps", llm.max_steps)}
            ${kv("Max tokens", llm.max_tokens)}
            ${kv("Timeout", llm.timeout_s != null ? `${llm.timeout_s}s` : null)}
          </dl>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- Safety (read-only)

function renderSafety(settings) {
  const policy = settings.policy;

  if (!policy) {
    return `
      <div class="settings-grid">
        ${readonlyHeader("safety", settings)}
        ${policyMissingNote(settings)}
      </div>`;
  }

  const allowlist = policy.allowlist || {};
  const actions = policy.actions || {};
  const risk = policy.risk || {};
  const dispositions = policy.dispositions || {};
  const gates = policy.replay_gates || {};
  const redaction = policy.redaction || {};
  const ruleNames = (redaction.rules || []).map((r) => r.name);

  return `
    <div class="settings-grid">
      ${readonlyHeader("safety", settings)}

      <div class="card">
        <div class="card-header"><span class="card-title">Allowlist</span></div>
        <div class="card-pad">
          <div class="card-eyebrow">Domains</div>
          <div class="chip-row">${chips(allowlist.domains)}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Actions</span></div>
        <div class="card-pad" style="display:flex;flex-direction:column;gap:var(--space-4);">
          <div>
            <div class="card-eyebrow">Allowed</div>
            <div class="chip-row">${chips(actions.allowed)}</div>
          </div>
          <div>
            <div class="card-eyebrow">Human only</div>
            <div class="chip-row">${chips(actions.human_only)}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Risk</span></div>
        <div class="card-pad" style="display:flex;flex-direction:column;gap:var(--space-4);">
          <dl class="kv-grid">${kv("Default tier", risk.default_tier)}</dl>
          <div>
            <div class="card-eyebrow">Elevated lexicon</div>
            <div class="chip-row">${chips(risk.lexicon?.elevated)}</div>
          </div>
          <div>
            <div class="card-eyebrow">Irreversible lexicon</div>
            <div class="chip-row">${chips(risk.lexicon?.irreversible)}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Dispositions</span></div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Risk tier</th><th>Automation</th><th>Human</th></tr></thead>
            <tbody>
              ${["safe", "elevated", "irreversible"]
                .map(
                  (tier) => `
                <tr>
                  <td data-label="Risk tier">${escapeHtml(tierLabel(tier))}</td>
                  <td data-label="Automation">${escapeHtml(String(dispositions.automation?.[tier] ?? "—"))}</td>
                  <td data-label="Human">${escapeHtml(String(dispositions.human?.[tier] ?? "—"))}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Replay gates</span></div>
        <div class="card-pad kv-list">
          ${boolRow("Irreversible requires approval", gates.irreversible_requires_approval)}
          ${boolRow("Irreversible requires caller opt-in", gates.irreversible_requires_caller_optin)}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Redaction</span></div>
        <div class="card-pad kv-list">
          ${boolRow("Always redact sensitive inputs", redaction.always_redact_sensitive_inputs)}
          ${boolRow("Blur sensitive regions in screenshots", redaction.blur_sensitive_regions_in_screenshots)}
        </div>
        <div class="card-pad" style="padding-top:0;">
          <div class="card-eyebrow">Rules (${ruleNames.length})</div>
          <div class="chip-row">${chips(ruleNames)}</div>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- LLM (read-only)

function renderLLM(settings) {
  const llm = settings.llm || {};

  return `
    <div class="settings-grid">
      ${readonlyHeader("llm", settings)}
      <div class="card">
        <div class="card-header"><span class="card-title">Model</span></div>
        <div class="card-pad">
          <dl class="kv-grid">
            ${kvHtml("Model", fieldStatic(llm.model))}
            ${kvHtml("Base URL", fieldStatic(llm.base_url))}
            ${kvHtml(
              "API key",
              statusPill(
                llm.api_key_configured ? "success" : "needs_human",
                llm.api_key_configured ? "Configured" : "Not configured"
              )
            )}
          </dl>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">Limits</span></div>
        <div class="card-pad">
          <dl class="kv-grid">
            ${kv("Max steps", llm.max_steps)}
            ${kv("Max tokens", llm.max_tokens)}
            ${kv("Timeout", llm.timeout_s != null ? `${llm.timeout_s}s` : null)}
          </dl>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- Session (read-only)

function renderSession(settings) {
  const session = settings.session || {};

  return `
    <div class="settings-grid">
      ${readonlyHeader("session", settings)}
      <div class="card">
        <div class="card-header"><span class="card-title">Session</span></div>
        <div class="card-pad">
          <dl class="kv-grid">
            ${kvHtml("Session ID", fieldStatic(session.session_id))}
            ${kv("Default hold", session.default_hold_minutes != null ? `${session.default_hold_minutes} min` : null)}
          </dl>
        </div>
      </div>
      <div class="card card-pad">
        <p style="font-size:var(--text-sm);color:var(--text-secondary);margin:0;">There is no operator identity
        or auth system yet — every action in this console is attributed to a single fixed
        <code class="mono">"operator"</code> identity. This is a documented cut, not a gap hidden from you.</p>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- shared render helpers

function readonlyHeader(tabId, settings) {
  return `<p class="form-row-desc" style="margin:0;">${escapeHtml(READONLY_NOTE[tabId])}${
    settings.policy_file
      ? ` Policy file: <span class="field-static" style="margin-left:4px;">${escapeHtml(
          settings.policy_file
        )}</span>`
      : ""
  }</p>`;
}

function policyMissingNote(settings) {
  return `<div class="card card-pad">
    <div>
      <strong style="font-size:var(--text-sm);">No policy loaded</strong>
      <p style="margin-top:4px;color:var(--text-secondary);font-size:var(--text-sm);margin-bottom:0;">
        ${
          settings.policy_file
            ? `PolicyEngine looked for <code class="mono">${escapeHtml(settings.policy_file)}</code> and found nothing usable. `
            : ""
        }Fields that come from the policy file are unavailable below.
      </p>
    </div>
  </div>`;
}

function skeletonHtml() {
  return `<div class="settings-grid">
    <div class="skeleton" style="height:140px;"></div>
    <div class="skeleton" style="height:140px;"></div>
  </div>`;
}

function kv(label, value) {
  const text = value === null || value === undefined || value === "" ? "—" : String(value);
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(text)}</dd></div>`;
}

function kvHtml(label, html) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${html}</dd></div>`;
}

function fieldStatic(value) {
  return `<span class="field-static">${escapeHtml(value === null || value === undefined || value === "" ? "—" : value)}</span>`;
}

function chips(list) {
  if (!list || !list.length) {
    return `<span style="font-size:var(--text-xs);color:var(--text-tertiary);">none configured</span>`;
  }
  return list.map((v) => `<span class="badge">${escapeHtml(v)}</span>`).join("");
}

function boolRow(label, value) {
  let valueHtml;
  if (value === true) {
    valueHtml = `<span style="font-weight:var(--weight-semibold);color:var(--success);">Yes</span>`;
  } else if (value === false) {
    valueHtml = `<span style="color:var(--text-tertiary);">No</span>`;
  } else {
    valueHtml = `<span style="color:var(--text-tertiary);">—</span>`;
  }
  return `<div class="kv-row"><span class="kv-row-label">${escapeHtml(
    label
  )}</span><span class="kv-row-value">${valueHtml}</span></div>`;
}

function tierLabel(tier) {
  return tier.charAt(0).toUpperCase() + tier.slice(1);
}
