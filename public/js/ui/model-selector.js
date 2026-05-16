// Provider-aware model selector — persists provider/model/effort picker state.
import { $ } from '../core/dom.js';
import * as api from '../core/api.js';

const SOURCE_STORAGE_KEY = 'artemis-provider-source';
const MODEL_STORAGE_KEY = 'artemis-model';
const REASONING_EFFORT_STORAGE_KEY = 'artemis-reasoning-effort';
const SPEED_TIER_STORAGE_KEY = 'artemis-speed-tier';

export const PROVIDER_LABELS = {
  "claude-code": "Claude Code",
  codex: "OpenAI / Codex",
  local: "Local",
  hermes: "Hermes",
  gemini: "Gemini",
  openrouter: "OpenRouter",
};

export const PROVIDER_PICKERS = {
  "claude-code": {
    models: [
      {
        value: "",
        label: "Auto",
        description: "Stay on Claude Code's default picker choice when you do not need to force a family.",
      },
      {
        value: "opus",
        label: "Opus 4.7",
        description: "Strongest Claude model for the hardest architectural work, complex refactors, and multi-file reasoning.",
      },
      {
        value: "opus-1m",
        label: "Opus 4.7 1M",
        description: "Opus 4.7's large-context path. Artemis maps this through Claude Code's verified claude-opus-4-7 alias so the CLI does not receive an invalid 1M-only model string.",
      },
      {
        value: "sonnet",
        label: "Sonnet 4.6",
        description: "Best default for most Artemis coding sessions: fast enough to stay responsive, strong enough for serious repo work.",
      },
      {
        value: "haiku",
        label: "Haiku 4.5",
        description: "Fastest Claude option for lighter questions, quick edits, and lower-cost helper turns.",
      },
    ],
  },
  codex: {
    models: [
      {
        value: "",
        label: "Auto",
        description: "Let Codex keep its default model choice when you just want the normal Codex path.",
      },
      {
        value: "gpt-5.5",
        label: "GPT-5.5",
        description: "Frontier Codex model for complex coding, research, and real-world work. Supports Fast tier.",
      },
      {
        value: "gpt-5.4",
        label: "GPT-5.4",
        description: "Strong general Codex path for everyday coding. Supports Fast tier.",
      },
      {
        value: "gpt-5.4-mini",
        label: "GPT-5.4-Mini",
        description: "Fast lightweight Codex option when speed matters more than depth.",
      },
      {
        value: "gpt-5.3-codex",
        label: "GPT-5.3-Codex",
        description: "Codex-optimized path for code-heavy work when you want a coding-specialized option.",
      },
      {
        value: "gpt-5.2",
        label: "GPT-5.2",
        description: "Balanced general-purpose GPT-5 option inside the Codex provider path.",
      },
    ],
    effort: {
      label: "Effort",
      description: "Codex CLI accepts -c model_reasoning_effort=<level>, so Artemis can carry this choice through when signed into Codex.",
      options: [
        { value: "low", label: "Low", description: "Fast responses with lighter reasoning." },
        { value: "medium", label: "Medium", description: "Balanced speed and reasoning depth for everyday tasks." },
        { value: "high", label: "High", description: "Greater reasoning depth for complex problems." },
        { value: "xhigh", label: "Extra High", description: "Maximum reasoning depth. Default for frontier models like GPT-5.5." },
      ],
      defaultValue: "medium",
    },
    speed: {
      label: "Speed",
      description: "Codex exposes service_tier=fast on select frontier models. Disabled when the chosen model does not support it.",
      supportsSpeedFor: ["gpt-5.4", "gpt-5.5"],
      options: [
        { value: "standard", label: "Standard", description: "Default service tier. Works for every Codex model." },
        { value: "fast", label: "Fast", description: "Prioritize latency when the model supports the Fast service tier.", requiresFast: true },
      ],
      defaultValue: "standard",
    },
  },
  local: {
    models: [
      {
        value: "",
        label: "Auto",
        description: "Let Artemis use the default loaded runtime model when a local backend is connected.",
      },
      {
        value: "qwen3-coder:30b",
        label: "Qwen3 Coder 30B",
        description: "Strong local coding default when your runtime has it available.",
      },
      {
        value: "deepseek-coder-v2:16b",
        label: "DeepSeek Coder V2 16B",
        description: "Smaller local coding model for faster turns on modest hardware.",
      },
      {
        value: "qwen3:30b",
        label: "Qwen3 30B",
        description: "General local model when you want a broader assistant rather than a coding-first one.",
      },
      {
        value: "nemotron",
        label: "Nemotron 70B",
        description: "Larger local option when you want more depth and your runtime can handle it.",
      },
    ],
  },
  gemini: {
    models: [
      {
        value: "gemini-2.5-flash",
        label: "Gemini 2.5 Flash",
        description: "Google's latest fast model with thinking. Best default — higher free-tier quota than 2.0 Flash and stronger reasoning.",
      },
      {
        value: "gemini-2.5-pro",
        label: "Gemini 2.5 Pro",
        description: "Most capable Gemini model. Best for complex multi-file reasoning and long contexts. Paid tier required.",
      },
      {
        value: "gemini-flash-2",
        label: "Gemini 2.0 Flash",
        description: "Previous generation fast model. Use if 2.5 Flash quota is exhausted.",
      },
      {
        value: "gemini-flash",
        label: "Gemini 1.5 Flash",
        description: "Fast and cost-efficient. Fallback for lighter workloads.",
      },
      {
        value: "gemini-pro",
        label: "Gemini 1.5 Pro",
        description: "Capable Gemini 1.5 model for complex reasoning and long contexts.",
      },
    ],
  },
  openrouter: {
    models: [
      {
        value: "llama-4-scout-free",
        label: "Llama 4 Scout (free)",
        description: "Meta's latest Scout model. Best free coding pick — 10M context window makes it ideal for large repo work at zero token cost.",
      },
      {
        value: "llama-4-maverick-free",
        label: "Llama 4 Maverick (free)",
        description: "Meta Llama 4 Maverick. Strong reasoning and coding with 1M context — second pick after Scout for complex multi-file tasks.",
      },
      {
        value: "devstral-free",
        label: "Devstral Small (free)",
        description: "Mistral's coding-focused model. Built specifically for software engineering tasks; 128k context.",
      },
      {
        value: "gemma-4-31b-free",
        label: "Gemma 4 31B (free)",
        description: "Google DeepMind long-context free model. Strong non-Chinese option for large-context repo reading; 262k context.",
      },
      {
        value: "gemma-4-26b-free",
        label: "Gemma 4 26B A4B (free)",
        description: "Google DeepMind MoE-style free model. Another long-context fallback; 262k context.",
      },
      {
        value: "nemotron-3-super-free",
        label: "Nemotron 3 Super (free)",
        description: "NVIDIA long-context free model. Useful for broad analysis and multi-step coding support; 262k context.",
      },
      {
        value: "laguna-m-free",
        label: "Laguna M.1 (free)",
        description: "Poolside coding-focused free model. Good candidate for repo work when Claude Sonnet would be overkill; 131k context.",
      },
      {
        value: "llama-3.3-70b-free",
        label: "Llama 3.3 70B (free)",
        description: "Meta Llama free model. Reliable general-purpose fallback; 65k context.",
      },
      {
        value: "nous-hermes-405b-free",
        label: "Hermes 3 405B (free)",
        description: "Nous Research Llama-based free model with 131k context. Worth testing for planning and code review tasks.",
      },
      {
        value: "laguna-xs-free",
        label: "Laguna XS.2 (free)",
        description: "Poolside smaller coding-focused free model. Best for quick cheap code questions; 131k context.",
      },
      {
        value: "mistral-7b-free",
        label: "Mistral 7B (free)",
        description: "Mistral free route. Lightweight fallback for simple tasks.",
      },
    ],
  },
};

