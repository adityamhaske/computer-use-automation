// The landing page: "what is the system doing right now?" at a glance. Automation state and the
// intervention queue come from the same poll every other page shares (ctx.subscribe, ~every
// 2.5s); recent-run history changes on a much slower cadence, so it is fetched once on mount
// instead of being re-pulled on every store tick.

import { api } from "../api.js";
import { icon } from "../icons.js";
import {
  statusPill,
  statusLabel,
  escapeHtml,
  truncate,
  fmtRelative,
  fmtDuration,
  fmtPercent,
  emptyState,
} from "../util.js";

const RUN_STATUSES = ["success", "business_outcome", "needs_human", "failed"];
const RECENT_LIMIT = 8;

export async function render(container, ctx) {
  let unmounted = false;
  const runsState = { loading: true, error: null, data: [] };

  async function loadRuns() {
    try {
      runsState.data = await api.runs();
      runsState.error = null;
    } catch (e) {
      runsState.error = e;
    } finally {
      runsState.loading = false;
      // The fetch can resolve after this page has been navigated away from — never paint a
      // container another page module now owns.
      if (!unmounted) paint(ctx.getStore());
    }
  }

  function paint(store) {
    container.innerHTML = pageHtml(store, runsState);
    wire(container, ctx);
  }

  paint(ctx.getStore());
  loadRuns();

  const unsub = ctx.subscribe((store) => {
    if (!unmounted) paint(store);
  });

  return () => {
    unmounted = true;
    unsub();
  };
}

// ---------------------------------------------------------------- markup

function pageHtml(store, runsState) {
  return `
    <div class="page-header">
      <div>
        <h1>Overview</h1>
        <p>What the system is doing right now — who holds the session, what's waiting on an
        operator, and how recent runs came out.</p>
      </div>
    </div>
    ${store.connected ? bodyHtml(store, runsState) : offlineHtml()}
  `;
}

function offlineHtml() {
  return `<div class="card">${emptyState({
    iconName: "wifiOff",
    title: "Console offline",
    body: "Lost contact with the operator console — retrying the connection. Nothing below is shown while the figures could be stale.",
  })}</div>`;
}

function bodyHtml(store, runsState) {
  return `
    ${interventionBanner(store)}
    <div class="card-grid" style="margin-bottom:var(--space-5);">
      ${automationCard(store.state)}
      ${sessionCard(store)}
      ${recentRunsCard(runsState)}
      ${driftCard(runsState)}
    </div>
    ${recentActivityCard(runsState)}
  `;
}

function interventionBanner(store) {
  const list = store.interventions || [];
  const state = store.state;
  const pendingCount = state && typeof state.pending === "number" ? state.pending : list.length;
  if (pendingCount <= 0 && list.length === 0) return "";

  // The queue only ever carries capability/reason detail on the interventions array itself
  // (state.pending is just a count) — oldest by stopped_at is the one that's waited longest.
  const oldest = list.length
    ? [...list].sort((a, b) => new Date(a.stopped_at || 0) - new Date(b.stopped_at || 0))[0]
    : null;
  const label = pendingCount === 1 ? "1 intervention waiting" : `${pendingCount} interventions waiting`;

  return `
    <div class="card card-pad" style="margin-bottom:var(--space-5);display:flex;align-items:center;gap:var(--space-4);flex-wrap:wrap;border-color:var(--warning);">
      <div style="flex:1;min-width:240px;display:flex;flex-direction:column;gap:6px;">
        <div style="display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap;">
          ${statusPill("needs_human", label)}
          ${
            oldest
              ? `<span style="font-size:var(--text-sm);font-weight:var(--weight-medium);">${escapeHtml(
                  oldest.capability || "—"
                )}</span>`
              : ""
          }
        </div>
        <p style="font-size:var(--text-sm);color:var(--text-secondary);margin:0;">
          ${
            oldest
              ? `Oldest — stopped ${fmtRelative(oldest.stopped_at)} — ${escapeHtml(
                  oldest.because || "no reason recorded"
                )}`
              : "Automation has handed a session to an operator."
          }
        </p>
      </div>
      <button class="btn btn-primary" data-goto="interventions">${icon("hand")} Go to interventions</button>
    </div>`;
}

function automationCard(state) {
  if (!state) {
    return `<div class="card stat-card"><div class="card-eyebrow">Automation</div><div class="skeleton" style="height:28px;margin-top:var(--space-2);"></div></div>`;
  }
  const metaParts = [`holder ${escapeHtml(state.holder || "—")}`];
  if (state.operator) metaParts.push(`operator ${escapeHtml(state.operator)}`);
  metaParts.push(`epoch ${state.epoch}`);
  return `
    <div class="card stat-card">
      <div class="card-eyebrow">Automation</div>
      <div class="stat-value">${statusPill(state.state)}</div>
      <div class="stat-meta">${metaParts.join(" · ")}</div>
    </div>`;
}

