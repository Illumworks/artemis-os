/**
 * pipeline-canvas.js — PIPE2
 * Visual pipeline canvas: drag-drop nodes, Bezier edges, zoom, palette, config drawer.
 * Light DOM only; no Shadow DOM.
 *
 * State shape mirrors PIPE1 PipelineNode + PipelineEdge TypedDicts:
 *   nodes: [ { id, type, label, config, position: {x,y}, ...extra } ]
 *   edges: [ { id, source_node_id, target_node_id, condition, data_shape, ...extra } ]
 *
 * Extra fields on nodes/edges are preserved (JSONB round-trip safe).
 */

import * as api from "../core/api.js";
import { describeCron } from "./cron-utils.js";
import { buildNodeCard, updateNodeCardPosition, setNodeCardSelected } from "./pipeline-node-card.js";
import { PipelinePalette } from "./pipeline-palette.js";
import { PipelineConfigDrawer } from "./pipeline-config-drawer.js";
import { PipelineAIPanel } from "./pipeline-ai-panel.js";

// ── Canvas store ──────────────────────────────────────────────────────────────

function createStore(pipeline) {
  return {
    id: pipeline.id,
    name: pipeline.name,
    // Deep-clone so we own the data; extra fields preserved
    nodes: (pipeline.nodes || []).map((n) => ({ ...n, position: { ...(n.position || { x: 0, y: 0 }) } })),
    edges: (pipeline.edges || []).map((e) => ({ ...e })),
    triggerConfig: pipeline.triggerConfig ?? null,
    zoom: 1.0,
    selectedNodeId: null,
    selectedEdgeId: null,
    dirty: false,
    // Undo/redo stacks (each entry: { nodes, edges } snapshot)
    undoStack: [],
    redoStack: [],
  };
}

function snapshot(state) {
  return {
    nodes: state.nodes.map((n) => ({ ...n, position: { ...n.position } })),
    edges: state.edges.map((e) => ({ ...e })),
  };
}

function pushUndo(state) {
  state.undoStack.push(snapshot(state));
  if (state.undoStack.length > 50) state.undoStack.shift();
  state.redoStack = [];
}

// ── Unique ID generator ───────────────────────────────────────────────────────

let _idCounter = Date.now();
function genId(prefix = "node") {
  return `${prefix}_${(_idCounter++).toString(36)}`;
}

// ── Default configs per node type ────────────────────────────────────────────

function defaultConfig(type) {
  switch (type) {
    case "trigger_scheduled": return { cron: "0 */4 * * *", timezone: "UTC" };
    case "trigger_webhook":   return { path: "/webhook" };
    case "trigger_event":     return { event_type: "signal.created" };
    case "agent_invocation":  return { agent_id: "", mode: "scheduled" };
    case "skill_call":        return { skill_id: "" };
    case "human_gate":        return { approval_kind: "manual", approvers: [], timeout_hours: 72 };
    case "conditional":       return { expression: "" };
    case "sub_pipeline":      return { pipeline_id: "" };
    default:                  return {};
  }
}

function defaultLabel(type) {
  const m = {
    trigger_manual:    "Manual Trigger",
    trigger_scheduled: "Scheduled Trigger",
    trigger_webhook:   "Webhook Trigger",
    trigger_event:     "Event Trigger",
    agent_invocation:  "Agent",
    skill_call:        "Skill",
    human_gate:        "Human Gate",
    conditional:       "Conditional",
    sub_pipeline:      "Sub-Pipeline",
  };
  return m[type] || type;
}

// ── Bezier edge path ──────────────────────────────────────────────────────────

const NODE_W = 180;
const NODE_H = 80;

function edgePath(sx, sy, tx, ty) {
  const dx = Math.abs(tx - sx);
  const cp = Math.max(60, dx * 0.45);
  return `M ${sx} ${sy} C ${sx + cp} ${sy}, ${tx - cp} ${ty}, ${tx} ${ty}`;
}

/**
 * Return port center in local canvas-inner coordinate space.
 * We use node.style.left/top (which are local-space px values) rather than
 * getBoundingClientRect so the SVG paths — which also live inside the same
 * canvas-inner transform container — are always in the same coordinate space
 * as the nodes. This fixes the zoom+drag edge-break bug (Fix Path 1).
 */
function getPortCenter(nodeEl, port) {
  const x = parseFloat(nodeEl.style.left) || 0;
  const y = parseFloat(nodeEl.style.top)  || 0;
  return port === "out"
    ? { x: x + NODE_W,      y: y + NODE_H / 2 }
    : { x: x,               y: y + NODE_H / 2 };
}

// ── Auto-layout (simple topological left-to-right) ───────────────────────────

function autoLayout(nodes, edges) {
  // Build adjacency
  const outEdges = {};
  const inCount = {};
  for (const n of nodes) { outEdges[n.id] = []; inCount[n.id] = 0; }
  for (const e of edges) {
    if (outEdges[e.source_node_id]) outEdges[e.source_node_id].push(e.target_node_id);
    if (inCount[e.target_node_id] !== undefined) inCount[e.target_node_id]++;
  }

  // Topological sort (Kahn)
  const queue = nodes.filter((n) => inCount[n.id] === 0).map((n) => n.id);
  const order = [];
  const rank = {};
  for (const id of queue) rank[id] = 0;
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    for (const nextId of (outEdges[id] || [])) {
      rank[nextId] = Math.max(rank[nextId] ?? 0, (rank[id] ?? 0) + 1);
      inCount[nextId]--;
      if (inCount[nextId] === 0) queue.push(nextId);
    }
  }
  // Any remaining (cycles) — append with next rank
  for (const n of nodes) {
    if (!order.includes(n.id)) { order.push(n.id); rank[n.id] = rank[n.id] ?? order.length; }
  }

  // Assign positions by rank column
  const cols = {};
  for (const id of order) {
    const r = rank[id] ?? 0;
    cols[r] = (cols[r] || 0);
    cols[r]++;
  }
  const colCount = {};
  const XGAP = 240, YGAP = 120, XOFF = 60, YOFF = 60;
  return nodes.map((n) => {
    const r = rank[n.id] ?? 0;
    colCount[r] = (colCount[r] ?? 0);
    const row = colCount[r];
    colCount[r]++;
    return { ...n, position: { x: XOFF + r * XGAP, y: YOFF + row * YGAP } };
  });
}

