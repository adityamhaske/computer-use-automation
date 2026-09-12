// The operator's main workflow: claim the live session, act on it, hand it back. This page owns
// the ONLY DOM in the app that forwards raw input — every gesture becomes a policed action through
// ws.js's `gesture` helper, which sends exactly {kind,x,y,key,text} to `/ws`, unchanged from the
// original console. Nothing here injects into the page directly (see AGENTS.md invariant 4).

import { api } from "../api.js";
import { icon } from "../icons.js";
import { gesture, onLive, connectLive, disconnectLive, isConnected } from "../ws.js";
import { statusPill, escapeHtml, truncate } from "../util.js";

// The broker's queue drops an intervention from `pending()` the instant it is claimed (it becomes
// CLAIMED, and `/api/interventions` only ever lists OPEN ones) — so the context a person claimed is
// captured here, client-side, at the moment of claim, or it is gone for the rest of the handoff.
let claimedCard = null;

export async function render(container, ctx) {
  claimedCard = null;

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Interventions</h1>
        <p>Claim the same live session automation was driving, act on it, and hand it back — every
        gesture here is authorized and recorded exactly like an automated step.</p>
      </div>
    </div>
    <div class="split-view">
      <div class="viewport-frame">
        <div class="viewport-header">
          <span class="viewport-conn" id="iv-conn" data-live="false">${icon("wifiOff")}<span>idle</span></span>
          <span class="viewport-url" id="iv-url">no live session</span>
          <span class="viewport-owner" id="iv-owner" hidden></span>
        </div>
        <div class="viewport-stage">
          <img id="iv-screen" alt="Live session" hidden />
          <div class="viewport-empty" id="iv-empty">
            ${icon("monitor")}
            <p id="iv-empty-text">Automation owns the session. Nothing is escalated right now —
            this panel goes live the moment an operator claims control.</p>
          </div>
        </div>
        <div class="viewport-footer">
          <span id="iv-log">—</span>
          <span id="iv-epoch"></span>
        </div>
      </div>
      <div class="rail" id="iv-rail"></div>
    </div>`;

  const img = container.querySelector("#iv-screen");
  img.addEventListener("click", (e) => {
    if (!isConnected()) return;
    const rect = img.getBoundingClientRect();
    // Scale from displayed pixels to the frame's own resolution — the two differ whenever the
    // viewport is narrower than the captured page, exactly as the original console computed it.
    gesture.click(
      (e.clientX - rect.left) * (img.naturalWidth / rect.width),
      (e.clientY - rect.top) * (img.naturalHeight / rect.height)
    );
  });

  const onKeydown = (e) => {
    if (!isConnected() || e.key.length !== 1) return;
    const active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) return;
    gesture.text(e.key);
  };
  document.addEventListener("keydown", onKeydown);

  paint(container, ctx, ctx.getStore());
  const unsubStore = ctx.subscribe((store) => paint(container, ctx, store));
  const unsubLive = onLive((event) => onLiveEvent(container, ctx, event));

  return () => {
    unsubStore();
    unsubLive();
    document.removeEventListener("keydown", onKeydown);
    // Deliberately NOT disconnectLive() here — navigating to another page must not drop an
    // operator's control of the session. Only Release does that.
  };
}

// ---------------------------------------------------------------- rendering

function paint(container, ctx, store) {
  const rail = container.querySelector("#iv-rail");
  const state = store.state;

  if (!store.connected || !state) {
    rail.innerHTML = `<div class="card card-pad" style="display:flex;gap:10px;align-items:center;color:var(--text-secondary);">${icon(
      "wifiOff"
    )} Console offline — retrying…</div>`;
    return;
  }

  updateViewportChrome(container, state);

  if (state.state === "human_control") {
    if (!isConnected()) connectLive();
    rail.innerHTML = railHuman(state, claimedCard);
    wireReleaseButton(container, ctx);
    return;
  }

  disconnectLive();
  container.querySelector("#iv-screen").hidden = true;
  container.querySelector("#iv-empty").hidden = false;
  const emptyText = container.querySelector("#iv-empty-text");

  if (state.state === "resuming") {
    emptyText.textContent = "The operator released the session. Reconciling before automation continues.";
    rail.innerHTML = `<div class="card card-pad">${statusPill(
      "resuming"
    )}<p style="margin-top:8px;color:var(--text-secondary);font-size:var(--text-sm);">Reconciling the session before automation resumes at the right step.</p></div>`;
    return;
  }

  emptyText.textContent =
    "Automation owns the session. Nothing is escalated right now — this panel goes live the moment an operator claims control.";

  if (!store.interventions.length) {
    rail.innerHTML = `<div class="card card-pad" style="display:flex;gap:10px;align-items:flex-start;">${icon(
      "check"
    )}<div><strong style="font-size:var(--text-sm);">No open interventions</strong><p style="margin-top:4px;color:var(--text-secondary);font-size:var(--text-sm);">Automation is running normally — nothing needs a person right now.</p></div></div>`;
    return;
  }

  rail.innerHTML = railQueue(store.interventions);
  wireClaimButtons(container, ctx);
}

function updateViewportChrome(container, state) {
  const conn = container.querySelector("#iv-conn");
  const url = container.querySelector("#iv-url");
  const owner = container.querySelector("#iv-owner");
  const epoch = container.querySelector("#iv-epoch");
  const live = state.state === "human_control";

  conn.dataset.live = String(live);
  conn.innerHTML = `${icon(live ? "wifi" : "wifiOff")}<span>${live ? "live" : "idle"}</span>`;
  url.textContent = `session ${truncate(state.session || "—", 28)}`;
  owner.hidden = false;
  owner.dataset.owner = state.holder;
  owner.textContent = state.holder === "human" ? "Human owns session" : "Automation owns session";
  epoch.textContent = `epoch ${state.epoch}`;
}

function railHuman(state, card) {
  const detail = card
    ? `<div class="card card-pad">
         <div class="card-eyebrow">${escapeHtml(card.capability || "—")}</div>
         <p style="font-size:var(--text-sm);font-weight:var(--weight-medium);">${escapeHtml(
           card.goal || ""
         )}</p>
         <p style="font-size:var(--text-sm);color:var(--text-secondary);margin-top:var(--space-2);">
           stopped at <code class="mono">${escapeHtml(card.stopped_at || "?")}</code> —
           ${escapeHtml(card.because || "")}
         </p>
       </div>`
    : `<div class="card card-pad">
         <p style="font-size:var(--text-sm);color:var(--text-tertiary);">This handoff started before
         this page loaded, so the original escalation context isn't available here — the live view
         and controls below still work.</p>
       </div>`;

  return `
    <div class="card">
      <div class="card-header"><span class="card-title">Human control active</span>${statusPill(
        "human_control"
      )}</div>
      <div class="card-pad kv-list">
        <div class="kv-row"><span class="kv-row-label">Operator</span><span class="kv-row-value">${escapeHtml(
          state.operator || "—"
        )}</span></div>
        <div class="kv-row"><span class="kv-row-label">Epoch</span><span class="kv-row-value">${state.epoch}</span></div>
      </div>
    </div>
    ${detail}
    <button class="btn btn-human" id="iv-release-btn" style="width:100%;">${icon(
      "hand"
    )} Release to automation</button>
    <p style="font-size:var(--text-2xs);color:var(--text-tertiary);text-align:center;">Click the
    viewport to send a click. Type to send characters. Both travel through the same policy chokepoint
    as automation, tagged <code class="mono">actor=human</code>.</p>`;
}

function railQueue(list) {
  return (
    `<div class="section-title">Waiting for an operator (${list.length})</div>` +
    list
      .map(
        (c) => `
    <div class="card">
      <div class="card-pad">
        <div class="card-eyebrow">${escapeHtml(c.capability || "—")}</div>
        <p style="font-size:var(--text-sm);font-weight:var(--weight-medium);margin-bottom:var(--space-2);">${escapeHtml(
          c.goal || ""
        )}</p>
        <p style="font-size:var(--text-sm);color:var(--text-secondary);">stopped at
          <code class="mono">${escapeHtml(c.stopped_at || "?")}</code> — ${escapeHtml(c.because || "")}
        </p>
      </div>
      <div style="padding:0 var(--space-5) var(--space-5);">
        <button class="btn btn-primary" style="width:100%;" data-claim="${escapeHtml(
          c.intervention
        )}">${icon("hand")} Take control</button>
      </div>
    </div>`
      )
      .join("")
  );
}

// ---------------------------------------------------------------- actions

function wireClaimButtons(container, ctx) {
  container.querySelectorAll("[data-claim]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.claim;
      const card = ctx.getStore().interventions.find((c) => c.intervention === id) || null;
      container.querySelectorAll("[data-claim]").forEach((b) => (b.disabled = true));
      try {
        // "operator" mirrors the identity the original console used — there is no operator
        // identity/auth system yet (a documented cut; see REPORT.md §5, About page).
        await api.claim(id, "operator");
        claimedCard = card;
        connectLive();
        ctx.showToast({ title: "Session claimed", message: "You now hold the live session.", tone: "success" });
      } catch (e) {
        container.querySelectorAll("[data-claim]").forEach((b) => (b.disabled = false));
        ctx.showToast({ title: "Could not claim", message: String(e.message || e), tone: "danger" });
      }
    });
  });
}

function wireReleaseButton(container, ctx) {
  const btn = container.querySelector("#iv-release-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const ok = await ctx.confirmDialog({
      title: "Release to automation?",
      message: "The executor will reconcile the session and resume at the right step.",
      confirmLabel: "Release",
      tone: "human",
    });
    if (!ok) return;
    btn.disabled = true;
    try {
      const r = await api.release();
      disconnectLive();
      claimedCard = null;
      ctx.showToast({ title: "Handed back to automation", message: r.human_delta, tone: "success" });
    } catch (e) {
      btn.disabled = false;
      ctx.showToast({ title: "Release failed", message: String(e.message || e), tone: "danger" });
    }
  });
}

function onLiveEvent(container, ctx, event) {
  const img = container.querySelector("#iv-screen");
  if (!img) return; // this page has since been unmounted
  const empty = container.querySelector("#iv-empty");
  const log = container.querySelector("#iv-log");

  if (event.type === "open") {
    log.textContent = "connected";
  } else if (event.type === "frame") {
    if (event.frame) {
      img.src = "data:image/png;base64," + event.frame;
      img.hidden = false;
      empty.hidden = true;
    }
    log.textContent =
      event.status && event.status !== "ok"
        ? `${event.status}${event.message ? ": " + event.message : ""}`
        : "gesture applied";
  } else if (event.type === "refused") {
    log.textContent = "refused: " + event.error;
    ctx.showToast({ title: "Gesture refused", message: event.error, tone: "warning" });
  } else if (event.type === "close") {
    img.hidden = true;
    empty.hidden = false;
    log.textContent = "disconnected";
  }
}
