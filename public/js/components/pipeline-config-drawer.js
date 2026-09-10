/**
<<<<<<< Updated upstream
 * pipeline-config-drawer.js — PIPE2 + PIPE3
 * Right-side config drawer that opens when a node is clicked.
 * PIPE2: generic JSON config view.
 * PIPE3: per-type forms with Form/JSON toggle. JSON view is the PIPE2 textarea, unchanged.
 * Light DOM only; no Shadow DOM.
 */

import { renderAgentInvocationForm } from "./node-config-forms/agent-invocation-form.js";
import { renderTriggerScheduledForm } from "./node-config-forms/trigger-scheduled-form.js";
import { renderHumanGateForm } from "./node-config-forms/human-gate-form.js";
import { renderConditionalForm } from "./node-config-forms/conditional-form.js";
import { renderSubPipelineForm } from "./node-config-forms/sub-pipeline-form.js";

=======
 * pipeline-config-drawer.js — PIPE2
 * Right-side config drawer that opens when a node is clicked.
 * PIPE2: generic JSON config view. PIPE3 ships per-type forms.
 * Light DOM only; no Shadow DOM.
 */

>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
// Node types that have a typed form in PIPE3
const TYPED_FORM_TYPES = new Set([
  "agent_invocation",
  "trigger_scheduled",
  "human_gate",
  "conditional",
  "sub_pipeline",
]);

