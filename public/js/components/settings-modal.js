// Web Component: Settings Modal
import {
  ensureMemoryEmbeddings,
  fetchMemoryEmbeddingStatus,
  exportWritingStudioSyncApi,
  importWritingStudioSyncApi,
  inspectWritingStudioSyncApi,
} from '../core/api.js';

const SETTINGS_KEY = 'artemis-settings';
const WRITING_STUDIO_SYNC_KEY = 'artemis-writing-studio-sync';

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}'); } catch { return {}; }
}

function saveSettings(s) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

function normalizeWritingSyncSettings(value = {}) {
  return {
    rootDir: typeof value.rootDir === 'string' ? value.rootDir : '',
    machineLabel: typeof value.machineLabel === 'string' ? value.machineLabel : '',
    autoSync: Boolean(value.rootDir && value.autoSync),
  };
}

function loadWritingSyncSettings() {
  try {
    return normalizeWritingSyncSettings(JSON.parse(localStorage.getItem(WRITING_STUDIO_SYNC_KEY) || '{}'));
  } catch {
    return normalizeWritingSyncSettings();
  }
}

function saveWritingSyncSettings(value) {
  const normalized = normalizeWritingSyncSettings(value);
  localStorage.setItem(WRITING_STUDIO_SYNC_KEY, JSON.stringify(normalized));
  window.dispatchEvent(new CustomEvent('writing-studio-sync:changed', { detail: normalized }));
  return normalized;
}

export function getSetting(key, fallback = true) {
  const s = loadSettings();
  return s[key] !== undefined ? s[key] : fallback;
}

