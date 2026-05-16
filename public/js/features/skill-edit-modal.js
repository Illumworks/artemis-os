import * as api from "../core/api.js";

const MODAL_ID = "skill-edit-modal";
let _onSave = null;

function buildModal() {
  const overlay = document.createElement("div");
  overlay.id = MODAL_ID;
  overlay.className = "modal-overlay hidden";
  overlay.innerHTML = `
    <div class="modal skill-edit-modal" style="width:600px;max-width:95vw">
      <div class="modal-header">
        <div>
          <div style="font-size:11px;color:var(--ink-5);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Skills library</div>
          <h3 id="skill-modal-title" style="margin:0">New skill</h3>
        </div>
        <button id="skill-modal-close" class="modal-close" aria-label="Close">&times;</button>
      </div>
      <div id="skill-modal-status" style="min-height:20px;margin-bottom:8px;font-size:13px"></div>

      <section id="skill-modal-template-step" style="display:none">
        <div style="font-size:13px;color:var(--ink-4);margin-bottom:10px">
          Pick a starter shape. You can edit everything in the next step.
        </div>
        <div id="skill-modal-template-list" style="display:flex;flex-direction:column;gap:8px;max-height:340px;overflow:auto"></div>
        <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;padding:16px 0 0;border-top:1px solid var(--border-1);margin-top:16px">
          <button id="skill-modal-template-cancel" class="btn btn-ghost">Cancel</button>
        </div>
      </section>

      <section id="skill-modal-form-step">
      <div style="display:flex;flex-direction:column;gap:14px">
        <label class="modal-field">
          <span class="modal-label">Name <span style="color:var(--accent)">*</span></span>
          <input id="skill-modal-name" type="text" class="modal-input" placeholder="e.g. Summarize thread" maxlength="120">
          <span class="modal-help">The display name. The slug (folder name) is derived from this — it stays fixed once saved.</span>
        </label>
        <label class="modal-field">
          <span class="modal-label">Description</span>
          <input id="skill-modal-description" type="text" class="modal-input" placeholder="One-line summary of what this skill does" maxlength="300">
          <span class="modal-help">Shown in the library and used by the assistant to decide when to apply this skill.</span>
        </label>
        <label class="modal-field">
          <span class="modal-label">Category</span>
          <div style="display:flex;gap:8px">
            <input id="skill-modal-category" type="text" class="modal-input" placeholder="e.g. communication, research, writing" list="skill-category-list" style="flex:1">
            <datalist id="skill-category-list"></datalist>
          </div>
          <span class="modal-help">Free-text. Pick an existing category or type a new one.</span>
        </label>
        <div class="modal-field">
          <span class="modal-label">Scope</span>
          <div style="display:flex;gap:16px;margin-top:4px">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="radio" name="skill-modal-scope" value="global" checked> Global
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="radio" name="skill-modal-scope" value="agent"> Agent-only
            </label>
          </div>
          <span class="modal-help"><strong>Global</strong> — assistant + every agent can use it. <strong>Agent-only</strong> — must be explicitly assigned to an agent.</span>
        </div>
        <div class="modal-field">
          <span class="modal-label">Provider compatibility</span>
          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" class="skill-modal-compat" value="all" checked> All providers
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" class="skill-modal-compat" value="claude"> Claude
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" class="skill-modal-compat" value="codex"> Codex
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" class="skill-modal-compat" value="gemini"> Gemini
            </label>
          </div>
          <span class="modal-help">The skill is only injected for the providers you select. Default <em>All</em> works for most cases.</span>
        </div>
        <label class="modal-field">
          <span class="modal-label">When to use (trigger hint)</span>
          <textarea id="skill-modal-when-to-use" class="modal-input" rows="2" placeholder="Describe the situation that should trigger this skill…" style="resize:vertical"></textarea>
          <span class="modal-help">One sentence the assistant reads to decide whether to apply this skill. Be specific about the situation.</span>
        </label>
        <label class="modal-field">
          <span class="modal-label">Skill body</span>
          <textarea id="skill-modal-body" class="modal-input" rows="10" placeholder="Write the skill instructions here. Markdown is supported." style="resize:vertical;font-family:var(--font-mono,monospace);font-size:12px"></textarea>
          <span class="modal-help">Saved as <code>SKILL.md</code> in <code>~/.artemis/skills/&lt;slug&gt;/</code>. Drop additional <code>.md</code> files in that folder later — they're appended in alphabetical order.</span>
        </label>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;padding:16px 0 0;border-top:1px solid var(--border-1);margin-top:16px">
        <button id="skill-modal-cancel" class="btn btn-ghost">Cancel</button>
        <button id="skill-modal-save" class="btn btn-primary">Save skill</button>
      </div>
      </section>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function getEls() {
  return {
    overlay: document.getElementById(MODAL_ID),
    title: document.getElementById("skill-modal-title"),
    status: document.getElementById("skill-modal-status"),
    templateStep: document.getElementById("skill-modal-template-step"),
    templateList: document.getElementById("skill-modal-template-list"),
    templateCancel: document.getElementById("skill-modal-template-cancel"),
    formStep: document.getElementById("skill-modal-form-step"),
    name: document.getElementById("skill-modal-name"),
    description: document.getElementById("skill-modal-description"),
    category: document.getElementById("skill-modal-category"),
    categoryList: document.getElementById("skill-category-list"),
    whenToUse: document.getElementById("skill-modal-when-to-use"),
    body: document.getElementById("skill-modal-body"),
    saveBtn: document.getElementById("skill-modal-save"),
    closeBtn: document.getElementById("skill-modal-close"),
    cancelBtn: document.getElementById("skill-modal-cancel"),
  };
}

function getScopeValue() {
  return document.querySelector('input[name="skill-modal-scope"]:checked')?.value || "global";
}

function getCompatValue() {
  const checked = [...document.querySelectorAll(".skill-modal-compat:checked")].map((el) => el.value);
  if (!checked.length || checked.includes("all")) return ["all"];
  return checked;
}

function setStatus(el, text, type = "") {
  if (!el) return;
  el.textContent = text;
  el.style.color = type === "error" ? "var(--error, #e53e3e)" : type === "ok" ? "var(--success, #38a169)" : "var(--ink-5)";
}

async function populateCategories(el) {
  try {
    const cats = await api.fetchSkillCategories();
    if (!el) return;
    el.innerHTML = cats.map((c) => `<option value="${c.category}">`).join("");
  } catch { /* non-fatal */ }
}

function closeModal() {
  const overlay = document.getElementById(MODAL_ID);
  if (overlay) overlay.classList.add("hidden");
  _onSave = null;
}

async function handleSave(skill) {
  const els = getEls();
  const name = els.name?.value.trim() || "";
  if (!name) {
    setStatus(els.status, "Name is required.", "error");
    els.name?.focus();
    return;
  }

  setStatus(els.status, "Saving…");
  if (els.saveBtn) els.saveBtn.disabled = true;

  const payload = {
    name,
    description: els.description?.value.trim() || "",
    category: els.category?.value.trim() || "",
    scope: getScopeValue(),
    provider_compat: getCompatValue(),
    when_to_use: els.whenToUse?.value.trim() || "",
    body: els.body?.value || "",
  };

  try {
    let saved;
    if (skill?.id) {
      saved = await api.updateSkillApi(skill.id, payload);
    } else {
      saved = await api.createSkillApi(payload);
    }
    if (saved?.error) {
      setStatus(els.status, saved.error, "error");
      if (els.saveBtn) els.saveBtn.disabled = false;
      return;
    }
    setStatus(els.status, "Saved.", "ok");
    setTimeout(() => {
      closeModal();
      _onSave?.(saved);
    }, 300);
  } catch (err) {
    setStatus(els.status, err?.message || "Save failed.", "error");
    if (els.saveBtn) els.saveBtn.disabled = false;
  }
}

function applyScope(scope) {
  document.querySelectorAll('input[name="skill-modal-scope"]').forEach((el) => {
    el.checked = el.value === scope;
  });
}

function applyCompat(compat) {
  document.querySelectorAll(".skill-modal-compat").forEach((el) => {
    el.checked = compat.includes(el.value);
  });
  const allBox = document.querySelector('.skill-modal-compat[value="all"]');
  if (allBox?.checked) {
    document.querySelectorAll(".skill-modal-compat:not([value='all'])").forEach((el) => { el.checked = false; });
  }
}

function fillFromSeed(seed) {
  const els = getEls();
  if (els.name) els.name.value = seed.name || "";
  if (els.description) els.description.value = seed.description || "";
  if (els.category) els.category.value = seed.category || "";
  if (els.whenToUse) els.whenToUse.value = seed.when_to_use || "";
  if (els.body) els.body.value = seed.body || "";
  applyScope(seed.scope || "global");
  applyCompat(Array.isArray(seed.provider_compat) && seed.provider_compat.length ? seed.provider_compat : ["all"]);
}

function showFormStep() {
  const els = getEls();
  if (els.templateStep) els.templateStep.style.display = "none";
  if (els.formStep) els.formStep.style.display = "";
  els.name?.focus();
}

async function showTemplateStep() {
  const els = getEls();
  if (els.formStep) els.formStep.style.display = "none";
  if (els.templateStep) els.templateStep.style.display = "";
  if (els.title) els.title.textContent = "Start a new skill";
  if (!els.templateList) return;

  els.templateList.innerHTML = `<div style="font-size:13px;color:var(--ink-5);padding:6px 2px">Loading templates…</div>`;
  let templates = [];
  try { templates = await api.fetchSkillTemplates(); } catch { /* fall through */ }

  if (!templates.length) {
    els.templateList.innerHTML = "";
    fillFromSeed({});
    showFormStep();
    return;
  }

  els.templateList.innerHTML = templates.map((t) => `
    <button type="button" class="skill-template-pick" data-template-id="${t.id}"
      style="text-align:left;padding:12px 14px;border:1px solid var(--border-1);border-radius:10px;background:var(--bg-2,transparent);cursor:pointer;display:flex;flex-direction:column;gap:4px">
      <span style="font-weight:600;font-size:14px">${t.label}</span>
      <span style="font-size:12px;color:var(--ink-5)">${t.summary}</span>
    </button>
  `).join("");

  els.templateList.querySelectorAll(".skill-template-pick").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.templateId;
      const tpl = templates.find((t) => t.id === id);
      fillFromSeed(tpl?.seed || {});
      showFormStep();
    });
    btn.addEventListener("mouseenter", () => { btn.style.borderColor = "var(--accent)"; });
    btn.addEventListener("mouseleave", () => { btn.style.borderColor = "var(--border-1)"; });
  });
}

export function openSkillEditModal(skill, onSave) {
  if (!document.getElementById(MODAL_ID)) buildModal();
  const els = getEls();
  _onSave = onSave || null;

  setStatus(els.status, "");
  if (els.saveBtn) els.saveBtn.disabled = false;

  const isNew = !skill?.id;

  // Populate fields (or reset)
  if (els.name) els.name.value = skill?.name || "";
  if (els.description) els.description.value = skill?.description || "";
  if (els.category) els.category.value = skill?.category || "";
  if (els.whenToUse) els.whenToUse.value = skill?.when_to_use || "";
  if (els.body) els.body.value = "";
  applyScope(skill?.scope || "global");
  const compat = (() => {
    try { return JSON.parse(skill?.provider_compat || '["all"]'); } catch { return ["all"]; }
  })();
  applyCompat(compat);

  // Load current body from API if editing
  if (skill?.id) {
    api.fetchSkill(skill.id).catch(() => {});
  }

  populateCategories(els.categoryList);

  // Handlers (attach once)
  const onClose = () => closeModal();
  els.closeBtn?.addEventListener("click", onClose, { once: true });
  els.cancelBtn?.addEventListener("click", onClose, { once: true });
  els.templateCancel?.addEventListener("click", onClose, { once: true });
  els.saveBtn?.addEventListener("click", () => handleSave(skill), { once: false });

  document.querySelector('.skill-modal-compat[value="all"]')?.addEventListener("change", (e) => {
    if (e.target.checked) {
      document.querySelectorAll(".skill-modal-compat:not([value='all'])").forEach((el) => { el.checked = false; });
    }
  });
  document.querySelectorAll(".skill-modal-compat:not([value='all'])").forEach((el) => {
    el.addEventListener("change", () => {
      const allCb = document.querySelector('.skill-modal-compat[value="all"]');
      if (allCb) allCb.checked = false;
    });
  });

  els.overlay?.addEventListener("click", (e) => {
    if (e.target === els.overlay) closeModal();
  }, { once: true });

  els.overlay?.classList.remove("hidden");

  if (isNew) {
    showTemplateStep();
  } else {
    if (els.title) els.title.textContent = "Edit skill";
    showFormStep();
  }
}
