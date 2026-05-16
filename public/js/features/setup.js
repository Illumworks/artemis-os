// First-run / unhealthy-claude setup flow.
// Shows the setup overlay when:
//   1) the server reports firstRun === true (no ~/.artemis/.initialized sentinel), OR
//   2) the claude-code provider is not connected (user hasn't installed/signed in yet).
// In either case, the user can re-check after running `claude auth login` in their
// terminal. Once claude reports connected, the primary CTA enables and dismissing
// the overlay writes the sentinel so the welcome modal doesn't double-up next boot.

import {
  ensureMemoryEmbeddings,
  fetchBootstrapStatus,
  fetchMemoryEmbeddingStatus,
  fetchProviderStatuses,
  refreshProviderStatuses,
  dismissWelcome,
} from '../core/api.js';

const SKIP_KEY = 'artemis-setup-skipped';
const WELCOME_KEY = 'artemis-welcome-seen';

async function init() {
  const overlay = document.getElementById('setup-overlay');
  if (!overlay) return;

  let bootstrap = { firstRun: false };
  let providers = null;
  let embeddingStatus = null;
  try {
    [bootstrap, providers, embeddingStatus] = await Promise.all([
      fetchBootstrapStatus().catch(() => ({ firstRun: false })),
      fetchProviderStatuses().catch(() => null),
      fetchMemoryEmbeddingStatus().catch(() => null),
    ]);
  } catch {
    return;
  }

  const claude = providers?.['claude-code'];
  const claudeReady = !!claude?.connected;
  const userSkipped = localStorage.getItem(SKIP_KEY) === '1';

  // Show only when:
  //   - this is the first run (no sentinel yet), OR
  //   - claude isn't ready AND the user hasn't explicitly skipped this session
  if (!bootstrap.firstRun && (claudeReady || userSkipped)) return;

  // Hide the legacy welcome overlay while setup is active — we'll let it run after.
  const legacyWelcome = document.getElementById('welcome-overlay');
  if (legacyWelcome) legacyWelcome.classList.add('hidden');

  overlay.classList.remove('hidden');
  render(overlay, providers, embeddingStatus);
  bind(overlay);
}

function render(overlay, providers, embeddingStatus = null) {
  const claude = providers?.['claude-code'];
  const codex = providers?.codex;
  const local = providers?.local;

  paintClaude(overlay, claude);
  paintMini(overlay, 'codex', codex, 'OpenAI Codex CLI. Optional.');
  paintMini(overlay, 'local', local, localHelp(local));
  paintMemoryEmbedding(overlay, embeddingStatus);

  const cta = overlay.querySelector('[data-role="continue"]');
  if (claude?.connected) {
    cta.disabled = false;
    cta.textContent = 'Start chatting';
  } else {
    cta.disabled = true;
    cta.textContent = 'Waiting for Claude Code';
  }
}

function paintClaude(overlay, claude) {
  const dot = overlay.querySelector('[data-role="claude-tile"] .setup-card-dot');
  const label = overlay.querySelector('[data-role="claude-label"]');
  const help = overlay.querySelector('[data-role="claude-help"]');
  const tile = overlay.querySelector('[data-role="claude-tile"]');

  if (!claude) {
    setDot(dot, 'error');
    label.textContent = 'Status unavailable';
    help.textContent = 'Could not reach the Artemis server. Refresh the page and try again.';
    help.classList.remove('is-empty');
    tile.dataset.state = 'error';
    return;
  }

  if (claude.connected) {
    setDot(dot, 'ready');
    const who = claude.email ? ` as ${claude.email}` : '';
    label.textContent = `Connected${who}`;
    help.classList.add('is-empty');
    help.innerHTML = '';
    tile.dataset.state = 'ready';
    return;
  }

  if (claude.label && /not installed|cli not installed/i.test(claude.label)) {
    setDot(dot, 'error');
    label.textContent = 'CLI not installed';
    help.innerHTML = `Install the Claude Code CLI, then come back here and click <strong>Re-check</strong>.<br>
      Quick install — run this in your terminal: <code>npm install -g @anthropic-ai/claude-code</code>`;
    help.classList.remove('is-empty');
    tile.dataset.state = 'error';
    return;
  }

  setDot(dot, 'warn');
  label.textContent = 'Sign in required';
  help.innerHTML = `In your terminal, run <code>claude auth login</code> and follow the prompts.
    When you're back, click <strong>Re-check</strong>.`;
  help.classList.remove('is-empty');
  tile.dataset.state = 'warn';
}

