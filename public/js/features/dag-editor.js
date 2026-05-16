// DAG Editor — SVG-based visual editor for agent dependency graphs
import { $ } from '../core/dom.js';
import { getState } from '../core/store.js';
import { escapeHtml } from '../core/utils.js';
import * as api from '../core/api.js';

const NS = "http://www.w3.org/2000/svg";
const NODE_W = 140;
const NODE_H = 50;

let dagNodes = [];
let dagEdges = [];
let dragging = null;
let connecting = null; // { fromId, startX, startY }
let tempLine = null;

// ── Public API ──

export function openDagModal(dag) {
  dagNodes = [];
  dagEdges = [];

  if (dag) {
    $.dagModalTitle.textContent = "Edit DAG";
    $.dagFormTitle.value = dag.title;
    $.dagFormDesc.value = dag.description || "";
    $.dagFormEditId.value = dag.id;
    dagNodes = dag.nodes.map(n => ({ ...n }));
    dagEdges = dag.edges.map(e => ({ ...e }));
  } else {
    $.dagModalTitle.textContent = "New DAG";
    $.dagFormTitle.value = "";
    $.dagFormDesc.value = "";
    $.dagFormEditId.value = "";
  }

  renderPalette();
  renderCanvas();
  $.dagModal.classList.remove("hidden");
  $.dagFormTitle.focus();
}

export function closeDagModal() {
  $.dagModal.classList.add("hidden");
  dagNodes = [];
  dagEdges = [];
}

// ── Palette ──

function renderPalette() {
  const agents = getState("agents") || [];
  $.dagNodePalette.innerHTML = `<div class="dag-palette-title">Drag to add</div>`;
  for (const agent of agents) {
    const item = document.createElement("div");
    item.className = "dag-palette-item";
    item.draggable = true;
    item.dataset.agentId = agent.id;
    item.textContent = agent.title;
    item.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", agent.id);
      e.dataTransfer.effectAllowed = "copy";
    });
    $.dagNodePalette.appendChild(item);
  }
}

// ── Canvas Rendering ──

