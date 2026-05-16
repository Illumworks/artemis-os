// Inline collapsible "How skills work" panel rendered at the top of the
// Skills surface. Lets a creator onboard without leaving the app or reading
// repo docs. Returns a string of HTML so it can be slotted into the existing
// operations-shell render flow.

export function renderSkillsGuideHTML() {
  return `
    <details class="ops-skills-guide" style="margin:0 0 14px;border:1px solid var(--border-1);border-radius:10px;background:var(--bg-2,transparent);padding:0">
      <summary style="cursor:pointer;padding:12px 14px;font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px;list-style:none">
        <span aria-hidden="true">›</span>
        <span>How skills work</span>
        <span style="margin-left:auto;font-weight:400;color:var(--ink-5);font-size:12px">First time here? Read this.</span>
      </summary>
      <div style="padding:0 14px 14px;font-size:13px;line-height:1.55;color:var(--ink-3)">
        <p style="margin:6px 0">
          <strong>A skill is a reusable instruction</strong> Artemis can apply to a chat or an agent.
          Each skill is one folder under <code>~/.artemis/skills/&lt;slug&gt;/</code> with a
          <code>SKILL.md</code> file that holds front-matter (name, description, scope, when-to-use)
          and a Markdown body.
        </p>
        <p style="margin:6px 0"><strong>How a skill activates:</strong></p>
        <ul style="margin:4px 0 8px 20px;padding:0">
          <li><strong>Slash command</strong> — type <code>/&lt;slug&gt;</code> in the composer to inject the skill body before your message.</li>
          <li><strong>Auto-injection</strong> — global skills are surfaced to the assistant on every turn; the model decides when its <em>when-to-use</em> hint matches.</li>
          <li><strong>Agent assignment</strong> — agent-only skills must be explicitly attached to an agent.</li>
        </ul>
        <p style="margin:6px 0"><strong>Three ways to create one:</strong></p>
        <ul style="margin:4px 0 8px 20px;padding:0">
          <li><strong>New skill</strong> button (top right) — pick a template, fill in the form, save.</li>
          <li><strong>Drop a <code>.md</code> file</strong> on the skill list, or paste a URL — imported as <em>proposed</em>.</li>
          <li><strong>ZIP import</strong> — works for multi-file skills with helper <code>.md</code> siblings.</li>
        </ul>
        <p style="margin:6px 0">
          <strong>Multi-file skills:</strong> drop additional <code>.md</code> files in the skill's folder.
          They're concatenated alphabetically into the assembled body at serve time, so you can split a
          long playbook into sections without changing the contract.
        </p>
        <p style="margin:6px 0;color:var(--ink-5);font-size:12px">
          Files live on disk under <code>~/.artemis/skills/</code> — Artemis reconciles them into the database
          on every restart. Edits made from this UI are written to disk; edits made on disk are picked up
          on the next reconcile.
        </p>
      </div>
    </details>
  `;
}
