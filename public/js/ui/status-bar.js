// Status Bar — VS Code-style bottom bar showing project info, git branch, model, costs
import { $ } from '../core/dom.js';
import { getState, on as onState } from '../core/store.js';
import { on } from '../core/events.js';
import * as api from '../core/api.js';
import { openCostDashboard } from '../features/cost-dashboard.js';

// ── DOM refs ──
const sbDot = document.getElementById("sb-dot");
const sbConnText = document.getElementById("sb-connection-text");
const sbBranchName = document.getElementById("sb-branch-name");
const sbProjectName = document.getElementById("sb-project-name");
const sbActivity = document.getElementById("sb-activity");
const sbSessionCost = document.getElementById("sb-session-cost");
const sbTotalCost = document.getElementById("sb-total-cost");
const sbBgSessions = document.getElementById("sb-bg-sessions");
const sbBgSep = document.getElementById("sb-bg-sep");
const sbBgCount = document.getElementById("sb-bg-count");

function setText(el, value) {
  if (el) el.textContent = value;
}

function setClassName(el, value) {
  if (el) el.className = value;
}

// ── Version ──
const sbVersion = document.getElementById("sb-version");
(async () => {
  try {
    const res = await fetch("/api/version");
    const { version } = await res.json();
    if (sbVersion) sbVersion.textContent = `v${version}`;
  } catch { /* ignore */ }
})();

// ── Connection status ──
on("ws:connected", () => {
  setClassName(sbDot, "sb-dot connected");
  setText(sbConnText, "connected");
});

on("ws:reconnected", () => {
  setClassName(sbDot, "sb-dot connected");
  setText(sbConnText, "connected");
});

on("ws:disconnected", () => {
  setClassName(sbDot, "sb-dot reconnecting");
  setText(sbConnText, "reconnecting");
});

// ── Project name ──
function updateProject() {
  const select = $.projectSelect;
  if (!select) return;
  const opt = select.options[select.selectedIndex];
  const name = opt?.textContent?.trim() || "no project";
  setText(sbProjectName, name);
}

// Listen for project changes
if ($.projectSelect) {
  $.projectSelect.addEventListener("change", () => {
    updateProject();
    fetchBranch();
  });
  // Watch for options being added (async project loading on page refresh)
  if (typeof MutationObserver !== "undefined") {
    const selectObserver = new MutationObserver(() => {
      updateProject();
      fetchBranch();
    });
    selectObserver.observe($.projectSelect, { childList: true });
  }
}
updateProject();

// ── Git branch ──
async function fetchBranch() {
  const cwd = $.projectSelect?.value;
  if (!cwd) {
    setText(sbBranchName, "--");
    return;
  }
  try {
    const data = await api.execCommand("git rev-parse --abbrev-ref HEAD", cwd);
    const branch = (data.stdout || data.output || "").trim();
    setText(sbBranchName, branch || "--");
  } catch {
    setText(sbBranchName, "--");
  }
}


// Click project → focus project selector
document.getElementById("sb-project")?.addEventListener("click", () => {
  $.projectSelect?.focus();
});

// ── Costs ──
// Mirror the header cost values
function syncCosts() {
  if ($.projectCostEl) sbSessionCost.textContent = $.projectCostEl.textContent;
  if ($.totalCostEl) sbTotalCost.textContent = $.totalCostEl.textContent;
}

// Observe cost changes in header
if ($.projectCostEl) {
  if (typeof MutationObserver !== "undefined") {
    const obs = new MutationObserver(syncCosts);
    obs.observe($.projectCostEl, { childList: true, characterData: true, subtree: true });
  }
}
if ($.totalCostEl) {
  if (typeof MutationObserver !== "undefined") {
    const obs = new MutationObserver(syncCosts);
    obs.observe($.totalCostEl, { childList: true, characterData: true, subtree: true });
  }
}
syncCosts();

// Click cost → open cost dashboard
document.getElementById("sb-cost")?.addEventListener("click", () => {
  openCostDashboard();
});

// ── Background sessions ──
function updateBgSessions() {
  const bgMap = getState("backgroundSessions");
  const count = bgMap ? bgMap.size : 0;
  if (!sbBgSessions || !sbBgSep) return;
  if (count > 0) {
    sbBgSessions.classList.remove("hidden");
    sbBgSep.classList.remove("hidden");
    setText(sbBgCount, count);
  } else {
    sbBgSessions.classList.add("hidden");
    sbBgSep.classList.add("hidden");
  }
}

onState("backgroundSessions", updateBgSessions);
updateBgSessions();

// ── Activity indicator ──
// Listen for streaming state changes
on("ws:message", (msg) => {
  if (msg.type === "text" || msg.type === "tool") {
    setText(sbActivity, msg.type === "tool"
      ? `running ${msg.name}...`
      : "streaming...");
  }
  if (msg.type === "done" || msg.type === "aborted" || msg.type === "error") {
    setText(sbActivity, "");
  }
  if (msg.type === "agent_started") {
    setText(sbActivity, `agent: ${msg.title}...`);
  }
  if (msg.type === "agent_progress") {
    setText(sbActivity, `agent: ${msg.action}...`);
  }
  if (msg.type === "agent_completed" || msg.type === "agent_error" || msg.type === "agent_aborted") {
    setText(sbActivity, "");
  }
  if (msg.type === "workflow_step" && msg.status === "running") {
    setText(sbActivity, "workflow running...");
  }
  if (msg.type === "workflow_completed") {
    setText(sbActivity, "");
  }

  // Sync costs on result messages
  if (msg.type === "result") {
    setTimeout(syncCosts, 100);
  }
});

// Refresh branch when sessions reload (might have changed branch)
on("ws:message", (msg) => {
  if (msg.type === "session") {
    setTimeout(fetchBranch, 500);
  }
});
