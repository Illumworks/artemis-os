/**
 * sub-pipeline-form.js — PIPE3
 * Typed config form for sub_pipeline nodes.
 * Fields: target pipeline picker (excludes self), mode, pass-through inputs note.
 */

const MODES = [
  { value: "inline",               label: "Inline (same execution context)" },
  { value: "async_fire_and_forget", label: "Async fire-and-forget" },
];

// ── Pipeline cache ────────────────────────────────────────────────────────────

let _pipelineCache = null;

async function _fetchPipelines() {
  if (_pipelineCache && Date.now() - _pipelineCache.ts < 30_000) return _pipelineCache.pipelines;
  try {
    const res = await fetch("/api/pipelines");
    if (!res.ok) return [];
    const data = await res.json();
    const pipelines = Array.isArray(data) ? data : (data.pipelines ?? data.items ?? []);
    _pipelineCache = { ts: Date.now(), pipelines };
    return pipelines;
  } catch {
    return [];
  }
}

// ── Render ───────────────────────────────────────────────────────────────────

export function renderSubPipelineForm(config, container, { currentPipelineId = null } = {}) {
  const cfg = config ?? {};
  const pipelineId = cfg.pipeline_id ?? "";
  const mode = cfg.mode ?? "inline";

  container.innerHTML = `
    <div class="ncf ncf-subpipe">
      <div class="ncf-field">
        <label class="ncf-label ncf-required">Target pipeline</label>
        <div class="ncf-picker" data-ncf-picker="pipeline">
          <input class="ncf-search" type="text" placeholder="Search pipelines…"
            autocomplete="off" value="${_esc(pipelineId)}">
          <div class="ncf-picker-results" hidden></div>
          <input type="hidden" class="ncf-pipeline-id" value="${_esc(pipelineId)}">
        </div>
        <div class="ncf-hint">Cannot reference itself. Excludes archived pipelines.</div>
      </div>

      <div class="ncf-field">
        <label class="ncf-label">Execution mode</label>
        <select class="ncf-select ncf-mode">
          ${MODES.map(
            (m) => `<option value="${m.value}"${m.value === mode ? " selected" : ""}>${m.label}</option>`
          ).join("")}
        </select>
      </div>

      <div class="ncf-field ncf-passthrough-note">
        <label class="ncf-label">Pass-through inputs</label>
        <div class="ncf-static-note">
          Inputs pass through via signal_queue / shared context.
          Data-shape mapping comes in PIPE4.
        </div>
      </div>
    </div>
  `;

  // Wire pipeline picker
  const pickerWrap = container.querySelector("[data-ncf-picker='pipeline']");
  const searchEl = pickerWrap.querySelector(".ncf-search");
  const resultsEl = pickerWrap.querySelector(".ncf-picker-results");
  const hiddenEl = pickerWrap.querySelector(".ncf-pipeline-id");

  let _allPipelines = [];
  _fetchPipelines().then((pipelines) => {
    // Exclude self
    _allPipelines = pipelines.filter(
      (p) => (p.pipeline_id ?? p.id) !== currentPipelineId
    );
    // Pre-populate search label
    const found = _allPipelines.find((p) => (p.pipeline_id ?? p.id) === pipelineId);
    if (found) searchEl.value = found.name ?? found.pipeline_id ?? pipelineId;
    _renderResults("");
  });

  function _renderResults(q) {
    const matches = q
      ? _allPipelines.filter(
          (p) =>
            (p.name ?? "").toLowerCase().includes(q.toLowerCase()) ||
            (p.pipeline_id ?? p.id ?? "").toLowerCase().includes(q.toLowerCase())
        )
      : _allPipelines;
    if (!matches.length) {
      resultsEl.innerHTML = `<div class="ncf-picker-empty">No pipelines found</div>`;
    } else {
      resultsEl.innerHTML = matches
        .slice(0, 20)
        .map((p) => {
          const id = p.pipeline_id ?? p.id ?? "";
          const name = p.name ?? id;
          const nodeCount = p.node_count ?? (Array.isArray(p.nodes) ? p.nodes.length : null);
          const sub = nodeCount != null ? `${nodeCount} nodes` : id;
          return `<button type="button" class="ncf-picker-item" data-id="${_esc(id)}"
              data-name="${_esc(name)}">
              <span class="ncf-picker-name">${_esc(name)}</span>
              <span class="ncf-picker-sub">${_esc(sub)}</span>
            </button>`;
        })
        .join("");
    }
  }

  searchEl.addEventListener("focus", () => {
    _renderResults(searchEl.value);
    resultsEl.hidden = false;
  });
  searchEl.addEventListener("input", () => {
    hiddenEl.value = searchEl.value;
    _renderResults(searchEl.value);
    resultsEl.hidden = false;
  });
  searchEl.addEventListener("blur", () => {
    setTimeout(() => { resultsEl.hidden = true; }, 150);
  });

  resultsEl.addEventListener("click", (e) => {
    const item = e.target.closest(".ncf-picker-item");
    if (!item) return;
    hiddenEl.value = item.dataset.id;
    searchEl.value = item.dataset.name || item.dataset.id;
    resultsEl.hidden = true;
  });

  return {
    getValues() {
      return {
        pipeline_id: container.querySelector(".ncf-pipeline-id")?.value ?? "",
        mode: container.querySelector(".ncf-mode")?.value ?? "inline",
      };
    },
    validate() {
      const id = container.querySelector(".ncf-pipeline-id")?.value ?? "";
      if (!id.trim()) return "Target pipeline is required.";
      if (id === currentPipelineId) return "Cannot reference the current pipeline (no self-loops).";
      return null;
    },
  };
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
