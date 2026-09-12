// Evidence browser: the same run records Runs summarizes, but read here for raw inspectability
// instead of a queue view — every trace event, snapshot, and screenshot exactly as it was captured
// on disk. Two different lenses on one underlying RunRecord; that overlap with Runs is expected.

import { api } from "../api.js";
import { icon } from "../icons.js";
import { statusPill, escapeHtml, fmtDate, fmtRelative, fmtDuration, jsonView, emptyState } from "../util.js";

export async function render(container, ctx) {
  const [kind, runId] = ctx.params;

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Evidence</h1>
        <p>Every run's trace, snapshots, and screenshots exactly as captured on disk — pick a run
        to inspect the raw record behind its Runs summary.</p>
      </div>
    </div>
    <div class="list-detail">
      <div class="list-panel" id="ev-list">
        <div class="skeleton" style="height:56px;"></div>
        <div class="skeleton" style="height:56px;"></div>
        <div class="skeleton" style="height:56px;"></div>
      </div>
      <div id="ev-detail"></div>
    </div>`;

  const listEl = container.querySelector("#ev-list");
  const detailEl = container.querySelector("#ev-detail");

  await Promise.all([loadList(listEl, ctx, kind, runId), loadDetail(detailEl, ctx, kind, runId)]);
}

// ---------------------------------------------------------------- list panel

async function loadList(listEl, ctx, activeKind, activeRunId) {
  let runs;
  try {
    runs = await api.runs();
  } catch (e) {
    listEl.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Couldn't load runs",
      body: String(e.message || e),
    });
    return;
  }

  if (!runs.length) {
    listEl.innerHTML = emptyState({
      iconName: "evidence",
      title: "No runs yet",
      body: "Evidence appears here once a run has been discovered or replayed.",
    });
    return;
  }

  listEl.innerHTML = runs
    .map((r) => {
      const active = r.kind === activeKind && r.run_id === activeRunId;
      return `
    <button class="list-panel-item" data-active="${String(active)}" data-kind="${escapeHtml(
        r.kind
      )}" data-run="${escapeHtml(r.run_id)}">
      <span class="list-panel-item-title">${escapeHtml(r.run_id)}</span>
      <span class="list-panel-item-meta"><span class="badge">${escapeHtml(r.kind)}</span>${fmtRelative(
        r.started_at
      )}</span>
    </button>`;
    })
    .join("");

  listEl.querySelectorAll("[data-kind]").forEach((btn) => {
    btn.addEventListener("click", () => {
      ctx.navigate(`evidence/${btn.dataset.kind}/${btn.dataset.run}`);
    });
  });
}

// ---------------------------------------------------------------- detail panel

async function loadDetail(detailEl, ctx, kind, runId) {
  if (!kind || !runId) {
    detailEl.innerHTML = emptyState({
      iconName: "evidence",
      title: "Pick a run to inspect",
      body: "Select a run from the list on the left to see its trace, snapshots, and screenshots.",
    });
    return;
  }

  detailEl.innerHTML = `<div class="skeleton" style="height:260px;"></div>`;

  let data;
  try {
    data = await api.run(kind, runId);
  } catch (e) {
    detailEl.innerHTML = emptyState({
      iconName: "alertTriangle",
      title: "Couldn't load this run",
      body: String(e.message || e),
    });
    return;
  }

  const { record, events, snapshots, screenshots } = data;
  const hasEvents = Array.isArray(events) && events.length > 0;
  const hasSnapshots = Array.isArray(snapshots) && snapshots.length > 0;
  const hasScreenshots = Array.isArray(screenshots) && screenshots.length > 0;

  const sections = [summarySection(record)];
  if (hasEvents) sections.push(traceSection(events));
  if (hasSnapshots) sections.push(snapshotsSection(snapshots));
  if (hasScreenshots) sections.push(screenshotsSection(kind, runId, screenshots));

  detailEl.innerHTML = `
    <button class="back-link" id="ev-back">${icon("chevronLeft")}<span>All runs</span></button>
    <div class="detail-panel-header">
      <h2>${escapeHtml(runId)}</h2>
      <span class="badge">${escapeHtml(kind)}</span>
    </div>
    <div style="display:flex;flex-direction:column;gap:var(--space-5);">${sections.join("")}</div>`;

  detailEl.querySelector("#ev-back").addEventListener("click", () => ctx.navigate("evidence"));

  if (hasSnapshots) wireSnapshots(detailEl, ctx, kind, runId);
  if (hasScreenshots) wireScreenshots(detailEl, kind, runId);
}

function summarySection(record) {
  const status = record?.result?.status;
  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Summary</span></div>
      <dl class="kv-grid card-pad">
        <div class="kv-grid-col-2"><dt>Capability</dt><dd class="mono">${escapeHtml(record?.capability_ref || "—")}</dd></div>
        <div><dt>Status</dt><dd>${status ? statusPill(status) : "—"}</dd></div>
        <div><dt>Started</dt><dd>${fmtDate(record?.started_at)}</dd></div>
        <div><dt>Duration</dt><dd>${fmtDuration(record?.duration_ms)}</dd></div>
      </dl>
    </div>`;
}

