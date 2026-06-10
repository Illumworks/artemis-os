// Agent Monitor — multi-agent monitoring dashboard
import { $ } from '../core/dom.js';
import { escapeHtml } from '../core/utils.js';
import * as api from '../core/api.js';
import { registerCommand } from '../ui/commands.js';

function formatDuration(ms) {
  if (!ms) return "0s";
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const s = secs % 60;
  return s > 0 ? `${mins}m ${s}s` : `${mins}m`;
}

function formatCost(n) {
  if (!n) return "$0.00";
  if (n < 0.01) return "$" + n.toFixed(4);
  return "$" + n.toFixed(2);
}

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

function pct(n, total) {
  if (!total) return "0%";
  return Math.round((n / total) * 100) + "%";
}

export async function openAgentMonitor() {
  $.agentMonitorModal.classList.remove("hidden");
  try {
    const data = await api.fetchAgentMetrics();
    renderMonitor(data);
  } catch (err) {
    $.agentMonitorContent.innerHTML = `<div class="am-empty">Failed to load metrics</div>`;
    console.error("Agent monitor error:", err);
  }
}

function closeAgentMonitor() {
  $.agentMonitorModal.classList.add("hidden");
}

function renderMonitor(data) {
  const el = $.agentMonitorContent;
  const ov = data.overview || {};
  const agents = data.agents || [];
  const byType = data.byType || [];
  const daily = data.daily || [];
  const recent = data.recent || [];

  const successRate = ov.total_runs > 0
    ? Math.round((ov.completed / ov.total_runs) * 100)
    : 0;

  let html = "";

  html += `<div class="am-section">
    <div class="am-section-title">About This Preview</div>
    <div class="am-empty">No agent activity yet — runs will appear here once launched.</div>
  </div>`;

  // ── Summary cards ──
  html += `<div class="am-cards">
    <div class="am-card">
      <div class="am-card-label">Total Runs</div>
      <div class="am-card-value">${ov.total_runs || 0}</div>
    </div>
    <div class="am-card">
      <div class="am-card-label">Total Cost</div>
      <div class="am-card-value">${formatCost(ov.total_cost)}</div>
    </div>
    <div class="am-card">
      <div class="am-card-label">Avg Duration</div>
      <div class="am-card-value">${formatDuration(ov.avg_duration)}</div>
    </div>
    <div class="am-card">
      <div class="am-card-label">Success Rate</div>
      <div class="am-card-value ${successRate >= 80 ? 'am-success' : successRate >= 50 ? 'am-warn' : 'am-error'}">${successRate}%</div>
    </div>
    <div class="am-card">
      <div class="am-card-label">Avg Turns</div>
      <div class="am-card-value">${Math.round(ov.avg_turns || 0)}</div>
    </div>
    <div class="am-card">
      <div class="am-card-label">Tokens</div>
      <div class="am-card-value">${formatTokens((ov.total_input_tokens || 0) + (ov.total_output_tokens || 0))}</div>
    </div>
  </div>`;

  // ── Run type breakdown ──
  if (byType.length > 0) {
    html += `<div class="am-section">
      <div class="am-section-title">By Run Type</div>
      <div class="am-type-grid">
        ${byType.map(t => `
          <div class="am-type-card">
            <span class="am-type-label">${escapeHtml(t.run_type)}</span>
            <span class="am-type-runs">${t.runs} runs</span>
            <span class="am-type-cost">${formatCost(t.cost)}</span>
          </div>
        `).join("")}
      </div>
    </div>`;
  }

  // ── Agent leaderboard ──
  if (agents.length > 0) {
    html += `<div class="am-section">
      <div class="am-section-title">Agent Leaderboard</div>
      <table class="am-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Runs</th>
            <th>Success</th>
            <th>Avg Duration</th>
            <th>Avg Cost</th>
            <th>Total Cost</th>
            <th>Avg Turns</th>
          </tr>
        </thead>
        <tbody>
          ${agents.map(a => {
            const rate = a.runs > 0 ? Math.round((a.successes / a.runs) * 100) : 0;
            return `<tr>
              <td class="am-agent-name">${escapeHtml(a.agent_title)}</td>
              <td>${a.runs}</td>
              <td class="${rate >= 80 ? 'am-success' : rate >= 50 ? 'am-warn' : 'am-error'}">${rate}%</td>
              <td>${formatDuration(a.avg_duration)}</td>
              <td>${formatCost(a.avg_cost)}</td>
              <td>${formatCost(a.total_cost)}</td>
              <td>${Math.round(a.avg_turns)}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>`;
  }

  // ── Cost by agent bar chart ──
  if (agents.length > 0) {
    const maxCost = Math.max(...agents.map(a => a.total_cost), 0.001);
    html += `<div class="am-section">
      <div class="am-section-title">Cost by Agent</div>
      <div class="am-bar-chart">
        ${agents.map(a => {
          const w = Math.round((a.total_cost / maxCost) * 100);
          return `<div class="am-bar-row">
            <span class="am-bar-label">${escapeHtml(a.agent_title)}</span>
            <div class="am-bar-bg"><div class="am-bar" style="width: ${w}%"></div></div>
            <span class="am-bar-value">${formatCost(a.total_cost)}</span>
          </div>`;
        }).join("")}
      </div>
    </div>`;
  }

  // ── Daily activity chart ──
  if (daily.length > 0) {
    const maxRuns = Math.max(...daily.map(d => d.runs), 1);
    html += `<div class="am-section">
      <div class="am-section-title">Daily Activity (Last 30 Days)</div>
      <div class="am-bar-chart">
        ${daily.map(d => {
          const w = Math.round((d.runs / maxRuns) * 100);
          return `<div class="am-bar-row">
            <span class="am-bar-label">${d.date.slice(5)}</span>
            <div class="am-bar-bg">
              <div class="am-bar" style="width: ${w}%"></div>
            </div>
            <span class="am-bar-value">${d.runs} runs / ${formatCost(d.cost)}</span>
          </div>`;
        }).join("")}
      </div>
    </div>`;
  }

  // ── Recent runs ──
  if (recent.length > 0) {
    html += `<div class="am-section">
      <div class="am-section-title">Recent Runs</div>
      <div class="am-recent">
        ${recent.map(r => {
          const statusClass = r.status === 'completed' ? 'am-success'
            : r.status === 'error' ? 'am-error'
            : r.status === 'aborted' ? 'am-warn' : '';
          const time = new Date(r.started_at * 1000).toLocaleString();
          return `<div class="am-recent-row">
            <span class="am-recent-agent">${escapeHtml(r.agent_title)}</span>
            <span class="am-recent-type">${escapeHtml(r.run_type)}</span>
            <span class="am-recent-status ${statusClass}">${r.status}</span>
            <span class="am-recent-duration">${formatDuration(r.duration_ms)}</span>
            <span class="am-recent-cost">${formatCost(r.cost_usd)}</span>
            <span class="am-recent-time">${time}</span>
          </div>`;
        }).join("")}
      </div>
    </div>`;
  }

  if (!agents.length && !recent.length) {
    html += `<div class="am-empty">No runs yet — launch an agent to see status, cost, and timing here.</div>`;
  }

  el.innerHTML = html;
}

// ── Event bindings ──
$.agentMonitorClose?.addEventListener("click", closeAgentMonitor);
$.agentMonitorModal?.addEventListener("click", (e) => {
  if (e.target === $.agentMonitorModal) closeAgentMonitor();
});

// Register /monitor command
registerCommand("monitor", {
  category: "agent",
  description: "Open multi-agent monitoring dashboard",
  execute() {
    openAgentMonitor();
  },
});
