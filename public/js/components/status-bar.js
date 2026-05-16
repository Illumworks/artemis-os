class StatusBar extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
  <footer class="status-bar" id="status-bar">
    <div class="status-bar-left">
      <span class="sb-item sb-version" id="sb-version" title="Artemis version"></span>
      <span class="sb-sep"></span>
      <span class="sb-item sb-connection" id="sb-connection" title="Connection status">
        <span class="sb-dot" id="sb-dot"></span>
        <span id="sb-connection-text">connecting</span>
      </span>
      <span class="sb-sep"></span>
      <span class="sb-item sb-branch" id="sb-branch" title="Current git branch">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
        <span id="sb-branch-name">--</span>
      </span>
      <span class="sb-sep"></span>
      <span class="sb-item sb-project" id="sb-project" title="Current project">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        <span id="sb-project-name">no project</span>
      </span>
    </div>
    <div class="status-bar-center">
      <span class="sb-item sb-activity" id="sb-activity"></span>
    </div>
    <div class="status-bar-right">
      <span class="sb-item sb-bg-sessions hidden" id="sb-bg-sessions" title="Background sessions">
        <span class="sb-bg-dot"></span>
        <span id="sb-bg-count">0</span> bg
      </span>
      <span class="sb-sep sb-bg-sep hidden" id="sb-bg-sep"></span>
      <span class="sb-item sb-tokens hidden" id="sb-streaming-tokens" title="Streaming tokens">
        <span id="sb-tokens-value">~0 tokens</span>
      </span>
      <span class="sb-sep sb-tokens-sep hidden" id="sb-tokens-sep"></span>
      <span class="sb-item sb-cost sb-cost-hint" id="sb-cost">
        <span id="sb-session-cost">$0.00</span>
        <span class="sb-cost-pipe">/</span>
        <span id="sb-total-cost">$0.00</span>
        <div class="sb-hint-popup">
          <div class="sb-hint-header">Session / Total Cost</div>
          <div class="sb-hint-body">
            <p>Every message carries a fixed <strong>~20k token</strong> overhead from the Claude Code SDK — this includes the system prompt (~12-15k), core tool schemas (~4-5k), and environment context (~1k).</p>
            <p>This cost is unavoidable and cannot be reduced. Disabling optional tools in Session settings saves only ~2-3k tokens.</p>
            <p class="sb-hint-dim">Subsequent turns benefit from Anthropic's prompt caching, which significantly reduces the effective cost.</p>
          </div>
        </div>
      </span>
    </div>
  </footer>`;
  }
}
customElements.define('artemis-status-bar', StatusBar);
