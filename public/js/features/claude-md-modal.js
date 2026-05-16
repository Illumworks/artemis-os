import { fetchFileContent, writeFileContent } from '../core/api.js';

const MODAL_ID = 'claude-md-modal';

function getProjectPath() {
  return document.getElementById('project-select')?.value || '';
}

function buildModal() {
  const overlay = document.createElement('div');
  overlay.id = MODAL_ID;
  overlay.className = 'modal-overlay hidden';
  overlay.innerHTML = `
    <div class="modal claude-md-modal">
      <div class="modal-header">
        <div>
          <div style="font-size:11px;color:var(--ink-5);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Project</div>
          <h3 style="margin:0">CLAUDE.md</h3>
        </div>
        <button id="claude-md-modal-close" class="modal-close" aria-label="Close">&times;</button>
      </div>
      <div id="claude-md-modal-status" class="claude-md-status"></div>
      <div class="claude-md-body">
        <textarea
          id="claude-md-textarea"
          class="claude-md-textarea"
          placeholder="# CLAUDE.md&#10;&#10;Add project instructions for Claude here..."
          spellcheck="false"
          disabled
        ></textarea>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-1)">
        <button id="claude-md-modal-cancel" class="btn btn-ghost">Cancel</button>
        <button id="claude-md-modal-save" class="btn btn-primary" disabled>Save</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function getEls() {
  return {
    overlay: document.getElementById(MODAL_ID),
    textarea: document.getElementById('claude-md-textarea'),
    status: document.getElementById('claude-md-modal-status'),
    saveBtn: document.getElementById('claude-md-modal-save'),
    closeBtn: document.getElementById('claude-md-modal-close'),
    cancelBtn: document.getElementById('claude-md-modal-cancel'),
  };
}

function setStatus(statusEl, text, type = '') {
  statusEl.textContent = text;
  statusEl.className = 'claude-md-status' + (type ? ` claude-md-status--${type}` : '');
}

export function openClaudeMdModal() {
  if (!document.getElementById(MODAL_ID)) buildModal();
  const { overlay, textarea, status, saveBtn } = getEls();

  const projectPath = getProjectPath();
  let originalContent = '';

  overlay.classList.remove('hidden');
  textarea.disabled = true;
  saveBtn.disabled = true;
  setStatus(status, '');

  if (!projectPath) {
    setStatus(status, 'No project selected', 'warning');
    return;
  }

  setStatus(status, 'Loading…');

  fetchFileContent(projectPath, 'CLAUDE.md')
    .then(data => {
      originalContent = data.content ?? '';
      textarea.value = originalContent;
      textarea.disabled = false;
      setStatus(status, '');
    })
    .catch(err => {
      const msg = err.message || '';
      if (/ENOENT|not found|no such file/i.test(msg)) {
        originalContent = '';
        textarea.value = '';
        textarea.disabled = false;
        setStatus(status, 'CLAUDE.md not found — type to create it', 'warning');
      } else {
        setStatus(status, `Load failed: ${msg}`, 'error');
      }
    });

  textarea.oninput = () => {
    saveBtn.disabled = textarea.value === originalContent;
  };

  saveBtn.onclick = async () => {
    if (textarea.value === originalContent) return;
    saveBtn.disabled = true;
    setStatus(status, 'Saving…');
    try {
      await writeFileContent(projectPath, 'CLAUDE.md', textarea.value);
      originalContent = textarea.value;
      setStatus(status, 'Saved', 'success');
    } catch (err) {
      setStatus(status, `Save failed: ${err.message}`, 'error');
      saveBtn.disabled = false;
    }
  };

  // Cmd+S / Ctrl+S
  textarea.onkeydown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      if (!saveBtn.disabled) saveBtn.click();
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.substring(0, s) + '  ' + textarea.value.substring(end);
      textarea.selectionStart = textarea.selectionEnd = s + 2;
      saveBtn.disabled = textarea.value === originalContent;
    }
  };
}

function closeModal() {
  const overlay = document.getElementById(MODAL_ID);
  if (overlay) overlay.classList.add('hidden');
}

document.addEventListener('click', (e) => {
  if (e.target.id === 'claude-md-btn' || e.target.closest('#claude-md-btn')) {
    openClaudeMdModal();
    return;
  }
  const overlay = document.getElementById(MODAL_ID);
  if (!overlay || overlay.classList.contains('hidden')) return;
  if (e.target === overlay) closeModal();
  if (e.target.id === 'claude-md-modal-close' || e.target.id === 'claude-md-modal-cancel') closeModal();
});

document.addEventListener('keydown', (e) => {
  const overlay = document.getElementById(MODAL_ID);
  if (!overlay || overlay.classList.contains('hidden')) return;
  if (e.key === 'Escape') closeModal();
});
