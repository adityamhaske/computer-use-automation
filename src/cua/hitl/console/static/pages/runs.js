// Runs: every automation run and replay the executor recorded. List view browses/filters the
// catalog of runs; detail view replays one run's full record, its raw trace.jsonl, and whatever
// snapshots/screenshots evidence exists for it. Read-only end to end — nothing here mutates state.

import { api } from "../api.js";
import { icon } from "../icons.js";
import {
  statusPill,
  escapeHtml,
  truncate,
  fmtDate,
  fmtDuration,
  fmtPercent,
  jsonView,
  emptyState,
} from "../util.js";

const KIND_FILTERS = [
  { kind: "all", label: "All" },
  { kind: "discovery", label: "Discovery" },
  { kind: "replay", label: "Replay" },
  { kind: "escalation", label: "Escalation" },
];

export async function render(container, ctx) {
  const [kind, runId] = ctx.params || [];
  if (kind && runId) {
    await renderDetail(container, ctx, kind, runId);
  } else {
    await renderList(container, ctx);
  }
}

// ==================================================================== list view

async function renderList(container, ctx) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Runs</h1>
        <p>Every discovery exploration, scripted replay, and human escalation the executor has
        recorded, newest first.</p>
      </div>
    </div>
    <div class="filter-bar">
      <input type="search" class="search-input" id="runs-search"
        placeholder="Search run ID or capability…" aria-label="Search runs" />
      <div class="segmented" id="runs-kind-filter">
        ${KIND_FILTERS.map(
          (f, i) =>
            `<button type="button" data-kind="${f.kind}" data-active="${i === 0}">${f.label}</button>`
        ).join("")}
      </div>
    </div>
    <div id="runs-list-slot"><div class="skeleton" style="height:320px;"></div></div>
  `;

  const slot = container.querySelector("#runs-list-slot");
  const searchInput = container.querySelector("#runs-search");
  const kindFilter = container.querySelector("#runs-kind-filter");

  let runs = [];
  try {
    runs = await api.runs();
  } catch (e) {
    slot.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Couldn't load runs",
      body: String((e && e.message) || e),
    });
    return;
  }

  runs = runs
    .slice()
    .sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));

  let activeKind = "all";

  function paint() {
    if (!runs.length) {
      slot.innerHTML = emptyState({
        iconName: "runs",
        title: "No runs recorded yet",
        body: "Once automation executes a capability or a replay runs, its record shows up here.",
      });
      return;
    }

    const term = searchInput.value.trim().toLowerCase();
    const filtered = runs.filter((r) => {
      if (activeKind !== "all" && r.kind !== activeKind) return false;
      if (!term) return true;
      return (
        (r.run_id || "").toLowerCase().includes(term) ||
        (r.capability_ref || "").toLowerCase().includes(term)
      );
    });

    if (!filtered.length) {
      slot.innerHTML = emptyState({
        iconName: "search",
        title: "No runs match your filters",
        body: "Try a different search term, or switch the type filter back to All.",
      });
      return;
    }

    slot.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Capability</th>
              <th>Type</th>
              <th>Status</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Steps</th>
              <th>Drift</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>${filtered.map(runRowHtml).join("")}</tbody>
        </table>
      </div>
    `;

    slot.querySelectorAll("tbody tr[data-clickable]").forEach((tr) => {
      const go = () => ctx.navigate(`runs/${tr.dataset.kind}/${tr.dataset.runId}`);
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      });
    });
  }

  searchInput.addEventListener("input", paint);
  kindFilter.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-kind]");
    if (!btn) return;
    activeKind = btn.dataset.kind;
    kindFilter.querySelectorAll("button").forEach((b) => (b.dataset.active = String(b === btn)));
    paint();
  });

  paint();
}

function runRowHtml(r) {
  return `
    <tr data-clickable="true" tabindex="0" data-run-id="${escapeHtml(r.run_id)}" data-kind="${escapeHtml(
    r.kind
  )}">
      <td data-label="Run"><code class="mono">${escapeHtml(truncate(r.run_id, 28))}</code></td>
      <td data-label="Capability">${escapeHtml(r.capability_ref || "—")}</td>
      <td data-label="Type"><span class="badge">${escapeHtml(capitalize(r.kind))}</span></td>
      <td data-label="Status">${statusPill(r.status)}</td>
      <td data-label="Started">${fmtDate(r.started_at)}</td>
      <td data-label="Duration">${fmtDuration(r.duration_ms)}</td>
      <td data-label="Steps">${r.steps ?? "—"}</td>
      <td data-label="Drift">${fmtPercent(r.drift_score)}</td>
      <td data-label="Evidence"><code class="mono">${escapeHtml(r.evidence_ref || "—")}</code></td>
    </tr>`;
}

// ==================================================================== detail view

