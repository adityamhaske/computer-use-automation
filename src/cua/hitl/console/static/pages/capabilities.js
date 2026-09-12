// The capabilities catalog: a read-only browser over what CapabilityStore has sealed. List on the
// left, full artifact detail on the right — the classic list-detail pattern also used by the
// evidence page. Nothing here mutates anything; every field shown comes straight off
// api.capability()/api.capabilities(), never invented client-side.

import { api } from "../api.js";
import { statusPill, escapeHtml, truncate, fmtDate, fmtPercent, emptyState, jsonView } from "../util.js";

// The three risk tiers a capability's steps declare map onto three of the existing status-pill
// tones — there is no dedicated "risk" tone in the design system, and the task is explicit that
// this reuse (rather than a new CSS rule) is the intended treatment.
const RISK_TONE = { safe: "success", elevated: "needs_human", irreversible: "failed" };
const RISK_ORDER = ["safe", "elevated", "irreversible"];

const TABS = [
  { key: "inputs", label: "Inputs" },
  { key: "outputs", label: "Outputs" },
  { key: "steps", label: "Steps" },
  { key: "recovery", label: "Recovery" },
  { key: "provenance", label: "Provenance" },
  { key: "schema", label: "Tool schema" },
];

const STABILITY_FIELDS = [
  ["runs", "Runs"],
  ["successes", "Successes"],
  ["business_outcomes", "Business outcomes"],
  ["needs_human", "Needs human"],
  ["failures", "Failures"],
  ["wrong_actions", "Wrong actions"],
  ["determinism_holds", "Determinism holds"],
  ["mean_drift", "Mean drift"],
];

