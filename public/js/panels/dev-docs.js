// Developer Documentation — extensible docs modal
// To add a new section, push to the `sections` array below.

// ── Section registry ────────────────────────────────────
// Each section: { id, title, icon (SVG string), render() → HTML string }
// render() is called once when the section is first viewed.

const sections = [];

/** Register a documentation section. Call before init or at module load time. */
export function registerDocSection(section) {
  if (!section.id || !section.title || !section.render) {
    throw new Error('registerDocSection requires id, title, and render');
  }
  sections.push(section);
}


// ── Built-in: Architecture Overview ─────────────────────

registerDocSection({
  id: 'architecture',
  title: 'How These Surfaces Work',
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
  render: () => `
    <h2>How These Surfaces Work</h2>
    <p>This is a simplified orientation note for the creator-facing parts of Artemis. It exists so we can remember how shell surfaces and core modules fit together while the product is still evolving.</p>
    <div class="callout"><strong>Why this exists:</strong> this section is mostly for us while we reshape the product. It is not final documentation for an end-user-facing extension system.</div>

    <h3>Current module layout</h3>
    <pre><code>main.js
  ├── core/
  │   ├── store.js          → Reactive state store (getState/setState/on)
  │   ├── dom.js            → Centralized DOM references ($)
  │   ├── constants.js      → Shared constants
  │   ├── events.js         → Event bus (on/emit)
  │   ├── utils.js          → Shared utilities
  │   ├── api.js            → All fetch() helpers
  │   └── ws.js             → WebSocket connection manager
  ├── ui/
  │   ├── formatting.js     → Markdown rendering, code highlighting
  │   ├── diff.js           → Code diff viewer
  │   ├── commands.js       → Slash command registry
  │   ├── messages.js       → Chat message rendering
  │   ├── parallel.js       → 2×2 parallel chat mode
  │   ├── notifications.js  → Push notifications + sound
  │   ├── permissions.js    → Tool approval modes
  │   ├── model-selector.js → Model picker (Opus/Sonnet/Haiku)
  │   ├── context-gauge.js  → Token usage indicator
  │   └── shortcuts.js      → Keyboard shortcuts
  ├── features/
  │   ├── sessions.js       → Session management + search
  │   ├── projects.js       → Project picker + system prompts
  │   ├── chat.js           → Main chat loop
  │   ├── prompts.js        → Prompt templates
  │   ├── workflows.js      → Multi-step workflows
  │   ├── agents.js         → Agent definitions, chains, DAGs
  │   ├── home.js           → Home screen + activity grid
  │   ├── attachments.js    → File/image attachments
  │   ├── voice-input.js    → Web Speech API input
  │   ├── telegram.js       → Telegram integration
  │   └── welcome.js        → Guided tour (Driver.js)
  ├── panels/
  │   ├── dev-project-files.js → Dev Projects file rail
  │   ├── mcp-manager.js    → MCP server management
  │   ├── tips-feed.js      → Tips &amp; shortcuts feed
  │   ├── assistant-bot.js  → Artemis assistant bot panel
  │   └── dev-docs.js       → This documentation modal
  └── core/optional-modules.js → Loads optional side surfaces</code></pre>

    <h3>What matters right now</h3>
    <ul>
      <li><strong>Event Bus</strong> lets modules and panels signal each other without directly depending on one another.</li>
      <li><strong>Reactive Store</strong> holds app state like active session, prompts, and workflows.</li>
      <li><strong>DOM Registry</strong> caches the built-in shell elements that always exist in the base UI.</li>
      <li><strong>API Layer</strong> is where frontend modules call the server.</li>
      <li><strong>Optional surfaces</strong> load through a small resilient loader so non-critical panels cannot block chat startup.</li>
    </ul>

    <h3>Event Bus Events</h3>
    <table class="param-table">
      <thead><tr><th>Event</th><th>Payload</th></tr></thead>
      <tbody>
        <tr><td>ws:message</td><td>Parsed WebSocket message object</td></tr>
        <tr><td>ws:connected</td><td><em>none</em> — initial connection established</td></tr>
        <tr><td>ws:reconnected</td><td><em>none</em> — reconnected after disconnect</td></tr>
        <tr><td>ws:disconnected</td><td><em>none</em> — connection lost</td></tr>
        <tr><td>dev-project-files:focus</td><td><em>none</em> — focus the Dev Projects files rail</td></tr>
      </tbody>
    </table>

    <h3>Store Keys</h3>
    <table class="param-table">
      <thead><tr><th>Key</th><th>Type</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td>view</td><td>string</td><td>Current view: <code>"home"</code> or <code>"chat"</code></td></tr>
        <tr><td>sessionId</td><td>string|null</td><td>Current active session ID</td></tr>
        <tr><td>parallelMode</td><td>boolean</td><td>Whether 2×2 parallel mode is active</td></tr>
        <tr><td>streamingCharCount</td><td>number</td><td>Character count during streaming</td></tr>
        <tr><td>notificationsEnabled</td><td>boolean</td><td>Whether push notifications are on</td></tr>
        <tr><td>sessionTokens</td><td>object</td><td><code>{ input, output, cacheRead, cacheCreation }</code></td></tr>
        <tr><td>prompts</td><td>array</td><td>Loaded prompt templates</td></tr>
        <tr><td>workflows</td><td>array</td><td>Loaded workflow definitions</td></tr>
        <tr><td>agents</td><td>array</td><td>Loaded agent definitions</td></tr>
        <tr><td>projectsData</td><td>array</td><td>Configured projects list</td></tr>
        <tr><td>attachedFiles</td><td>array</td><td>Files attached to current message</td></tr>
        <tr><td>imageAttachments</td><td>array</td><td>Images attached to current message</td></tr>
        <tr><td>backgroundSessions</td><td>Map</td><td>Sessions running in background</td></tr>
      </tbody>
    </table>

    <div class="callout">Today this is still closer to implementation notes than polished documentation. Keep it as a creator reference while the installer, skills, and module architecture settle.</div>
  `,
});

