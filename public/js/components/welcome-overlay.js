class WelcomeOverlay extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
  <div id="welcome-overlay" class="welcome-overlay hidden">
    <div class="welcome-container">
      <div class="welcome-mascot">
        <img src="/icons/artemis-mark.png" alt="Artemis" class="welcome-mark" draggable="false">
      </div>
      <h1 class="welcome-title">Welcome to <span>Artemis</span></h1>
      <div class="welcome-version">v1.0 &middot; personal AI workspace</div>
      <p class="welcome-description">
        Your local Artemis workspace for ongoing work. Resume sessions, start focused chats,
        manage projects, and keep workflows, memory, and tools within reach from one place.
      </p>
      <div class="welcome-features">
        <div class="welcome-feature">
          <div class="welcome-feature-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12h7"/><path d="M3 18h11"/><path d="M3 6h9"/><path d="M17 7l4 5-4 5"/><path d="M14 12h7"/>
            </svg>
          </div>
          <div>
            <div class="welcome-feature-title">Dashboard</div>
            <div class="welcome-feature-desc">See what needs attention, pick the next move, and jump back into real work without losing context</div>
          </div>
        </div>
        <div class="welcome-feature">
          <div class="welcome-feature-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div>
            <div class="welcome-feature-title">Chat & Sessions</div>
            <div class="welcome-feature-desc">Stream replies across providers, resume saved threads, and keep each project tied to the work it already holds</div>
          </div>
        </div>
        <div class="welcome-feature">
          <div class="welcome-feature-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v6"/><path d="M16.5 5.5 12 10l-4.5-4.5"/><path d="M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/><path d="M9 17h6"/>
            </svg>
          </div>
          <div>
            <div class="welcome-feature-title">Tools & Automation</div>
            <div class="welcome-feature-desc">Reach for workflows, approvals, memory, and creator tooling when the work calls for more than a single chat</div>
          </div>
        </div>
      </div>
      <div class="welcome-actions">
        <button id="welcome-get-started" class="welcome-btn-primary">Get Started</button>
        <button id="welcome-take-tour" class="welcome-btn-secondary">Take a Tour</button>
      </div>
      <div class="welcome-hint">Press <kbd>Enter</kbd> or <kbd>Esc</kbd> to skip</div>
    </div>
  </div>`;
  }
}
customElements.define('artemis-welcome-overlay', WelcomeOverlay);
