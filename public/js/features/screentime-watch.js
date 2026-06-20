// Screen-Time Watch — internal dashboard.
//
// Hero: a 50-state (+ DC) US heat map, each state colored by its stance from
// screentime_state_stance (favorable / unfavorable / neutral / no_info). Below
// it: a searchable, filterable signal repository (screentime_signals). The map
// is rendered as an inline-SVG TILE-GRID cartogram — equal-size square tiles
// placed at each state's approximate grid position. No mapping/charting
// dependency is added (org rule); a tile grid keeps every state equally legible
// (stance is per-state, so state area should not bias the read) and renders
// crisply with plain SVG.
//
// Self-mounting: subscribes to the `view` store and paints into
// #app-shell-content when the screentime-watch view is active — no edit to the
// central home.js shell dispatcher is required.

import { on as onState, getState } from "../core/store.js";
import { escapeHtml } from "../core/utils.js";
import { SCREENTIME_VIEW, normalizeAppView } from "../core/navigation.js";

const SHELL_CONTENT_SELECTOR = "#app-shell-content";

// 2-letter code → [col, row] on a 11-wide × 8-tall tile grid (standard US tile
// cartogram layout: NPR/“tile grid map”). DC sits next to MD.
const TILE_GRID = {
  AK: [0, 0],
  ME: [10, 0],
  VT: [8, 0], NH: [9, 0],
  WA: [1, 1], ID: [2, 1], MT: [3, 1], ND: [4, 1], MN: [5, 1], IL: [6, 1], WI: [6, 0], MI: [7, 1], NY: [8, 1], MA: [9, 1], RI: [10, 1],
  OR: [1, 2], NV: [2, 2], WY: [3, 2], SD: [4, 2], IA: [5, 2], IN: [6, 2], OH: [7, 2], PA: [8, 2], NJ: [9, 2], CT: [10, 2],
  CA: [1, 3], UT: [2, 3], CO: [3, 3], NE: [4, 3], MO: [5, 3], KY: [6, 3], WV: [7, 3], VA: [8, 3], MD: [9, 3], DE: [10, 3],
  AZ: [2, 4], NM: [3, 4], KS: [4, 4], AR: [5, 4], TN: [6, 4], NC: [7, 4], SC: [8, 4], DC: [9, 4],
  OK: [4, 5], LA: [5, 5], MS: [6, 5], AL: [7, 5], GA: [8, 5],
  HI: [0, 6], TX: [4, 6], FL: [9, 6],
};

const TILE = 46; // tile size (px) in SVG units
const GAP = 8; // gap between tiles
const COLS = 11;
const ROWS = 7;

const STANCE_META = {
  favorable: { label: "Favorable", swatch: "var(--st-favorable)", emoji: "🟢" },
  unfavorable: { label: "Unfavorable", swatch: "var(--st-unfavorable)", emoji: "🔴" },
  neutral: { label: "Neutral", swatch: "var(--st-neutral)", emoji: "⚪" },
  no_info: { label: "No info", swatch: "var(--st-noinfo)", emoji: "⚪" },
};

const STATUS_OPTIONS = ["proposed", "passed", "amended", "guidance", "news"];
const LEVEL_OPTIONS = ["state", "district"];
const STANCE_OPTIONS = ["favorable", "unfavorable", "neutral"];
const PAGE_SIZE = 50;

// ── Module state ────────────────────────────────────────────────────────────
let _mounted = false;
let _stateStance = null; // { states, counts, total_states }
let _isOwner = false;
let _filters = { state: "", level: "", status: "", stance: "", since: "", q: "" };
let _offset = 0;
let _signalsData = null; // { signals, total, limit, offset }
let _loadToken = 0;

function esc(s) {
  return escapeHtml(String(s ?? ""));
}

function shell() {
  return document.querySelector(SHELL_CONTENT_SELECTOR);
}

// ── Data ──────────────────────────────────────────────────────────────────────
async function fetchStateStance() {
  const res = await fetch("/api/screentime/state-stance");
  if (!res.ok) throw new Error(`state-stance ${res.status}`);
  return res.json();
}

function buildSignalsQuery() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(_filters)) {
    if (v) p.set(k, v);
  }
  p.set("limit", String(PAGE_SIZE));
  p.set("offset", String(_offset));
  return p.toString();
}