let providerStatuses = {};

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getProviderStatus(providerId) {
  return providerStatuses?.[providerId] || null;
}

function getPickerConfig(providerId = getSelectedProvider()) {
  return PROVIDER_PICKERS[providerId] || PROVIDER_PICKERS["claude-code"];
}

function getRuntimeLocalModels() {
  const models = providerStatuses?.local?.models;
  if (!Array.isArray(models) || models.length === 0) return null;

  const backend = providerStatuses?.local?.backend;
  const runtimeLabel = backend === "lmstudio" ? "LM Studio" : backend === "ollama" ? "Ollama" : "your local runtime";
  const seen = new Set();
  const runtimeModels = [{
    value: "",
    label: "Auto",
    description: `Let Artemis stick with the default loaded model from ${runtimeLabel}.`,
  }];

  for (const model of models) {
    const value = model.id || model.name;
    const label = model.name || model.id;
    if (!value || seen.has(value)) continue;
    seen.add(value);
    runtimeModels.push({
      value,
      label,
      description: `${label} is currently available from ${runtimeLabel}.`,
    });
  }

  return runtimeModels.length > 1 ? runtimeModels : null;
}

export function getSourceModels(source = getSelectedProvider()) {
  if (source === "local") {
    return getRuntimeLocalModels() || getPickerConfig("local").models;
  }
  return getPickerConfig(source).models;
}