// ── Main PipelineCanvas class ─────────────────────────────────────────────────

export class PipelineCanvas {
  constructor({ container, pipeline, onSaved }) {
    this._container = container;
    this._onSaved = onSaved;
    this._state = createStore(pipeline);
    this._nodeEls = new Map(); // nodeId → DOM element
    this._draggingNodeId = null;
    this._dragOffset = { x: 0, y: 0 };
    this._edgeDraft = null; // { sourceNodeId, svgLine }
    this._showJson = false;
    this._saveDebounce = null;
    this._palette = null;
    this._drawer = null;
    this.el = null;

    // Pan state (view-only, not persisted to DB)
    this._panX = 0;
    this._panY = 0;
    this._isPanning = false;
    this._panStart = { x: 0, y: 0 };       // mouse position when pan began
    this._panOrigin = { x: 0, y: 0 };      // _panX/_panY when pan began
    this._spaceHeld = false;

    // Performance: edge index by nodeId for O(connected) drag updates
    this._edgeIndex = new Map(); // nodeId → Set<edgeId>
    this._rafPending = false;    // requestAnimationFrame gate
    this._dragPendingNode = null; // buffered drag state for RAF

    // AI Assistant panel
    this._aiPanel = null;
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  mount() {
    this._buildShell();
    this._mountPalette();
    this._mountDrawer();
    this._mountAIPanel();
    this._renderAll();
    this._wireToolbar();
    this._wireCanvasEvents();
    this._wireKeyboard();
  }

  destroy() {
    document.removeEventListener("mousemove", this._onMouseMove);
    document.removeEventListener("mouseup",   this._onMouseUp);
    document.removeEventListener("keydown",   this._onKeyDown);
    document.removeEventListener("keyup",     this._onKeyUp);
    this._aiPanel?.destroy();
    if (this.el) this.el.remove();
  }

  // ── Shell construction ────────────────────────────────────────────────────

  _buildShell() {
    this.el = document.createElement("div");
    this.el.className = "pcv-shell";
    this.el.innerHTML = `
      <div class="pcv-toolbar">
        <div class="pcv-toolbar-left">
          <button class="pbtn pbtn-p pcv-btn-save" title="Save (Cmd/Ctrl+S)">Save</button>
          <button class="pbtn pbtn-g pcv-btn-run"  title="Run pipeline">Run</button>
          <span class="pcv-dirty-dot" title="Unsaved changes" style="display:none">●</span>
        </div>
        <div class="pcv-toolbar-center">
          <span class="pcv-pipeline-name"></span>
        </div>
        <div class="pcv-toolbar-right">
          <button class="pbtn pbtn-g pcv-btn-layout" title="Auto-layout nodes">Layout</button>
          <button class="pbtn pbtn-g pcv-btn-fit"    title="Fit all nodes in view">Fit</button>
          <button class="pbtn pbtn-g pcv-btn-zoom-out" title="Zoom out">−</button>
          <span class="pcv-zoom-label">100%</span>
          <button class="pbtn pbtn-g pcv-btn-zoom-in" title="Zoom in">+</button>
          <button class="pbtn pbtn-g pcv-btn-json" title="Toggle JSON editor">View JSON</button>
          <button class="pbtn pbtn-g pcv-btn-ai" title="Toggle AI Assistant panel">✦ AI</button>
        </div>
      </div>

      <div class="pcv-workspace">
        <div class="pcv-canvas-wrap">
          <div class="pcv-canvas" tabindex="0">
            <div class="pcv-canvas-inner">
              <svg class="pcv-edges-svg" xmlns="http://www.w3.org/2000/svg">
                <defs>
                  <marker id="pcv-arrow" markerWidth="8" markerHeight="8"
                    refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L0,6 L8,3 z" class="pcv-arrow-head"/>
                  </marker>
                  <marker id="pcv-arrow-accent" markerWidth="8" markerHeight="8"
                    refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L0,6 L8,3 z" class="pcv-arrow-head pcv-arrow-head--accent"/>
                  </marker>
                </defs>
                <g class="pcv-edges-g"></g>
                <line class="pcv-edge-draft" style="display:none" stroke-dasharray="6,4"/>
              </svg>
            </div>
          </div>
        </div>
        <div class="pcv-json-panel" style="display:none">
          <div class="pcv-json-hint">JSON editor — edit and toggle back to canvas to apply changes.</div>
          <textarea class="pcv-json-textarea" spellcheck="false" rows="30"></textarea>
          <div class="pcv-json-footer">
            <button class="pbtn pbtn-p pcv-json-apply">Apply &amp; return to canvas</button>
            <span class="pcv-json-err" style="display:none"></span>
          </div>
        </div>
      </div>

      <div class="pcv-empty-state" style="display:none">
        <div class="pcv-empty-text">
          This pipeline is empty.<br/>
          Drag a node from the left palette to get started.
        </div>
      </div>
    `;

    this._container.appendChild(this.el);

    // Set pipeline name
    this.el.querySelector(".pcv-pipeline-name").textContent = this._state.name || "";
  }

  // ── Palette ───────────────────────────────────────────────────────────────

  _mountPalette() {
    const wrap = document.createElement("div");
    wrap.className = "pcv-palette-wrap";
    // Insert before the canvas wrap
    const workspace = this.el.querySelector(".pcv-workspace");
    workspace.insertBefore(wrap, workspace.firstChild);

    this._palette = new PipelinePalette({
      onDragStart: (data, e) => {
        // Store drag data for canvas drop
        this._paletteDragData = data;
      },
    });
    this._palette.mount(wrap);
  }

  // ── Drawer ────────────────────────────────────────────────────────────────

  _mountDrawer() {
    const wrap = document.createElement("div");
    wrap.className = "pcv-drawer-wrap";
    const workspace = this.el.querySelector(".pcv-workspace");
    workspace.appendChild(wrap);

    this._drawer = new PipelineConfigDrawer({
      pipelineId: this._state.id ?? null,
      onSave: (nodeId, updates) => {
        pushUndo(this._state);
        const idx = this._state.nodes.findIndex((n) => n.id === nodeId);
        if (idx >= 0) {
          const nextNode = { ...this._state.nodes[idx], ...updates };
          if (nextNode.type === "trigger_scheduled" && nextNode.config?.cron) {
            nextNode.label = describeCron(nextNode.config.cron) || nextNode.label;
          }
          this._state.nodes[idx] = nextNode;
          this._markDirty();
          this._redrawNode(nodeId);
          this._updateConnectedEdges(nodeId);
        }
      },
      onDelete: (nodeId) => {
        this._deleteNode(nodeId);
      },
      onClose: () => {
        this._state.selectedNodeId = null;
        this._updateNodeSelections();
      },
    });
    this._drawer.mount(wrap);
  }

  // ── AI Panel ──────────────────────────────────────────────────────────────

  _mountAIPanel() {
    const wrap = document.createElement("div");
    wrap.className = "pcv-ai-panel-wrap";
    const workspace = this.el.querySelector(".pcv-workspace");
    workspace.appendChild(wrap);

    this._aiPanel = new PipelineAIPanel({
      pipelineId: this._state.id,
      getCanvasState: () => this.getState(),
      onProposalAccept: (proposal, updatedNodes, updatedEdges) => {
        // Apply the accepted proposal to canvas state + mark dirty
        this._state.undoStack.push({
          nodes: this._state.nodes.map((n) => ({ ...n, position: { ...n.position } })),
          edges: this._state.edges.map((e) => ({ ...e })),
        });
        if (this._state.undoStack.length > 50) this._state.undoStack.shift();
        this._state.redoStack = [];
        this._state.nodes = updatedNodes;
        this._state.edges = updatedEdges;
        this._markDirty();
        this._renderAll();
      },
      onToggle: (isOpen) => {
        const btn = this.el?.querySelector(".pcv-btn-ai");
        if (btn) {
          btn.classList.toggle("pcv-btn-ai--active", isOpen);
        }
        // Reflow canvas wrap when panel opens/closes
        const canvasWrap = this.el?.querySelector(".pcv-canvas-wrap");
        if (canvasWrap) {
          canvasWrap.style.marginRight = isOpen ? "340px" : "";
        }
      },
    });
    this._aiPanel.mount(wrap);
  }

  // ── Full render ───────────────────────────────────────────────────────────

  _renderAll() {
    this._renderNodes();
    this._renderEdges();
    this._renderEmptyState();
    this._updateZoomLabel();
  }

  _renderNodes() {
    const inner = this.el.querySelector(".pcv-canvas-inner");
    if (!inner) return;

    // Remove stale node elements
    const currentIds = new Set(this._state.nodes.map((n) => n.id));
    for (const [id, el] of this._nodeEls) {
      if (!currentIds.has(id)) { el.remove(); this._nodeEls.delete(id); }
    }

    // Add / update node cards
    for (const node of this._state.nodes) {
      const existing = this._nodeEls.get(node.id);
      if (existing) {
        updateNodeCardPosition(existing, node.position.x, node.position.y);
        setNodeCardSelected(existing, this._state.selectedNodeId === node.id);
        this._drawer.syncNode(node);
      } else {
        const card = buildNodeCard(node, {
          selected: this._state.selectedNodeId === node.id,
          hasError: false,
        });
        inner.appendChild(card);
        this._nodeEls.set(node.id, card);
        this._wireNodeCard(card, node.id);
      }
    }

    this._renderEmptyState();
  }

  _redrawNode(nodeId) {
    const node = this._state.nodes.find((n) => n.id === nodeId);
    const existing = this._nodeEls.get(nodeId);
    if (!node || !existing) {
      this._renderNodes();
      return;
    }

    const card = buildNodeCard(node, {
      selected: this._state.selectedNodeId === node.id,
      hasError: existing.classList.contains("pcv-node--error"),
    });
    existing.replaceWith(card);
    this._nodeEls.set(nodeId, card);
    this._wireNodeCard(card, nodeId);
    this._drawer.syncNode(node);
    this._renderEmptyState();
  }

  _renderEdges() {
    const g = this.el?.querySelector(".pcv-edges-g");
    if (!g) return;
    g.innerHTML = "";

    // Rebuild edge index for O(connected) drag updates
    this._edgeIndex = new Map();
    for (const edge of this._state.edges) {
      for (const nid of [edge.source_node_id, edge.target_node_id]) {
        if (!this._edgeIndex.has(nid)) this._edgeIndex.set(nid, new Set());
        this._edgeIndex.get(nid).add(edge.id);
      }
      this._renderEdge(g, edge);
    }
  }

  _renderEdge(g, edge, opts = {}) {
    const srcEl = this._nodeEls.get(edge.source_node_id);
    const tgtEl = this._nodeEls.get(edge.target_node_id);
    if (!srcEl || !tgtEl) return;

    const src = getPortCenter(srcEl, "out");
    const tgt = getPortCenter(tgtEl, "in");
    const d = edgePath(src.x, src.y, tgt.x, tgt.y);

    const selected = this._state.selectedEdgeId === edge.id;

    // Invisible fat hit area
    const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
    hit.setAttribute("d", d);
    hit.setAttribute("class", "pcv-edge-hit");
    hit.dataset.edgeId = edge.id;
    g.appendChild(hit);

    // Visible path
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("class", `pcv-edge-path${selected ? " pcv-edge-path--selected" : ""}`);
    path.setAttribute("marker-end", selected ? "url(#pcv-arrow-accent)" : "url(#pcv-arrow)");
    path.dataset.edgeId = edge.id;
    g.appendChild(path);

    // Mid-point delete button (appears on selection)
    if (selected) {
      const mx = (srcEl ? parseFloat(srcEl.style.left) + NODE_W : 0);
      const mx2 = parseFloat(tgtEl.style.left);
      const my = (src.y + tgt.y) / 2;
      const midX = (src.x + tgt.x) / 2;
      const midY = (src.y + tgt.y) / 2;

      const fo = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
      fo.setAttribute("x", midX - 12);
      fo.setAttribute("y", midY - 12);
      fo.setAttribute("width", "24");
      fo.setAttribute("height", "24");
      fo.innerHTML = `<button class="pcv-edge-delete" data-edge-id="${edge.id}" title="Delete edge">×</button>`;
      g.appendChild(fo);
    }

    // Click handler on hit area
    hit.addEventListener("click", (e) => {
      e.stopPropagation();
      this._selectEdge(edge.id);
    });
    path.addEventListener("click", (e) => {
      e.stopPropagation();
      this._selectEdge(edge.id);
    });
  }

  _renderEmptyState() {
    const es = this.el?.querySelector(".pcv-empty-state");
    if (es) es.style.display = this._state.nodes.length === 0 ? "" : "none";
    // Also ensure palette is open when empty
    if (this._state.nodes.length === 0 && this._palette) {
      this._palette.setOpen(true);
    }
  }

  _updateZoomLabel() {
    const lbl = this.el?.querySelector(".pcv-zoom-label");
    if (lbl) lbl.textContent = `${Math.round(this._state.zoom * 100)}%`;
    this._applyTransform();
  }

  /** Apply combined pan + zoom transform to pcv-canvas-inner. */
  _applyTransform() {
    const inner = this.el?.querySelector(".pcv-canvas-inner");
    if (inner) {
      inner.style.transform =
        `translate(${this._panX}px, ${this._panY}px) scale(${this._state.zoom})`;
    }
  }

  _updateNodeSelections() {
    for (const [id, el] of this._nodeEls) {
      setNodeCardSelected(el, this._state.selectedNodeId === id);
    }
  }

  _markDirty() {
    this._state.dirty = true;
    const dot = this.el?.querySelector(".pcv-dirty-dot");
    if (dot) dot.style.display = "";
  }

  _clearDirty() {
    this._state.dirty = false;
    const dot = this.el?.querySelector(".pcv-dirty-dot");
    if (dot) dot.style.display = "none";
  }

  // ── Toolbar wiring ────────────────────────────────────────────────────────

  _wireToolbar() {
    const tb = this.el.querySelector(".pcv-toolbar");

    tb.querySelector(".pcv-btn-save")?.addEventListener("click", () => this._save());
    tb.querySelector(".pcv-btn-run")?.addEventListener("click",  () => this._run());

    tb.querySelector(".pcv-btn-zoom-in")?.addEventListener("click", () => {
      this._state.zoom = Math.min(2.0, this._state.zoom + 0.1);
      this._updateZoomLabel();
    });
    tb.querySelector(".pcv-btn-zoom-out")?.addEventListener("click", () => {
      this._state.zoom = Math.max(0.25, this._state.zoom - 0.1);
      this._updateZoomLabel();
    });

    tb.querySelector(".pcv-btn-fit")?.addEventListener("click", () => this._fitToView());
    tb.querySelector(".pcv-btn-layout")?.addEventListener("click", () => this._autoLayout());
    tb.querySelector(".pcv-btn-json")?.addEventListener("click", () => this._toggleJson());
    tb.querySelector(".pcv-btn-ai")?.addEventListener("click", () => this._aiPanel?.toggle());
  }

  // ── Canvas events ─────────────────────────────────────────────────────────

  _wireCanvasEvents() {
    const canvas = this.el.querySelector(".pcv-canvas");
    const canvasWrap = this.el.querySelector(".pcv-canvas-wrap");

    const onPaletteDragOver = (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const onPaletteDrop = (e) => {
      e.preventDefault();
      e.stopPropagation();
      this._handlePaletteDrop(e);
    };

    // Click on canvas background → deselect
    canvas.addEventListener("click", (e) => {
      if (e.target === canvas || e.target.classList.contains("pcv-canvas-inner") ||
          e.target.classList.contains("pcv-edges-svg")) {
        this._deselectAll();
      }
    });

    // Right-click context menu on canvas
    canvas.addEventListener("contextmenu", (e) => {
      if (e.target === canvas || e.target.classList.contains("pcv-canvas-inner")) {
        e.preventDefault();
        this._showCanvasContextMenu(e);
      }
    });

    // Drag-drop from palette
    canvas.addEventListener("dragover", onPaletteDragOver);
    canvas.addEventListener("drop", onPaletteDrop);
    canvasWrap?.addEventListener("dragover", onPaletteDragOver);
    canvasWrap?.addEventListener("drop", onPaletteDrop);

    // Edge delete button (delegated)
    this.el.querySelector(".pcv-edges-svg")?.addEventListener("click", (e) => {
      const btn = e.target.closest?.(".pcv-edge-delete");
      if (btn) {
        e.stopPropagation();
        this._deleteEdge(btn.dataset.edgeId);
      }
    });

    // Wheel: ctrlKey → zoom; no ctrlKey → pan (trackpad two-finger scroll)
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (e.ctrlKey) {
        // Zoom
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        this._state.zoom = Math.min(2.0, Math.max(0.25, this._state.zoom + delta));
        this._updateZoomLabel();
      } else {
        // Pan (trackpad two-finger or horizontal scroll)
        this._panX -= e.deltaX;
        this._panY -= e.deltaY;
        this._applyTransform();
      }
    }, { passive: false });

    // Middle-mouse drag → pan (mousedown/up wired to document for capture)
    canvas.addEventListener("mousedown", (e) => {
      if (e.button === 1) {
        e.preventDefault();
        // Middle-mouse pan only if no node drag is active
        if (!this._draggingNodeId) {
          this._startPan(e);
        }
      }
    });

    // Space+left-drag → pan; wired via _wireKeyboard + mousedown on canvas
    canvas.addEventListener("mousedown", (e) => {
      if (e.button === 0 && this._spaceHeld && !this._draggingNodeId) {
        e.preventDefault();
        e.stopPropagation();
        this._startPan(e);
      }
    });

    // Mouse events for node drag + edge creation
    this._onMouseMove = this._handleMouseMove.bind(this);
    this._onMouseUp   = this._handleMouseUp.bind(this);
    document.addEventListener("mousemove", this._onMouseMove);
    document.addEventListener("mouseup",   this._onMouseUp);
  }