function paintMini(overlay, key, status, fallbackHelp) {
  const tile = overlay.querySelector(`[data-role="${key}-tile"]`);
  if (!tile) return;
  const dot = tile.querySelector('.setup-card-dot');
  const label = tile.querySelector(`[data-role="${key}-label"]`);
  const help = tile.querySelector('.setup-mini-help');

  if (!status) {
    setDot(dot, 'idle');
    label.textContent = 'Unknown';
    help.textContent = fallbackHelp;
    return;
  }

  setDot(dot, status.connected ? 'ready' : status.available ? 'warn' : 'idle');
  label.textContent = status.label || (status.connected ? 'Connected' : 'Not connected');
  if (status.connected && key === 'local' && typeof status.modelCount === 'number') {
    help.textContent = `${status.modelCount} model${status.modelCount === 1 ? '' : 's'} via ${status.backend || 'local backend'}.`;
  }
}

function paintMemoryEmbedding(overlay, status) {
  const tile = overlay.querySelector('[data-role="memory-embedding-tile"]');
  if (!tile) return;
  const dot = tile.querySelector('.setup-card-dot');
  const label = tile.querySelector('[data-role="memory-embedding-label"]');
  const help = tile.querySelector('[data-role="memory-embedding-help"]');
  const ensure = tile.querySelector('[data-role="memory-embedding-ensure"]');
  const progress = tile.querySelector('[data-role="memory-embedding-progress"]');

  if (progress && !progress.dataset.busy) progress.textContent = '';

  if (!status) {
    setDot(dot, 'idle');
    label.textContent = 'Status unavailable';
    help.textContent = 'FTS keyword memory is still available. Re-check when the server is reachable.';
    ensure.disabled = false;
    ensure.textContent = 'Install model';
    return;
  }

  if (!status.vectorStoreAvailable) {
    setDot(dot, 'warn');
    label.textContent = 'Vector store unavailable';
    help.textContent = 'Semantic recall needs sqlite-vec. Artemis will keep using keyword memory until it is available.';
    ensure.disabled = true;
    ensure.textContent = 'Install model';
    return;
  }

  if (status.semanticRetrievalAvailable) {
    setDot(dot, 'ready');
    label.textContent = 'Ready';
    help.textContent = `${status.modelName || 'Embedding model'} is installed, warmed, and available for semantic recall.`;
    ensure.disabled = true;
    ensure.textContent = 'Installed';
    return;
  }

  if (status.present || status.installed) {
    setDot(dot, 'warn');
    label.textContent = status.warmed ? 'Installed' : 'Installed, warms on use';
    help.textContent = 'The model files are present. Semantic recall becomes ready after the process warms the model.';
    ensure.disabled = false;
    ensure.textContent = 'Verify model';
    return;
  }

  setDot(dot, 'idle');
  label.textContent = 'Not installed';
  const missing = Array.isArray(status.missingFiles) ? status.missingFiles.length : 0;
  help.textContent = missing
    ? `Semantic recall is optional. ${missing} model file${missing === 1 ? '' : 's'} still need to be downloaded.`
    : 'Semantic recall is optional. Install the local model when you want better memory matching.';
  ensure.disabled = false;
  ensure.textContent = 'Install model';
}

function localHelp(local) {
  if (!local) return 'Ollama or LM Studio on this machine.';
  return 'Auto-detects Ollama (:11434) or LM Studio (:1234). Optional.';
}

function setDot(dot, state) {
  if (!dot) return;
  dot.dataset.state = state;
}