function getReasoningConfig(source = getSelectedProvider()) {
  return getPickerConfig(source).effort || null;
}

function getSpeedConfig(source = getSelectedProvider()) {
  return getPickerConfig(source).speed || null;
}

function isSpeedFastSupported(source = getSelectedProvider(), model = getSelectedModel()) {
  const config = getSpeedConfig(source);
  if (!config || !Array.isArray(config.supportsSpeedFor)) return false;
  return config.supportsSpeedFor.includes(model);
}

function getProviderStatusTone(providerId, status) {
  if (!status) return "neutral";
  if (status.available === false) return "offline";
  if (status.connected === false) return providerId === "local" ? "offline" : "warning";
  return providerId === "codex" ? "codex" : providerId === "local" ? "local" : "connected";
}

function getProviderStatusText(providerId, status) {
  if (!status?.label) return "";
  if (providerId === "codex" && status.connected) return "Resume ready";
  if (providerId === "codex" && status.available) return "Sign in";
  return status.label;
}

function getProviderGuide(providerId, status = getProviderStatus(providerId)) {
  if (providerId === "local") {
    if (status?.connected && status?.backend === "lmstudio") {
      return {
        eyebrow: "Local runtime",
        title: "LM Studio is active",
        body: "Use Local when you want work to stay on this machine. LM Studio is connected right now, so Artemis can use the models you currently have loaded there.",
      };
    }
    if (status?.connected && status?.backend === "ollama") {
      return {
        eyebrow: "Local runtime",
        title: "Ollama is active",
        body: "Use Local when you want work to stay on this machine. Ollama is connected right now, so Artemis can use the models your Ollama runtime is serving.",
      };
    }
    return {
      eyebrow: "Local runtime",
      title: "Local is unavailable",
      body: "Start LM Studio or Ollama first, then reopen Source. Local only works when one of those runtimes is already running.",
    };
  }

  if (providerId === "codex") {
    if (status?.connected) {
      return {
        eyebrow: "Cloud provider",
        title: "OpenAI / Codex is ready",
        body: "Use OpenAI / Codex when you want the alternate cloud path. Artemis mirrors the current Codex model families here, but only exposes the controls the installed Codex runtime can actually honor.",
      };
    }
    return {
      eyebrow: "Cloud provider",
      title: "OpenAI / Codex sign-in required",
      body: "OpenAI / Codex needs sign-in in the Codex app or CLI before Artemis can use it here.",
    };
  }

  if (providerId === "claude-code") {
    return {
      eyebrow: "Default workflow",
      title: status?.connected ? "Claude Code is ready" : "Claude Code sign-in required",
      body: status?.connected
        ? "Use Claude Code as the default choice when you want the most complete Artemis coding workflow. This path now also mirrors Claude's visible effort control."
        : "Claude Code also needs sign-in in its own CLI first, then Artemis can use it here.",
    };
  }

  if (providerId === "gemini") {
    return {
      eyebrow: "Cloud provider",
      title: "Google Gemini",
      body: "Use Gemini when you want Google's models for chat and workflow steps. Requires GEMINI_API_KEY in ~/.artemis/.env. Gemini 2.0 Flash is the default.",
    };
  }

  if (providerId === "openrouter") {
    return {
      eyebrow: "Cloud provider",
      title: "OpenRouter",
      body: "OpenRouter routes requests to many model providers through one API key. Free-tier models are available with no per-token cost. Requires OPENROUTER_API_KEY in ~/.artemis/.env.",
    };
  }

  return {
    eyebrow: "Coming later",
    title: "Hermes is not enabled yet",
    body: "Hermes stays disabled until its provider contract is implemented and verified.",
  };
}

