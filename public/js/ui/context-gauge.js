// Context Window Indicator — compact SVG ring icon with hover/click detail popup
import { getState, setState } from '../core/store.js';
import { $ } from '../core/dom.js';

const RING_CIRC = 2 * Math.PI * 7; // r=7 → ≈43.98

const MODEL_LIMITS = {
  opus: 1_000_000,
  default: 200_000,
};

function getLimit() {
  const model = $.modelSelect?.value || '';
  return MODEL_LIMITS[model] || MODEL_LIMITS.default;
}

function formatTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

function getContextStatus(total, limit) {
  const pct = Math.min((total / limit) * 100, 100);
  if (pct >= 90) {
    return {
      pct,
      bucket: 'critical',
      headline: 'Context window is nearly full',
      guidance: 'Start a new session soon. Run a handoff/close-session summary before continuing heavy work.',
    };
  }
  if (pct >= 80) {
    return {
      pct,
      bucket: 'warning-high',
      headline: 'Context window is getting full',
      guidance: 'Good time to prepare a handoff or switch to a fresh session after the next checkpoint.',
    };
  }
  if (pct >= 50) {
    return {
      pct,
      bucket: 'warning',
      headline: 'Context window is halfway used',
      guidance: 'Still healthy, but long planning/build sessions should keep an eye on context.',
    };
  }
  return {
    pct,
    bucket: 'normal',
    headline: 'Context window has room',
    guidance: 'No session handoff needed yet.',
  };
}

function renderGauge(tokens) {
  if (!$.contextGauge) return;

  const total = tokens.input + tokens.output + tokens.cacheRead + tokens.cacheCreation;
  const limit = getLimit();
  const status = getContextStatus(total, limit);
  const pct = status.pct;

  $.contextGauge.classList.remove('hidden');

  // SVG ring: drive via stroke-dashoffset
  if ($.contextGaugeFill) {
    $.contextGaugeFill.style.strokeDasharray = String(RING_CIRC);
    $.contextGaugeFill.style.strokeDashoffset = String(RING_CIRC * (1 - pct / 100));
    $.contextGaugeFill.classList.remove('warning', 'critical');
    if (pct >= 80) $.contextGaugeFill.classList.add('critical');
    else if (pct >= 50) $.contextGaugeFill.classList.add('warning');
  }

  // Wrapper urgency class (drives label color + ring glow)
  $.contextGauge.classList.remove('warning', 'critical');
  if (pct >= 80) $.contextGauge.classList.add('critical');
  else if (pct >= 50) $.contextGauge.classList.add('warning');

  // Label: compact percent
  if ($.contextGaugeLabel) $.contextGaugeLabel.textContent = Math.round(pct) + '%';

  // Popup header percent
  if ($.cgpPct) $.cgpPct.textContent = Math.round(pct) + '% full';

  // Popup mini-bar
  if ($.cgpBarFill) {
    $.cgpBarFill.style.width = pct + '%';
    $.cgpBarFill.classList.remove('warning', 'critical');
    if (pct >= 80) $.cgpBarFill.classList.add('critical');
    else if (pct >= 50) $.cgpBarFill.classList.add('warning');
  }

  // Popup token breakdown
  if ($.cgpTokens) {
    $.cgpTokens.innerHTML = [
      `<span class="cgp-token-row"><span>Total</span><span>${formatTokens(total)} / ${formatTokens(limit)}</span></span>`,
      `<span class="cgp-token-row cgp-dim"><span>Input</span><span>${formatTokens(tokens.input)}</span></span>`,
      `<span class="cgp-token-row cgp-dim"><span>Output</span><span>${formatTokens(tokens.output)}</span></span>`,
      `<span class="cgp-token-row cgp-dim"><span>Cache read</span><span>${formatTokens(tokens.cacheRead)}</span></span>`,
      `<span class="cgp-token-row cgp-dim"><span>Cache write</span><span>${formatTokens(tokens.cacheCreation)}</span></span>`,
    ].join('');
  }

  // Popup guidance + CTA
  if ($.cgpGuidance) $.cgpGuidance.textContent = status.guidance;
  if ($.cgpCta) $.cgpCta.classList.toggle('hidden', pct < 80);

  // Accessibility label
  $.contextGauge.setAttribute('aria-label',
    `Context window: ${Math.round(pct)}% full. ${status.guidance}`);
}

export function updateContextGauge(input, output, cacheRead, cacheCreation) {
  const tokens = getState('sessionTokens');
  tokens.input += (input || 0);
  tokens.output += (output || 0);
  tokens.cacheRead += (cacheRead || 0);
  tokens.cacheCreation += (cacheCreation || 0);
  setState('sessionTokens', { ...tokens });
  renderGauge(tokens);
}

export function resetContextGauge() {
  const fresh = { input: 0, output: 0, cacheRead: 0, cacheCreation: 0 };
  setState('sessionTokens', fresh);
  if ($.contextGauge) $.contextGauge.classList.add('hidden');
}

// Popup: click toggles pinned state; click outside dismisses
$.contextGauge?.addEventListener('click', (e) => {
  if (!e.target.closest('#cgp-cta')) {
    $.contextGauge.classList.toggle('is-open');
  }
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('#context-gauge')) {
    $.contextGauge?.classList.remove('is-open');
  }
}, { capture: true });

// CTA → trigger new session
$.cgpCta?.addEventListener('click', () => {
  document.getElementById('new-session-btn')?.click();
  $.contextGauge?.classList.remove('is-open');
});

// Re-render when model changes (limit may differ)
$.modelSelect?.addEventListener('change', () => {
  const tokens = getState('sessionTokens');
  const total = tokens.input + tokens.output + tokens.cacheRead + tokens.cacheCreation;
  if (total > 0) renderGauge(tokens);
});

export async function loadContextGauge(sessionId) {
  if (!sessionId) return;
  try {
    const messages = await (await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages-single`)).json();
    const tokens = { input: 0, output: 0, cacheRead: 0, cacheCreation: 0 };
    for (const msg of messages) {
      if (msg.role === 'result') {
        const data = JSON.parse(msg.content);
        tokens.input += (data.input_tokens || 0);
        tokens.output += (data.output_tokens || 0);
        tokens.cacheRead += (data.cache_read_tokens || 0);
        tokens.cacheCreation += (data.cache_creation_tokens || 0);
      }
    }
    setState('sessionTokens', tokens);
    const total = tokens.input + tokens.output + tokens.cacheRead + tokens.cacheCreation;
    if (total > 0) {
      renderGauge(tokens);
    } else if ($.contextGauge) {
      $.contextGauge.classList.add('hidden');
    }
  } catch (err) {
    console.error('Failed to load context gauge:', err);
  }
}
