import { escapeHtml } from "../core/utils.js";

export function renderPermissionCard(event) {
  const args = event.args || {};
  return `
    <div class="dev-permission-card" data-permission-id="${escapeHtml(event.permission_id)}">
      <div class="dev-permission-top">
        <strong>${escapeHtml(event.tool_name || "tool")}</strong>
        <span>approval required</span>
      </div>
      <pre>${escapeHtml(JSON.stringify(args, null, 2))}</pre>
      <div class="dev-permission-actions">
        <button class="dev-btn danger" data-deny="${escapeHtml(event.permission_id)}">Deny</button>
        <button class="dev-btn" data-trust="${escapeHtml(event.permission_id)}">Approve and trust</button>
        <button class="dev-btn primary" data-approve="${escapeHtml(event.permission_id)}">Approve</button>
      </div>
    </div>
  `;
}