function buildProviderMenuMarkup(providerId, status) {
  const base = PROVIDER_LABELS[providerId] || providerId;
  const statusText = getProviderStatusText(providerId, status);
  const tone = getProviderStatusTone(providerId, status);

  if (!statusText) {
    return `<span class="provider-option-name">${base}</span>`;
  }

  return `
    <span class="provider-option-name">${base}</span>
    <span class="provider-option-status provider-option-status-${tone}">${statusText}</span>
  `;
}

function applyProviderStatus(providerId, status) {
  const item = document.getElementById(`source-item-${providerId}`);
  if (!item) return;

  item.innerHTML = buildProviderMenuMarkup(providerId, status);
  item.dataset.displayLabel = PROVIDER_LABELS[providerId] || providerId;
  item.title = status?.label ? `${PROVIDER_LABELS[providerId] || providerId}: ${status.label}` : (PROVIDER_LABELS[providerId] || providerId);
  item.dataset.providerStatus = getProviderStatusTone(providerId, status);

  if (providerId === "claude-code" || providerId === "local" || providerId === "codex") {
    if (status?.connected === false || status?.available === false) {
      item.classList.add("header-submenu-item-disabled");
      item.disabled = true;
    } else {
      item.classList.remove("header-submenu-item-disabled");
      item.disabled = false;
    }
    return;
  }

  item.classList.add("header-submenu-item-disabled");
  item.disabled = true;
}

function renderSourceGuide(providerId = getSelectedProvider()) {
  const eyebrow = document.getElementById("source-guide-eyebrow");
  const title = document.getElementById("source-guide-title");
  const body = document.getElementById("source-guide-body");
  if (!eyebrow || !title || !body) return;

  const guide = getProviderGuide(providerId);
  eyebrow.textContent = guide.eyebrow;
  title.textContent = guide.title;
  body.textContent = guide.body;
}

function initSourceGuide() {
  const submenu = document.getElementById("source-submenu");
  if (!submenu || submenu.dataset.guideBound === "true") return;

  submenu.addEventListener("mouseover", (event) => {
    const item = event.target.closest(".header-submenu-item[data-value]");
    if (!item) return;
    renderSourceGuide(item.dataset.value);
  });

  submenu.addEventListener("focusin", (event) => {
    const item = event.target.closest(".header-submenu-item[data-value]");
    if (!item) return;
    renderSourceGuide(item.dataset.value);
  });

  submenu.addEventListener("mouseleave", () => {
    renderSourceGuide();
  });

  submenu.dataset.guideBound = "true";
}

function buildMenuButtons(options, targetId) {
  return options
    .map((item) => {
      const disabled = Boolean(item.disabled);
      const tooltip = item.disabledReason || item.description || item.label;
      return `
      <button
        class="header-submenu-item header-submenu-item-rich${disabled ? " header-submenu-item-disabled" : ""}"
        data-target="${targetId}"
        data-value="${escapeHtml(item.value)}"
        data-display-label="${escapeHtml(item.label)}"
        title="${escapeHtml(tooltip)}"
        ${disabled ? "disabled aria-disabled=\"true\"" : ""}
      >
        <span class="header-submenu-item-copy">
          <span class="header-submenu-item-title">${escapeHtml(item.label)}</span>
          ${item.description ? `<span class="header-submenu-item-description">${escapeHtml(item.description)}</span>` : ""}
        </span>
      </button>
    `;
    })
    .join("");
}