function renderCanvas() {
  $.dagCanvas.innerHTML = "";

  // Defs for arrow marker
  const defs = svgEl("defs");
  const marker = svgEl("marker", {
    id: "dag-arrow", viewBox: "0 0 10 10", refX: "10", refY: "5",
    markerWidth: "8", markerHeight: "8", orient: "auto-start-reverse",
  });
  marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "var(--text-dim)" }));
  defs.appendChild(marker);
  $.dagCanvas.appendChild(defs);

  // Edges
  for (const edge of dagEdges) {
    const fromNode = dagNodes.find(n => n.id === edge.from);
    const toNode = dagNodes.find(n => n.id === edge.to);
    if (!fromNode || !toNode) continue;

    const coords = {
      x1: fromNode.x + NODE_W, y1: fromNode.y + NODE_H / 2,
      x2: toNode.x, y2: toNode.y + NODE_H / 2,
    };
    // Wider invisible hit target for easier clicking
    const hitArea = svgEl("line", {
      ...coords,
      stroke: "transparent",
      "stroke-width": "12",
      cursor: "pointer",
    });
    const line = svgEl("line", {
      ...coords,
      class: "dag-edge",
      "marker-end": "url(#dag-arrow)",
      "pointer-events": "none",
    });
    // Click or right-click to delete edge
    const removeEdge = (e) => {
      e.preventDefault();
      dagEdges = dagEdges.filter(de => !(de.from === edge.from && de.to === edge.to));
      renderCanvas();
    };
    hitArea.addEventListener("contextmenu", removeEdge);
    hitArea.addEventListener("click", removeEdge);
    hitArea.addEventListener("mouseenter", () => line.classList.add("dag-edge-hover"));
    hitArea.addEventListener("mouseleave", () => line.classList.remove("dag-edge-hover"));
    hitArea.setAttribute("title", "Click to remove connection");
    $.dagCanvas.appendChild(line);
    $.dagCanvas.appendChild(hitArea);
  }

  // Nodes
  for (const node of dagNodes) {
    const agents = getState("agents") || [];
    const agent = agents.find(a => a.id === node.agentId);
    const title = agent ? agent.title : node.agentId;

    const g = svgEl("g", { class: "dag-node", transform: `translate(${node.x}, ${node.y})` });

    const rect = svgEl("rect", {
      width: NODE_W, height: NODE_H, rx: "6",
      class: "dag-node-rect",
    });
    g.appendChild(rect);

    const text = svgEl("text", {
      x: NODE_W / 2, y: NODE_H / 2 + 1,
      class: "dag-node-text",
      "text-anchor": "middle", "dominant-baseline": "middle",
    });
    text.textContent = title.length > 16 ? title.slice(0, 14) + "…" : title;
    g.appendChild(text);

    // Output port (right side)
    const outPort = svgEl("circle", {
      cx: NODE_W, cy: NODE_H / 2, r: "6",
      class: "dag-port dag-port-out",
    });
    g.appendChild(outPort);

    // Input port (left side)
    const inPort = svgEl("circle", {
      cx: 0, cy: NODE_H / 2, r: "6",
      class: "dag-port dag-port-in",
    });
    g.appendChild(inPort);

    // Delete button
    const del = svgEl("text", {
      x: NODE_W - 8, y: 12,
      class: "dag-node-delete",
      "text-anchor": "middle",
    });
    del.textContent = "×";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      dagNodes = dagNodes.filter(n => n.id !== node.id);
      dagEdges = dagEdges.filter(de => de.from !== node.id && de.to !== node.id);
      renderCanvas();
    });
    g.appendChild(del);

    // Drag node
    let dragOffset = null;
    rect.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const pt = svgPoint(e);
      dragOffset = { dx: pt.x - node.x, dy: pt.y - node.y };
      dragging = node.id;
    });

    // Start connection from output port
    outPort.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      connecting = { fromId: node.id };
      tempLine = svgEl("line", {
        x1: node.x + NODE_W, y1: node.y + NODE_H / 2,
        x2: node.x + NODE_W, y2: node.y + NODE_H / 2,
        class: "dag-edge dag-edge-temp",
      });
      $.dagCanvas.appendChild(tempLine);
    });

    // Receive connection on input port
    inPort.addEventListener("mouseup", (e) => {
      if (connecting && connecting.fromId !== node.id) {
        const exists = dagEdges.some(de => de.from === connecting.fromId && de.to === node.id);
        if (!exists) {
          dagEdges.push({ from: connecting.fromId, to: node.id });
        }
      }
    });

    $.dagCanvas.appendChild(g);
  }

  // Global mouse handlers for drag and connect
  $.dagCanvas.onmousemove = (e) => {
    const pt = svgPoint(e);
    if (dragging) {
      const node = dagNodes.find(n => n.id === dragging);
      if (node) {
        node.x = Math.max(0, pt.x - (dragOffset?.dx || NODE_W / 2));
        node.y = Math.max(0, pt.y - (dragOffset?.dy || NODE_H / 2));
        renderCanvas();
      }
    }
    if (connecting && tempLine) {
      tempLine.setAttribute("x2", pt.x);
      tempLine.setAttribute("y2", pt.y);
    }
  };

  $.dagCanvas.onmouseup = () => {
    dragging = null;
    connecting = null;
    if (tempLine) {
      tempLine.remove();
      tempLine = null;
      renderCanvas();
    }
  };

  // Update viewBox to fit content
  if (dagNodes.length > 0) {
    const maxX = Math.max(...dagNodes.map(n => n.x + NODE_W + 30));
    const maxY = Math.max(...dagNodes.map(n => n.y + NODE_H + 30));
    $.dagCanvas.setAttribute("viewBox", `0 0 ${Math.max(maxX, 500)} ${Math.max(maxY, 300)}`);
  } else {
    $.dagCanvas.setAttribute("viewBox", "0 0 500 300");
  }
}

// keep dragOffset accessible in onmousemove
let dragOffset = null;
const origOnMouseMove = null;

// Patch: track dragOffset globally
const origRect = null;
{
  const _origRenderCanvas = renderCanvas;
  // We already handle dragOffset via closure in rect.mousedown above.
  // Just need to make it available to the onmousemove handler.
}