export async function render(container, ctx) {
  const ref = ctx.params[0] || null;

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Capabilities</h1>
        <p>The sealed catalog of capabilities this console can run, replay, or hand to a human —
        pick one from the list to inspect its contract, steps, and evidence.</p>
      </div>
    </div>
    <div class="list-detail" id="cap-root">
      <div class="list-panel" id="cap-list">
        <div class="skeleton" style="height:60px;"></div>
        <div class="skeleton" style="height:60px;"></div>
        <div class="skeleton" style="height:60px;"></div>
      </div>
      <div id="cap-detail">
        <div class="skeleton" style="height:320px;"></div>
      </div>
    </div>`;

  const root = container.querySelector("#cap-root");
  const listEl = container.querySelector("#cap-list");
  const detailEl = container.querySelector("#cap-detail");

  // Kick the detail fetch off in parallel with the list fetch (when there is one to make) —
  // attach a no-op catch immediately so a fast failure here doesn't surface as an unhandled
  // rejection while we're still awaiting the list below; the real handling happens further down.
  const detailPromise = ref ? api.capability(ref) : null;
  if (detailPromise) detailPromise.catch(() => {});

  let caps;
  try {
    caps = await api.capabilities();
  } catch (e) {
    root.innerHTML = `<div style="grid-column:1/-1;">${emptyState({
      iconName: "alertTriangle",
      title: "Could not load the catalog",
      body: String((e && e.message) || e),
    })}</div>`;
    return;
  }

  if (!caps.length) {
    root.innerHTML = `<div style="grid-column:1/-1;">${emptyState({
      iconName: "capabilities",
      title: "No capabilities in the catalog",
      body: "Nothing has been sealed into the catalog yet — run discovery first.",
    })}</div>`;
    return;
  }

  listEl.innerHTML = renderListItems(caps, ref);
  wireList(listEl, ctx);

  if (!ref) {
    detailEl.innerHTML = emptyState({
      iconName: "capabilities",
      title: "Select a capability",
      body: "Choose one from the list to see its contract, steps, and evidence.",
    });
    return;
  }

  try {
    const result = await detailPromise;
    renderDetail(detailEl, result);
  } catch (e) {
    detailEl.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Could not load this capability",
      body: String((e && e.message) || e),
    });
  }
}

// ---------------------------------------------------------------- list panel

function renderListItems(caps, activeRef) {
  return caps
    .map(
      (cap) => `
    <button class="list-panel-item" data-ref="${escapeHtml(cap.ref)}" data-active="${
        cap.ref === activeRef
      }">
      <span class="list-panel-item-title">${escapeHtml(cap.title || cap.id)}</span>
      <span class="list-panel-item-meta">${escapeHtml(cap.id)}@${escapeHtml(cap.version)} ${statusPill(
        cap.state
      )}</span>
    </button>`
    )
    .join("");
}

function wireList(listEl, ctx) {
  listEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-ref]");
    if (!btn) return;
    ctx.navigate("capabilities/" + btn.dataset.ref);
  });
}

// ---------------------------------------------------------------- detail panel

function renderDetail(detailEl, result) {
  const { capability, state, tool_schema, stability } = result;
  const app = (capability.surface && capability.surface.app) || {};
  const driverCaps =
    (capability.surface && capability.surface.driver_capabilities && capability.surface.driver_capabilities.join(", ")) ||
    "—";
  const maxRisk = highestRisk(capability.steps);

  detailEl.innerHTML = `
    <div class="detail-panel-header">
      <h2>${escapeHtml(capability.title || capability.id)}</h2>
      ${statusPill(state)}
    </div>
    <dl class="kv-grid" style="margin-bottom:var(--space-6);">
      <div class="kv-grid-col-2"><dt>ID</dt><dd class="mono">${escapeHtml(capability.id)}</dd></div>
      <div><dt>Version</dt><dd>${escapeHtml(capability.version)}</dd></div>
      <div><dt>Vendor / product</dt><dd>${escapeHtml(app.vendor || "—")} / ${escapeHtml(app.product || "—")}</dd></div>
      <div><dt>Driver capabilities</dt><dd>${escapeHtml(driverCaps)}</dd></div>
      <div><dt>Max risk</dt><dd>${maxRisk ? statusPill(RISK_TONE[maxRisk], maxRisk) : "—"}</dd></div>
      <div><dt>Content hash</dt><dd class="mono">${escapeHtml(truncate(capability.content_hash || "—", 20))}</dd></div>
    </dl>
    ${
      capability.description
        ? `<p style="color:var(--text-secondary);font-size:var(--text-sm);margin-bottom:var(--space-6);max-width:760px;">${escapeHtml(
            capability.description
          )}</p>`
        : ""
    }
    ${renderStability(stability)}
    <div class="tabs" id="cap-tabs">
      ${TABS.map(
        (t, i) => `<button class="tab" data-tab="${t.key}" data-active="${i === 0}">${t.label}</button>`
      ).join("")}
    </div>
    <div id="cap-tab-panels" style="margin-top:var(--space-5);">
      <div data-panel="inputs">${renderInputsTable(capability.inputs)}</div>
      <div data-panel="outputs" hidden>${renderOutputsTable(capability.outputs)}</div>
      <div data-panel="steps" hidden>${renderStepsTrack(capability.steps)}</div>
      <div data-panel="recovery" hidden>${renderRecovery(capability.recovery)}</div>
      <div data-panel="provenance" hidden>${renderProvenance(capability.provenance)}</div>
      <div data-panel="schema" hidden><pre class="json-viewer">${jsonView(tool_schema)}</pre></div>
    </div>`;

  wireTabs(detailEl);
}

function wireTabs(detailEl) {
  const tabsEl = detailEl.querySelector("#cap-tabs");
  const panelsEl = detailEl.querySelector("#cap-tab-panels");
  if (!tabsEl || !panelsEl) return;
  tabsEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tab]");
    if (!btn) return;
    tabsEl.querySelectorAll("[data-tab]").forEach((b) => (b.dataset.active = String(b === btn)));
    panelsEl.querySelectorAll("[data-panel]").forEach((p) => {
      p.hidden = p.dataset.panel !== btn.dataset.tab;
    });
  });
}

function highestRisk(steps) {
  if (!steps || !steps.length) return null;
  let best = null;
  let bestIdx = -1;
  for (const step of steps) {
    const idx = RISK_ORDER.indexOf(step.risk);
    if (idx > bestIdx) {
      bestIdx = idx;
      best = step.risk;
    }
  }
  return best;
}

// ---------------------------------------------------------------- stability

function renderStability(stability) {
  if (!stability || typeof stability !== "object" || !Object.keys(stability).length) return "";
  const cards = Object.entries(stability)
    .map(([name, evalResult]) => renderStabilityCard(name, evalResult))
    .join("");
  if (!cards) return "";
  return `
    <div class="section-title">Stability</div>
    <div class="card-grid" style="margin-bottom:var(--space-6);">${cards}</div>`;
}

function renderStabilityCard(name, ev) {
  if (!ev || typeof ev !== "object") return "";
  const rows = STABILITY_FIELDS.filter(([key]) => ev[key] !== undefined && ev[key] !== null)
    .map(([key, label]) => {
      let value = ev[key];
      if (key === "mean_drift") value = fmtPercent(value);
      else if (key === "determinism_holds") value = value ? "Yes" : "No";
      return `<div class="kv-row"><span class="kv-row-label">${escapeHtml(
        label
      )}</span><span class="kv-row-value">${escapeHtml(String(value))}</span></div>`;
    })
    .join("");
  return `
    <div class="card">
      <div class="card-header"><span class="card-title">${escapeHtml(name)}</span></div>
      <div class="card-pad kv-list">${
        rows || `<p style="color:var(--text-tertiary);font-size:var(--text-sm);">No data.</p>`
      }</div>
    </div>`;
}

// ---------------------------------------------------------------- tabs: inputs / outputs

function renderInputsTable(inputs) {
  if (!inputs || !inputs.length) {
    return emptyState({ iconName: "file", title: "No inputs", body: "This capability takes no inputs." });
  }
  const rows = inputs
    .map(
      (inp) => `
    <tr>
      <td data-label="Name"><code class="mono">${escapeHtml(inp.name)}</code></td>
      <td data-label="Type">${escapeHtml(inp.type || "—")}</td>
      <td data-label="Required">${inp.required ? "Yes" : "No"}</td>
      <td data-label="Sensitive">${inp.sensitive ? "Yes" : "No"}</td>
      <td data-label="Description">${escapeHtml(inp.description || "—")}</td>
    </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table class="data-table">
    <thead><tr><th>Name</th><th>Type</th><th>Required</th><th>Sensitive</th><th>Description</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderOutputsTable(outputs) {
  if (!outputs || !outputs.length) {
    return emptyState({ iconName: "file", title: "No outputs", body: "This capability declares no outputs." });
  }
  const rows = outputs
    .map(
      (out) => `
    <tr>
      <td data-label="Name"><code class="mono">${escapeHtml(out.name)}</code></td>
      <td data-label="Type">${escapeHtml(out.type || "—")}</td>
      <td data-label="Sensitive">${out.sensitive ? "Yes" : "No"}</td>
      <td data-label="Description">${escapeHtml(out.description || "—")}</td>
    </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table class="data-table">
    <thead><tr><th>Name</th><th>Type</th><th>Sensitive</th><th>Description</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

// ---------------------------------------------------------------- tabs: steps

function renderStepsTrack(steps) {
  if (!steps || !steps.length) {
    return emptyState({ iconName: "layers", title: "No steps", body: "This capability has no recorded steps." });
  }
  return `<div class="step-track">${steps
    .map((step) => {
      const tone = RISK_TONE[step.risk];
      return `
      <div class="step-track-item">
        <div class="step-track-dot"></div>
        <div class="step-track-body">
          <div class="step-track-title">
            <span class="mono">${escapeHtml(step.id)}</span>
            <span class="badge">${escapeHtml((step.action && step.action.type) || "—")}</span>
            ${tone ? statusPill(tone, step.risk) : ""}
          </div>
          <div class="step-track-meta">${escapeHtml(step.description || "—")}</div>
        </div>
      </div>`;
    })
    .join("")}</div>`;
}

// ---------------------------------------------------------------- tabs: recovery

function renderRecovery(recovery) {
  if (!recovery || !recovery.length) {
    return emptyState({
      iconName: "shield",
      title: "No recovery rules",
      body: "This capability declares no automated recovery strategies.",
    });
  }
  return `<div class="card-grid">${recovery
    .map(
      (r) => `
    <div class="card">
      <div class="card-header"><span class="card-title mono">${escapeHtml(r.id)}</span></div>
      <div class="card-pad kv-list">
        <div class="kv-row"><span class="kv-row-label">Max attempts</span><span class="kv-row-value">${
          r.max_attempts ?? "—"
        }</span></div>
        <div class="kv-row"><span class="kv-row-label">Backoff</span><span class="kv-row-value">${escapeHtml(
          fmtField(r.backoff)
        )}</span></div>
      </div>
      ${
        r.description
          ? `<p style="padding:0 var(--space-5) var(--space-5);color:var(--text-secondary);font-size:var(--text-sm);">${escapeHtml(
              r.description
            )}</p>`
          : ""
      }
    </div>`
    )
    .join("")}</div>`;
}

function fmtField(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ---------------------------------------------------------------- tabs: provenance

function renderProvenance(provenance) {
  const d = provenance && provenance.discovered_by;
  if (!d) {
    return emptyState({
      iconName: "info",
      title: "Hand-authored",
      body: "No discovery run — this capability was hand-authored.",
    });
  }
  return `<div class="kv-list">
    <div class="kv-row"><span class="kv-row-label">Model</span><span class="kv-row-value">${escapeHtml(
      d.model || "—"
    )}</span></div>
    <div class="kv-row"><span class="kv-row-label">Run</span><span class="kv-row-value mono">${escapeHtml(
      d.run_id || "—"
    )}</span></div>
    <div class="kv-row"><span class="kv-row-label">At</span><span class="kv-row-value">${escapeHtml(
      fmtDate(d.at)
    )}</span></div>
  </div>`;
}
