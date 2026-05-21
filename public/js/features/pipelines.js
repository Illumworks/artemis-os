/**
 * Pipelines page — PIPE1 + PIPE2
 * List + visual canvas (default) + JSON editor (power-user fallback).
 */
import { escapeHtml } from "../core/utils.js";
import * as api from "../core/api.js";
import { PipelineCanvas } from "../components/pipeline-canvas.js";

let _pipelines = [];
let _loaded = false;
let _error = null;
let _search = "";
let _sortBy = "updated";
let _editing = null;
let _editJson = "";
let _editErr = null;
let _showNew = false;

// PIPE2: active canvas instance
let _canvas = null;

const MOUNT = "#pipelines-page-root";
const getRoot = () => document.querySelector(MOUNT);

async function loadPipelines() {
  _error = null;
  try {
    _pipelines = await api.listPipelinesApi();
    _loaded = true;
  } catch (e) {
    _error = e.message || String(e);
    _loaded = true;
  }
  render();
}

function showToast(label, title = "", { isError = false, ms = 4000 } = {}) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `bg-toast${isError ? " toast-error" : ""}`;
  toast.innerHTML = `
    <span class="bg-toast-dot"></span>
    <div class="bg-toast-body">
      <div class="bg-toast-label"></div>
      <div class="bg-toast-title"></div>
    </div>
    <button class="bg-toast-close" title="Dismiss">&times;</button>`;
  toast.querySelector(".bg-toast-label").textContent = label;
  toast.querySelector(".bg-toast-title").textContent = title;
  const dismiss = () => {
    toast.classList.add("toast-exit");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  toast.querySelector(".bg-toast-close")?.addEventListener("click", dismiss);
  container.appendChild(toast);
  setTimeout(() => { if (toast.parentNode) dismiss(); }, ms);
}

function filtered() {
  let list = _pipelines.slice();
  if (_search) {
    const q = _search.toLowerCase();
    list = list.filter((p) => p.name.toLowerCase().includes(q));
  }
  return _sortBy === "name"
    ? list.sort((a, b) => a.name.localeCompare(b.name))
    : list.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
}

function runBadge(run) {
  if (!run) return '<span class="pb pb-none">No runs</span>';
  const mod = { queued: "queued", running: "running", awaiting_approval: "waiting",
    succeeded: "success", failed: "failed", cancelled: "cancelled" }[run.status] || "unknown";
  return `<span class="pb pb-${mod}">${escapeHtml(run.status)}</span>`;
}

function dot(status) {
  return `<span class="pdot pdot-${escapeHtml(status)}"></span>`;
}

function toggle(p) {
  const act = p.status === "active";
  return `<button
    class="pswitch"
    role="switch"
    aria-checked="${act ? "true" : "false"}"
    aria-label="${act ? "Disable" : "Enable"} ${escapeHtml(p.name)}"
    data-id="${p.id}"
    data-action="${act ? "disable" : "enable"}">
      <span class="pswitch-track"><span class="pswitch-thumb"></span></span>
      <span class="pswitch-label">${act ? "Active" : "Paused"}</span>
    </button>`;
}

function trigger(p) {
  const n = (p.nodes || []).find((x) => x.type?.startsWith("trigger_"));
  if (!n) return "Manual";
  return { trigger_scheduled: n.config?.cron || "Scheduled",
    trigger_webhook: "Webhook", trigger_event: "Event" }[n.type] || "Manual";
}

