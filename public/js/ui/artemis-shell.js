// Artemis OS shell behavior — wires interactions for the design-ported shell:
//   • Rail section collapse toggles (Workspace / Operations / Dev Projects)
//   • Parallel segmented control ↔ hidden #toggle-parallel-btn checkbox
//   • Composer + menu toggle (#composer-plus-btn ↔ #composer-plus-menu)
//   • Theme toggle (html[data-theme] + .is-light/.is-dark on button)
//   • Status orb popover
//   • Empty-state swap (#dp-chat-empty vs #messages) based on message count
//
// Legacy modules (header-dropdowns, right-panel, sidebar-toggle, etc.) keep
// working against the hidden-state nodes. This module only adds behaviors that
// the new shell composition needs.

import { $ } from '../core/dom.js';
import { getState, setState, on as storeOn } from '../core/store.js';
import { getSourceModels } from './model-selector.js';
import {
  DEFAULT_APP_VIEW,
  WORKSPACE_VIEW,
  CALENDAR_VIEW,
  MEETINGS_VIEW,
  JIRA_VIEW,
  OKR_VIEW,
  MEMORY_VIEW,
  WRITING_STUDIO_VIEW,
  MARKETING_DASHBOARD_VIEW,
  MARKETING_CAMPAIGNS_VIEW,
  MARKETING_SIGNALS_VIEW,
  MARKETING_APPROVALS_VIEW,
  MARKETING_SIGNAL_PLAYBOOK_VIEW,
  ROUTING_VIEW,
  COST_VIEW,
  isShellView,
} from '../core/navigation.js';
import { openIntegrationsModal } from '../components/integrations-modal.js';

// data-nav (from index.html rail markup) → view id understood by home.js
// view listener. Keep in sync with SECONDARY_NAV_DESTINATIONS.
const RAIL_NAV_VIEW_MAP = {
  home: DEFAULT_APP_VIEW,
  calendar: CALENDAR_VIEW,
  meetings: MEETINGS_VIEW,
  jira: JIRA_VIEW,
  okr: OKR_VIEW,
  writing: WRITING_STUDIO_VIEW,
  'writing-studio': WRITING_STUDIO_VIEW,
  automations: 'automations',
  pipelines: 'pipelines',
  'pipeline-run-history': 'pipelines',
  skills: 'skills',
  agents: 'agents',
  workflows: 'workflows',
  memory: MEMORY_VIEW,
  'marketing-dashboard': MARKETING_DASHBOARD_VIEW,
  'marketing-campaigns': MARKETING_CAMPAIGNS_VIEW,
  'marketing-signals': MARKETING_SIGNALS_VIEW,
  'marketing-approvals': MARKETING_APPROVALS_VIEW,
  'signal-playbook': MARKETING_SIGNAL_PLAYBOOK_VIEW,
  'marketing-prioritization': MARKETING_SIGNALS_VIEW,
  'signals-inbox': MARKETING_SIGNALS_VIEW,
  'where-to-focus': MARKETING_SIGNALS_VIEW,
};

// Focus hints for Workspace sub-sections that still share a parent view.
const RAIL_NAV_FOCUS_MAP = {
  writing: 'writing-studio',
  'writing-studio': 'writing-studio',
};

// ── Rail section collapse ──────────────────────────────────────────────
function initRailSectionToggles() {
  document.querySelectorAll('.rail-section-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const section = btn.closest('.rail-section-collapse');
      if (!section) return;
      const isOpen = section.classList.contains('open');
      section.classList.toggle('open', !isOpen);
      section.classList.toggle('closed', isOpen);
      btn.setAttribute('aria-expanded', String(!isOpen));
    });
  });
}

// ── Parallel segmented control ─────────────────────────────────────────
function initParallelSeg() {
  const btns = document.querySelectorAll('.dp-parallel-seg-btn');
  if (btns.length === 0) return;
  btns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const val = parseInt(btn.dataset.parallel || '1', 10);
      // Mark active
      btns.forEach((b) => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-checked', String(active));
      });
      // Directly enter/exit with the exact pane count — no checkbox bridge
      // needed since enterParallelMode/exitParallelMode manage the checkbox.
      if (val === 1) {
        import('./parallel.js').then(({ exitParallelMode }) => exitParallelMode());
      } else {
        import('./parallel.js').then(({ enterParallelMode }) => enterParallelMode(val));
      }
    });
  });
}