async function renderDetail(container, ctx, kind, runId) {
  container.innerHTML = `
    <button type="button" class="back-link" id="runs-back">${icon(
      "chevronLeft"
    )} Back to runs</button>
    <div id="runs-detail-slot"><div class="skeleton" style="height:420px;"></div></div>
  `;
  container.querySelector("#runs-back").addEventListener("click", () => ctx.navigate("runs"));

  const slot = container.querySelector("#runs-detail-slot");

  let data;
  try {
    data = await api.run(kind, runId);
  } catch (e) {
    slot.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Couldn't load this run",
      body: String((e && e.message) || e),
    });
    return;
  }

  const record = data && data.record;
  const events = (data && data.events) || [];
  const snapshots = (data && data.snapshots) || [];
  const screenshots = (data && data.screenshots) || [];

  if (!record) {
    slot.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Run record not found",
      body: `No record on disk for ${kind}/${runId}.`,
    });
    return;
  }

  const result = record.result || null;

  slot.innerHTML = `
    <div class="detail-panel-header">
      <h2>${escapeHtml(record.run_id)}</h2>
      ${statusPill(result && result.status)}
    </div>

    <div class="card card-pad" style="margin-bottom:var(--space-6);">
      <dl class="kv-grid">
        <div class="kv-grid-col-2"><dt>Capability</dt><dd class="mono">${escapeHtml(record.capability_ref || "—")}</dd></div>
        <div><dt>Kind</dt><dd>${escapeHtml(capitalize(record.kind || kind))}</dd></div>
        <div><dt>Started</dt><dd>${fmtDate(record.started_at)}</dd></div>
        <div><dt>Duration</dt><dd>${fmtDuration(record.duration_ms)}</dd></div>
        <div><dt>Drift</dt><dd>${fmtPercent(result && result.drift_score)}</dd></div>
        <div><dt>Human actions</dt><dd>${record.human_actions ?? "—"}</dd></div>
        <div class="kv-grid-col-2"><dt>Evidence</dt><dd><code class="mono">${escapeHtml(
          record.evidence_ref || "—"
        )}</code></dd></div>
      </dl>
    </div>

    ${resultOutcomeHtml(result)}

    <div class="card card-pad" style="margin-bottom:var(--space-6);">
      <div class="section-title">Steps</div>
      ${stepsHtml(record.steps)}
    </div>

    ${traceSectionHtml(events, snapshots, screenshots)}
  `;

  wireTrace(container, ctx, kind, runId, events, snapshots, screenshots);
}

// -------------------------------------------------------------- outcome/error/intervention

function resultOutcomeHtml(result) {
  if (!result) return "";

  if (result.outcome) {
    return `
      <div class="card" style="margin-bottom:var(--space-6);">
        <div class="card-header"><span class="card-title">Outcome</span></div>
        <div class="card-pad kv-list">
          <div class="kv-row"><span class="kv-row-label">Code</span><span class="kv-row-value">${escapeHtml(
            result.outcome.code || "—"
          )}</span></div>
        </div>
        <div class="card-pad" style="padding-top:0;">
          <pre class="json-viewer">${jsonView(result.outcome.data ?? {})}</pre>
        </div>
      </div>`;
  }

  if (result.error) {
    const e = result.error;
    return `
      <div class="card" style="margin-bottom:var(--space-6);">
        <div class="card-header"><span class="card-title">Error</span></div>
        <div class="card-pad kv-list">
          <div class="kv-row"><span class="kv-row-label">Code</span><span class="kv-row-value">${escapeHtml(
            e.code || "—"
          )}</span></div>
          <div class="kv-row"><span class="kv-row-label">Message</span><span class="kv-row-value">${escapeHtml(
            e.message || "—"
          )}</span></div>
          <div class="kv-row"><span class="kv-row-label">Step</span><span class="kv-row-value">${escapeHtml(
            e.step_id || "—"
          )}</span></div>
          <div class="kv-row"><span class="kv-row-label">Expected</span><span class="kv-row-value">${scalar(
            e.expected
          )}</span></div>
          <div class="kv-row"><span class="kv-row-label">Observed</span><span class="kv-row-value">${scalar(
            e.observed
          )}</span></div>
        </div>
      </div>`;
  }

  if (result.intervention) {
    const iv = result.intervention;
    return `
      <div class="card" style="margin-bottom:var(--space-6);">
        <div class="card-header"><span class="card-title">Intervention</span></div>
        <div class="card-pad kv-list">
          <div class="kv-row"><span class="kv-row-label">Reason</span><span class="kv-row-value">${escapeHtml(
            iv.reason || "—"
          )}</span></div>
          <div class="kv-row"><span class="kv-row-label">Step</span><span class="kv-row-value">${escapeHtml(
            iv.step_id || "—"
          )}</span></div>
        </div>
      </div>`;
  }

  return "";
}

// -------------------------------------------------------------- steps