function card(p) {
  const nodes = (p.nodes || []).length;
  const compact = nodes <= 1;
  const actions = `
    <button class="pbtn pbtn-p popen-canvas" data-id="${p.id}">Open Canvas</button>
    <button class="pbtn pbtn-g pedit" data-id="${p.id}">Edit JSON</button>
    <button class="pbtn pbtn-g prun" data-id="${p.id}">Run</button>`;
  if (compact) {
    return `<div class="pcard pcard-c" data-pid="${p.id}">
      <div class="pcc">${dot(p.status)}<span class="pcn">${escapeHtml(p.name)}</span>
      ${toggle(p)}<span class="pcm">${escapeHtml(trigger(p))}</span>${runBadge(p.latestRun)}
      <div class="pca">${actions}</div></div></div>`;
  }
  return `<div class="pcard" data-pid="${p.id}">
    <div class="pch">${dot(p.status)}<h3>${escapeHtml(p.name)}</h3>${toggle(p)}${runBadge(p.latestRun)}</div>
    ${p.description ? `<p class="pcd">${escapeHtml(p.description.slice(0, 120))}</p>` : ""}
    <div class="pcs"><span>${nodes} nodes</span><span>${(p.edges||[]).length} edges</span><span>${escapeHtml(trigger(p))}</span></div>
    <div class="pca">${actions}</div></div>`;
}

function editPanel() {
  if (!_editing) return "";
  return `<div class="pedit-panel">
    <div class="peph"><h3>Edit: ${escapeHtml(_editing.name)}</h3><button class="pbtn pbtn-g" id="pec">Close</button></div>
    <p class="pehint">PIPE1 JSON editor — visual canvas in PIPE2.</p>
    ${_editErr ? `<div class="peerr">${escapeHtml(_editErr)}</div>` : ""}
    <textarea class="petxt" id="petxt" rows="18" spellcheck="false">${escapeHtml(_editJson)}</textarea>
    <div class="pef"><button class="pbtn pbtn-p" id="pes">Save</button><button class="pbtn pbtn-g" id="pec2">Cancel</button></div>
  </div>`;
}

function newForm() {
  return `<div class="pedit-panel">
    <div class="peph"><h3>New Pipeline</h3><button class="pbtn pbtn-g" id="pnc">Close</button></div>
    <label class="plbl">Name</label><input class="pinp" id="pnn" type="text" placeholder="My pipeline" />
    <label class="plbl">Description</label><input class="pinp" id="pnd" type="text" placeholder="Optional" />
    <div class="pef"><button class="pbtn pbtn-p" id="pns">Create</button><button class="pbtn pbtn-g" id="pnc2">Cancel</button></div>
  </div>`;
}

export function render() {
  const root = getRoot();
  if (!root) return;
  if (!_loaded) { root.innerHTML = `<div class="ppg"><div class="pload">Loading pipelines…</div></div>`; return; }
  const list = (() => {
    if (_error) return `<div class="pempty"><strong>Load failed</strong><span>${escapeHtml(_error)}</span></div>`;
    const f = filtered();
    if (!f.length) return `<div class="pempty"><strong>No pipelines yet.</strong><span>Create one or seed via <code>scripts/seed_marketing_pipeline.py</code>.</span></div>`;
    return f.map(card).join("");
  })();
  root.innerHTML = `<div class="ppg">
    <div class="ppgh">
      <div class="pptr"><h2>Pipelines</h2><button class="pbtn pbtn-p" id="pnbtn">+ New</button></div>
      <p class="ppdesc">Unified orchestration. Canvas in PIPE2; execution in PIPE4.</p>
      <div class="ptb">
        <input class="psrch" id="psrch" type="search" placeholder="Search…" value="${escapeHtml(_search)}" />
        <div class="psrt">
          <button class="psb ${_sortBy === "updated" ? "psb-a" : ""}" data-sort="updated">Recent</button>
          <button class="psb ${_sortBy === "name" ? "psb-a" : ""}" data-sort="name">Name</button>
        </div>
      </div>
    </div>
    <div class="plist">${list}</div>
    ${_showNew ? newForm() : ""}
    ${editPanel()}
  </div>`;
  wire(root);
}