// ── Built-in: Links & Resources ─────────────────────────

registerDocSection({
  id: 'resources',
  title: 'Roadmap Notes',
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  render: () => `
    <h2>Roadmap Notes</h2>
    <p>This section replaces the old resource dump. The main point right now is to keep the creator-facing work aligned with the actual Artemis roadmap.</p>

    <h3>Known gaps to redesign</h3>
    <ul>
      <li>The current docs and extension notes are still too legacy and developer-specific.</li>
      <li>Artemis needs its own installer so creator setup does not depend on older legacy assumptions.</li>
      <li>Skills need a native create/upload flow and a provider-aware packaging model.</li>
      <li>Memory needs a more robust long-context architecture with lower token pressure.</li>
    </ul>

    <h3>Near-term creator priorities</h3>
    <ul>
      <li>simplify creator docs so they are useful to us without overwhelming normal users</li>
      <li>replace the legacy skills marketplace approach with Artemis-native creation and upload flows</li>
      <li>clean up repo structure and leftover legacy product surfaces</li>
      <li>design the extension story around Artemis-native workflows instead of GitHub-centric assumptions</li>
    </ul>

    <div class="callout">For now, treat these docs as internal working notes for Artemis evolution rather than final public-facing documentation.</div>
  `,
});

// ── Modal renderer ──────────────────────────────────────

let overlayEl = null;
let activeSection = null;
const renderedCache = {};

function buildNav() {
  return sections.map(s => `
    <button class="dev-docs-nav-item${s.id === activeSection ? ' active' : ''}" data-section="${s.id}">
      ${s.icon || ''}
      <span>${s.title}</span>
    </button>
  `).join('');
}

function showSection(id) {
  activeSection = id;
  if (!overlayEl) return;

  // Update nav
  overlayEl.querySelectorAll('.dev-docs-nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.section === id);
  });

  // Update content
  const contentEl = overlayEl.querySelector('.dev-docs-content');
  const section = sections.find(s => s.id === id);
  if (!section) return;

  // Cache rendered HTML
  if (!renderedCache[id]) {
    renderedCache[id] = section.render();
  }

  // Hide all, show target
  contentEl.querySelectorAll('.dev-docs-section').forEach(el => {
    el.classList.toggle('active', el.dataset.section === id);
  });

  // Update header title
  const titleEl = overlayEl.querySelector('.dev-docs-title');
  if (titleEl) titleEl.textContent = section.title;
}

export function openDevDocs(sectionId) {
  if (overlayEl) {
    if (sectionId) showSection(sectionId);
    return;
  }

  activeSection = sectionId || sections[0]?.id || 'architecture';

  overlayEl = document.createElement('div');
  overlayEl.className = 'dev-docs-overlay';

  const currentSection = sections.find(s => s.id === activeSection);

  overlayEl.innerHTML = `
    <div class="dev-docs-modal">
      <nav class="dev-docs-nav">
        <div class="dev-docs-nav-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
          <span>Creator Docs</span>
        </div>
        ${buildNav()}
      </nav>
      <div class="dev-docs-body">
        <div class="dev-docs-header">
          <span class="dev-docs-title">${currentSection?.title || 'Documentation'}</span>
          <button class="dev-docs-close" title="Close (Esc)">&times;</button>
        </div>
        <div class="dev-docs-content">
          ${sections.map(s => {
            if (!renderedCache[s.id]) renderedCache[s.id] = s.render();
            return `<div class="dev-docs-section${s.id === activeSection ? ' active' : ''}" data-section="${s.id}">${renderedCache[s.id]}</div>`;
          }).join('')}
        </div>
      </div>
    </div>
  `;

  // ── Event bindings ──
  // Close
  overlayEl.querySelector('.dev-docs-close').addEventListener('click', closeDevDocs);
  overlayEl.addEventListener('click', (e) => {
    if (e.target === overlayEl) closeDevDocs();
  });

  // Nav clicks
  overlayEl.querySelectorAll('.dev-docs-nav-item').forEach(btn => {
    btn.addEventListener('click', () => showSection(btn.dataset.section));
  });

  // Esc key
  overlayEl._onKey = (e) => {
    if (e.key === 'Escape') closeDevDocs();
  };
  document.addEventListener('keydown', overlayEl._onKey);

  document.body.appendChild(overlayEl);
}

export function closeDevDocs() {
  if (!overlayEl) return;
  document.removeEventListener('keydown', overlayEl._onKey);
  overlayEl.remove();
  overlayEl = null;
}

// ── Init: wire up the header button ─────────────────────
function init() {
  const btn = document.getElementById('dev-docs-btn');
  if (btn) {
    btn.addEventListener('click', () => openDevDocs());
  }
}

init();