// ---------------------------------------------------------------- trace

function traceSection(events) {
  const rows = events
    .map((e) => {
      const { seq, at, event, actor, ...rest } = e;
      return `
      <details style="border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);padding:var(--space-2) var(--space-3);">
        <summary style="cursor:pointer;display:flex;align-items:center;gap:var(--space-3);font-size:var(--text-sm);">
          <span class="mono" style="color:var(--text-tertiary);min-width:2.5em;">#${
            seq ?? "—"
          }</span>
          <span class="badge">${escapeHtml(event || "—")}</span>
          <span class="badge" style="font-family:var(--font-mono);">${escapeHtml(actor || "—")}</span>
          <span style="margin-left:auto;color:var(--text-tertiary);font-size:var(--text-xs);">${fmtDate(
            at
          )}</span>
        </summary>
        <pre class="json-viewer" style="margin-top:var(--space-2);">${jsonView(rest)}</pre>
      </details>`;
    })
    .join("");

  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Trace</span><span class="badge">${events.length} events</span></div>
      <div class="card-pad" style="display:flex;flex-direction:column;gap:var(--space-2);">${rows}</div>
    </div>`;
}

// ---------------------------------------------------------------- snapshots

function snapshotsSection(snapshots) {
  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Snapshots</span><span class="badge">${snapshots.length}</span></div>
      <div class="card-pad">
        <div class="chip-row" id="ev-snapshot-chips">
          ${snapshots
            .map(
              (name) =>
                `<button class="btn btn-secondary btn-sm" data-snapshot="${escapeHtml(
                  name
                )}">${escapeHtml(name)}</button>`
            )
            .join("")}
        </div>
        <div id="ev-snapshot-panel" style="margin-top:var(--space-3);"></div>
      </div>
    </div>`;
}

function wireSnapshots(detailEl, ctx, kind, runId) {
  const chips = Array.from(detailEl.querySelectorAll("#ev-snapshot-chips [data-snapshot]"));
  const panel = detailEl.querySelector("#ev-snapshot-panel");

  chips.forEach((btn) => {
    btn.addEventListener("click", async () => {
      chips.forEach((b) => b.classList.toggle("btn-primary", b === btn));
      chips.forEach((b) => b.classList.toggle("btn-secondary", b !== btn));
      panel.innerHTML = `<div class="skeleton" style="height:80px;"></div>`;
      try {
        const content = await api.runFile(kind, runId, "snapshots/" + btn.dataset.snapshot);
        panel.innerHTML = `<pre class="json-viewer">${jsonView(content)}</pre>`;
      } catch (e) {
        panel.innerHTML = emptyState({
          iconName: "alertTriangle",
          title: "Couldn't load snapshot",
          body: String(e.message || e),
        });
        ctx.showToast({ title: "Snapshot load failed", message: String(e.message || e), tone: "danger" });
      }
    });
  });
}

// ---------------------------------------------------------------- screenshots

function screenshotsSection(kind, runId, screenshots) {
  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Screenshots</span><span class="badge">${screenshots.length}</span></div>
      <div class="card-pad">
        <div style="display:flex;flex-wrap:wrap;gap:var(--space-3);" id="ev-screenshot-thumbs">
          ${screenshots
            .map(
              (name) => `
          <button class="btn btn-secondary btn-icon" data-screenshot="${escapeHtml(
            name
          )}" title="${escapeHtml(name)}" style="padding:4px;">
            <img src="${api.runScreenshotUrl(kind, runId, name)}" alt="Screenshot ${escapeHtml(
                name
              )}" style="width:140px;height:90px;object-fit:cover;border-radius:var(--radius-sm);display:block;" />
          </button>`
            )
            .join("")}
        </div>
        <div id="ev-screenshot-preview" style="margin-top:var(--space-3);"></div>
      </div>
    </div>`;
}

function wireScreenshots(detailEl, kind, runId) {
  const thumbs = Array.from(detailEl.querySelectorAll("#ev-screenshot-thumbs [data-screenshot]"));
  const preview = detailEl.querySelector("#ev-screenshot-preview");

  thumbs.forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.screenshot;
      const isOpen = preview.dataset.name === name;
      thumbs.forEach((b) => (b.style.outline = "none"));
      if (isOpen) {
        preview.innerHTML = "";
        preview.dataset.name = "";
        return;
      }
      preview.dataset.name = name;
      btn.style.outline = "2px solid var(--accent)";
      preview.innerHTML = `<img src="${api.runScreenshotUrl(
        kind,
        runId,
        name
      )}" alt="Screenshot ${escapeHtml(
        name
      )}" style="max-width:480px;border-radius:var(--radius-md);border:1px solid var(--border);" />`;
    });
  });
}
