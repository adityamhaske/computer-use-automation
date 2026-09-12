// Static engineering-thesis page, plus a couple of best-effort live facts. No subscriptions, no
// intervals, nothing outside `container` — so this page returns no cleanup function.

import { icon } from "../icons.js";
import { escapeHtml } from "../util.js";

const INVARIANTS = [
  {
    text: "Replay never calls or imports the LLM — deterministic, cheap, auditable.",
  },
  {
    text:
      "Every action reaches a surface through one chokepoint: resolve, then authorize, then " +
      "dispatch — automation, replay, and a human operator all take the same path.",
  },
  {
    text:
      "A target is identified semantically (role, name, structural anchor), never by a raw CSS " +
      "selector — the same artifact can replay on a rebranded tenant or a different driver.",
  },
  {
    text: "Unknown states fail closed — an unrecognized screen stops the run and escalates, rather than guessing.",
  },
  {
    text: "A capability is immutable and content-hashed once sealed — what ran is always answerable.",
  },
];

const FLOW_STEPS = [
  "LLM discovery",
  "Target descriptor",
  "Policy engine",
  "Surface driver",
  "Capability artifact",
  "Deterministic replay",
];

export async function render(container, ctx) {
  container.innerHTML = `
    <div class="about-layout">
      <div class="prose">
        <h1>Computer-use automation</h1>
        <div class="card card-pad" style="border-left:3px solid var(--accent);">
          <p style="font-size:var(--text-lg);font-weight:var(--weight-medium);color:var(--text);margin:0;">
            Discovery is probabilistic. Execution is deterministic.
          </p>
        </div>
        <p>An LLM discovers a flow exactly once, driving a live application the way a person would.
        That single run is compiled into a typed, versioned, content-hashed capability artifact —
        inputs, steps, outcomes, and recovery rules the model figured out, now written down.</p>
        <p>From that point on, replay is deterministic: the artifact runs with no model in the loop,
        and it is what an AI agent invokes in production as a normal typed function — resolve a
        target, get it authorized, dispatch it, repeat.</p>
        <p>When a run meets something the artifact does not recognize, the system fails closed and
        hands the live session to a human. The operator acts through the same policy chokepoint,
        tagged <code class="mono">actor=human</code>, and hands it back — the executor re-anchors
        on the session as it now stands rather than restarting from step 1.</p>
        <div class="invariant-list">
          ${INVARIANTS.map(
            (inv, i) => `
          <div class="invariant-row">
            <span class="invariant-num">${i + 1}</span>
            <span>${escapeHtml(inv.text)}</span>
          </div>`
          ).join("")}
        </div>
      </div>
      <div>
        <div class="flow">
          ${FLOW_STEPS.map(
            (step, i) => `
          ${i > 0 ? `<div class="flow-arrow">${icon("arrowDown")}</div>` : ""}
          <div class="flow-step">
            <span class="flow-step-index">${i + 1}</span>
            <span>${escapeHtml(step)}</span>
          </div>`
          ).join("")}
        </div>
        <div class="card card-pad" id="about-live-facts" style="margin-top:var(--space-5);">
          <div class="card-eyebrow">Live facts</div>
          <div class="kv-list" id="about-live-facts-body">
            <div class="skeleton" style="height:16px;"></div>
            <div class="skeleton" style="height:16px;"></div>
          </div>
        </div>
      </div>
    </div>`;

  loadLiveFacts(container, ctx);
}

// ---------------------------------------------------------------- live facts

async function loadLiveFacts(container, ctx) {
  const body = container.querySelector("#about-live-facts-body");
  if (!body) return;

  const rows = [];

  // Both live rows depend on reading one capability's full artifact, so fetch it once —
  // each row is still rendered independently so a missing field on one doesn't hide the other.
  let capability = null;
  try {
    const list = await ctx.api.capabilities();
    if (list && list.length) {
      const full = await ctx.api.capability(list[0].ref);
      capability = full && full.capability ? full.capability : null;
    }
  } catch {
    // best-effort — no capabilities row rendered below
  }

  if (capability && capability.schema_version != null) {
    rows.push(
      `<div class="kv-row"><span class="kv-row-label">Schema version</span><span class="kv-row-value mono">${escapeHtml(
        capability.schema_version
      )}</span></div>`
    );
  }

  if (
    capability &&
    capability.surface &&
    Array.isArray(capability.surface.driver_capabilities) &&
    capability.surface.driver_capabilities.length
  ) {
    rows.push(
      `<div class="kv-row"><span class="kv-row-label">Driver capabilities</span><span class="kv-row-value">${escapeHtml(
        capability.surface.driver_capabilities.join(", ")
      )}</span></div>`
    );
  }

  rows.push(
    `<div class="kv-row"><span class="kv-row-label">Deployment</span><span class="kv-row-value" style="text-align:right;font-weight:var(--weight-normal);color:var(--text-secondary);">Single process, files on disk — no queue, no database, no multi-tenant infrastructure</span></div>`
  );

  if (container.querySelector("#about-live-facts-body")) {
    body.innerHTML = rows.join("");
  }
}