async function fetchSignals() {
  const res = await fetch(`/api/screentime/signals?${buildSignalsQuery()}`);
  if (!res.ok) throw new Error(`signals ${res.status}`);
  return res.json();
}

async function fetchIsOwner() {
  try {
    const res = await fetch("/api/me");
    if (!res.ok) return false;
    const me = await res.json();
    return Boolean(me?.is_owner);
  } catch {
    return false;
  }
}

// ── Rendering: heat map (inline SVG tile grid) ──────────────────────────────────
function renderMap(stateStance) {
  const byState = {};
  for (const s of stateStance?.states || []) byState[s.state] = s;

  const width = COLS * (TILE + GAP) - GAP;
  const height = ROWS * (TILE + GAP) - GAP;

  const tiles = Object.entries(TILE_GRID)
    .map(([code, [col, row]]) => {
      const entry = byState[code] || { stance: "no_info", signal_count: 0, rationale: "No signals yet." };
      const meta = STANCE_META[entry.stance] || STANCE_META.no_info;
      const x = col * (TILE + GAP);
      const y = row * (TILE + GAP);
      const title = `${code} — ${meta.label}\n${entry.signal_count} signal${entry.signal_count === 1 ? "" : "s"}\n${entry.rationale || ""}`;
      const labelInk = entry.stance === "no_info" || entry.stance === "neutral" ? "var(--st-tile-ink-dim)" : "var(--st-tile-ink)";
      const count = entry.signal_count > 0
        ? `<text x="${x + TILE - 6}" y="${y + 14}" text-anchor="end" class="st-tile-count">${entry.signal_count}</text>`
        : "";
      return `
        <g class="st-tile" data-st-state="${code}" tabindex="0" role="button"
           aria-label="${esc(title)}">
          <title>${esc(title)}</title>
          <rect x="${x}" y="${y}" width="${TILE}" height="${TILE}" rx="6"
                fill="${meta.swatch}" class="st-tile-rect" />
          <text x="${x + TILE / 2}" y="${y + TILE / 2 + 5}" text-anchor="middle"
                class="st-tile-label" fill="${labelInk}">${code}</text>
          ${count}
        </g>`;
    })
    .join("");

  return `
    <svg viewBox="0 0 ${width} ${height}" class="st-map-svg" role="img"
         aria-label="50-state screen-time policy stance heat map">
      ${tiles}
    </svg>`;
}

function renderLegend(counts = {}) {
  const items = [
    ["favorable", counts.favorable || 0],
    ["unfavorable", counts.unfavorable || 0],
    ["neutral", counts.neutral || 0],
    ["no_info", counts.no_info || 0],
  ];
  return `
    <div class="st-legend">
      ${items
        .map(
          ([k, n]) => `
        <span class="st-legend-item">
          <span class="st-legend-dot" style="background:${STANCE_META[k].swatch}"></span>
          ${STANCE_META[k].label}<span class="st-legend-count">${n}</span>
        </span>`
        )
        .join("")}
    </div>`;
}

// ── Rendering: repository filters + table ───────────────────────────────────────
function optionList(values, selected, allLabel) {
  const opts = [`<option value="">${esc(allLabel)}</option>`];
  for (const v of values) {
    const sel = v === selected ? " selected" : "";
    opts.push(`<option value="${esc(v)}"${sel}>${esc(v.charAt(0).toUpperCase() + v.slice(1))}</option>`);
  }
  return opts.join("");
}

function renderFilters() {
  const stateOptions = (_stateStance?.states || [])
    .map((s) => s.state)
    .sort()
    .map((code) => `<option value="${code}"${code === _filters.state ? " selected" : ""}>${code}</option>`)
    .join("");
  return `
    <div class="st-filters" data-st-filters>
      <div class="st-filter">
        <label>State</label>
        <select data-st-filter="state"><option value="">All states</option>${stateOptions}</select>
      </div>
      <div class="st-filter">
        <label>Level</label>
        <select data-st-filter="level">${optionList(LEVEL_OPTIONS, _filters.level, "All levels")}</select>
      </div>
      <div class="st-filter">
        <label>Status</label>
        <select data-st-filter="status">${optionList(STATUS_OPTIONS, _filters.status, "All statuses")}</select>
      </div>
      <div class="st-filter">
        <label>Stance</label>
        <select data-st-filter="stance">${optionList(STANCE_OPTIONS, _filters.stance, "All stances")}</select>
      </div>
      <div class="st-filter">
        <label>Since</label>
        <input type="date" data-st-filter="since" value="${esc(_filters.since)}" />
      </div>
      <div class="st-filter st-filter-search">
        <label>Search</label>
        <input type="search" data-st-filter="q" placeholder="Title, summary, angle…" value="${esc(_filters.q)}" />
      </div>
      <button type="button" class="st-btn st-btn-ghost" data-st-action="clear-filters">Clear</button>
    </div>`;
}

