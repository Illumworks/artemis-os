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
let _archivedFilter = "default";
let _openMenuId = null;
let _confirm = null;

// PIPE2: active canvas instance
let _canvas = null;

const MOUNT = "#pipelines-page-root";
const ARCHIVED_FILTER_KEY = "artemis.pipelines.archived-filter";
const getRoot = () => document.querySelector(MOUNT);

async function loadPipelines() {
  _error = null;
  try {
    if (_archivedFilter === "include") {
      const [visible, archived] = await Promise.all([
        api.listPipelinesApi(),
        api.listPipelinesApi({ status: "archived" }),
      ]);
      _pipelines = [...visible, ...archived];
    } else {
      _pipelines = await api.listPipelinesApi(
        _archivedFilter === "only" ? { status: "archived" } : {}
      );
    }
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
  if (p.status === "archived") {
    return `<span class="parchived-label">Archived</span>`;
  }
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
  const menu = `<div class="pmenu">
    <button class="pkebab" aria-label="Pipeline actions for ${escapeHtml(p.name)}" data-id="${p.id}">⋯</button>
    ${_openMenuId === p.id ? `<div class="pmenu-list">
      ${p.status === "archived" ? `
        <button class="pmenu-item prestore" data-menu-action="restore" data-id="${p.id}">Restore</button>
        <button class="pmenu-item pmenu-danger ppermadelete" data-menu-action="permanent" data-id="${p.id}">Permanently delete</button>`
      : `<button class="pmenu-item parchive" data-menu-action="archive" data-id="${p.id}">Archive</button>`}
    </div>` : ""}
  </div>`;
  if (compact) {
    return `<div class="pcard pcard-c" data-pid="${p.id}">
      <div class="pcc">${dot(p.status)}<span class="pcn">${escapeHtml(p.name)}</span>${menu}
      ${toggle(p)}<span class="pcm">${escapeHtml(trigger(p))}</span>${runBadge(p.latestRun)}
      <div class="pca">${actions}</div></div></div>`;
  }
  return `<div class="pcard" data-pid="${p.id}">
    <div class="pch">${dot(p.status)}<h3>${escapeHtml(p.name)}</h3>${menu}${toggle(p)}${runBadge(p.latestRun)}</div>
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

function confirmDialog() {
  if (!_confirm) return "";
  const okDisabled = _confirm.type === "permanent" && _confirm.typed !== _confirm.pipeline.name;
  return `<div class="pmodal-backdrop" role="presentation">
    <div class="pmodal" role="dialog" aria-modal="true" aria-labelledby="pmodal-title">
      <h3 id="pmodal-title">${escapeHtml(_confirm.title)}</h3>
      <p>${escapeHtml(_confirm.message)}</p>
      ${_confirm.type === "permanent" ? `
        <label class="plbl" for="pconfirm-name">Type pipeline name to confirm</label>
        <input class="pinp" id="pconfirm-name" type="text" value="${escapeHtml(_confirm.typed || "")}" autocomplete="off" />
        <div class="pconfirm-name">${escapeHtml(_confirm.pipeline.name)}</div>` : ""}
      <div class="pef">
        <button class="pbtn pbtn-g" id="pmodal-cancel">Cancel</button>
        <button class="pbtn ${_confirm.type === "permanent" ? "pbtn-danger" : "pbtn-p"}" id="pmodal-confirm" ${okDisabled ? "disabled" : ""}>${escapeHtml(_confirm.confirmLabel)}</button>
      </div>
    </div>
  </div>`;
}

async function handlePipelineMenuAction(action, id) {
  const pipeline = _pipelines.find((x) => x.id === id);
  if (!pipeline) return;
  if (action === "restore") {
    try {
      await api.updatePipelineApi(pipeline.id, { status: "active" });
      showToast("Pipeline restored");
      _openMenuId = null;
      await loadPipelines();
    } catch (e) { showToast("Restore failed", e.message, { isError: true }); }
    return;
  }
  _confirm = action === "archive" ? {
    type: "archive",
    pipeline,
    title: `Archive ${pipeline.name}?`,
    message: "Pipelines in archive are paused and hidden from default list but can be restored.",
    confirmLabel: "Archive",
  } : {
    type: "permanent",
    pipeline,
    typed: "",
    title: `Permanently delete ${pipeline.name}?`,
    message: "This cannot be undone. All run history will be lost.",
    confirmLabel: "Permanently delete",
  };
  _openMenuId = null;
  render();
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
        <div class="pfilter" aria-label="Archived filter">
          <button class="psb ${_archivedFilter === "default" ? "psb-a" : ""}" data-archive-filter="default">Default</button>
          <button class="psb ${_archivedFilter === "include" ? "psb-a" : ""}" data-archive-filter="include">Include archived</button>
          <button class="psb ${_archivedFilter === "only" ? "psb-a" : ""}" data-archive-filter="only">Only archived</button>
        </div>
      </div>
    </div>
    <div class="plist">${list}</div>
    ${_showNew ? newForm() : ""}
    ${editPanel()}
    ${confirmDialog()}
  </div>`;
  wire(root);
}

function wire(root) {
  root.querySelector("#psrch")?.addEventListener("input", (e) => { _search = e.target.value; render(); });
  root.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => { _sortBy = b.dataset.sort; render(); }));
  root.querySelectorAll("[data-archive-filter]").forEach((b) => b.addEventListener("click", async () => {
    _archivedFilter = b.dataset.archiveFilter;
    localStorage.setItem(ARCHIVED_FILTER_KEY, _archivedFilter);
    _openMenuId = null;
    await loadPipelines();
  }));
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
  root.querySelectorAll(".pkebab").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    _openMenuId = _openMenuId === b.dataset.id ? null : b.dataset.id;
    render();
  }));
  if (!root.dataset.pipelineMenuWired) {
    root.dataset.pipelineMenuWired = "true";
    const handleMenuAction = async (e) => {
      const target = e.target instanceof Element ? e.target : e.target?.parentElement;
      const b = target?.closest("[data-menu-action]");
      if (!b) return;
      if (e.type === "click" && e.detail !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      await handlePipelineMenuAction(b.dataset.menuAction, b.dataset.id);
    };
    root.addEventListener("pointerdown", handleMenuAction);
    root.addEventListener("click", handleMenuAction);
  }
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
  root.querySelector("#pmodal-cancel")?.addEventListener("click", () => { _confirm = null; render(); });
  root.querySelector("#pconfirm-name")?.addEventListener("input", (e) => {
    if (_confirm) _confirm.typed = e.target.value;
    const confirm = root.querySelector("#pmodal-confirm");
    if (confirm && _confirm) confirm.disabled = _confirm.typed !== _confirm.pipeline.name;
  });
  root.querySelector("#pmodal-confirm")?.addEventListener("click", async () => {
    if (!_confirm) return;
    try {
      if (_confirm.type === "archive") {
        await api.deletePipelineApi(_confirm.pipeline.id);
        showToast("Pipeline archived");
      } else {
        await api.permanentDeletePipelineApi(_confirm.pipeline.id);
        showToast("Pipeline deleted permanently");
      }
      _confirm = null;
      await loadPipelines();
    } catch (e) { showToast("Pipeline action failed", e.message, { isError: true }); }
  });
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
  _editing = null; _showNew = false; _openMenuId = null; _confirm = null;
  _archivedFilter = localStorage.getItem(ARCHIVED_FILTER_KEY) || "default";
  if (!["default", "include", "only"].includes(_archivedFilter)) _archivedFilter = "default";
  closeCanvas();
  render();
  loadPipelines();
}
