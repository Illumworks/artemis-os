class ArtemisHelpModal extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div id="help-modal" class="modal-overlay hidden">
        <div class="modal help-modal">
          <div class="modal-header">
            <div>
              <div class="help-modal-eyebrow">Quick guidance</div>
              <h3>Artemis Help</h3>
            </div>
            <button id="help-modal-close" class="modal-close">&times;</button>
          </div>
          <div class="help-modal-body">
            <section class="help-card">
              <div class="help-card-title">Max Turns</div>
              <p class="help-card-text">A turn is one internal step Artemis lets the provider take while working on your request. Simple answers may use only a few turns. Tool use, code edits, and retries use more.</p>
              <ul class="help-bullets">
                <li><strong>10</strong> — quick questions, tiny fixes, or one-file edits</li>
                <li><strong>30</strong> — best default for most normal chat and coding work</li>
                <li><strong>50</strong> — better when you expect a few tools, file reads, or a medium task</li>
                <li><strong>100</strong> — useful for bigger debugging, multi-step changes, or long tool runs</li>
                <li><strong>Unlimited</strong> — only when you want the least interruption and accept more cost and loop risk</li>
              </ul>
              <p class="help-card-text">If you are unsure, leave it at <strong>30</strong>. Raise it when Artemis stops too early on bigger tasks.</p>
            </section>

            <section class="help-card">
              <div class="help-card-title">Which provider should I use?</div>
              <p class="help-card-text">Pick the provider based on where you want the work to run and what kind of setup you are comfortable with.</p>
              <ul class="help-bullets">
                <li><strong>Claude Code</strong> — best default if you want the most complete Artemis coding workflow</li>
                <li><strong>Codex</strong> — alternate cloud option that is currently best used for stable chat and resume work</li>
                <li><strong>Local</strong> — use this when you want work to stay on your machine through LM Studio or Ollama</li>
              </ul>
              <p class="help-card-text">Claude Code and Codex both need their own sign-in outside Artemis. Local needs a runtime running first.</p>
            </section>

            <section class="help-card">
              <div class="help-card-title">Which model should I use?</div>
              <p class="help-card-text">Model choice is mainly a tradeoff between speed, depth, cost, and privacy.</p>
              <ul class="help-bullets">
                <li><strong>Auto</strong> — best when you do not want to think about model choice</li>
                <li><strong>Haiku</strong> — fastest, cheapest option for lightweight tasks</li>
                <li><strong>Sonnet</strong> — balanced default for most everyday coding and chat work</li>
                <li><strong>Opus</strong> — slowest but strongest for harder reasoning and messier problems</li>
                <li><strong>Local runtime models</strong> — depends on what LM Studio or Ollama currently has loaded and how powerful your machine is</li>
              </ul>
              <p class="help-card-text">If you are unsure, use <strong>Auto</strong> or <strong>Sonnet</strong>.</p>
            </section>

            <section class="help-card help-card-note">
              <div class="help-card-title">Current model reality</div>
              <p class="help-card-text">Artemis does not yet expose every available model from every provider. Today the selector is curated: Claude Code exposes <strong>Auto / Haiku / Sonnet / Opus</strong>, Codex exposes <strong>Auto / GPT-5.5 / GPT-5.4 / GPT-5.4-Mini / GPT-5.3-Codex / GPT-5.2</strong>, and Local shows the models currently loaded in LM Studio or Ollama.</p>
            </section>

            <section class="help-card help-card-note">
              <div class="help-card-title">What is still in preview?</div>
              <p class="help-card-text">The floating Assistant Bot, the agent / chain / DAG / workflow builder surfaces, and the creator-facing docs and skills tools are still advanced development features. They are available for exploration, but they are not positioned as the polished first-release path.</p>
            </section>

            <div class="help-modal-actions">
              <button type="button" id="help-open-dev-docs" class="help-action-btn">Open Creator Docs</button>
              <button type="button" id="help-close-btn" class="help-action-btn help-action-btn-primary">Done</button>
            </div>
          </div>
        </div>
      </div>
    `;

    const overlay = this.querySelector("#help-modal");
    const closeBtn = this.querySelector("#help-modal-close");
    const doneBtn = this.querySelector("#help-close-btn");
    const docsBtn = this.querySelector("#help-open-dev-docs");
    const openBtn = document.getElementById("help-center-btn");

    const close = () => overlay.classList.add("hidden");
    const open = () => overlay.classList.remove("hidden");

    closeBtn?.addEventListener("click", close);
    doneBtn?.addEventListener("click", close);
    overlay?.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });
    openBtn?.addEventListener("click", open);
    docsBtn?.addEventListener("click", () => {
      close();
      document.getElementById("dev-docs-btn")?.click();
    });
  }
}

if (!customElements.get("artemis-help-modal")) {
  customElements.define("artemis-help-modal", ArtemisHelpModal);
}