  _startPan(e) {
    this._isPanning = true;
    this._panStart  = { x: e.clientX, y: e.clientY };
    this._panOrigin = { x: this._panX,  y: this._panY  };
    this._updatePanCursor();
  }

  _updatePanCursor() {
    const canvas = this.el?.querySelector(".pcv-canvas");
    if (!canvas) return;
    if (this._isPanning) {
      canvas.classList.add("panning");
      canvas.classList.remove("pan-ready");
    } else if (this._spaceHeld) {
      canvas.classList.remove("panning");
      canvas.classList.add("pan-ready");
    } else {
      canvas.classList.remove("panning", "pan-ready");
    }
  }

  _wireNodeCard(card, nodeId) {
    // Click → select / open drawer
    card.addEventListener("click", (e) => {
      e.stopPropagation();
      this._selectNode(nodeId);
    });

    // Right-click context menu
    card.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this._showNodeContextMenu(nodeId, e);
    });

    // Mousedown on card body → start node drag
    card.addEventListener("mousedown", (e) => {
      // Ignore if clicking a port
      if (e.target.classList.contains("pcv-port")) return;
      if (e.button !== 0) return;
      e.stopPropagation();

      const node = this._state.nodes.find((n) => n.id === nodeId);
      if (!node) return;

      const inner = this.el.querySelector(".pcv-canvas-inner");
      const rect = inner.getBoundingClientRect();
      const scale = this._state.zoom;

      this._draggingNodeId = nodeId;
      this._dragOffset = {
        x: (e.clientX - rect.left) / scale - node.position.x,
        y: (e.clientY - rect.top)  / scale - node.position.y,
      };
      card.classList.add("pcv-node--dragging");
      this._pushUndoForDrag = false; // push undo on first real move
    });

    // Mousedown on out-port → start edge drag
    const outPort = card.querySelector(".pcv-port--out");
    outPort?.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      this._startEdgeDraft(nodeId, e);
    });

    // Mouseup on in-port → complete edge drag
    const inPort = card.querySelector(".pcv-port--in");
    inPort?.addEventListener("mouseup", (e) => {
      if (this._edgeDraft) {
        e.stopPropagation();
        this._finishEdgeDraft(nodeId);
      }
    });
  }

  // ── Mouse handlers ────────────────────────────────────────────────────────

  _handleMouseMove(e) {
    // Pan
    if (this._isPanning) {
      this._panX = this._panOrigin.x + (e.clientX - this._panStart.x);
      this._panY = this._panOrigin.y + (e.clientY - this._panStart.y);
      this._applyTransform();
      return;
    }

    // Node drag — throttled via requestAnimationFrame
    if (this._draggingNodeId) {
      const inner = this.el?.querySelector(".pcv-canvas-inner");
      if (!inner) return;
      const rect = inner.getBoundingClientRect();
      const scale = this._state.zoom;
      // Compute position in local canvas space (undo scale, undo pan-offset that
      // getBoundingClientRect already includes after applyTransform)
      const x = (e.clientX - rect.left) / scale - this._dragOffset.x;
      const y = (e.clientY - rect.top)  / scale - this._dragOffset.y;

      // Buffer the drag update; RAF will flush it
      this._dragPendingNode = { id: this._draggingNodeId, x: Math.max(0, x), y: Math.max(0, y) };
      if (!this._rafPending) {
        this._rafPending = true;
        requestAnimationFrame(() => this._flushDrag());
      }
    }

    // Edge draft
    if (this._edgeDraft) {
      const inner = this.el?.querySelector(".pcv-canvas-inner");
      if (!inner) return;
      const rect = inner.getBoundingClientRect();
      const scale = this._state.zoom;
      const x = (e.clientX - rect.left) / scale;
      const y = (e.clientY - rect.top)  / scale;
      const draftLine = this.el?.querySelector(".pcv-edge-draft");
      if (draftLine) {
        const src = this._edgeDraft.srcPos;
        draftLine.setAttribute("x1", src.x);
        draftLine.setAttribute("y1", src.y);
        draftLine.setAttribute("x2", x);
        draftLine.setAttribute("y2", y);
        draftLine.style.display = "";
      }
    }
  }

  /** Flush a buffered node drag update — called inside requestAnimationFrame. */
  _flushDrag() {
    this._rafPending = false;
    const pending = this._dragPendingNode;
    if (!pending) return;
    this._dragPendingNode = null;

    const node = this._state.nodes.find((n) => n.id === pending.id);
    if (!node) return;

    if (!this._pushUndoForDrag) {
      pushUndo(this._state);
      this._pushUndoForDrag = true;
    }
    node.position.x = pending.x;
    node.position.y = pending.y;
    const el = this._nodeEls.get(pending.id);
    if (el) updateNodeCardPosition(el, node.position.x, node.position.y);

    // Only update edges connected to the dragged node (O(connected) not O(all))
    this._updateConnectedEdges(pending.id);
    this._markDirty();
  }

  /** Recompute only the SVG paths for edges connected to a given node. */
  _updateConnectedEdges(nodeId) {
    const g = this.el?.querySelector(".pcv-edges-g");
    if (!g) return;
    const connectedEdgeIds = this._edgeIndex.get(nodeId);
    if (!connectedEdgeIds || connectedEdgeIds.size === 0) return;

    for (const edgeId of connectedEdgeIds) {
      const edge = this._state.edges.find((e) => e.id === edgeId);
      if (!edge) continue;
      const srcEl = this._nodeEls.get(edge.source_node_id);
      const tgtEl = this._nodeEls.get(edge.target_node_id);
      if (!srcEl || !tgtEl) continue;

      const src = getPortCenter(srcEl, "out");
      const tgt = getPortCenter(tgtEl, "in");
      const d   = edgePath(src.x, src.y, tgt.x, tgt.y);

      // Update both the hit path and the visible path in-place
      g.querySelectorAll(`[data-edge-id="${edgeId}"]`).forEach((el) => {
        if (el.tagName === "path") el.setAttribute("d", d);
      });
    }
  }

  _handleMouseUp(e) {
    if (this._isPanning) {
      this._isPanning = false;
      this._updatePanCursor();
      return;
    }
    if (this._draggingNodeId) {
      // Flush any buffered RAF drag so the final position is committed
      if (this._dragPendingNode) this._flushDrag();
      const el = this._nodeEls.get(this._draggingNodeId);
      if (el) el.classList.remove("pcv-node--dragging");
      this._draggingNodeId = null;
    }
    if (this._edgeDraft) {
      // If not finished on a port, cancel
      this._cancelEdgeDraft();
    }
  }

  // ── Edge drag ─────────────────────────────────────────────────────────────

  _startEdgeDraft(sourceNodeId, e) {
    const srcEl = this._nodeEls.get(sourceNodeId);
    if (!srcEl) return;
    const srcPos = getPortCenter(srcEl, "out");
    this._edgeDraft = { sourceNodeId, srcPos };
  }

  _finishEdgeDraft(targetNodeId) {
    if (!this._edgeDraft) return;
    const { sourceNodeId } = this._edgeDraft;
    this._cancelEdgeDraft();

    if (sourceNodeId === targetNodeId) return;
    // Check duplicate
    const dup = this._state.edges.find(
      (e) => e.source_node_id === sourceNodeId && e.target_node_id === targetNodeId
    );
    if (dup) return;

    pushUndo(this._state);
    this._state.edges.push({
      id: genId("edge"),
      source_node_id: sourceNodeId,
      target_node_id: targetNodeId,
      condition: null,
      data_shape: null,
    });
    this._markDirty();
    this._renderEdges();
  }

  _cancelEdgeDraft() {
    this._edgeDraft = null;
    const draftLine = this.el?.querySelector(".pcv-edge-draft");
    if (draftLine) draftLine.style.display = "none";
  }

  // ── Selection ─────────────────────────────────────────────────────────────

  _selectNode(nodeId) {
    this._state.selectedNodeId = nodeId;
    this._state.selectedEdgeId = null;
    this._updateNodeSelections();
    this._renderEdges();
    const node = this._state.nodes.find((n) => n.id === nodeId);
    if (node && this._drawer) this._drawer.open(node);
  }

  _selectEdge(edgeId) {
    this._state.selectedEdgeId = edgeId;
    this._state.selectedNodeId = null;
    this._updateNodeSelections();
    this._renderEdges();
  }

  _deselectAll() {
    this._state.selectedNodeId = null;
    this._state.selectedEdgeId = null;
    this._updateNodeSelections();
    this._renderEdges();
  }

  // ── Node / edge deletion ──────────────────────────────────────────────────

  _deleteNode(nodeId) {
    pushUndo(this._state);
    this._state.nodes = this._state.nodes.filter((n) => n.id !== nodeId);
    this._state.edges = this._state.edges.filter(
      (e) => e.source_node_id !== nodeId && e.target_node_id !== nodeId
    );
    if (this._state.selectedNodeId === nodeId) this._state.selectedNodeId = null;
    this._markDirty();
    this._renderAll();
  }

  _deleteEdge(edgeId) {
    pushUndo(this._state);
    this._state.edges = this._state.edges.filter((e) => e.id !== edgeId);
    if (this._state.selectedEdgeId === edgeId) this._state.selectedEdgeId = null;
    this._markDirty();
    this._renderEdges();
  }

  _deleteSelected() {
    if (this._state.selectedNodeId) this._deleteNode(this._state.selectedNodeId);
    else if (this._state.selectedEdgeId) this._deleteEdge(this._state.selectedEdgeId);
  }

  // ── Palette drop ──────────────────────────────────────────────────────────

  _handlePaletteDrop(e) {
    let data;
    try {
      const raw =
        e.dataTransfer.getData("application/x-artemis-pipeline-node") ||
        e.dataTransfer.getData("text/plain");
      data = JSON.parse(raw);
    } catch {
      data = this._paletteDragData;
    }
    if (!data?.type) return;

    const inner = this.el?.querySelector(".pcv-canvas-inner");
    if (!inner) return;
    const rect = inner.getBoundingClientRect();
    const scale = this._state.zoom;
    const x = (e.clientX - rect.left) / scale;
    const y = (e.clientY - rect.top)  / scale;

    pushUndo(this._state);
    const newNode = {
      id: genId("node"),
      type: data.type,
      label: data.label || defaultLabel(data.type),
      config: { ...defaultConfig(data.type), ...(data.config || {}) },
      position: { x: Math.max(0, x - NODE_W / 2), y: Math.max(0, y - NODE_H / 2) },
    };
    this._state.nodes.push(newNode);
    this._markDirty();
    this._renderAll();
    this._selectNode(newNode.id);
  }

  // ── Context menus ─────────────────────────────────────────────────────────

  _showCanvasContextMenu(e) {
    this._removeContextMenu();
    const menu = document.createElement("div");
    menu.className = "pcv-ctx-menu";
    menu.style.cssText = `left:${e.clientX}px;top:${e.clientY}px`;

    const NODE_TYPES = [
      { type: "trigger_manual",    label: "Manual Trigger" },
      { type: "trigger_scheduled", label: "Scheduled Trigger" },
      { type: "agent_invocation",  label: "Agent" },
      { type: "skill_call",        label: "Skill" },
      { type: "human_gate",        label: "Human Gate" },
      { type: "conditional",       label: "Conditional" },
      { type: "sub_pipeline",      label: "Sub-Pipeline" },
    ];

    menu.innerHTML = `
      <div class="pcv-ctx-menu-header">Add node…</div>
      ${NODE_TYPES.map((t) =>
        `<button class="pcv-ctx-item" data-type="${t.type}">${_esc(t.label)}</button>`
      ).join("")}
    `;

    document.body.appendChild(menu);

    const inner = this.el?.querySelector(".pcv-canvas-inner");
    const rect = inner?.getBoundingClientRect() || { left: 0, top: 0 };
    const scale = this._state.zoom;
    const canvasX = (e.clientX - rect.left) / scale;
    const canvasY = (e.clientY - rect.top)  / scale;

    menu.querySelectorAll(".pcv-ctx-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        pushUndo(this._state);
        const type = btn.dataset.type;
        const newNode = {
          id: genId("node"),
          type,
          label: defaultLabel(type),
          config: defaultConfig(type),
          position: { x: Math.max(0, canvasX - NODE_W / 2), y: Math.max(0, canvasY - NODE_H / 2) },
        };
        this._state.nodes.push(newNode);
        this._markDirty();
        this._renderAll();
        this._selectNode(newNode.id);
        this._removeContextMenu();
      });
    });

    setTimeout(() => document.addEventListener("click", this._ctxDismiss = () => this._removeContextMenu(), { once: true }), 0);
  }

  _showNodeContextMenu(nodeId, e) {
    this._removeContextMenu();
    const menu = document.createElement("div");
    menu.className = "pcv-ctx-menu";
    menu.style.cssText = `left:${e.clientX}px;top:${e.clientY}px`;
    menu.innerHTML = `
      <button class="pcv-ctx-item" data-action="edit">Edit config</button>
      <button class="pcv-ctx-item" data-action="duplicate">Duplicate</button>
      <button class="pcv-ctx-item pcv-ctx-item--danger" data-action="delete">Delete</button>
    `;
    document.body.appendChild(menu);

    menu.querySelector("[data-action='edit']")?.addEventListener("click", () => {
      this._selectNode(nodeId);
      this._removeContextMenu();
    });
    menu.querySelector("[data-action='duplicate']")?.addEventListener("click", () => {
      const node = this._state.nodes.find((n) => n.id === nodeId);
      if (node) {
        pushUndo(this._state);
        this._state.nodes.push({
          ...node,
          id: genId("node"),
          position: { x: node.position.x + 220, y: node.position.y + 40 },
        });
        this._markDirty();
        this._renderAll();
      }
      this._removeContextMenu();
    });
    menu.querySelector("[data-action='delete']")?.addEventListener("click", () => {
      this._deleteNode(nodeId);
      this._removeContextMenu();
    });

    setTimeout(() => document.addEventListener("click", this._ctxDismiss = () => this._removeContextMenu(), { once: true }), 0);
  }

  _removeContextMenu() {
    document.querySelectorAll(".pcv-ctx-menu").forEach((m) => m.remove());
  }

  // ── Keyboard ──────────────────────────────────────────────────────────────

  _wireKeyboard() {
    this._onKeyDown = (e) => {
      const onCanvas = this.el?.contains(document.activeElement) || document.activeElement === document.body;
      if (!onCanvas) return;

      // Space → pan-ready cursor (actual pan starts on mousedown)
      if (e.key === " " && !e.target.matches("input,textarea,[contenteditable]")) {
        e.preventDefault();
        this._spaceHeld = true;
        this._updatePanCursor();
        return;
      }

      const isMeta = e.metaKey || e.ctrlKey;
      if (isMeta && e.key === "s") { e.preventDefault(); this._save(); return; }
      if (isMeta && e.key === "z") { e.preventDefault(); this._undo(); return; }
      if (isMeta && (e.key === "y" || (e.shiftKey && e.key === "z"))) {
        e.preventDefault(); this._redo(); return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && !e.target.matches("input,textarea,[contenteditable]")) {
        this._deleteSelected();
      }
    };
    this._onKeyUp = (e) => {
      if (e.key === " ") {
        this._spaceHeld = false;
        // Cancel space-triggered pan if still panning (but not mid-drag)
        if (this._isPanning) {
          this._isPanning = false;
        }
        this._updatePanCursor();
      }
    };
    document.addEventListener("keydown", this._onKeyDown);
    document.addEventListener("keyup",   this._onKeyUp);
  }

  // ── Undo / redo ───────────────────────────────────────────────────────────

  _undo() {
    if (!this._state.undoStack.length) return;
    this._state.redoStack.push(snapshot(this._state));
    const prev = this._state.undoStack.pop();
    this._state.nodes = prev.nodes;
    this._state.edges = prev.edges;
    this._state.selectedNodeId = null;
    this._state.selectedEdgeId = null;
    this._markDirty();
    this._renderAll();
  }

  _redo() {
    if (!this._state.redoStack.length) return;
    this._state.undoStack.push(snapshot(this._state));
    const next = this._state.redoStack.pop();
    this._state.nodes = next.nodes;
    this._state.edges = next.edges;
    this._markDirty();
    this._renderAll();
  }

  // ── Save ──────────────────────────────────────────────────────────────────

  async _save() {
    try {
      await api.updatePipelineApi(this._state.id, {
        nodes: this._state.nodes,
        edges: this._state.edges,
        triggerConfig: this._state.triggerConfig,
      });
      this._clearDirty();
      if (this._onSaved) this._onSaved();
      this._showToast("Saved");
    } catch (err) {
      this._showToast(`Save failed: ${err.message}`, true);
    }
  }

  async _run() {
    try {
      const run = await api.runPipelineApi(this._state.id);
      const shortRunId = String(run?.id || "").slice(0, 8) || "new";
      this._showToast(`Run #${shortRunId} started. Watch progress on canvas.`);
    } catch (err) {
      this._showToast(`Run failed: ${err.message}`, true);
    }
  }

  _showToast(msg, isError = false) {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `bg-toast${isError ? " toast-error" : ""}`;
    toast.innerHTML = `
      <span class="bg-toast-dot"></span>
      <div class="bg-toast-body"><div class="bg-toast-label"></div></div>
      <button class="bg-toast-close">&times;</button>`;
    toast.querySelector(".bg-toast-label").textContent = msg;
    const dismiss = () => {
      toast.classList.add("toast-exit");
      toast.addEventListener("animationend", () => toast.remove(), { once: true });
    };
    toast.querySelector(".bg-toast-close")?.addEventListener("click", dismiss);
    container.appendChild(toast);
    setTimeout(() => { if (toast.parentNode) dismiss(); }, 3000);
  }

  // ── JSON toggle ───────────────────────────────────────────────────────────

  _toggleJson() {
    this._showJson = !this._showJson;
    const canvasWrap = this.el.querySelector(".pcv-canvas-wrap");
    const jsonPanel  = this.el.querySelector(".pcv-json-panel");
    const btn        = this.el.querySelector(".pcv-btn-json");

    if (this._showJson) {
      const jsonVal = JSON.stringify({ nodes: this._state.nodes, edges: this._state.edges }, null, 2);
      jsonPanel.querySelector(".pcv-json-textarea").value = jsonVal;
      jsonPanel.querySelector(".pcv-json-err").style.display = "none";
      canvasWrap.style.display = "none";
      jsonPanel.style.display = "";
      btn.textContent = "View Canvas";
      this._wireJsonPanel();
    } else {
      canvasWrap.style.display = "";
      jsonPanel.style.display = "none";
      btn.textContent = "View JSON";
    }
  }

  _wireJsonPanel() {
    const panel = this.el.querySelector(".pcv-json-panel");
    panel.querySelector(".pcv-json-apply")?.addEventListener("click", () => {
      const txt = panel.querySelector(".pcv-json-textarea").value;
      const errEl = panel.querySelector(".pcv-json-err");
      let parsed;
      try { parsed = JSON.parse(txt); }
      catch (err) {
        errEl.textContent = `JSON error: ${err.message}`;
        errEl.style.display = "";
        return;
      }
      errEl.style.display = "none";
      pushUndo(this._state);
      this._state.nodes = (parsed.nodes || []).map((n) => ({
        ...n,
        position: n.position || { x: 0, y: 0 },
      }));
      this._state.edges = parsed.edges || [];
      this._markDirty();
      this._showJson = false;
      this._toggleJson(); // switches display
      this._renderAll();
    });
  }

  // ── Zoom + fit ────────────────────────────────────────────────────────────

  _fitToView() {
    if (!this._state.nodes.length) return;
    const canvas = this.el.querySelector(".pcv-canvas");
    if (!canvas) return;

    const xs = this._state.nodes.map((n) => n.position.x);
    const ys = this._state.nodes.map((n) => n.position.y);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    const maxX = Math.max(...xs) + NODE_W;
    const maxY = Math.max(...ys) + NODE_H;

    const W = canvas.clientWidth  || 800;
    const H = canvas.clientHeight || 600;
    const PADDING = 60;

    const scaleX = (W - PADDING * 2) / (maxX - minX || 1);
    const scaleY = (H - PADDING * 2) / (maxY - minY || 1);
    this._state.zoom = Math.min(2.0, Math.max(0.25, Math.min(scaleX, scaleY)));

    this._updateZoomLabel();
    this._renderEdges();
  }

  // ── Auto-layout ───────────────────────────────────────────────────────────

  _autoLayout() {
    pushUndo(this._state);
    this._state.nodes = autoLayout(this._state.nodes, this._state.edges);
    this._markDirty();
    this._renderAll();
    this._fitToView();
  }

  // ── Accessors (for tests) ─────────────────────────────────────────────────

  getState() {
    return {
      nodes: this._state.nodes,
      edges: this._state.edges,
    };
  }

  getNodeById(id) {
    return this._state.nodes.find((n) => n.id === id);
  }

  /** Returns view-only pan offset {x, y}. Not stored in pipeline data. */
  getPan() {
    return { x: this._panX, y: this._panY };
  }

  /** Returns current zoom level. */
  getZoom() {
    return this._state.zoom;
  }
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