function statusBadge(status) {
  return `<span class="st-badge st-status-${esc(status)}">${esc(status)}</span>`;
}

function stanceBadge(stance) {
  const meta = STANCE_META[stance] || STANCE_META.neutral;
  return `<span class="st-badge st-stance" style="--st-badge:${meta.swatch}">${meta.emoji} ${esc(meta.label)}</span>`;
}

function renderRows(signals) {
  if (!signals.length) {
    return `<tr><td colspan="6" class="st-empty">No signals match these filters.</td></tr>`;
  }
  return signals
    .map((s) => {
      const where = s.level === "district" && s.district_name
        ? `${esc(s.state)} · ${esc(s.district_name)}`
        : esc(s.state);
      const date = s.discovered_at ? new Date(s.discovered_at).toLocaleDateString() : "—";
      const link = s.source_url
        ? `<a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer" class="st-src">${esc(s.source_type)} ↗</a>`
        : `<span class="st-src st-src-none">${esc(s.source_type)}</span>`;
      const angle = s.amira_angle ? `<div class="st-angle">${esc(s.amira_angle)}</div>` : "";
      const summary = s.summary ? `<div class="st-summary">${esc(s.summary)}</div>` : "";
      return `
        <tr class="st-row">
          <td class="st-cell-title">
            <div class="st-title">${esc(s.title)}</div>
            ${summary}${angle}
          </td>
          <td>${where}</td>
          <td>${statusBadge(s.status)}</td>
          <td>${stanceBadge(s.stance)}</td>
          <td>${link}</td>
          <td class="st-cell-date">${date}</td>
        </tr>`;
    })
    .join("");
}

function renderPager(data) {
  const total = data?.total || 0;
  if (total <= PAGE_SIZE) return "";
  const start = total === 0 ? 0 : _offset + 1;
  const end = Math.min(_offset + PAGE_SIZE, total);
  const prevDisabled = _offset <= 0 ? " disabled" : "";
  const nextDisabled = _offset + PAGE_SIZE >= total ? " disabled" : "";
  return `
    <div class="st-pager">
      <span class="st-pager-info">${start}–${end} of ${total}</span>
      <button type="button" class="st-btn st-btn-ghost" data-st-action="prev"${prevDisabled}>Prev</button>
      <button type="button" class="st-btn st-btn-ghost" data-st-action="next"${nextDisabled}>Next</button>
    </div>`;
}

function renderRepository() {
  const data = _signalsData;
  const total = data?.total ?? 0;
  const rows = data ? renderRows(data.signals) : `<tr><td colspan="6" class="st-empty">Loading…</td></tr>`;
  return `
    <section class="st-repo">
      <div class="st-repo-head">
        <h3>Signal repository</h3>
        <span class="st-repo-count" data-st-repo-count>${total} signal${total === 1 ? "" : "s"}</span>
      </div>
      ${renderFilters()}
      <div class="st-table-wrap">
        <table class="st-table">
          <thead>
            <tr>
              <th>Move</th><th>Where</th><th>Status</th><th>Stance</th><th>Source</th><th>Found</th>
            </tr>
          </thead>
          <tbody data-st-rows>${rows}</tbody>
        </table>
      </div>
      <div data-st-pager>${renderPager(data)}</div>
    </section>`;
}

function renderScrub() {
  if (!_isOwner) return "";
  return `
    <div class="st-scrub">
      <button type="button" class="st-btn st-btn-danger" data-st-action="purge">Purge screen-time data</button>
      <span class="st-scrub-note">Owner-only · clears all signals + the state rollup.</span>
    </div>`;
}

