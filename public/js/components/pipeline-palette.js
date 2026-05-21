/**
 * pipeline-palette.js — PIPE2
 * Left-rail palette for dragging node types onto the pipeline canvas.
 * Light DOM only; no Shadow DOM.
 */
import * as api from "../core/api.js";

const PALETTE_TYPES = [
  {
    group: "Triggers",
    items: [
      { type: "trigger_manual",    label: "Manual",    icon: "▶" },
      { type: "trigger_scheduled", label: "Scheduled", icon: "⏱" },
      { type: "trigger_webhook",   label: "Webhook",   icon: "⚡" },
      { type: "trigger_event",     label: "Event",     icon: "📡" },
    ],
  },
  {
    group: "Logic",
    items: [
      { type: "human_gate",  label: "Human Gate",  icon: "🧑" },
      { type: "conditional", label: "Conditional", icon: "◇" },
    ],
  },
];

// Dynamic groups loaded from API
const DYNAMIC_GROUPS = [
  { group: "Agents",        type: "agent_invocation", icon: "🤖", apiKey: "agents",    fetchFn: "fetchAgents",        idKey: "agentId",    nameKey: "displayName" },
  { group: "Skills",        type: "skill_call",        icon: "⚙", apiKey: "skills",    fetchFn: "fetchSkills",        idKey: "id",         nameKey: "name"        },
  { group: "Sub-Pipelines", type: "sub_pipeline",      icon: "⊞", apiKey: "pipelines", fetchFn: "listPipelinesApi",   idKey: "id",         nameKey: "name"        },
];

export class PipelinePalette {
  constructor({ onDragStart }) {
    this._onDragStart = onDragStart;
    this._open = true;
    this._dynamicData = {};
    this._search = {};
    this._expanded = {};
    this.el = null;
  }

  async mount(container) {
    this.el = document.createElement("div");
    this.el.className = "pcv-palette";
    container.appendChild(this.el);
    this._render();
    await this._loadDynamic();
  }

  setOpen(open) {
    this._open = open;
    if (this.el) {
      this.el.classList.toggle("pcv-palette--collapsed", !open);
      const body = this.el.querySelector(".pcv-palette-body");
      if (body) body.style.display = open ? "" : "none";
    }
  }

  toggle() {
    this.setOpen(!this._open);
  }

  _render() {
    if (!this.el) return;
    this.el.innerHTML = `
      <div class="pcv-palette-header">
        <span class="pcv-palette-title">Nodes</span>
        <button class="pcv-palette-toggle" title="${this._open ? "Collapse palette" : "Expand palette"}">
          ${this._open ? "◀" : "▶"}
        </button>
      </div>
      <div class="pcv-palette-body" style="${this._open ? "" : "display:none"}">
        ${this._renderStaticGroups()}
        ${this._renderDynamicGroups()}
      </div>
    `;
    this._wire();
  }

  _renderStaticGroups() {
    return PALETTE_TYPES.map((g) => `
      <div class="pcv-palette-group">
        <div class="pcv-palette-group-label">${_esc(g.group)}</div>
        <div class="pcv-palette-items">
          ${g.items.map((item) => this._renderItem(item)).join("")}
        </div>
      </div>
    `).join("");
  }

  _renderDynamicGroups() {
    return DYNAMIC_GROUPS.map((g) => {
      const key = g.group;
      const isExpanded = this._expanded[key] !== false; // default expanded
      const items = (this._dynamicData[key] || []);
      const q = (this._search[key] || "").toLowerCase();
      const filtered = q ? items.filter((x) => (x._name || "").toLowerCase().includes(q)) : items;
      const loading = !this._dynamicData[key];

      return `
        <div class="pcv-palette-group">
          <button class="pcv-palette-group-label pcv-palette-group-toggle" data-group="${_esc(key)}">
            <span>${_esc(g.group)}</span>
            <span class="pcv-palette-group-arrow">${isExpanded ? "▾" : "▸"}</span>
          </button>
          ${isExpanded ? `
            <div class="pcv-palette-group-body">
              <input class="pcv-palette-search" type="search"
                placeholder="Search ${_esc(g.group.toLowerCase())}…"
                data-group="${_esc(key)}"
                value="${_esc(this._search[key] || "")}" />
              <div class="pcv-palette-items">
                ${loading
                  ? `<div class="pcv-palette-loading">Loading…</div>`
                  : filtered.length === 0
                    ? `<div class="pcv-palette-empty">No results</div>`
                    : filtered.map((item) => this._renderItem({
                        type: g.type,
                        label: item._name,
                        icon: g.icon,
                        config: item._config,
                      })).join("")
                }
              </div>
            </div>
          ` : ""}
        </div>
      `;
    }).join("");
  }

  _renderItem({ type, label, icon, config = {} }) {
    const data = JSON.stringify({ type, label, config });
    return `
      <div class="pcv-palette-item"
        draggable="true"
        data-palette-item="${_esc(JSON.stringify({ type, label, config }))}"
        title="${_esc(label)}">
        <span class="pcv-palette-item-icon">${icon}</span>
        <span class="pcv-palette-item-label">${_esc(label)}</span>
      </div>
    `;
  }

  _wire() {
    if (!this.el) return;

    // Toggle palette open/close
    this.el.querySelector(".pcv-palette-toggle")?.addEventListener("click", () => this.toggle());

    // Group expand toggles
    this.el.querySelectorAll(".pcv-palette-group-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const group = btn.dataset.group;
        this._expanded[group] = this._expanded[group] === false ? true : false;
        this._render();
      });
    });

    // Search inputs
    this.el.querySelectorAll(".pcv-palette-search").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const group = e.target.dataset.group;
        this._search[group] = e.target.value;
        this._render();
      });
    });

    // Drag start
    this.el.querySelectorAll(".pcv-palette-item[draggable]").forEach((item) => {
      item.addEventListener("dragstart", (e) => {
        try {
          const data = JSON.parse(item.dataset.paletteItem);
          e.dataTransfer.setData("text/plain", JSON.stringify(data));
          e.dataTransfer.effectAllowed = "copy";
          if (this._onDragStart) this._onDragStart(data, e);
        } catch {}
      });
    });
  }

  async _loadDynamic() {
    for (const g of DYNAMIC_GROUPS) {
      try {
        let items = [];
        if (g.fetchFn === "fetchAgents") {
          const raw = await api.fetchAgents();
          items = (Array.isArray(raw) ? raw : []).map((a) => ({
            _name: a.displayName || a.name || a.agentId || a.id || "Agent",
            _config: { agent_id: a.agentId || a.id, mode: "scheduled" },
          }));
        } else if (g.fetchFn === "fetchSkills") {
          const raw = await api.fetchSkills();
          items = (Array.isArray(raw) ? raw : []).map((s) => ({
            _name: s.name || s.slug || s.id || "Skill",
            _config: { skill_id: s.id || s.slug },
          }));
        } else if (g.fetchFn === "listPipelinesApi") {
          const raw = await api.listPipelinesApi();
          items = (Array.isArray(raw) ? raw : []).map((p) => ({
            _name: p.name || p.id || "Pipeline",
            _config: { pipeline_id: p.id },
          }));
        }
        this._dynamicData[g.group] = items;
      } catch {
        this._dynamicData[g.group] = [];
      }
      this._render();
    }
  }
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
