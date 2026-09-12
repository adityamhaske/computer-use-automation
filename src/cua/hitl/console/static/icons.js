// Hand-authored line icons (24x24 viewBox, stroke-based, 1.75 weight) — no icon font or CDN,
// so the console renders identically offline. Each returns an inline <svg> string; callers set
// width/height via CSS on the containing element (nav items, buttons, etc. size them at 18px).
const S = 'stroke="currentColor" fill="none" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"';

export const icons = {
  overview: `<svg viewBox="0 0 24 24" ${S}><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>`,
  runs: `<svg viewBox="0 0 24 24" ${S}><path d="M4 6h16M4 12h16M4 18h10"/></svg>`,
  interventions: `<svg viewBox="0 0 24 24" ${S}><path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4"/><circle cx="12" cy="17" r="0.5" fill="currentColor"/></svg>`,
  capabilities: `<svg viewBox="0 0 24 24" ${S}><path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>`,
  evidence: `<svg viewBox="0 0 24 24" ${S}><rect x="3" y="3" width="18" height="5" rx="1.5"/><path d="M5 8v11a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8"/><path d="M10 13h4"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .32 1.77l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.6 1.6 0 0 0-1.77-.32 1.6 1.6 0 0 0-1 1.46V21a2 2 0 1 1-4 0v-.09a1.6 1.6 0 0 0-1-1.46 1.6 1.6 0 0 0-1.77.32l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.6 1.6 0 0 0 .32-1.77 1.6 1.6 0 0 0-1.46-1H3a2 2 0 1 1 0-4h.09a1.6 1.6 0 0 0 1.46-1 1.6 1.6 0 0 0-.32-1.77l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.6 1.6 0 0 0 1.77.32H9a1.6 1.6 0 0 0 1-1.46V3a2 2 0 1 1 4 0v.09a1.6 1.6 0 0 0 1 1.46 1.6 1.6 0 0 0 1.77-.32l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.6 1.6 0 0 0-.32 1.77V9a1.6 1.6 0 0 0 1.46 1H21a2 2 0 1 1 0 4h-.09a1.6 1.6 0 0 0-1.46 1Z"/></svg>`,
  about: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r="0.6" fill="currentColor"/></svg>`,
  chevronLeft: `<svg viewBox="0 0 24 24" ${S}><path d="m14 6-6 6 6 6"/></svg>`,
  chevronRight: `<svg viewBox="0 0 24 24" ${S}><path d="m10 6 6 6-6 6"/></svg>`,
  menu: `<svg viewBox="0 0 24 24" ${S}><path d="M4 7h16M4 12h16M4 17h16"/></svg>`,
  close: `<svg viewBox="0 0 24 24" ${S}><path d="m6 6 12 12M18 6 6 18"/></svg>`,
  sun: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  moon: `<svg viewBox="0 0 24 24" ${S}><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>`,
  monitor: `<svg viewBox="0 0 24 24" ${S}><rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/></svg>`,
  wifi: `<svg viewBox="0 0 24 24" ${S}><path d="M3 8.5a16 16 0 0 1 18 0"/><path d="M6.2 12.1a11.5 11.5 0 0 1 11.6 0"/><path d="M9.5 15.6a7 7 0 0 1 5 0"/><circle cx="12" cy="19" r="0.6" fill="currentColor"/></svg>`,
  wifiOff: `<svg viewBox="0 0 24 24" ${S}><path d="M3 3l18 18"/><path d="M6.2 12.1a11.5 11.5 0 0 1 6.5-2.4M17.8 12.1a11.4 11.4 0 0 0-2.7-1.9M9.5 15.6a7 7 0 0 1 5-0.2"/><circle cx="12" cy="19" r="0.6" fill="currentColor"/></svg>`,
  refresh: `<svg viewBox="0 0 24 24" ${S}><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"/></svg>`,
  externalLink: `<svg viewBox="0 0 24 24" ${S}><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/></svg>`,
  check: `<svg viewBox="0 0 24 24" ${S}><path d="M4 12.5 9.5 18 20 6"/></svg>`,
  alertTriangle: `<svg viewBox="0 0 24 24" ${S}><path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4"/><circle cx="12" cy="17" r="0.5" fill="currentColor"/></svg>`,
  x: `<svg viewBox="0 0 24 24" ${S}><path d="m6 6 12 12M18 6 6 18"/></svg>`,
  info: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r="0.6" fill="currentColor"/></svg>`,
  user: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></svg>`,
  bot: `<svg viewBox="0 0 24 24" ${S}><rect x="4" y="9" width="16" height="10" rx="2"/><path d="M12 5v4M9 14h.01M15 14h.01"/><circle cx="12" cy="4" r="1"/></svg>`,
  arrowDown: `<svg viewBox="0 0 24 24" ${S}><path d="M12 4v14M6 13l6 6 6-6"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>`,
  drift: `<svg viewBox="0 0 24 24" ${S}><path d="M3 17c3-8 6-12 9-12s6 4 9 12"/><path d="M3 17h18"/></svg>`,
  archive: `<svg viewBox="0 0 24 24" ${S}><rect x="3" y="3" width="18" height="5" rx="1.5"/><path d="M5 8v11a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8"/><path d="M10 13h4"/></svg>`,
  download: `<svg viewBox="0 0 24 24" ${S}><path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>`,
  play: `<svg viewBox="0 0 24 24" ${S}><path d="M6 4.5v15l13-7.5-13-7.5Z"/></svg>`,
  search: `<svg viewBox="0 0 24 24" ${S}><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>`,
  hand: `<svg viewBox="0 0 24 24" ${S}><path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 12V4a1.5 1.5 0 0 1 3 0v8M14 12V5.5a1.5 1.5 0 0 1 3 0V13"/><path d="M17 10.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1a7 7 0 0 1-6-3.4L4 14c-.6-1 0-2.3 1.2-2.3.5 0 1 .2 1.3.6L8 14"/></svg>`,
  lock: `<svg viewBox="0 0 24 24" ${S}><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`,
  package: `<svg viewBox="0 0 24 24" ${S}><path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>`,
  file: `<svg viewBox="0 0 24 24" ${S}><path d="M6 3h8l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v5h5"/></svg>`,
  layers: `<svg viewBox="0 0 24 24" ${S}><path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 13 9 5 9-5M3 8v10M21 8v10"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" ${S}><path d="M12 3 4 6v6c0 5 3.5 7.5 8 9 4.5-1.5 8-4 8-9V6l-8-3Z"/><path d="m9 12 2 2 4-4"/></svg>`,
  server: `<svg viewBox="0 0 24 24" ${S}><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><circle cx="7" cy="7" r="0.6" fill="currentColor"/><circle cx="7" cy="17" r="0.6" fill="currentColor"/></svg>`,
};

export function icon(name, extra = "") {
  return (icons[name] || icons.info).replace("<svg ", `<svg ${extra} `);
}