function renderPage() {
  const counts = _stateStance?.counts || {};
  const activeState = _filters.state ? ` · filtered to ${esc(_filters.state)}` : "";
  return `
    ${pageStyles()}
    <div class="st-page">
      <header class="st-hero">
        <div class="st-hero-row">
          <div>
            <div class="st-eyebrow">Internal · Screen-Time Watch</div>
            <h2 class="st-hero-title">National screen-time policy stance${activeState}</h2>
            <p class="st-hero-sub">Where state &amp; district screen-time moves either restrict or carve out evidence-based tools like Amira. Click a state to filter the repository.</p>
          </div>
          ${renderScrub()}
        </div>
      </header>

      <section class="st-map-section">
        <div class="st-map-card">
          <div class="st-map-head">
            <h3>50-state heat map</h3>
            ${renderLegend(counts)}
          </div>
          <div class="st-map-wrap" data-st-map>${renderMap(_stateStance)}</div>
        </div>
      </section>

      ${renderRepository()}
    </div>`;
}

// ── Partial re-renders ──────────────────────────────────────────────────────────
function repaintRepository() {
  const rowsEl = shell()?.querySelector("[data-st-rows]");
  const pagerEl = shell()?.querySelector("[data-st-pager]");
  const countEl = shell()?.querySelector("[data-st-repo-count]");
  if (rowsEl && _signalsData) rowsEl.innerHTML = renderRows(_signalsData.signals);
  if (pagerEl && _signalsData) pagerEl.innerHTML = renderPager(_signalsData);
  if (countEl && _signalsData) {
    const t = _signalsData.total;
    countEl.textContent = `${t} signal${t === 1 ? "" : "s"}`;
  }
}

function repaintHeroFilterLabel() {
  const titleEl = shell()?.querySelector(".st-hero-title");
  if (titleEl) {
    titleEl.innerHTML = `National screen-time policy stance${_filters.state ? ` · filtered to ${esc(_filters.state)}` : ""}`;
  }
}

async function reloadSignals() {
  const token = ++_loadToken;
  try {
    const data = await fetchSignals();
    if (token !== _loadToken) return;
    _signalsData = data;
    repaintRepository();
  } catch (err) {
    if (token !== _loadToken) return;
    const rowsEl = shell()?.querySelector("[data-st-rows]");
    if (rowsEl) rowsEl.innerHTML = `<tr><td colspan="6" class="st-empty">Failed to load signals.</td></tr>`;
    console.error("screentime: reloadSignals failed", err);
  }
}

// ── Event handling ──────────────────────────────────────────────────────────────
function highlightMapSelection() {
  shell()?.querySelectorAll(".st-tile").forEach((g) => {
    g.classList.toggle("is-selected", g.dataset.stState === _filters.state);
  });
}

function onMapClick(stateCode) {
  _filters.state = _filters.state === stateCode ? "" : stateCode;
  _offset = 0;
  const sel = shell()?.querySelector('[data-st-filter="state"]');
  if (sel) sel.value = _filters.state;
  highlightMapSelection();
  repaintHeroFilterLabel();
  reloadSignals();
}

let _searchDebounce = null;

function wireEvents() {
  const root = shell();
  if (!root) return;

  root.addEventListener("click", (e) => {
    const tile = e.target.closest(".st-tile");
    if (tile && root.contains(tile)) {
      onMapClick(tile.dataset.stState);
      return;
    }
    const actionEl = e.target.closest("[data-st-action]");
    if (!actionEl || !root.contains(actionEl)) return;
    const action = actionEl.dataset.stAction;
    if (action === "clear-filters") {
      _filters = { state: "", level: "", status: "", stance: "", since: "", q: "" };
      _offset = 0;
      renderInto(root, { mapOnly: false });
      return;
    }
    if (action === "prev") {
      _offset = Math.max(0, _offset - PAGE_SIZE);
      reloadSignals();
      return;
    }
    if (action === "next") {
      _offset = _offset + PAGE_SIZE;
      reloadSignals();
      return;
    }
    if (action === "purge") {
      doPurge();
      return;
    }
  });

  root.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tile = e.target.closest(".st-tile");
    if (tile && root.contains(tile)) {
      e.preventDefault();
      onMapClick(tile.dataset.stState);
    }
  });

  root.addEventListener("change", (e) => {
    const f = e.target.closest("[data-st-filter]");
    if (!f || !root.contains(f)) return;
    const key = f.dataset.stFilter;
    if (key === "q") return; // handled by input/debounce
    _filters[key] = f.value;
    _offset = 0;
    if (key === "state") {
      highlightMapSelection();
      repaintHeroFilterLabel();
    }
    reloadSignals();
  });

  root.addEventListener("input", (e) => {
    const f = e.target.closest('[data-st-filter="q"]');
    if (!f || !root.contains(f)) return;
    _filters.q = f.value;
    _offset = 0;
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => reloadSignals(), 250);
  });
}