function wire(root) {
  root.querySelector("#psrch")?.addEventListener("input", (e) => { _search = e.target.value; render(); });
  root.querySelectorAll(".psb").forEach((b) => b.addEventListener("click", () => { _sortBy = b.dataset.sort; render(); }));
  root.querySelector("#pnbtn")?.addEventListener("click", () => { _showNew = true; render(); });
  ["#pnc", "#pnc2"].forEach((id) => root.querySelector(id)?.addEventListener("click", () => { _showNew = false; render(); }));
  root.querySelector("#pns")?.addEventListener("click", async () => {
    const name = root.querySelector("#pnn")?.value?.trim();
    if (!name) { showToast("Create failed", "Name required", { isError: true }); return; }
    const desc = root.querySelector("#pnd")?.value?.trim() || null;
    try { await api.createPipelineApi({ name, description: desc, nodes: [], edges: [] }); _showNew = false; await loadPipelines(); }
    catch (e) { showToast("Create failed", e.message, { isError: true }); }
  });
  root.querySelectorAll(".pswitch").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      if (b.dataset.action === "enable") await api.enablePipelineApi(b.dataset.id);
      else await api.disablePipelineApi(b.dataset.id);
      await loadPipelines();
    } catch (e) { showToast("Toggle failed", e.message, { isError: true }); }
  }));
  root.querySelectorAll(".pedit").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    const p = _pipelines.find((x) => x.id === b.dataset.id);
    if (!p) return;
    _editing = p;
    _editJson = JSON.stringify({ nodes: p.nodes, edges: p.edges, triggerConfig: p.triggerConfig }, null, 2);
    _editErr = null;
    render();
  }));
  ["#pec", "#pec2"].forEach((id) => root.querySelector(id)?.addEventListener("click", () => { _editing = null; render(); }));
  root.querySelector("#petxt")?.addEventListener("input", (e) => { _editJson = e.target.value; });
  root.querySelector("#pes")?.addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(_editJson); } catch (e) { _editErr = `JSON error: ${e.message}`; render(); return; }
    try {
      await api.updatePipelineApi(_editing.id, { nodes: parsed.nodes ?? [], edges: parsed.edges ?? [], triggerConfig: parsed.triggerConfig ?? null });
      _editing = null; await loadPipelines();
    } catch (e) { _editErr = `Save failed: ${e.message}`; render(); }
  });
  root.querySelectorAll(".prun").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await api.runPipelineApi(b.dataset.id);
      showToast("Run queued — execution engine arrives in PIPE4.", "Status will appear in run history.");
      await loadPipelines();
    } catch (e) { showToast("Run failed", e.message, { isError: true }); }
  }));
  root.querySelectorAll(".popen-canvas").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    const p = _pipelines.find((x) => x.id === b.dataset.id);
    if (p) openCanvas(p);
  }));
}

// ── PIPE2: Canvas view ─────────────────────────────────────────────────────

let _canvasOverlay = null;

function openCanvas(pipeline) {
  // Tear down any existing canvas
  closeCanvas();

  _canvasOverlay = document.createElement("div");
  _canvasOverlay.className = "pcv-overlay";

  const header = document.createElement("div");
  header.className = "pcv-overlay-header";
  header.innerHTML = `
    <span class="pcv-overlay-title">${escapeHtml(pipeline.name)}</span>
    <button class="pbtn pbtn-g pcv-overlay-close" title="Back to pipeline list">✕ Close</button>
  `;
  _canvasOverlay.appendChild(header);

  const body = document.createElement("div");
  body.className = "pcv-overlay-body";
  _canvasOverlay.appendChild(body);

  const root = getRoot();
  if (root) root.appendChild(_canvasOverlay);

  _canvas = new PipelineCanvas({
    container: body,
    pipeline,
    onSaved: () => loadPipelines(),
  });
  _canvas.mount();

  header.querySelector(".pcv-overlay-close")?.addEventListener("click", closeCanvas);
}

function closeCanvas() {
  if (_canvas) { _canvas.destroy(); _canvas = null; }
  if (_canvasOverlay) { _canvasOverlay.remove(); _canvasOverlay = null; }
}

export function initPipelinesPage() {
  _loaded = false; _error = null; _search = ""; _sortBy = "updated";
  _editing = null; _showNew = false;
  closeCanvas();
  render();
  loadPipelines();
}
