/**
 * pipeline-config-drawer.js — PIPE2
 * Right-side config drawer that opens when a node is clicked.
 * PIPE2: generic JSON config view. PIPE3 ships per-type forms.
 * Light DOM only; no Shadow DOM.
 */

const TYPE_LABELS = {
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

export class PipelineConfigDrawer {
  constructor({ onSave, onDelete, onClose }) {
    this._onSave = onSave;
    this._onDelete = onDelete;
    this._onClose = onClose;
    this._node = null;
    this._editJson = "";
    this._editErr = null;
    this.el = null;
  }

  mount(container) {
    this.el = document.createElement("div");
    this.el.className = "pcv-drawer pcv-drawer--hidden";
    container.appendChild(this.el);

    // Close on outside click
    document.addEventListener("mousedown", (e) => {
      if (this._node && this.el && !this.el.contains(e.target)) {
        // Check that the click wasn't on a canvas node
        const onNode = e.target.closest?.(".pcv-node");
        if (!onNode) this.close();
      }
    });

    // Close on Escape
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this._node) this.close();
    });
  }

  open(node) {
    this._node = node;
    this._editJson = JSON.stringify(node.config ?? {}, null, 2);
    this._editErr = null;
    this._render();
    this.el?.classList.remove("pcv-drawer--hidden");
  }

  close() {
    this._node = null;
    this.el?.classList.add("pcv-drawer--hidden");
    if (this._onClose) this._onClose();
  }

  isOpen() {
    return !!this._node;
  }

  /** Call when canvas re-renders nodes to keep the drawer label current */
  syncNode(node) {
    if (this._node && this._node.id === node.id) {
      this._node = node;
      // Only update label — don't overwrite pending JSON edits
      const labelEl = this.el?.querySelector(".pcv-drawer-label");
      if (labelEl) labelEl.textContent = node.label || node.id;
    }
  }

  _render() {
    if (!this.el || !this._node) return;
    const n = this._node;
    const typeLabel = TYPE_LABELS[n.type] || n.type || "Node";

    this.el.innerHTML = `
      <div class="pcv-drawer-header">
        <div class="pcv-drawer-header-body">
          <div class="pcv-drawer-label-row">
            <span class="pcv-drawer-label" contenteditable="true" spellcheck="false"
              title="Click to rename">${_esc(n.label || n.id)}</span>
          </div>
          <div class="pcv-drawer-type">${_esc(typeLabel)}</div>
        </div>
        <div class="pcv-drawer-header-actions">
          <button class="pcv-drawer-delete pbtn pbtn-danger" title="Delete node">Delete</button>
          <button class="pcv-drawer-close" title="Close">✕</button>
        </div>
      </div>

      <div class="pcv-drawer-body">
        <label class="pcv-drawer-section-label">Node ID</label>
        <div class="pcv-drawer-id">${_esc(n.id)}</div>

        <label class="pcv-drawer-section-label">Config <span class="pcv-drawer-hint">(JSON — PIPE3 ships type-specific forms)</span></label>
        ${this._editErr ? `<div class="pcv-drawer-err">${_esc(this._editErr)}</div>` : ""}
        <textarea class="pcv-drawer-json" rows="10" spellcheck="false">${_esc(this._editJson)}</textarea>
      </div>

      <div class="pcv-drawer-footer">
        <button class="pcv-drawer-save pbtn pbtn-p">Save</button>
        <button class="pcv-drawer-cancel pbtn pbtn-g">Cancel</button>
      </div>
    `;

    this._wire();
  }

  _wire() {
    if (!this.el) return;

    this.el.querySelector(".pcv-drawer-close")?.addEventListener("click", () => this.close());
    this.el.querySelector(".pcv-drawer-cancel")?.addEventListener("click", () => this.close());

    this.el.querySelector(".pcv-drawer-json")?.addEventListener("input", (e) => {
      this._editJson = e.target.value;
    });

    this.el.querySelector(".pcv-drawer-save")?.addEventListener("click", () => {
      let parsed;
      try {
        parsed = JSON.parse(this._editJson);
      } catch (err) {
        this._editErr = `JSON error: ${err.message}`;
        this._render();
        return;
      }

      // Get possibly-edited label
      const labelEl = this.el.querySelector(".pcv-drawer-label");
      const newLabel = labelEl?.textContent?.trim() || this._node.label;

      this._editErr = null;
      if (this._onSave) {
        this._onSave(this._node.id, { config: parsed, label: newLabel });
      }
      this.close();
    });

    this.el.querySelector(".pcv-drawer-delete")?.addEventListener("click", () => {
      if (this._onDelete) this._onDelete(this._node.id);
      this.close();
    });
  }
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