// ── Composer plus menu ─────────────────────────────────────────────────
function initComposerPlusMenu() {
  const btn = document.getElementById('composer-plus-btn');
  const menu = document.getElementById('composer-plus-menu');
  if (!btn || !menu) return;

  const close = () => {
    menu.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    menu.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden')) open();
    else close();
  });

  // Close on outside click / Esc
  document.addEventListener('click', (e) => {
    if (menu.classList.contains('hidden')) return;
    if (menu.contains(e.target) || btn.contains(e.target)) return;
    close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.classList.contains('hidden')) close();
  });

  // Clicking any menu item closes the menu (behavior for its action is
  // delegated to the item's own handlers — e.g. attach-btn opens file picker).
  menu.querySelectorAll('.dp-composer-plus-item').forEach((item) => {
    item.addEventListener('click', () => close());
  });
}

// ── Theme toggle ───────────────────────────────────────────────────────
// Theme handling is owned by ui/theme.js (it also swaps Mermaid + hljs
// themes). Previously artemis-shell.js double-bound the click handler,
// which flipped the theme twice per click for a no-op net change. Deferring
// to theme.js here. In addition, sync the button's .is-light/.is-dark class
// since the design CSS keys off those instead of aria-checked.
function initThemeButtonClassSync() {
  const btn = document.getElementById('theme-toggle-btn');
  if (!btn) return;
  const sync = () => {
    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    btn.classList.toggle('is-light', theme === 'light');
    btn.classList.toggle('is-dark', theme === 'dark');
    btn.setAttribute('aria-checked', String(theme === 'dark'));
  };
  sync();
  const mo = new MutationObserver(sync);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

// Status orb popover is owned by ui/status-popover.js (uses design .status-pop-* classes).

// ── Empty state swap ───────────────────────────────────────────────────
export function refreshChatEmptyState() {
  const empty = document.getElementById('dp-chat-empty');
  const messages = document.getElementById('messages');
  if (!empty || !messages) return;
  // Exclude the Artemis chat empty-state card from the "has content" check.
  const realChildren = [...messages.children].filter(
    (c) => !c.classList.contains('artemis-chat-empty')
  );
  const hasContent = realChildren.length > 0;
  empty.classList.toggle('hidden', hasContent);
  messages.classList.toggle('hidden', !hasContent);
}

function initChatEmptyObserver() {
  const messages = document.getElementById('messages');
  if (!messages) return;
  const mo = new MutationObserver(() => refreshChatEmptyState());
  mo.observe(messages, { childList: true });
  // Initial pass
  refreshChatEmptyState();
}

// ── Session Config tray ────────────────────────────────────────────────
// DIRECT PORT of DpConfigTray / DpConfigList / DpConfigDetail / DpConfigHint
// from claude-design-ui-redesign/artemis-os/devprojects.jsx (L164-373).
// Structure: .dp-cfg-drop > .dp-cfg-drop-body (grid 260|1fr) with
//   .dp-cfg-list on the left (Provider/Model/Approval/Effort?/Max Turns/
//     Advanced divider/Disabled Tools)
//   .dp-cfg-drop-detail on the right — renders .dp-cfg-detail (options +
//     runtime eyebrow/title/body) for the hovered row, or .dp-cfg-hint if
//     no row is hovered.
// State mirrors the approved design's `cfg` object and also best-effort
// syncs to the hidden legacy <select>s (#source-select etc.) when those
// are present, so existing feature wiring still picks up the change.
const CFG_SOURCES = [
  { id: 'claude-code', name: 'Claude Code', status: 'CONNECTED',          statusKind: 'ok',
    runtime: 'CLAUDE CODE',   runtimeTitle: 'Claude Code is linked',
    body: 'Claude Code runs on Anthropic\u2019s managed infrastructure. Use this when you want the latest models and tool coverage out of the box.' },
  { id: 'codex',       name: 'Codex',       status: 'RESUME READY',       statusKind: 'warn',
    runtime: 'CODEX RUNTIME', runtimeTitle: 'Codex can be resumed',
    body: 'Codex paused your last session. Resume to continue with the same context and tools, or start a fresh one.' },
  { id: 'local',       name: 'Local',       status: 'READY VIA LM STUDIO', statusKind: 'good',
    runtime: 'LOCAL RUNTIME', runtimeTitle: 'LM Studio is active',
    body: 'Use Local when you want work to stay on this machine. LM Studio is connected right now, so Artemis can use the models you currently have loaded there.' },
  { id: 'gemini',      name: 'Gemini',      status: 'NO API KEY',         statusKind: 'muted',
    runtime: 'GOOGLE GEMINI', runtimeTitle: 'Gemini \u2014 add API key',
    body: 'Google Gemini models (2.0 Flash, 1.5 Pro, 1.5 Flash). Add your GEMINI_API_KEY in Connectors to activate. Free-tier quota available at aistudio.google.com.' },
  { id: 'openrouter',  name: 'OpenRouter',  status: 'NO API KEY',         statusKind: 'muted',
    runtime: 'OPENROUTER',    runtimeTitle: 'OpenRouter \u2014 add API key',
    body: 'Route to coding-friendly free models like GPT-OSS, Laguna, Nemotron, Gemma, Llama, and Mistral through one key. Add your OPENROUTER_API_KEY in Connectors to activate.' },
];
const CFG_APPROVALS = [
  { id: 'bypass',         name: 'Bypass',         body: 'No confirmations. Artemis runs tools and writes files without asking. Use for trusted automation and scratch sessions.' },
  { id: 'confirm-writes', name: 'Confirm Writes', body: 'Artemis asks before writing, editing, or deleting files. Reads and queries run freely. A good default for day-to-day work.' },
  { id: 'confirm-all',    name: 'Confirm All',    body: 'Confirm every tool call \u2014 reads, writes, shell, network. Use this when you want to watch Artemis closely.' },
  { id: 'plan-mode',      name: 'Plan Mode',      body: 'Artemis drafts a plan and shows it to you before touching anything. Tools are disabled until you approve the plan.' },
];
// CFG_MODELS is now dynamic — driven by model-selector.js PROVIDER_PICKERS so the
// Session Config tray always shows the same model catalog as the legacy dropdowns.
function getCfgModels(source = 'claude-code') {
  const items = getSourceModels(source);
  return items.map((item) => ({
    id:             item.value,       // e.g. "", "sonnet", "opus", "haiku", "gpt-5.4"
    name:           item.label,       // e.g. "Auto", "Sonnet 4.6", "GPT-5.4"
    eyebrow:        source === 'claude-code' ? 'CLAUDE CODE' : source === 'codex' ? 'CODEX' : source.toUpperCase(),
    eyebrowHeading: item.label,
    body:           item.description || item.label,
  }));
}
const CFG_EFFORTS = [
  { id: 'low',    name: 'Low',    body: 'Shortest thinking budget. Ships quickly, good for obvious tasks.' },
  { id: 'medium', name: 'Medium', body: 'Balanced thinking budget. Default for most sessions.' },
  { id: 'high',   name: 'High',   body: 'Maximum thinking. Slower, but best for hard problems.' },
];
const CFG_MAXTURNS = [10, 20, 30, 50, 100, 'Unlimited'];
const CFG_DTOOLS = [
  { id: 'none',       name: 'none',       body: 'All tools available.' },
  { id: 'no-write',   name: 'No writes',  body: 'Block Edit, Write, Delete, and shell commands that modify state.' },
  { id: 'no-net',     name: 'No network', body: 'Block Fetch, Web, and any outbound network calls.' },
  { id: 'reads-only', name: 'Reads only', body: 'Artemis can read but not act. Good for planning sessions.' },
];

// tiny escape helper
const _esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

function initSessionConfigTray() {
  const btn = document.getElementById('header-project-pill-btn');
  const tray = document.getElementById('session-config-tray');
  if (!btn || !tray) return;

  // Local state (mirrors `cfg` in devprojects.jsx DevProjectsView).
  // Initialize `source` from the same localStorage key model-selector.js writes
  // on change, so the tray UI doesn't drift from the value chat.js actually
  // sends in payload.provider. Without this the tray hardcodes 'claude-code'
  // while sends route to whatever was last persisted (e.g. 'codex' from an
  // earlier picker click), producing a "Codex doesn't support sonnet" error
  // even when the tray says Claude Code.
  let _initialSource = 'claude-code';
  try {
    const persisted = localStorage.getItem('artemis-provider-source');
    if (['claude-code', 'codex', 'local', 'gemini', 'openrouter'].includes(persisted)) {
      _initialSource = persisted;
    }
  } catch {}
  const cfg = {
    source: _initialSource,
    approval: 'confirm-writes',
    model: 'auto',
    effort: 'medium',
    maxTurns: 30,
    disabledTools: 'none',
  };
  let menuRow = 'source'; // which row's detail to show; null → hint

  // Best-effort sync to legacy hidden <select>s when present.
  const syncToLegacy = (key, value) => {
    const map = {
      source: 'source-select',
      model: 'model-select',
      approval: 'perm-mode-select',
      maxTurns: 'max-turns-select',
    };
    const sel = map[key] && document.getElementById(map[key]);
    if (!sel) return;
    const v = String(value);
    let hasOpt = [...sel.options].some((o) => o.value === v);
    if (!hasOpt && (key === 'source' || key === 'model')) {
      sel.add(new Option(v || 'auto', v));
      hasOpt = true;
    }
    if (!hasOpt) return;
    sel.value = v;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  };

  // ---- Row (DpConfigRow) ----
  const rowHtml = (id, label, value, valueKind) => {
    const active = menuRow === id;
    return `
      <div class="dp-cfg-row${active ? ' active' : ''}" data-row="${id}">
        <span class="dp-cfg-row-label">${_esc(label)}</span>
        <span class="dp-cfg-row-value ${valueKind || ''}">${_esc(value)}</span>
        <svg class="dp-cfg-row-caret" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
      </div>`;
  };

  // ---- List (DpConfigList) ----
  const listHtml = () => {
    const source = CFG_SOURCES.find((s) => s.id === cfg.source) || CFG_SOURCES[0];
    const approval = CFG_APPROVALS.find((a) => a.id === cfg.approval) || CFG_APPROVALS[1];
    const cfgModels = getCfgModels(cfg.source);
    const model = cfgModels.find((m) => m.id === cfg.model) || cfgModels[0];
    const dtool = CFG_DTOOLS.find((d) => d.id === cfg.disabledTools) || CFG_DTOOLS[0];
    const showEffort = cfg.source === 'claude-code' || cfg.source === 'codex';
    const sourceValue = cfg.source === 'claude-code' ? 'Claude Code' : source.name;
    return `
      <div class="dp-cfg-list">
        ${rowHtml('source', 'Provider', sourceValue, 'mono')}
        ${rowHtml('model', 'Model', model.name, 'mono')}
        ${rowHtml('approval', 'Approval', approval.name, 'mono')}
        ${showEffort ? rowHtml('effort', 'Effort', cfg.effort.charAt(0).toUpperCase() + cfg.effort.slice(1), 'plain') : ''}
        ${rowHtml('maxturns', 'Max Turns', cfg.maxTurns, 'plain')}
        <div class="dp-cfg-advanced-divider">Advanced</div>
        ${rowHtml('dtools', 'Disabled Tools', dtool.name, 'plain')}
      </div>`;
  };

  // ---- Detail (DpConfigDetail / DpConfigHint) ----
  const hintHtml = () => `
    <div class="dp-cfg-hint">
      <div class="dp-cfg-hint-eyebrow">SESSION CONFIG</div>
      <div class="dp-cfg-hint-title">Pick a setting</div>
      <div class="dp-cfg-hint-body">Select any row to see options and a short note about what it does. Changes apply to the next message you send.</div>
    </div>`;

  const optionRow = (value, name, active, extraCls = '', statusTag = '') => `
    <div class="dp-cfg-option${active ? ' active' : ''}${extraCls ? ' ' + extraCls : ''}" data-value="${_esc(value)}">
      <span class="dp-cfg-option-name">${_esc(name)}</span>${statusTag}
    </div>`;

  const runtimeBlock = (eyebrow, title, body) => `
    <div class="dp-cfg-runtime">
      <div class="dp-cfg-runtime-eyebrow">${_esc(eyebrow)}</div>
      <div class="dp-cfg-runtime-title">${_esc(title)}</div>
      <div class="dp-cfg-runtime-body">${_esc(body)}</div>
    </div>`;

  const detailHtml = () => {
    if (!menuRow) return hintHtml();

    if (menuRow === 'source') {
      const current = CFG_SOURCES.find((s) => s.id === cfg.source) || CFG_SOURCES[0];
      const opts = CFG_SOURCES.map((s) => {
        const tag = `<span class="dp-cfg-status-tag dp-cfg-status-${s.statusKind}">${_esc(s.status)}</span>`;
        return optionRow(s.id, s.name, s.id === cfg.source, s.statusKind === 'muted' ? 'muted' : '', tag);
      }).join('');
      return `<div class="dp-cfg-detail" data-key="source">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock(current.runtime, current.runtimeTitle, current.body)}
      </div>`;
    }

    if (menuRow === 'approval') {
      const current = CFG_APPROVALS.find((a) => a.id === cfg.approval) || CFG_APPROVALS[1];
      const opts = CFG_APPROVALS.map((a) => optionRow(a.id, a.name, a.id === cfg.approval, 'simple')).join('');
      return `<div class="dp-cfg-detail" data-key="approval">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock('APPROVAL POLICY', current.name, current.body)}
      </div>`;
    }

    if (menuRow === 'model') {
      const cfgModels = getCfgModels(cfg.source);
      const current = cfgModels.find((m) => m.id === cfg.model) || cfgModels[0];
      const opts = cfgModels.map((m) => optionRow(m.id, m.name, m.id === cfg.model, 'simple')).join('');
      return `<div class="dp-cfg-detail" data-key="model">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock(current.eyebrow, current.eyebrowHeading, current.body)}
      </div>`;
    }

    if (menuRow === 'effort') {
      const current = CFG_EFFORTS.find((e) => e.id === cfg.effort) || CFG_EFFORTS[1];
      const opts = CFG_EFFORTS.map((e) => optionRow(e.id, e.name, e.id === cfg.effort, 'simple')).join('');
      return `<div class="dp-cfg-detail" data-key="effort">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock('THINKING EFFORT', current.name, current.body)}
      </div>`;
    }

    if (menuRow === 'maxturns') {
      const opts = CFG_MAXTURNS.map((n) => optionRow(n, n, n === cfg.maxTurns, 'simple')).join('');
      return `<div class="dp-cfg-detail" data-key="maxturns">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock('MAX TURNS', cfg.maxTurns, 'Artemis stops after this many back-and-forth turns in a single session. Raise it for long-running tasks; cap it for quick triage.')}
      </div>`;
    }

    if (menuRow === 'dtools') {
      const current = CFG_DTOOLS.find((d) => d.id === cfg.disabledTools) || CFG_DTOOLS[0];
      const opts = CFG_DTOOLS.map((d) => optionRow(d.id, d.name, d.id === cfg.disabledTools, 'simple')).join('');
      return `<div class="dp-cfg-detail" data-key="dtools">
        <div class="dp-cfg-options">${opts}</div>
        ${runtimeBlock('DISABLED TOOLS', current.name, current.body)}
      </div>`;
    }

    return hintHtml();
  };

  const render = () => {
    tray.innerHTML = `
      <div class="dp-cfg-drop-body">
        ${listHtml()}
        <div class="dp-cfg-drop-detail">
          ${detailHtml()}
        </div>
      </div>`;
  };

  // ---- interactions ----
  const setMenuRow = (id) => {
    if (menuRow === id) return;
    menuRow = id;
    render();
  };
  const setCfg = (patch) => {
    Object.assign(cfg, patch);
    for (const [k, v] of Object.entries(patch)) syncToLegacy(k, v);
    render();
  };

  const onTrayOver = (e) => {
    const parallelRow = e.target.closest('.dp-cfg-row-parallel');
    if (parallelRow) { setMenuRow(null); return; }
    const row = e.target.closest('.dp-cfg-row[data-row]');
    if (row) setMenuRow(row.dataset.row);
  };

  const onTrayClick = (e) => {
    const opt = e.target.closest('.dp-cfg-option[data-value]');
    if (opt) {
      if (opt.classList.contains('muted')) return;
      const key = opt.closest('.dp-cfg-detail')?.dataset.key;
      const value = opt.dataset.value;
      if (!key) return;
      if (key === 'source')       { setCfg({ source: value, model: '' }); syncToLegacy('model', ''); }
      else if (key === 'approval')setCfg({ approval: value });
      else if (key === 'model')   setCfg({ model: value });
      else if (key === 'effort')  setCfg({ effort: value });
      else if (key === 'maxturns')setCfg({ maxTurns: value === 'Unlimited' ? 'Unlimited' : Number(value) });
      else if (key === 'dtools')  setCfg({ disabledTools: value });
      return;
    }
    // Row header click also sets the menu row explicitly (for touch / click)
    const row = e.target.closest('.dp-cfg-row[data-row]');
    if (row) { setMenuRow(row.dataset.row); return; }
  };

  const position = () => {
    const rect = btn.getBoundingClientRect();
    const gap = 8;
    const trayWidth = 640; // design value for .dp-cfg-drop
    const left = Math.max(12, Math.min(rect.right - trayWidth, window.innerWidth - trayWidth - 12));
    tray.style.position = 'fixed';
    tray.style.top = `${Math.round(rect.bottom + gap)}px`;
    tray.style.left = `${Math.round(left)}px`;
    tray.style.width = `${trayWidth}px`;
    tray.style.zIndex = '60';
  };

  // Refresh Gemini/OpenRouter status badges from the server each time the tray opens.
  const refreshProviderStatus = () => {
    fetch('/api/providers/status').then((r) => r.ok ? r.json() : null).then((data) => {
      if (!data) return;
      const geminiEntry = CFG_SOURCES.find((s) => s.id === 'gemini');
      const orEntry = CFG_SOURCES.find((s) => s.id === 'openrouter');
      if (geminiEntry) {
        geminiEntry.status = data.gemini?.configured ? 'CONNECTED' : 'NO API KEY';
        geminiEntry.statusKind = data.gemini?.configured ? 'ok' : 'muted';
        geminiEntry.runtimeTitle = data.gemini?.configured ? 'Gemini is active' : 'Gemini — add API key';
        geminiEntry.body = data.gemini?.configured
          ? 'Gemini API key is set. Choose a model below — Gemini 2.0 Flash is the default.'
          : 'Google Gemini models (2.0 Flash, 1.5 Pro, 1.5 Flash). Add your GEMINI_API_KEY in Connectors to activate. Free-tier quota available at aistudio.google.com.';
      }
      if (orEntry) {
        orEntry.status = data.openrouter?.configured ? 'CONNECTED' : 'NO API KEY';
        orEntry.statusKind = data.openrouter?.configured ? 'ok' : 'muted';
        orEntry.runtimeTitle = data.openrouter?.configured ? 'OpenRouter is active' : 'OpenRouter — add API key';
        orEntry.body = data.openrouter?.configured
          ? 'OpenRouter key is set. The model picker starts with non-Chinese free coding candidates: GPT-OSS, Laguna, Nemotron, Gemma, Llama, Mistral, and Hermes.'
          : 'Route to coding-friendly free models like GPT-OSS, Laguna, Nemotron, Gemma, Llama, and Mistral through one key. Add your OPENROUTER_API_KEY in Connectors to activate.';
      }
      render();
    }).catch(() => {});
  };

  const open = () => {
    render();
    position();
    refreshProviderStatus();
    tray.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
    setTimeout(() => {
      document.addEventListener('click', onOutside);
      document.addEventListener('keydown', onEsc);
      window.addEventListener('resize', position);
      window.addEventListener('scroll', position, true);
    }, 0);
  };
  const close = () => {
    tray.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onOutside);
    document.removeEventListener('keydown', onEsc);
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
  };
  const onOutside = (e) => {
    if (tray.contains(e.target) || btn.contains(e.target)) return;
    close();
  };
  const onEsc = (e) => { if (e.key === 'Escape') close(); };

  tray.addEventListener('mouseover', onTrayOver);
  tray.addEventListener('click', onTrayClick);
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (tray.classList.contains('hidden')) open(); else close();
  });
}