function syncSubmenuState(selectId, submenuId, displayId) {
  const select = document.getElementById(selectId);
  const submenu = document.getElementById(submenuId);
  const display = document.getElementById(displayId);
  if (!select || !submenu) return;

  let matchedText = null;
  submenu.querySelectorAll(`.header-submenu-item[data-target="${selectId}"]`).forEach((item) => {
    const isActive = item.dataset.value === select.value;
    item.classList.toggle("active", isActive);
    if (isActive) matchedText = item.dataset.displayLabel || item.textContent.trim();
  });

  if (display && matchedText) {
    display.textContent = matchedText;
  }
}

function ensureSpeedControls() {
  let select = document.getElementById("speed-tier-select");
  if (!select) {
    select = document.createElement("select");
    select.id = "speed-tier-select";
    select.className = "header-hidden-select";
    select.title = "Speed tier";
    const modelSelect = document.getElementById("model-select");
    modelSelect?.parentElement?.appendChild(select);
  }

  let row = document.getElementById("speed-tier-row");
  if (!row) {
    // Insert right after the effort row if present, else after model row.
    const anchor =
      document.getElementById("reasoning-effort-row") ||
      document.getElementById("model-display")?.closest(".header-dropdown-item");
    row = document.createElement("div");
    row.id = "speed-tier-row";
    row.className = "header-dropdown-item has-submenu";
    row.innerHTML = `
      <span class="header-dropdown-item-label">Speed</span>
      <span class="header-dropdown-item-value" id="speed-tier-display">Standard</span>
      <svg class="header-dropdown-arrow" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 6 15 12 9 18"/></svg>
      <div class="header-submenu header-submenu--right header-submenu--stacked" id="speed-tier-submenu"></div>
    `;
    anchor?.insertAdjacentElement("afterend", row);
  }

  return {
    row,
    select,
    submenu: document.getElementById("speed-tier-submenu"),
    display: document.getElementById("speed-tier-display"),
  };
}

function ensureReasoningControls() {
  let select = document.getElementById("reasoning-effort-select");
  if (!select) {
    select = document.createElement("select");
    select.id = "reasoning-effort-select";
    select.className = "header-hidden-select";
    select.title = "Reasoning effort";
    const modelSelect = document.getElementById("model-select");
    modelSelect?.parentElement?.appendChild(select);
  }

  let row = document.getElementById("reasoning-effort-row");
  if (!row) {
    const modelRow = document.getElementById("model-display")?.closest(".header-dropdown-item");
    row = document.createElement("div");
    row.id = "reasoning-effort-row";
    row.className = "header-dropdown-item has-submenu";
    row.innerHTML = `
      <span class="header-dropdown-item-label">Effort</span>
      <span class="header-dropdown-item-value" id="reasoning-effort-display">Medium</span>
      <svg class="header-dropdown-arrow" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 6 15 12 9 18"/></svg>
      <div class="header-submenu header-submenu--right header-submenu--stacked" id="reasoning-effort-submenu"></div>
    `;
    modelRow?.insertAdjacentElement("afterend", row);
  }

  return {
    row,
    select,
    submenu: document.getElementById("reasoning-effort-submenu"),
    display: document.getElementById("reasoning-effort-display"),
  };
}

function renderModelGuide(providerId = getSelectedProvider(), modelValue = getSelectedModel()) {
  const eyebrow = document.getElementById("model-guide-eyebrow");
  const title = document.getElementById("model-guide-title");
  const body = document.getElementById("model-guide-body");
  if (!eyebrow || !title || !body) return;

  const option = getSourceModels(providerId).find((item) => item.value === modelValue) || getSourceModels(providerId)[0];
  eyebrow.textContent = `${PROVIDER_LABELS[providerId] || providerId} picker`;
  title.textContent = option?.label || "Auto";
  body.textContent = option?.description || "Pick the model family you want Artemis to use for this provider.";
}