class ArtemisSettingsModal extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div id="settings-modal" class="modal-overlay hidden">
        <div class="modal settings-modal">
          <div class="modal-header">
            <h3>Settings</h3>
            <button id="settings-modal-close" class="modal-close">&times;</button>
          </div>
          <div class="settings-list">
            <label class="settings-row">
              <span class="settings-label">
                <strong>Assistant Bot Preview</strong>
                <small>Show the floating assistant bot bubble. Hidden by default for the v1 shell.</small>
              </span>
              <input type="checkbox" id="setting-assistant-bot" class="settings-toggle">
            </label>
            <section class="settings-memory-card" data-role="settings-memory-embedding" aria-live="polite">
              <div class="settings-memory-head">
                <div>
                  <strong>Semantic Memory</strong>
                  <small data-role="settings-memory-label">Checking…</small>
                </div>
                <span class="settings-memory-dot" data-state="loading"></span>
              </div>
              <p data-role="settings-memory-help">
                Local embeddings improve memory recall. Keyword search stays available without them.
              </p>
              <div class="settings-memory-actions">
                <button type="button" class="settings-action-btn" data-role="settings-memory-recheck">Re-check</button>
                <button type="button" class="settings-action-btn settings-action-btn-primary" data-role="settings-memory-ensure">Install model</button>
              </div>
              <div class="settings-memory-progress" data-role="settings-memory-progress"></div>
            </section>
            <section class="settings-writing-card" data-role="settings-writing-sync" aria-live="polite">
              <div class="settings-memory-head">
                <div>
                  <strong>Writing Studio Sync</strong>
                  <small data-role="settings-writing-label">Saved locally for Writing Studio.</small>
                </div>
                <span class="settings-writing-pill" data-role="settings-writing-pill">Auto off</span>
              </div>
              <p data-role="settings-writing-help">
                Choose the repo-backed folder where saved Writing Studio work should export. This replaces the old rail-level sync controls.
              </p>
              <label class="settings-field">
                <span>Sync folder</span>
                <input type="text" class="settings-input" data-role="settings-writing-root-dir" placeholder="/absolute/path/to/repo/writing-studio">
              </label>
              <label class="settings-field">
                <span>Machine label</span>
                <input type="text" class="settings-input" data-role="settings-writing-machine-label" placeholder="desktop (optional)">
              </label>
              <label class="settings-row settings-inline-row">
                <span class="settings-label">
                  <strong>Autosync saved work</strong>
                  <small data-role="settings-writing-note">Transient thread turns stay local until you save or promote them.</small>
                </span>
                <input type="checkbox" class="settings-toggle" data-role="settings-writing-auto-sync">
              </label>
              <div class="settings-memory-actions">
                <button type="button" class="settings-action-btn" data-role="settings-writing-inspect">Inspect repo</button>
                <button type="button" class="settings-action-btn" data-role="settings-writing-export">Export</button>
                <button type="button" class="settings-action-btn settings-action-btn-primary" data-role="settings-writing-import">Import</button>
              </div>
              <div class="settings-memory-progress" data-role="settings-writing-status"></div>
              <div class="settings-writing-summary" data-role="settings-writing-summary"></div>
            </section>
          </div>
        </div>
      </div>
    `;

    const overlay = this.querySelector('#settings-modal');
    const closeBtn = this.querySelector('#settings-modal-close');
    const botToggle = this.querySelector('#setting-assistant-bot');
    const memoryRecheck = this.querySelector('[data-role="settings-memory-recheck"]');
    const memoryEnsure = this.querySelector('[data-role="settings-memory-ensure"]');
    const writingRootDir = this.querySelector('[data-role="settings-writing-root-dir"]');
    const writingMachineLabel = this.querySelector('[data-role="settings-writing-machine-label"]');
    const writingAutoSync = this.querySelector('[data-role="settings-writing-auto-sync"]');
    const writingInspect = this.querySelector('[data-role="settings-writing-inspect"]');
    const writingExport = this.querySelector('[data-role="settings-writing-export"]');
    const writingImport = this.querySelector('[data-role="settings-writing-import"]');

    // Init toggle state
    botToggle.checked = getSetting('assistantBot', false);
    this.syncSummary = null;
    this.paintWritingSyncSettings(loadWritingSyncSettings());
    void this.refreshMemoryEmbeddingStatus();

    closeBtn.addEventListener('click', () => overlay.classList.add('hidden'));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.add('hidden'); });

    botToggle.addEventListener('change', () => {
      const s = loadSettings();
      s.assistantBot = botToggle.checked;
      saveSettings(s);
      // Dispatch event so the bot module can react
      window.dispatchEvent(new CustomEvent('setting:assistantBot', { detail: botToggle.checked }));
    });

    memoryRecheck?.addEventListener('click', async () => {
      memoryRecheck.disabled = true;
      const original = memoryRecheck.textContent;
      memoryRecheck.textContent = 'Checking…';
      try {
        await this.refreshMemoryEmbeddingStatus();
      } finally {
        memoryRecheck.disabled = false;
        memoryRecheck.textContent = original;
      }
    });

    memoryEnsure?.addEventListener('click', async () => {
      const progress = this.querySelector('[data-role="settings-memory-progress"]');
      memoryEnsure.disabled = true;
      memoryEnsure.textContent = 'Installing…';
      if (progress) {
        progress.dataset.busy = '1';
        progress.textContent = 'Downloading and verifying the local embedding model…';
      }
      try {
        const result = await ensureMemoryEmbeddings();
        const messages = Array.isArray(result?.progress) ? result.progress.filter(Boolean) : [];
        if (progress) progress.textContent = messages.slice(-2).join(' ') || 'Model verified.';
        this.paintMemoryEmbeddingStatus(result?.status || await fetchMemoryEmbeddingStatus());
      } catch (error) {
        if (progress) progress.textContent = error?.message || 'Install failed. Keyword memory remains available.';
        await this.refreshMemoryEmbeddingStatus();
      } finally {
        if (progress) delete progress.dataset.busy;
      }
    });

    const saveWritingPreferences = () => {
      const next = saveWritingSyncSettings({
        rootDir: writingRootDir?.value?.trim() || '',
        machineLabel: writingMachineLabel?.value?.trim() || '',
        autoSync: Boolean(writingAutoSync?.checked),
      });
      this.paintWritingSyncSettings(next);
    };

    writingRootDir?.addEventListener('input', saveWritingPreferences);
    writingMachineLabel?.addEventListener('input', saveWritingPreferences);
    writingAutoSync?.addEventListener('change', saveWritingPreferences);

    writingInspect?.addEventListener('click', async () => {
      await this.runWritingSyncAction('inspect');
    });
    writingExport?.addEventListener('click', async () => {
      await this.runWritingSyncAction('export');
    });
    writingImport?.addEventListener('click', async () => {
      await this.runWritingSyncAction('import');
    });

    // Open from settings button
    document.getElementById('settings-btn')?.addEventListener('click', () => {
      botToggle.checked = getSetting('assistantBot', false);
      this.paintWritingSyncSettings(loadWritingSyncSettings());
      void this.refreshMemoryEmbeddingStatus();
      overlay.classList.remove('hidden');
    });
  }

  async refreshMemoryEmbeddingStatus() {
    try {
      this.paintMemoryEmbeddingStatus(await fetchMemoryEmbeddingStatus());
    } catch {
      this.paintMemoryEmbeddingStatus(null);
    }
  }

  paintMemoryEmbeddingStatus(status) {
    const card = this.querySelector('[data-role="settings-memory-embedding"]');
    if (!card) return;
    const dot = card.querySelector('.settings-memory-dot');
    const label = card.querySelector('[data-role="settings-memory-label"]');
    const help = card.querySelector('[data-role="settings-memory-help"]');
    const ensure = card.querySelector('[data-role="settings-memory-ensure"]');
    const progress = card.querySelector('[data-role="settings-memory-progress"]');

    if (progress && !progress.dataset.busy) progress.textContent = '';

    const paint = (state, labelText, helpText, ensureText, ensureDisabled) => {
      dot.dataset.state = state;
      label.textContent = labelText;
      help.textContent = helpText;
      ensure.textContent = ensureText;
      ensure.disabled = ensureDisabled;
    };

    if (!status) {
      paint('idle', 'Status unavailable', 'Could not reach the readiness endpoint. Keyword memory is still available.', 'Install model', false);
      return;
    }

    if (!status.vectorStoreAvailable) {
      paint('warn', 'Vector store unavailable', 'Semantic recall needs sqlite-vec. Artemis will keep using keyword memory until it is available.', 'Install model', true);
      return;
    }

    if (status.semanticRetrievalAvailable) {
      paint('ready', 'Ready', `${status.modelName || 'Embedding model'} is installed, warmed, and available for semantic recall.`, 'Installed', true);
      return;
    }

    if (status.present || status.installed) {
      paint('warn', status.warmed ? 'Installed' : 'Installed, warms on use', 'The model files are present. Semantic recall becomes ready after the process warms the model.', 'Verify model', false);
      return;
    }

    const missing = Array.isArray(status.missingFiles) ? status.missingFiles.length : 0;
    paint(
      'idle',
      'Not installed',
      missing
        ? `${missing} model file${missing === 1 ? '' : 's'} still need to be downloaded. Keyword memory remains available.`
        : 'Install the local model when you want semantic memory matching.',
      'Install model',
      false,
    );
  }

  paintWritingSyncSettings(settings) {
    const normalized = normalizeWritingSyncSettings(settings);
    const rootDirInput = this.querySelector('[data-role="settings-writing-root-dir"]');
    const machineLabelInput = this.querySelector('[data-role="settings-writing-machine-label"]');
    const autoSyncInput = this.querySelector('[data-role="settings-writing-auto-sync"]');
    const pill = this.querySelector('[data-role="settings-writing-pill"]');
    const note = this.querySelector('[data-role="settings-writing-note"]');

    if (rootDirInput) rootDirInput.value = normalized.rootDir;
    if (machineLabelInput) machineLabelInput.value = normalized.machineLabel;
    if (autoSyncInput) autoSyncInput.checked = normalized.autoSync;
    if (pill) {
      pill.textContent = normalized.autoSync ? 'Auto on' : 'Auto off';
      pill.dataset.state = normalized.autoSync ? 'active' : 'idle';
    }
    if (note) {
      note.textContent = normalized.autoSync
        ? 'Autosync only touches durable Writing Studio records after explicit save actions.'
        : 'Transient thread turns stay local until you save or promote them.';
    }
  }

  setWritingSyncStatus(text) {
    const el = this.querySelector('[data-role="settings-writing-status"]');
    if (el) el.textContent = text || '';
  }

  paintWritingSyncSummary(action, result) {
    const summary = this.querySelector('[data-role="settings-writing-summary"]');
    if (!summary) return;
    if (!result) {
      summary.innerHTML = '';
      return;
    }
    const counts = result.counts && typeof result.counts === 'object'
      ? Object.entries(result.counts)
      : [];
    const label = action === 'inspect' ? 'Last inspection' : action === 'import' ? 'Last import' : 'Last export';
    summary.innerHTML = `
      <strong>${label}</strong>
      ${result.rootDir ? `<span>${result.rootDir}</span>` : ''}
      ${counts.length ? `<div class="settings-writing-summary-chips">${counts.map(([key, value]) => `<span>${value} ${String(key).replaceAll(/([a-z0-9])([A-Z])/g, '$1 $2').replaceAll(/[_-]+/g, ' ').toLowerCase()}</span>`).join('')}</div>` : ''}
    `;
  }

  async runWritingSyncAction(action) {
    const rootDir = this.querySelector('[data-role="settings-writing-root-dir"]')?.value?.trim() || '';
    const machineLabel = this.querySelector('[data-role="settings-writing-machine-label"]')?.value?.trim() || '';
    const buttons = [
      this.querySelector('[data-role="settings-writing-inspect"]'),
      this.querySelector('[data-role="settings-writing-export"]'),
      this.querySelector('[data-role="settings-writing-import"]'),
    ].filter(Boolean);
    if (!rootDir) {
      this.setWritingSyncStatus('Add the repo-backed sync folder first.');
      return;
    }

    const normalized = saveWritingSyncSettings({
      rootDir,
      machineLabel,
      autoSync: Boolean(this.querySelector('[data-role="settings-writing-auto-sync"]')?.checked),
    });
    this.paintWritingSyncSettings(normalized);

    buttons.forEach((button) => { button.disabled = true; });
    this.setWritingSyncStatus(action === 'inspect' ? 'Inspecting Writing Studio repo snapshot…' : action === 'import' ? 'Importing Writing Studio repo snapshot…' : 'Exporting Writing Studio sync files…');
    try {
      const result = action === 'inspect'
        ? await inspectWritingStudioSyncApi({ rootDir })
        : action === 'import'
          ? await importWritingStudioSyncApi({ rootDir })
          : await exportWritingStudioSyncApi({ rootDir, machineLabel: machineLabel || undefined });
      this.syncSummary = { action, result };
      this.paintWritingSyncSummary(action, result);
      this.setWritingSyncStatus(action === 'inspect'
        ? `Loaded Writing Studio repo snapshot from ${rootDir}.`
        : action === 'import'
          ? `Imported Writing Studio sync files from ${rootDir}.`
          : `Exported Writing Studio sync files to ${rootDir}.`);
    } catch (error) {
      this.setWritingSyncStatus(error?.message || 'Writing Studio sync action failed.');
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }
}

customElements.define('artemis-settings-modal', ArtemisSettingsModal);