// ── Profile / settings popover ─────────────────────────────────────────
// Mirrors the design's Rail settings popover (shell.jsx #settingsOpen block).
// Injects a `.settings-popover` menu above the rail-user card on click, with
// outside-click + Esc dismissal. Routes each item to the appropriate legacy
// control if present; otherwise the menu item is a no-op.
function initProfilePopover() {
  const userBtn = document.getElementById('rail-user');
  const footer = userBtn?.closest('.rail-footer');
  if (!userBtn || !footer) return;
  const caret = userBtn.querySelector('.rail-user-caret');

  let popover = null;

  const buildPopover = () => {
    const el = document.createElement('div');
    el.className = 'settings-popover';
    el.setAttribute('role', 'menu');
    el.innerHTML = `
      <div class="settings-pop-item" data-action="account">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>
        <span>Account &amp; workspace</span>
      </div>
      <div class="settings-pop-item" data-action="settings">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>Settings</span>
      </div>
      <div class="settings-pop-item" data-action="connectors">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8" width="7" height="8" rx="1.5"/><rect x="14" y="8" width="7" height="8" rx="1.5"/><path d="M10 12h4"/><path d="M6.5 8V5.5M6.5 18.5V16"/><path d="M17.5 8V5.5M17.5 18.5V16"/></svg>
        <span>Connectors</span>
      </div>
      <div class="settings-pop-item" data-action="signal-playbook">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/><path d="M8 7h8"/><path d="M8 11h6"/></svg>
        <span>Signal Playbook</span>
      </div>
      <div class="settings-pop-item" data-action="cost">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="1" x2="12" y2="23"/>
          <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
        </svg>
        <span>Cost</span>
      </div>
      <div class="settings-pop-item" data-action="routing">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 12h4"/><path d="M16 7l-4 3.5"/><path d="M16 17l-4-3.5"/></svg>
        <span>Routing</span>
      </div>
      <div class="settings-pop-divider"></div>
      <div class="settings-pop-item" data-action="help">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.5c-1 0-1.5 0.5-1.5 2M12 17v.5"/></svg>
        <span>Help &amp; docs</span>
      </div>
      <div class="settings-pop-item" data-action="signout">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l-5-5 5-5M5 12h12"/></svg>
        <span>Sign out</span>
      </div>
    `;
    return el;
  };

  const open = () => {
    if (popover) return;
    popover = buildPopover();
    footer.insertBefore(popover, userBtn);
    userBtn.classList.add('open');
    if (caret) caret.style.transform = 'rotate(-90deg)';
    userBtn.setAttribute('aria-expanded', 'true');
    // Route clicks
    popover.addEventListener('click', (e) => {
      const item = e.target.closest('.settings-pop-item');
      if (!item) return;
      const action = item.dataset.action;
      close();
      handleAction(action);
    });
    setTimeout(() => {
      document.addEventListener('click', onOutside);
      document.addEventListener('keydown', onEsc);
    }, 0);
  };
  const close = () => {
    if (!popover) return;
    popover.remove();
    popover = null;
    userBtn.classList.remove('open');
    if (caret) caret.style.transform = 'rotate(90deg)';
    userBtn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onOutside);
    document.removeEventListener('keydown', onEsc);
  };
  const onOutside = (e) => {
    if (!popover) return;
    if (popover.contains(e.target) || userBtn.contains(e.target)) return;
    close();
  };
  const onEsc = (e) => { if (e.key === 'Escape') close(); };

  const handleAction = (action) => {
    switch (action) {
      case 'settings': {
        // Route to the legacy Artemis settings modal if present
        const modal = document.querySelector('artemis-settings-modal');
        if (modal && typeof modal.open === 'function') return modal.open();
        // Fallback: open the legacy settings-btn if wired
        document.getElementById('settings-btn')?.click();
        break;
      }
      case 'connectors': {
        // Open the Integrations modal (rail page removed — modal is the only surface)
        openIntegrationsModal();
        break;
      }
      case 'signal-playbook': {
        setState('view', MARKETING_SIGNAL_PLAYBOOK_VIEW);
        break;
      }
      case 'cost': {
        setState('view', COST_VIEW);
        break;
      }
      case 'routing': {
        setState('view', ROUTING_VIEW);
        break;
      }
      case 'account': {
        const modal = document.querySelector('artemis-account-modal');
        if (modal && typeof modal.open === 'function') return modal.open();
        break;
      }
      case 'help': {
        const modal = document.querySelector('artemis-help-modal')
          || document.querySelector('artemis-shortcuts-modal');
        if (modal && typeof modal.open === 'function') return modal.open();
        break;
      }
      case 'signout': {
        // Placeholder — standalone Artemis doesn't have a real auth flow yet
        break;
      }
    }
  };

  userBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (popover) close(); else open();
  });
  userBtn.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (popover) close(); else open();
    }
  });
}

