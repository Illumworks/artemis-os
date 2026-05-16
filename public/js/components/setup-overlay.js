class SetupOverlay extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
  <div id="setup-overlay" class="setup-overlay hidden" role="dialog" aria-modal="true" aria-labelledby="setup-title">
    <div class="setup-container">
      <div class="setup-mascot">
        <img src="/icons/artemis-mark.png" alt="Artemis" class="setup-mark" draggable="false">
      </div>
      <h1 id="setup-title" class="setup-title">Welcome to <span>Artemis</span></h1>
      <div class="setup-eyebrow">first-run setup</div>
      <p class="setup-description">
        Artemis runs locally and uses the <strong>Claude Code CLI</strong> for conversations.
        That's the only thing required to start chatting. Everything else is optional.
      </p>

      <section class="setup-card setup-card-required" data-role="claude-tile" aria-live="polite">
        <header class="setup-card-head">
          <span class="setup-card-dot" data-state="loading"></span>
          <div class="setup-card-titles">
            <div class="setup-card-title">Claude Code</div>
            <div class="setup-card-status" data-role="claude-label">Checking…</div>
          </div>
          <span class="setup-card-badge">Required</span>
        </header>
        <div class="setup-card-body" data-role="claude-body">
          <div class="setup-card-help" data-role="claude-help"></div>
          <div class="setup-card-actions">
            <button class="setup-btn setup-btn-secondary" data-role="recheck">Re-check</button>
            <a class="setup-btn-link" href="https://docs.claude.com/en/docs/claude-code/setup" target="_blank" rel="noopener noreferrer">Install guide ↗</a>
          </div>
        </div>
      </section>

      <details class="setup-optional">
        <summary>
          <span>Optional providers</span>
          <span class="setup-optional-hint">Codex, Local AI, Telegram — set up later</span>
        </summary>
        <div class="setup-optional-grid">
          <div class="setup-mini" data-role="codex-tile">
            <div class="setup-mini-row">
              <span class="setup-card-dot" data-state="loading"></span>
              <span class="setup-mini-name">Codex</span>
              <span class="setup-mini-status" data-role="codex-label">Checking…</span>
            </div>
            <div class="setup-mini-help">OpenAI's CLI. Optional.</div>
          </div>
          <div class="setup-mini" data-role="local-tile">
            <div class="setup-mini-row">
              <span class="setup-card-dot" data-state="loading"></span>
              <span class="setup-mini-name">Local AI</span>
              <span class="setup-mini-status" data-role="local-label">Checking…</span>
            </div>
            <div class="setup-mini-help">Ollama or LM Studio on this machine.</div>
          </div>
          <div class="setup-mini" data-role="telegram-tile">
            <div class="setup-mini-row">
              <span class="setup-card-dot" data-state="idle"></span>
              <span class="setup-mini-name">Telegram</span>
              <span class="setup-mini-status">Not configured</span>
            </div>
            <div class="setup-mini-help">Approve agent actions from your phone.</div>
          </div>
        </div>
      </details>

      <details class="setup-optional">
        <summary>
          <span>Semantic memory</span>
          <span class="setup-optional-hint">Local embeddings for better recall — FTS works without it</span>
        </summary>
        <div class="setup-semantic" data-role="memory-embedding-tile" aria-live="polite">
          <div class="setup-mini-row">
            <span class="setup-card-dot" data-state="loading"></span>
            <span class="setup-mini-name">MiniLM model</span>
            <span class="setup-mini-status" data-role="memory-embedding-label">Checking…</span>
          </div>
          <div class="setup-mini-help" data-role="memory-embedding-help">
            Artemis can use semantic retrieval after the local model is installed and warmed.
          </div>
          <div class="setup-card-actions">
            <button class="setup-btn setup-btn-secondary" data-role="memory-embedding-ensure">Install model</button>
            <button class="setup-btn-link" data-role="memory-embedding-recheck">Re-check</button>
          </div>
          <div class="setup-progress" data-role="memory-embedding-progress"></div>
        </div>
      </details>

      <details class="setup-optional setup-creator">
        <summary>
          <span>Extend Artemis</span>
          <span class="setup-optional-hint">Build your own skills — no folder spelunking required</span>
        </summary>
        <div class="setup-creator-body">
          <p class="setup-creator-copy">
            Skills are reusable instructions you can attach to chats or agents. Pick a starter
            template, fill in a form, and save — Artemis writes the file to
            <code>~/.artemis/skills/</code> for you.
          </p>
          <button class="setup-btn setup-btn-secondary" data-role="open-skill-creator">Build a skill</button>
        </div>
      </details>

      <div class="setup-actions">
        <button class="setup-btn setup-btn-primary" data-role="continue" disabled>Start chatting</button>
        <button class="setup-btn-link" data-role="skip">Skip — I'll set this up later</button>
      </div>

      <div class="setup-hint">You can reopen this from <kbd>Settings</kbd> if you sign in later.</div>
    </div>
  </div>`;
  }
}
customElements.define('artemis-setup-overlay', SetupOverlay);