function stepsHtml(steps) {
  if (!steps || !steps.length) {
    return emptyState({
      iconName: "layers",
      title: "No steps recorded",
      body: "This run has no step records.",
    });
  }
  return `<div class="step-track">${steps.map(stepItemHtml).join("")}</div>`;
}

function stepItemHtml(step) {
  let meta = `${escapeHtml(step.actor || "—")} · ${fmtDuration(step.duration_ms)}`;
  if (step.recovery_rule_applied) meta += ` · recovered via ${escapeHtml(step.recovery_rule_applied)}`;
  if (step.error_code) meta += ` · ${escapeHtml(step.error_code)}`;

  return `
    <div class="step-track-item" data-authorized="${step.authorized ? "true" : "false"}">
      <div class="step-track-dot"></div>
      <div class="step-track-body">
        <div class="step-track-title">${escapeHtml(step.step_id || "—")} <span class="badge">${escapeHtml(
    step.action_type || "—"
  )}</span></div>
        <div class="step-track-meta">${meta}</div>
      </div>
    </div>`;
}

// -------------------------------------------------------------- raw trace tabs

function traceTabs(events, snapshots, screenshots) {
  const tabs = [];
  if (events.length) tabs.push({ key: "events", label: "Events" });
  if (snapshots.length) tabs.push({ key: "snapshots", label: "Snapshots" });
  if (screenshots.length) tabs.push({ key: "screenshots", label: "Screenshots" });
  return tabs;
}

function traceSectionHtml(events, snapshots, screenshots) {
  const tabs = traceTabs(events, snapshots, screenshots);

  if (!tabs.length) {
    return (
      `<div class="section-title">Raw trace</div>` +
      emptyState({
        iconName: "file",
        title: "No trace data",
        body: "This run has no recorded events, snapshots, or screenshots.",
      })
    );
  }

  return `
    <div class="section-title">Raw trace</div>
    <div class="tabs" id="runs-trace-tabs">
      ${tabs
        .map(
          (t, i) =>
            `<button type="button" class="tab" data-tab="${t.key}" data-active="${i === 0}">${t.label}</button>`
        )
        .join("")}
    </div>
    <div id="runs-trace-panel" style="margin-top:var(--space-4);"></div>
  `;
}

function wireTrace(container, ctx, kind, runId, events, snapshots, screenshots) {
  const tabsBar = container.querySelector("#runs-trace-tabs");
  const panel = container.querySelector("#runs-trace-panel");
  if (!tabsBar || !panel) return;

  function paintTab(tabKey) {
    if (tabKey === "events") {
      panel.innerHTML = `<pre class="json-viewer">${jsonView(events)}</pre>`;
      return;
    }

    if (tabKey === "snapshots") {
      panel.innerHTML = `
        <div class="chip-row">
          ${snapshots
            .map(
              (name) =>
                `<button type="button" class="btn btn-secondary btn-sm" data-snapshot="${escapeHtml(
                  name
                )}">${escapeHtml(name)}</button>`
            )
            .join("")}
        </div>
        <div id="runs-snapshot-view" style="margin-top:var(--space-3);"></div>
      `;
      const view = panel.querySelector("#runs-snapshot-view");
      panel.querySelectorAll("[data-snapshot]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          view.innerHTML = `<div class="skeleton" style="height:120px;"></div>`;
          try {
            const content = await api.runFile(kind, runId, "snapshots/" + btn.dataset.snapshot);
            view.innerHTML = `<pre class="json-viewer">${jsonView(content)}</pre>`;
          } catch (e) {
            view.innerHTML = emptyState({
              iconName: "alertTriangle",
              title: "Couldn't load snapshot",
              body: String((e && e.message) || e),
            });
          }
        });
      });
      return;
    }

    if (tabKey === "screenshots") {
      panel.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:var(--space-4);">
          ${screenshots
            .map(
              (name) => `
            <div>
              <div class="card-eyebrow">${escapeHtml(name)}</div>
              <img src="${api.runScreenshotUrl(kind, runId, name)}" alt="${escapeHtml(name)}"
                style="max-width:100%;border:1px solid var(--border);border-radius:var(--radius-md);" />
            </div>`
            )
            .join("")}
        </div>
      `;
    }
  }

  tabsBar.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      tabsBar.querySelectorAll(".tab").forEach((b) => (b.dataset.active = String(b === btn)));
      paintTab(btn.dataset.tab);
    });
  });

  const first = tabsBar.querySelector(".tab");
  if (first) paintTab(first.dataset.tab);
}

// ==================================================================== small helpers

function capitalize(value) {
  const s = String(value || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—";
}

/** Render an error field that might be a plain scalar or a structured observation object,
 * without pretty-printing it across multiple lines inside a single-line kv-row-value. */
function scalar(value) {
  if (value == null) return "—";
  if (typeof value === "object") return escapeHtml(JSON.stringify(value));
  return escapeHtml(String(value));
}