export class PipelineConfigDrawer {
  constructor({ onSave, onDelete, onClose, pipelineId = null }) {
    this._onSave = onSave;
    this._onDelete = onDelete;
    this._onClose = onClose;
    this._pipelineId = pipelineId; // for sub_pipeline self-exclusion
    this._node = null;
    this._editJson = "";
    this._editErr = null;
    this._viewMode = "form"; // "form" | "json"
    this._formController = null; // { getValues, validate } returned by form renderers
=======
export class PipelineConfigDrawer {
  constructor({ onSave, onDelete, onClose }) {
    this._onSave = onSave;
    this._onDelete = onDelete;
    this._onClose = onClose;
    this._node = null;
    this._editJson = "";
    this._editErr = null;
>>>>>>> Stashed changes
    this.el = null;
  }

  mount(container) {
    this.el = document.createElement("div");
    this.el.className = "pcv-drawer pcv-drawer--hidden";
    container.appendChild(this.el);

    // Close on outside click
    document.addEventListener("mousedown", (e) => {
      if (this._node && this.el && !this.el.contains(e.target)) {
<<<<<<< Updated upstream
=======
        // Check that the click wasn't on a canvas node
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    this._formController = null;
    // Default to form view if the type has a typed form; otherwise JSON
    this._viewMode = TYPED_FORM_TYPES.has(node.type) ? "form" : "json";
=======
>>>>>>> Stashed changes
    this._render();
    this.el?.classList.remove("pcv-drawer--hidden");
  }

  close() {
    this._node = null;
<<<<<<< Updated upstream
    this._formController = null;
=======
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
=======
      // Only update label — don't overwrite pending JSON edits
>>>>>>> Stashed changes
      const labelEl = this.el?.querySelector(".pcv-drawer-label");
      if (labelEl) labelEl.textContent = node.label || node.id;
    }
  }

<<<<<<< Updated upstream
  // ── Private ──────────────────────────────────────────────────────────────

=======
>>>>>>> Stashed changes
  _render() {
    if (!this.el || !this._node) return;
    const n = this._node;
    const typeLabel = TYPE_LABELS[n.type] || n.type || "Node";
<<<<<<< Updated upstream
    const hasTypedForm = TYPED_FORM_TYPES.has(n.type);
=======
>>>>>>> Stashed changes

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

<<<<<<< Updated upstream
      ${hasTypedForm ? `
        <div class="pcv-drawer-view-toggle">
          <button class="pcv-view-btn${this._viewMode === "form" ? " pcv-view-btn--active" : ""}"
            data-view="form">Form</button>
          <button class="pcv-view-btn${this._viewMode === "json" ? " pcv-view-btn--active" : ""}"
            data-view="json">JSON</button>
        </div>
      ` : ""}

=======
>>>>>>> Stashed changes
      <div class="pcv-drawer-body">
        <label class="pcv-drawer-section-label">Node ID</label>
        <div class="pcv-drawer-id">${_esc(n.id)}</div>

<<<<<<< Updated upstream
        ${this._viewMode === "form" && hasTypedForm
          ? `<div class="pcv-drawer-form-host"></div>`
          : `
            ${this._editErr ? `<div class="pcv-drawer-err">${_esc(this._editErr)}</div>` : ""}
            <label class="pcv-drawer-section-label">Config (JSON)</label>
            <textarea class="pcv-drawer-json" rows="10" spellcheck="false">${_esc(this._editJson)}</textarea>
          `}
=======
        <label class="pcv-drawer-section-label">Config <span class="pcv-drawer-hint">(JSON — PIPE3 ships type-specific forms)</span></label>
        ${this._editErr ? `<div class="pcv-drawer-err">${_esc(this._editErr)}</div>` : ""}
        <textarea class="pcv-drawer-json" rows="10" spellcheck="false">${_esc(this._editJson)}</textarea>
>>>>>>> Stashed changes
      </div>

      <div class="pcv-drawer-footer">
        <button class="pcv-drawer-save pbtn pbtn-p">Save</button>
        <button class="pcv-drawer-cancel pbtn pbtn-g">Cancel</button>
      </div>
    `;

<<<<<<< Updated upstream
    // Render typed form if in form mode
    if (this._viewMode === "form" && hasTypedForm) {
      const host = this.el.querySelector(".pcv-drawer-form-host");
      if (host) this._mountForm(n.type, n.config ?? {}, host);
    }

    this._wire();
  }

  _mountForm(type, config, host) {
    this._formController = null;
    switch (type) {
      case "agent_invocation":
        this._formController = renderAgentInvocationForm(config, host);
        break;
      case "trigger_scheduled":
        this._formController = renderTriggerScheduledForm(config, host);
        break;
      case "human_gate":
        this._formController = renderHumanGateForm(config, host);
        break;
      case "conditional":
        this._formController = renderConditionalForm(config, host);
        break;
      case "sub_pipeline":
        this._formController = renderSubPipelineForm(config, host, {
          currentPipelineId: this._pipelineId,
        });
        break;
    }
  }

=======
    this._wire();
  }

>>>>>>> Stashed changes
  _wire() {
    if (!this.el) return;

    this.el.querySelector(".pcv-drawer-close")?.addEventListener("click", () => this.close());
    this.el.querySelector(".pcv-drawer-cancel")?.addEventListener("click", () => this.close());

<<<<<<< Updated upstream
    // View toggle buttons
    this.el.querySelectorAll(".pcv-view-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetView = btn.dataset.view;
        if (targetView === this._viewMode) return;

        // Sync state before switching
        if (this._viewMode === "form" && this._formController) {
          // Form → JSON: pull values from form, serialize to JSON
          const vals = this._formController.getValues();
          // Merge: form values override, unknown fields from existing JSON preserved
          let existing = {};
          try { existing = JSON.parse(this._editJson); } catch { /* ok */ }
          const merged = { ...existing, ...vals };
          this._editJson = JSON.stringify(merged, null, 2);
        } else if (this._viewMode === "json") {
          // JSON → Form: parse current textarea value into config
          try {
            const parsed = JSON.parse(this._editJson);
            this._node = { ...this._node, config: parsed };
          } catch {
            // Invalid JSON — stay in JSON mode with error
            this._editErr = "Fix JSON before switching to Form view.";
            this._render();
            return;
          }
        }

        this._viewMode = targetView;
        this._editErr = null;
        this._render();
      });
    });

    // JSON textarea
=======
>>>>>>> Stashed changes
    this.el.querySelector(".pcv-drawer-json")?.addEventListener("input", (e) => {
      this._editJson = e.target.value;
    });

<<<<<<< Updated upstream
    // Save
    this.el.querySelector(".pcv-drawer-save")?.addEventListener("click", () => {
      this._doSave();
    });

    // Delete
=======
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

>>>>>>> Stashed changes
    this.el.querySelector(".pcv-drawer-delete")?.addEventListener("click", () => {
      if (this._onDelete) this._onDelete(this._node.id);
      this.close();
    });
  }
<<<<<<< Updated upstream

  _doSave() {
    const labelEl = this.el?.querySelector(".pcv-drawer-label");
    const newLabel = labelEl?.textContent?.trim() || this._node.label;

    if (this._viewMode === "form" && this._formController) {
      // Validate form
      const err = this._formController.validate?.();
      if (err) {
        this._editErr = err;
        // Show error at top of form
        const existingErr = this.el.querySelector(".pcv-drawer-form-err");
        if (existingErr) {
          existingErr.textContent = err;
        } else {
          const host = this.el.querySelector(".pcv-drawer-form-host");
          if (host) {
            const errEl = document.createElement("div");
            errEl.className = "pcv-drawer-err pcv-drawer-form-err";
            errEl.textContent = err;
            host.parentNode.insertBefore(errEl, host);
          }
        }
        return;
      }

      const formVals = this._formController.getValues();
      // Merge with any extra fields from previous JSON to preserve unknown keys
      let existing = {};
      try { existing = JSON.parse(this._editJson); } catch { /* ok */ }
      const merged = { ...existing, ...formVals };

      if (this._onSave) this._onSave(this._node.id, { config: merged, label: newLabel });
      this.close();
      return;
    }

    // JSON mode
    let parsed;
    try {
      parsed = JSON.parse(this._editJson);
    } catch (err) {
      this._editErr = `JSON error: ${err.message}`;
      this._render();
      return;
    }

    this._editErr = null;
    if (this._onSave) this._onSave(this._node.id, { config: parsed, label: newLabel });
    this.close();
  }
=======
>>>>>>> Stashed changes
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