function bind(overlay) {
  const recheck = overlay.querySelector('[data-role="recheck"]');
  const cta = overlay.querySelector('[data-role="continue"]');
  const skip = overlay.querySelector('[data-role="skip"]');
  const embeddingEnsure = overlay.querySelector('[data-role="memory-embedding-ensure"]');
  const embeddingRecheck = overlay.querySelector('[data-role="memory-embedding-recheck"]');

  recheck?.addEventListener('click', async () => {
    recheck.disabled = true;
    const original = recheck.textContent;
    recheck.textContent = 'Checking…';
    try {
      const [providers, embeddingStatus] = await Promise.all([
        refreshProviderStatuses(),
        fetchMemoryEmbeddingStatus().catch(() => null),
      ]);
      render(overlay, providers, embeddingStatus);
    } catch {
      const help = overlay.querySelector('[data-role="claude-help"]');
      help.textContent = 'Re-check failed. Please try again.';
      help.classList.remove('is-empty');
    } finally {
      recheck.disabled = false;
      recheck.textContent = original;
    }
  });

  embeddingRecheck?.addEventListener('click', async () => {
    embeddingRecheck.disabled = true;
    const original = embeddingRecheck.textContent;
    embeddingRecheck.textContent = 'Checking…';
    try {
      const status = await fetchMemoryEmbeddingStatus();
      paintMemoryEmbedding(overlay, status);
    } catch {
      paintMemoryEmbedding(overlay, null);
    } finally {
      embeddingRecheck.disabled = false;
      embeddingRecheck.textContent = original;
    }
  });

  embeddingEnsure?.addEventListener('click', async () => {
    const progress = overlay.querySelector('[data-role="memory-embedding-progress"]');
    embeddingEnsure.disabled = true;
    embeddingEnsure.textContent = 'Installing…';
    if (progress) {
      progress.dataset.busy = '1';
      progress.textContent = 'Downloading and verifying the local embedding model…';
    }
    try {
      const result = await ensureMemoryEmbeddings();
      const messages = Array.isArray(result?.progress) ? result.progress.filter(Boolean) : [];
      if (progress) progress.textContent = messages.slice(-2).join(' ') || 'Model verified.';
      paintMemoryEmbedding(overlay, result?.status || await fetchMemoryEmbeddingStatus());
    } catch (error) {
      if (progress) progress.textContent = error?.message || 'Install failed. FTS memory remains available.';
      const status = await fetchMemoryEmbeddingStatus().catch(() => null);
      paintMemoryEmbedding(overlay, status);
    } finally {
      if (progress) delete progress.dataset.busy;
    }
  });

  cta?.addEventListener('click', async () => {
    if (cta.disabled) return;
    try { await dismissWelcome(); } catch { /* non-fatal */ }
    // Treat dismissal as "welcome seen" so the legacy overlay doesn't appear next.
    try { localStorage.setItem(WELCOME_KEY, '1'); } catch { /* noop */ }
    hide(overlay);
  });

  skip?.addEventListener('click', () => {
    try { localStorage.setItem(SKIP_KEY, '1'); } catch { /* noop */ }
    hide(overlay);
  });

  const creator = overlay.querySelector('[data-role="open-skill-creator"]');
  creator?.addEventListener('click', async () => {
    try { localStorage.setItem(SKIP_KEY, '1'); } catch { /* noop */ }
    hide(overlay);
    try {
      const { setState } = await import('../core/store.js');
      setState('view', 'skills');
    } catch { /* non-fatal */ }
    try {
      const { openSkillEditModal } = await import('./skill-edit-modal.js');
      // Defer one tick so the skills surface mounts before the modal opens.
      setTimeout(() => openSkillEditModal(null, () => {}), 80);
    } catch { /* non-fatal */ }
  });
}

function hide(overlay) {
  overlay.classList.add('hiding');
  overlay.addEventListener('transitionend', () => {
    overlay.classList.add('hidden');
  }, { once: true });
}

init();