async function doPurge() {
  const ok = window.confirm(
    "Purge ALL screen-time data?\n\nThis permanently clears every screen-time signal and the per-state rollup. This cannot be undone."
  );
  if (!ok) return;
  try {
    const res = await fetch("/api/screentime/purge", { method: "POST" });
    if (res.status === 403) {
      window.alert("Purge is owner-only. Your account is not permitted to do this.");
      return;
    }
    if (!res.ok) throw new Error(`purge ${res.status}`);
    // Reload both the map and the repository to reflect the empty state.
    _stateStance = await fetchStateStance();
    _offset = 0;
    renderInto(shell(), { mapOnly: false });
  } catch (err) {
    console.error("screentime: purge failed", err);
    window.alert("Purge failed. See console for details.");
  }
}

// ── Mount / view lifecycle ────────────────────────────────────────────────────
function renderInto(root, _opts = {}) {
  if (!root) return;
  root.innerHTML = renderPage();
  highlightMapSelection();
  // Initial signals load (paints into the already-rendered table body).
  if (!_signalsData) {
    const rowsEl = root.querySelector("[data-st-rows]");
    if (rowsEl) rowsEl.innerHTML = `<tr><td colspan="6" class="st-empty">Loading…</td></tr>`;
  } else {
    repaintRepository();
  }
}

async function enterView() {
  const root = shell();
  if (!root) return;
  root.innerHTML = `${pageStyles()}<div class="st-page"><div class="st-loading">Loading Screen-Time Watch…</div></div>`;
  try {
    [_stateStance, _isOwner] = await Promise.all([fetchStateStance(), fetchIsOwner()]);
  } catch (err) {
    console.error("screentime: failed to load state stance", err);
    _stateStance = { states: [], counts: {}, total_states: 0 };
  }
  renderInto(root, { mapOnly: false });
  if (!_mounted) {
    wireEvents();
    _mounted = true;
  }
  // Load the signal repository.
  await reloadSignals();
}

function handleViewChange(view) {
  if (normalizeAppView(view) === SCREENTIME_VIEW) {
    enterView();
  }
}

// Subscribe + handle the case where we boot already on this view.
onState("view", handleViewChange);
if (normalizeAppView(getState("view")) === SCREENTIME_VIEW) {
  handleViewChange(getState("view"));
}

