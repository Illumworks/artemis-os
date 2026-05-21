/**
 * pipeline-node-card.js — PIPE2
 * Renders a single pipeline node card on the canvas.
 * Light DOM only; no Shadow DOM.
 */

const NODE_TYPE_META = {
  trigger_manual:    { icon: "▶", label: "Manual Trigger",    border: "accent" },
  trigger_scheduled: { icon: "⏱", label: "Scheduled Trigger", border: "accent" },
  trigger_webhook:   { icon: "⚡", label: "Webhook Trigger",   border: "accent" },
  trigger_event:     { icon: "📡", label: "Event Trigger",     border: "accent" },
  agent_invocation:  { icon: "🤖", label: "Agent",             border: "default" },
  skill_call:        { icon: "⚙", label: "Skill",             border: "default" },
  human_gate:        { icon: "🧑", label: "Human Gate",        border: "warning" },
  conditional:       { icon: "◇", label: "Conditional",       border: "default" },
  sub_pipeline:      { icon: "⊞", label: "Sub-Pipeline",      border: "strong" },
};

function getNodeMeta(type) {
  return NODE_TYPE_META[type] || { icon: "●", label: type, border: "default" };
}

function configSummary(node) {
  const cfg = node.config || {};
  if (node.type === "agent_invocation" || node.type === "skill_call") {
    const id = cfg.agent_id || cfg.skill_id || cfg.id || "";
    if (id) {
      const short = id.split(".").pop();
      return `id: ${short}`;
    }
  }
  if (node.type === "trigger_scheduled") {
    return cfg.cron ? `cron: ${cfg.cron}` : "Scheduled";
  }
  if (node.type === "trigger_webhook") return "Webhook";
  if (node.type === "trigger_event") return cfg.event_type || "Event";
  if (node.type === "human_gate") {
    const k = cfg.approval_kind || "approval";
    return `gate: ${k}`;
  }
  if (node.type === "conditional") return cfg.expression ? "condition" : "Conditional";
  if (node.type === "sub_pipeline") return cfg.pipeline_id || "sub-pipeline";
  return "";
}

/**
 * Build the DOM element for a node card.
 * @param {Object} node - PipelineNode
 * @param {boolean} selected
 * @param {boolean} hasError
 * @returns {HTMLElement}
 */
export function buildNodeCard(node, { selected = false, hasError = false } = {}) {
  const meta = getNodeMeta(node.type);
  const summary = configSummary(node);

  const el = document.createElement("div");
  el.className = [
    "pcv-node",
    `pcv-node--${meta.border}`,
    selected ? "pcv-node--selected" : "",
    hasError ? "pcv-node--error" : "",
  ].filter(Boolean).join(" ");

  el.dataset.nodeId = node.id;
  el.style.left = `${node.position?.x ?? 0}px`;
  el.style.top = `${node.position?.y ?? 0}px`;

  el.innerHTML = `
    <div class="pcv-node-inner">
      <div class="pcv-node-icon" aria-hidden="true">${meta.icon}</div>
      <div class="pcv-node-body">
        <div class="pcv-node-label">${_esc(node.label || node.id)}</div>
        <div class="pcv-node-type">${_esc(meta.label)}</div>
        ${summary ? `<div class="pcv-node-summary">${_esc(summary)}</div>` : ""}
      </div>
      ${hasError ? `<span class="pcv-node-errdot" title="Last run failed at this step"></span>` : ""}
    </div>
    <div class="pcv-port pcv-port--in" data-node-id="${node.id}" data-port="in" title="Input port"></div>
    <div class="pcv-port pcv-port--out" data-node-id="${node.id}" data-port="out" title="Output port"></div>
  `;
  return el;
}

/** Update position of an existing node card element */
export function updateNodeCardPosition(el, x, y) {
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
}

/** Update selection state of a card element */
export function setNodeCardSelected(el, selected) {
  el.classList.toggle("pcv-node--selected", selected);
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