// ── Floating assistant orb ─────────────────────────────────────────────
// assistant-bot.js (loaded as an optional module) wires togglePanel directly
// to #assistant-fab. No routing needed from the shell side.
function initAssistantFab() {}

// ── Faded Artemis mark — opacity scales with cursor proximity ──────────
function initEmptyStateProximity() {
  const empty = document.getElementById('dp-chat-empty');
  const mark = empty?.querySelector('.dp-chat-mark');
  if (!empty || !mark) return;
  empty.setAttribute('data-proximity', '1');

  let rafId = 0;
  const RADIUS = 260; // px at which proximity maxes out

  const onMove = (e) => {
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = 0;
      const rect = mark.getBoundingClientRect();
      if (rect.width === 0) return;
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const dx = e.clientX - cx;
      const dy = e.clientY - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const proximity = Math.max(0, 1 - dist / RADIUS);
      // 0.18 min → ~0.83 max (baseline 0.18, + proximity * 0.65)
      const opacity = 0.18 + proximity * 0.65;
      empty.style.setProperty('--dp-chat-mark-proximity', proximity.toFixed(3));
      empty.style.setProperty('--dp-chat-mark-opacity', opacity.toFixed(3));
    });
  };

  const onLeave = () => {
    empty.style.setProperty('--dp-chat-mark-proximity', '0');
    empty.style.setProperty('--dp-chat-mark-opacity', '0.18');
  };

  window.addEventListener('mousemove', onMove, { passive: true });
  window.addEventListener('mouseleave', onLeave);

  // Click anywhere on the empty area → focus the composer
  empty.addEventListener('click', () => {
    const input = document.querySelector('#message-input, .dp-composer-input, .dp-composer textarea');
    input?.focus();
  });
}