function initModelGuide() {
  const submenu = document.getElementById("model-submenu");
  if (!submenu || submenu.dataset.guideBound === "true") return;

  submenu.addEventListener("mouseover", (event) => {
    const item = event.target.closest('.header-submenu-item[data-target="model-select"]');
    if (!item) return;
    renderModelGuide(getSelectedProvider(), item.dataset.value);
  });

  submenu.addEventListener("focusin", (event) => {
    const item = event.target.closest('.header-submenu-item[data-target="model-select"]');
    if (!item) return;
    renderModelGuide(getSelectedProvider(), item.dataset.value);
  });

  submenu.addEventListener("mouseleave", () => {
    renderModelGuide();
  });

  submenu.dataset.guideBound = "true";
}

function renderModelOptions(source, { dispatchChange = true } = {}) {
  const select = $.modelSelect;
  const submenu = document.getElementById("model-submenu");
  const options = getSourceModels(source);
  if (!select || !submenu) return;
  submenu.classList.add("header-submenu--stacked");

  const current = select.value;
  select.innerHTML = options
    .map((item) => `<option value="${escapeHtml(item.value)}" ${item.disabled ? "disabled" : ""}>${escapeHtml(item.label)}</option>`)
    .join("");

  submenu.innerHTML = `
    ${buildMenuButtons(options, "model-select")}
    <div class="source-guide-card model-guide-card" id="model-guide-card" aria-live="polite">
      <div class="source-guide-eyebrow" id="model-guide-eyebrow">${escapeHtml((PROVIDER_LABELS[source] || source) + " picker")}</div>
      <div class="source-guide-title" id="model-guide-title"></div>
      <div class="source-guide-body" id="model-guide-body"></div>
    </div>
  `;

  const validValues = new Set(options.filter((item) => !item.disabled).map((item) => item.value));
  select.value = validValues.has(current) ? current : "";
  syncSubmenuState("model-select", "model-submenu", "model-display");
  renderModelGuide(source, select.value);
  initModelGuide();

  if (dispatchChange) {
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function renderReasoningEffort(source, { dispatchChange = true } = {}) {
  const config = getReasoningConfig(source);
  const { row, select, submenu } = ensureReasoningControls();
  if (!row || !select || !submenu) return;
  submenu.classList.add("header-submenu--stacked");

  if (!config) {
    row.hidden = true;
    row.style.display = "none";
    select.innerHTML = "";
    select.value = "";
    return;
  }

  row.hidden = false;
  row.style.display = "";

  const current = localStorage.getItem(REASONING_EFFORT_STORAGE_KEY) || select.value || config.defaultValue;
  const validValues = new Set(config.options.map((item) => item.value));

  select.innerHTML = config.options
    .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`)
    .join("");
  submenu.innerHTML = buildMenuButtons(config.options, "reasoning-effort-select");

  select.value = validValues.has(current) ? current : config.defaultValue;
  syncSubmenuState("reasoning-effort-select", "reasoning-effort-submenu", "reasoning-effort-display");

  if (dispatchChange) {
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function buildSpeedMenuButtons(options, targetId, fastSupported) {
  return options
    .map((item) => {
      const disabled = item.requiresFast && !fastSupported;
      const tooltip = disabled
        ? "The currently selected Codex model does not support the Fast service tier. Switch to GPT-5.4 or GPT-5.5 to enable it."
        : (item.description || item.label);
      return `
      <button
        class="header-submenu-item header-submenu-item-rich${disabled ? " header-submenu-item-disabled" : ""}"
        data-target="${targetId}"
        data-value="${escapeHtml(item.value)}"
        data-display-label="${escapeHtml(item.label)}"
        title="${escapeHtml(tooltip)}"
        ${disabled ? "disabled aria-disabled=\"true\"" : ""}
      >
        <span class="header-submenu-item-copy">
          <span class="header-submenu-item-title">${escapeHtml(item.label)}</span>
          ${item.description ? `<span class="header-submenu-item-description">${escapeHtml(item.description)}</span>` : ""}
        </span>
      </button>
    `;
    })
    .join("");
}

function renderSpeedTier(source, { dispatchChange = true } = {}) {
  const config = getSpeedConfig(source);
  const { row, select, submenu } = ensureSpeedControls();
  if (!row || !select || !submenu) return;
  submenu.classList.add("header-submenu--stacked");

  if (!config) {
    row.hidden = true;
    row.style.display = "none";
    select.innerHTML = "";
    select.value = "";
    return;
  }

  row.hidden = false;
  row.style.display = "";

  const fastSupported = isSpeedFastSupported(source, getSelectedModel());
  const saved = localStorage.getItem(SPEED_TIER_STORAGE_KEY) || select.value || config.defaultValue;
  const validValues = new Set(config.options.map((item) => item.value));

  select.innerHTML = config.options
    .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`)
    .join("");
  submenu.innerHTML = buildSpeedMenuButtons(config.options, "speed-tier-select", fastSupported);

  let chosen = validValues.has(saved) ? saved : config.defaultValue;
  // If Fast is chosen but unsupported for the current model, coerce back to standard
  // without overwriting the persisted preference — so switching models reveals it again.
  if (chosen === "fast" && !fastSupported) {
    select.value = config.defaultValue;
  } else {
    select.value = chosen;
  }
  syncSubmenuState("speed-tier-select", "speed-tier-submenu", "speed-tier-display");

  if (dispatchChange) {
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

async function loadProviderStatuses() {
  try {
    const statuses = await api.fetchProviderStatuses();
    providerStatuses = statuses || {};
    for (const providerId of Object.keys(PROVIDER_LABELS)) {
      applyProviderStatus(providerId, statuses?.[providerId]);
    }
    if (getSelectedProvider() === "local") {
      renderModelOptions("local");
    }
    renderSourceGuide();
    renderModelGuide();
  } catch (err) {
    console.error("Failed to load provider statuses:", err);
  }
}

export function getSelectedProvider() {
  return $.sourceSelect?.value || "claude-code";
}

export function getSelectedModel() {
  return $.modelSelect?.value || "";
}

export function getSelectedReasoningEffort() {
  if (!getReasoningConfig()) return "";
  return document.getElementById("reasoning-effort-select")?.value || getReasoningConfig()?.defaultValue || "";
}

export function getSelectedSpeedTier() {
  const config = getSpeedConfig();
  if (!config) return "";
  const fastSupported = isSpeedFastSupported();
  const value = document.getElementById("speed-tier-select")?.value || config.defaultValue || "standard";
  if (value === "fast" && !fastSupported) return "standard";
  return value;
}

export function getSelectedSpeedTierLabel() {
  const config = getSpeedConfig();
  const tier = getSelectedSpeedTier();
  const label = config?.options?.find((item) => item.value === tier)?.label;
  return label || "";
}

export function getSelectedProviderLabel() {
  return PROVIDER_LABELS[getSelectedProvider()] || getSelectedProvider();
}

export function getSelectedProviderHint() {
  const provider = getSelectedProvider();
  if (provider === "codex") {
    return getProviderStatus("codex")?.connected ? "Resume ready" : "Sign in required";
  }
  if (provider === "local") {
    const backend = providerStatuses?.local?.backend;
    const status = getProviderStatus("local");
    if (backend === "lmstudio") return "LM Studio";
    if (backend === "ollama") return "Ollama";
    if (status?.available === false || status?.connected === false) return "Local unavailable";
    return "LM Studio / Ollama";
  }
  if (provider === "claude-code") {
    return getProviderStatus("claude-code")?.connected ? "Full toolchain" : "Sign in required";
  }
  return "";
}

export function getSelectedModelLabel() {
  const model = getSelectedModel();
  const source = getSelectedProvider();
  const label = getSourceModels(source).find((item) => item.value === model)?.label;
  return label || model || "Auto";
}

export function getSelectedReasoningEffortLabel() {
  const config = getReasoningConfig();
  const effort = getSelectedReasoningEffort();
  const label = config?.options?.find((item) => item.value === effort)?.label;
  return label || "";
}

function init() {
  const savedSource = localStorage.getItem(SOURCE_STORAGE_KEY);
  if (savedSource && $.sourceSelect) $.sourceSelect.value = savedSource;

  renderModelOptions(getSelectedProvider(), { dispatchChange: false });
  renderReasoningEffort(getSelectedProvider(), { dispatchChange: false });
  renderSpeedTier(getSelectedProvider(), { dispatchChange: false });

  const savedModel = localStorage.getItem(MODEL_STORAGE_KEY);
  if (savedModel && $.modelSelect) {
    const validValues = new Set(getSourceModels(getSelectedProvider()).map((item) => item.value));
    $.modelSelect.value = validValues.has(savedModel) ? savedModel : $.modelSelect.value;
  }

  const savedEffort = localStorage.getItem(REASONING_EFFORT_STORAGE_KEY);
  const effortSelect = document.getElementById("reasoning-effort-select");
  const effortConfig = getReasoningConfig();
  if (savedEffort && effortSelect && effortConfig) {
    const validEfforts = new Set(effortConfig.options.map((item) => item.value));
    effortSelect.value = validEfforts.has(savedEffort) ? savedEffort : effortConfig.defaultValue;
  }

  const savedSpeed = localStorage.getItem(SPEED_TIER_STORAGE_KEY);
  const speedSelect = document.getElementById("speed-tier-select");
  const speedConfig = getSpeedConfig();
  if (savedSpeed && speedSelect && speedConfig) {
    const validSpeeds = new Set(speedConfig.options.map((item) => item.value));
    speedSelect.value = validSpeeds.has(savedSpeed) ? savedSpeed : speedConfig.defaultValue;
  }

  syncSubmenuState("model-select", "model-submenu", "model-display");
  syncSubmenuState("reasoning-effort-select", "reasoning-effort-submenu", "reasoning-effort-display");
  syncSubmenuState("speed-tier-select", "speed-tier-submenu", "speed-tier-display");
  renderModelGuide(getSelectedProvider(), getSelectedModel());
  renderSourceGuide(getSelectedProvider());

  $.sourceSelect?.addEventListener("change", () => {
    localStorage.setItem(SOURCE_STORAGE_KEY, $.sourceSelect.value);
    renderModelOptions($.sourceSelect.value);
    renderReasoningEffort($.sourceSelect.value);
    renderSpeedTier($.sourceSelect.value);
    renderSourceGuide($.sourceSelect.value);
  });

  $.modelSelect?.addEventListener("change", () => {
    localStorage.setItem(MODEL_STORAGE_KEY, $.modelSelect.value);
    syncSubmenuState("model-select", "model-submenu", "model-display");
    renderModelGuide(getSelectedProvider(), $.modelSelect.value);
    // Re-render speed picker so Fast option disable state matches the new model.
    renderSpeedTier(getSelectedProvider(), { dispatchChange: false });
  });

  effortSelect?.addEventListener("change", () => {
    localStorage.setItem(REASONING_EFFORT_STORAGE_KEY, effortSelect.value);
    syncSubmenuState("reasoning-effort-select", "reasoning-effort-submenu", "reasoning-effort-display");
  });

  document.getElementById("speed-tier-select")?.addEventListener("change", (e) => {
    localStorage.setItem(SPEED_TIER_STORAGE_KEY, e.target.value);
    syncSubmenuState("speed-tier-select", "speed-tier-submenu", "speed-tier-display");
  });

  initSourceGuide();
  initModelGuide();
  loadProviderStatuses();
}

try {
  init();
} catch (err) {
  console.warn("[model-selector] init skipped:", err.message);
}