// ── Scoped styles (inlined to avoid editing index.html; uses design tokens) ────
function pageStyles() {
  return `<style data-st-styles>
    .st-page {
      --st-favorable: var(--success, #2F7D4F);
      --st-unfavorable: var(--danger, #B7451E);
      --st-neutral: var(--ink-4, #8F8576);
      --st-noinfo: var(--rule, rgba(30,26,21,0.10));
      --st-tile-ink: #fff;
      --st-tile-ink-dim: var(--ink-2, #3A332B);
      font-family: var(--font-display, system-ui, sans-serif);
      color: var(--ink, #1E1A15);
      max-width: 1180px;
      margin: 0 auto;
      padding: 8px 4px 56px;
    }
    .st-loading { padding: 48px; color: var(--ink-3); }
    .st-eyebrow {
      font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
      color: var(--amber, #C77E2B); margin-bottom: 6px;
    }
    .st-hero { margin-bottom: 18px; }
    .st-hero-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .st-hero-title { font-size: 26px; font-weight: 800; margin: 0 0 6px; line-height: 1.15; }
    .st-hero-sub { color: var(--ink-3, #655C50); margin: 0; max-width: 640px; font-size: 14px; }
    .st-map-section { margin-bottom: 26px; }
    .st-map-card, .st-repo {
      background: var(--surface-card, rgba(255,253,248,0.7));
      border: 1px solid var(--rule, rgba(30,26,21,0.10));
      border-radius: 14px;
      padding: 20px 22px;
    }
    .st-map-head, .st-repo-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
    .st-map-head h3, .st-repo-head h3 { margin: 0; font-size: 16px; font-weight: 700; }
    .st-legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--ink-3); }
    .st-legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .st-legend-dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
    .st-legend-count { font-weight: 700; color: var(--ink-2); margin-left: 2px; }
    .st-map-wrap { overflow-x: auto; }
    .st-map-svg { width: 100%; height: auto; max-width: 720px; display: block; margin: 0 auto; }
    .st-tile { cursor: pointer; }
    .st-tile-rect { transition: filter .12s ease, stroke .12s ease; stroke: transparent; stroke-width: 2; }
    .st-tile:hover .st-tile-rect, .st-tile:focus .st-tile-rect { filter: brightness(1.08); stroke: var(--ink, #1E1A15); }
    .st-tile.is-selected .st-tile-rect { stroke: var(--amber, #C77E2B); stroke-width: 3; }
    .st-tile:focus { outline: none; }
    .st-tile-label { font: 700 13px var(--font-mono, monospace); pointer-events: none; }
    .st-tile-count { font: 600 9px var(--font-mono, monospace); fill: rgba(255,255,255,0.85); pointer-events: none; }
    .st-filters { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 14px; }
    .st-filter { display: flex; flex-direction: column; gap: 4px; }
    .st-filter label { font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--ink-4); }
    .st-filter select, .st-filter input {
      font: inherit; font-size: 13px; padding: 6px 8px;
      border: 1px solid var(--rule); border-radius: 8px;
      background: var(--surface, rgba(255,253,248,0.6)); color: var(--ink);
    }
    .st-filter-search { flex: 1; min-width: 180px; }
    .st-filter-search input { width: 100%; }
    .st-btn { font: inherit; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 8px; cursor: pointer; border: 1px solid var(--rule); }
    .st-btn-ghost { background: transparent; color: var(--ink-2); }
    .st-btn-ghost:hover { background: var(--rule); }
    .st-btn-ghost:disabled { opacity: .4; cursor: default; }
    .st-btn-danger { background: var(--danger, #B7451E); color: #fff; border-color: transparent; }
    .st-btn-danger:hover { filter: brightness(1.06); }
    .st-scrub { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
    .st-scrub-note { font-size: 11px; color: var(--ink-4); }
    .st-table-wrap { overflow-x: auto; }
    .st-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .st-table thead th { text-align: left; font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--ink-4); padding: 8px 10px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
    .st-row td { padding: 12px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }
    .st-cell-title { max-width: 420px; }
    .st-title { font-weight: 600; color: var(--ink); }
    .st-summary { color: var(--ink-3); font-size: 12px; margin-top: 3px; }
    .st-angle { color: var(--amber); font-size: 12px; margin-top: 4px; font-style: italic; }
    .st-cell-date { white-space: nowrap; color: var(--ink-3); font-variant-numeric: tabular-nums; }
    .st-badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
    .st-status-proposed { background: rgba(199,126,43,0.15); color: var(--amber); }
    .st-status-passed { background: rgba(183,69,30,0.14); color: var(--danger); }
    .st-status-amended { background: rgba(199,126,43,0.12); color: var(--amber); }
    .st-status-guidance, .st-status-news { background: var(--rule); color: var(--ink-3); }
    .st-stance { background: color-mix(in srgb, var(--st-badge) 16%, transparent); color: var(--ink-2); }
    .st-src { color: var(--accent, #2F6FB3); text-decoration: none; font-weight: 600; }
    .st-src:hover { text-decoration: underline; }
    .st-src-none { color: var(--ink-4); font-weight: 400; }
    .st-empty { padding: 28px 10px; text-align: center; color: var(--ink-3); }
    .st-pager { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 14px; }
    .st-pager-info { font-size: 12px; color: var(--ink-3); }
    @media (max-width: 640px) {
      .st-hero-row { flex-direction: column; }
      .st-scrub { align-items: flex-start; }
    }
  </style>`;
}