// ── Drop handler ──

$.dagCanvas?.parentElement?.addEventListener("dragover", (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});

$.dagCanvas?.parentElement?.addEventListener("drop", (e) => {
  e.preventDefault();
  const agentId = e.dataTransfer.getData("text/plain");
  if (!agentId) return;

  const rect = $.dagCanvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;

  // Scale to SVG coords
  const svgRect = $.dagCanvas.viewBox.baseVal;
  const scaleX = svgRect.width / rect.width;
  const scaleY = svgRect.height / rect.height;

  const nodeId = `n${Date.now()}`;
  dagNodes.push({
    id: nodeId,
    agentId,
    x: Math.max(0, x * scaleX - NODE_W / 2),
    y: Math.max(0, y * scaleY - NODE_H / 2),
  });
  renderCanvas();
});

// ── Auto Layout ──

function autoLayout() {
  if (dagNodes.length === 0) return;

  // Simple left-to-right layout based on topological order
  const inDegree = new Map();
  const adj = new Map();
  for (const n of dagNodes) {
    inDegree.set(n.id, 0);
    adj.set(n.id, []);
  }
  for (const e of dagEdges) {
    adj.get(e.from)?.push(e.to);
    inDegree.set(e.to, (inDegree.get(e.to) || 0) + 1);
  }

  const levels = [];
  const remaining = new Set(dagNodes.map(n => n.id));
  while (remaining.size > 0) {
    const level = [];
    for (const id of remaining) {
      if (inDegree.get(id) === 0) level.push(id);
    }
    if (level.length === 0) {
      // Remaining nodes are in a cycle — just add them
      level.push(...remaining);
      remaining.clear();
    }
    levels.push(level);
    for (const id of level) {
      remaining.delete(id);
      for (const next of adj.get(id) || []) {
        inDegree.set(next, (inDegree.get(next) || 0) - 1);
      }
    }
  }

  const xGap = 200;
  const yGap = 80;
  for (let li = 0; li < levels.length; li++) {
    const level = levels[li];
    const totalH = level.length * NODE_H + (level.length - 1) * (yGap - NODE_H);
    const startY = Math.max(20, (300 - totalH) / 2);
    for (let ni = 0; ni < level.length; ni++) {
      const node = dagNodes.find(n => n.id === level[ni]);
      if (node) {
        node.x = 40 + li * xGap;
        node.y = startY + ni * yGap;
      }
    }
  }
  renderCanvas();
}

// ── Save ──

async function saveDag() {
  const title = $.dagFormTitle.value.trim();
  if (!title) { alert("Title is required"); return; }
  if (dagNodes.length < 2) { alert("A DAG needs at least 2 nodes"); return; }

  const editId = $.dagFormEditId.value;
  const data = {
    title,
    description: $.dagFormDesc.value.trim(),
    nodes: dagNodes.map(n => ({ id: n.id, agentId: n.agentId, x: Math.round(n.x), y: Math.round(n.y) })),
    edges: dagEdges.map(e => ({ from: e.from, to: e.to })),
  };

  try {
    if (editId) {
      await api.updateDag(editId, data);
    } else {
      await api.createDag(data);
    }
    closeDagModal();
    // Reload agents panel (which includes DAGs)
    const { loadAgents } = await import('./agents.js');
    await loadAgents();
  } catch (err) {
    alert(err.message);
  }
}

// ── Helpers ──

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  return el;
}

function svgPoint(e) {
  const rect = $.dagCanvas.getBoundingClientRect();
  const vb = $.dagCanvas.viewBox.baseVal;
  return {
    x: (e.clientX - rect.left) * (vb.width / rect.width),
    y: (e.clientY - rect.top) * (vb.height / rect.height),
  };
}

// ── Event Bindings ──

$.dagModalClose?.addEventListener("click", closeDagModal);
$.dagModalCancel?.addEventListener("click", closeDagModal);
$.dagModal?.addEventListener("click", (e) => {
  if (e.target === $.dagModal) closeDagModal();
});
$.dagModalSave?.addEventListener("click", saveDag);
$.dagAutoLayout?.addEventListener("click", autoLayout);
