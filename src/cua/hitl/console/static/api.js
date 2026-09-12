// Every HTTP call the console makes, in one place. Nothing here mutates the domain model beyond
// what the existing endpoints already did (claim/release/gesture) — the additions are read-only
// projections of data the backend already produces (catalog, evidence, run records).

async function req(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* body wasn't JSON — keep statusText */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.text();
}

export const api = {
  // ---- existing surface (unchanged wire shape) ----
  state: () => req("/api/state"),
  interventions: () => req("/api/interventions"),
  claim: (id, operator) =>
    req(`/api/claim/${encodeURIComponent(id)}?operator=${encodeURIComponent(operator)}`, {
      method: "POST",
    }),
  release: () => req("/api/release", { method: "POST" }),

  // ---- read-only additions, backed by CapabilityStore / evidence on disk ----
  runs: () => req("/api/runs"),
  run: (kind, id) => req(`/api/runs/${kind}/${encodeURIComponent(id)}`),
  runFile: (kind, id, name) =>
    req(`/api/runs/${kind}/${encodeURIComponent(id)}/file/${encodeURIComponent(name)}`),
  runScreenshotUrl: (kind, id, name) =>
    `/api/runs/${kind}/${encodeURIComponent(id)}/screenshot/${encodeURIComponent(name)}`,

  capabilities: () => req("/api/capabilities"),
  capability: (ref) => req(`/api/capabilities/${encodeURIComponent(ref)}`),

  settings: () => req("/api/settings"),
};
