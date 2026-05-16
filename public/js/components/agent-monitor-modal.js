class AgentMonitorModal extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
  <div id="agent-monitor-modal" class="modal-overlay hidden">
    <div class="modal agent-monitor-modal">
      <div class="modal-header">
        <h3>Agent Monitor</h3>
        <button id="agent-monitor-close" class="modal-close">&times;</button>
      </div>
      <div class="af-preview-note">
        Preview monitor: this view helps after a run has launched, but it only shows lightweight recent activity. It is not yet the final Artemis launch-debug, durable run-history, or maintenance surface.
      </div>
      <div id="agent-monitor-content"></div>
    </div>
  </div>`;

    const overlay = this.querySelector('#agent-monitor-modal');
    this.querySelector('#agent-monitor-close').addEventListener('click', () => overlay.classList.add('hidden'));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.add('hidden'); });
  }
}
customElements.define('artemis-agent-monitor-modal', AgentMonitorModal);