// ── Rail nav routing: highlight + dispatch setState('view', …) so
//    home.js swaps the visible shell surface.
function syncRailActiveFromView(view) {
  if (!view) return;
  const items = document.querySelectorAll('.rail-item[data-nav]');
  let matched = null;
  items.forEach((item) => {
    const navKey = item.getAttribute('data-nav');
    if (RAIL_NAV_VIEW_MAP[navKey] === view) matched = item;
  });
  if (!matched) return;
  items.forEach((i) => i.classList.remove('active'));
  matched.classList.add('active');
}

function initRailNavHighlight() {
  const items = document.querySelectorAll('.rail-item[data-nav]');
  items.forEach((item) => {
    item.addEventListener('click', () => {
      items.forEach((i) => i.classList.remove('active'));
      item.classList.add('active');

      const navKey = item.getAttribute('data-nav');
      const view = RAIL_NAV_VIEW_MAP[navKey];
      if (!view) return;

      const focus = RAIL_NAV_FOCUS_MAP[navKey];
      if (focus) {
        try { localStorage.setItem('artemis-shell-module-focus', focus); } catch {}
      }
      if (isShellView(view)) {
        setState('sessionId', null);
      }
      setState('view', view);
    });
    item.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        item.click();
      }
    });
  });

  // Sync highlight with current view (covers reload-restore and
  // non-rail code paths that call setState('view', …)).
  syncRailActiveFromView(getState('view'));
  storeOn('view', (v) => syncRailActiveFromView(v));
}

// Boot — run after DOM is ready
function boot() {
  initRailSectionToggles();
  initParallelSeg();
  initComposerPlusMenu();
  initThemeButtonClassSync();
  initChatEmptyObserver();
  initRailNavHighlight();
  initAssistantFab();
  initEmptyStateProximity();
  initProfilePopover();
  initSessionConfigTray();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