function sessionCard(store) {
  const state = store.state;
  const pendingCount =
    state && typeof state.pending === "number" ? state.pending : (store.interventions || []).length;
  const session = state && state.session ? truncate(state.session, 28) : "—";
  return `
    <div class="card stat-card">
      <div class="card-eyebrow">Active session</div>
      <div class="stat-value mono" style="font-size:var(--text-xl);">${escapeHtml(session)}</div>
      <div class="stat-meta">${pendingCount} pending intervention${pendingCount === 1 ? "" : "s"}</div>
    </div>`;
}

function recentRunsCard(runsState) {
  if (runsState.loading) {
    return `<div class="card stat-card"><div class="card-eyebrow">Recent runs</div><div class="skeleton" style="height:28px;margin-top:var(--space-2);"></div></div>`;
  }
  if (runsState.error) {
    return `
      <div class="card stat-card">
        <div class="card-eyebrow">Recent runs</div>
        <div class="stat-value">—</div>
        <div class="stat-meta">Could not load runs — ${escapeHtml(String(runsState.error.message || runsState.error))}</div>
      </div>`;
  }
  const runs = runsState.data;
  const counts = { success: 0, business_outcome: 0, needs_human: 0, failed: 0 };
  for (const r of runs) {
    if (r.status in counts) counts[r.status] += 1;
  }
  const chips = RUN_STATUSES.filter((s) => counts[s] > 0)
    .map((s) => statusPill(s, `${counts[s]} ${statusLabel(s)}`))
    .join("");
  return `
    <div class="card stat-card">
      <div class="card-eyebrow">Recent runs</div>
      <div class="stat-value">${runs.length}</div>
      <div class="stat-meta chip-row" style="margin-top:var(--space-2);">
        ${chips || "no runs recorded yet"}
      </div>
    </div>`;
}

function driftCard(runsState) {
  if (runsState.loading) {
    return `<div class="card stat-card"><div class="card-eyebrow">Drift (replay avg)</div><div class="skeleton" style="height:28px;margin-top:var(--space-2);"></div></div>`;
  }
  if (runsState.error) {
    return `
      <div class="card stat-card">
        <div class="card-eyebrow">Drift (replay avg)</div>
        <div class="stat-value">—</div>
        <div class="stat-meta">Could not load runs</div>
      </div>`;
  }
  const scored = runsState.data.filter((r) => r.kind === "replay" && r.drift_score != null);
  const avg = scored.length ? scored.reduce((sum, r) => sum + r.drift_score, 0) / scored.length : 0;
  return `
    <div class="card stat-card">
      <div class="card-eyebrow">Drift (replay avg)</div>
      <div class="stat-value">${fmtPercent(avg)}</div>
      <div class="stat-meta">${
        scored.length
          ? `Signal across ${scored.length} replay run${scored.length === 1 ? "" : "s"} with a recorded score — 0% is healthy`
          : "No replay runs with a recorded drift score yet"
      }</div>
    </div>`;
}

function recentActivityCard(runsState) {
  if (runsState.loading) {
    return `
      <div class="card">
        <div class="card-header"><span class="card-title">Recent activity</span></div>
        <div class="card-pad"><div class="skeleton" style="height:180px;"></div></div>
      </div>`;
  }
  if (runsState.error) {
    return `
      <div class="card">
        <div class="card-header"><span class="card-title">Recent activity</span></div>
        ${emptyState({
          iconName: "alertTriangle",
          title: "Could not load runs",
          body: String(runsState.error.message || runsState.error),
        })}
      </div>`;
  }
  const runs = [...runsState.data]
    .sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0))
    .slice(0, RECENT_LIMIT);
  if (!runs.length) {
    return `
      <div class="card">
        <div class="card-header"><span class="card-title">Recent activity</span></div>
        ${emptyState({
          iconName: "runs",
          title: "No runs yet",
          body: "Discovery, replay, and escalation runs will show up here once they happen.",
        })}
      </div>`;
  }
  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Recent activity</span></div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Capability</th>
              <th>Kind</th>
              <th>Started</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            ${runs.map(runRowHtml).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function runRowHtml(run) {
  const kind = run.kind || "—";
  return `
    <tr data-clickable="true" tabindex="0" role="button" aria-label="Open run ${escapeHtml(
      run.run_id || ""
    )}" data-run-kind="${escapeHtml(run.kind || "")}" data-run-id="${escapeHtml(run.run_id || "")}">
      <td data-label="Status">${statusPill(run.status)}</td>
      <td data-label="Capability">${escapeHtml(truncate(run.capability_ref || "—", 48))}</td>
      <td data-label="Kind"><span class="badge">${escapeHtml(kind)}</span></td>
      <td data-label="Started">${fmtRelative(run.started_at)}</td>
      <td data-label="Duration">${fmtDuration(run.duration_ms)}</td>
    </tr>`;
}

// ---------------------------------------------------------------- wiring

function wire(container, ctx) {
  const gotoBtn = container.querySelector("[data-goto='interventions']");
  if (gotoBtn) gotoBtn.addEventListener("click", () => ctx.navigate("interventions"));

  container.querySelectorAll("tbody tr[data-clickable='true']").forEach((row) => {
    const openRun = () => {
      const { runKind, runId } = row.dataset;
      if (runKind && runId) ctx.navigate(`runs/${runKind}/${runId}`);
    };
    row.addEventListener("click", openRun);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openRun();
      }
    });
  });
}
