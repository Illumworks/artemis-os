/**
 * pipeline-ai-panel.js — Pipeline AI Assistant panel (inline canvas AI).
 *
 * Mounts as a collapsible right-side panel inside the pipeline canvas.
 * Communicates with backend via SSE (POST /api/pipelines/{id}/assistant/turn).
 * Parses PROPOSAL_BEGIN…PROPOSAL_END tokens from the stream and renders ghost
 * nodes/edges on the canvas for Accept/Reject.
 *
 * See: artemis/pipelines/assistant/ for the backend.
 * Parallel pattern: public/js/components/agent-builder-panel.js (O1).
 */

// No api.js import needed — this component uses fetch directly for the
// assistant endpoints which aren't in the shared api.js helper layer.

// ── Constants ─────────────────────────────────────────────────────────────────

const PROPOSAL_RE = /PROPOSAL_BEGIN\s+(\{[\s\S]*?\})\s+PROPOSAL_END/g;
const PANEL_WIDTH = 340;

// ── PipelineAIPanel ───────────────────────────────────────────────────────────

export class PipelineAIPanel {
  /**
   * @param {object} opts
   * @param {string} opts.pipelineId
   * @param {Function} opts.onProposalAccept  — called with (proposal, updatedNodes, updatedEdges)
   * @param {Function} opts.getCanvasState    — returns {nodes, edges} from canvas
   * @param {Function} [opts.onToggle]        — called with (isOpen) when panel opens/closes
   */
  constructor({ pipelineId, onProposalAccept, getCanvasState, onToggle }) {
    this._pipelineId = pipelineId;
    this._onProposalAccept = onProposalAccept;
    this._getCanvasState = getCanvasState;
    this._onToggle = onToggle || null;

    this._isOpen = false;
    this._isStreaming = false;
    this._conversation = []; // [{role, content, proposals?}]
    this._pendingProposals = new Map(); // proposalId → proposal object
    this._isFirstTurn = true;
    this.el = null;
    this._msgListEl = null;
    this._inputEl = null;
    this._sendBtn = null;
    this._abortController = null;
  }

  // ── Lifecycle ────────────────────────────────────────────────────────────────

  mount(container) {
    this.el = document.createElement("div");
    this.el.className = "pai-panel pai-panel--closed";
    this.el.innerHTML = `
      <div class="pai-header">
        <span class="pai-header-icon">✦</span>
        <span class="pai-header-title">AI Assistant</span>
        <button class="pai-header-clear" title="Clear conversation">↺</button>
        <button class="pai-header-close" title="Close panel">✕</button>
      </div>
      <div class="pai-body">
        <div class="pai-messages" role="log" aria-live="polite"></div>
        <div class="pai-input-row">
          <textarea class="pai-input" rows="2"
            placeholder="Ask AI…  (e.g. 'What does this pipeline do?')"
            aria-label="Ask the pipeline AI assistant"></textarea>
          <button class="pai-send pbtn pbtn-p" aria-label="Send">Send</button>
        </div>
      </div>
    `;
    container.appendChild(this.el);

    this._msgListEl = this.el.querySelector(".pai-messages");
    this._inputEl   = this.el.querySelector(".pai-input");
    this._sendBtn   = this.el.querySelector(".pai-send");

    this._wire();
    this._loadHistory();
  }

  destroy() {
    this._abortController?.abort();
    if (this.el) this.el.remove();
  }

  open() {
    this._isOpen = true;
    this.el.classList.remove("pai-panel--closed");
    this.el.classList.add("pai-panel--open");
    this._inputEl?.focus();
    if (this._onToggle) this._onToggle(true);
  }

  close() {
    this._isOpen = false;
    this.el.classList.remove("pai-panel--open");
    this.el.classList.add("pai-panel--closed");
    if (this._onToggle) this._onToggle(false);
  }

  toggle() {
    this._isOpen ? this.close() : this.open();
  }

  isOpen() { return this._isOpen; }

  // ── Wiring ────────────────────────────────────────────────────────────────

  _wire() {
    this.el.querySelector(".pai-header-close")?.addEventListener("click", () => this.close());
    this.el.querySelector(".pai-header-clear")?.addEventListener("click", () => this._clearConversation());

    this._sendBtn.addEventListener("click", () => this._sendMessage());
    this._inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this._sendMessage();
      }
    });

    // Delegate accept/reject clicks in message list
    this._msgListEl.addEventListener("click", (e) => {
      const acceptBtn = e.target.closest("[data-action='accept']");
      const rejectBtn = e.target.closest("[data-action='reject']");
      if (acceptBtn) {
        const pid = acceptBtn.dataset.proposalId;
        this._acceptProposal(pid, acceptBtn.closest(".pai-proposal"));
      } else if (rejectBtn) {
        const pid = rejectBtn.dataset.proposalId;
        this._rejectProposal(pid, rejectBtn.closest(".pai-proposal"));
      }
    });
  }

  // ── History loading ───────────────────────────────────────────────────────

  async _loadHistory() {
    try {
      const r = await fetch(
        `/api/pipelines/${this._pipelineId}/assistant/conversation`,
        { credentials: "include" }
      );
      if (!r.ok) return;
      const resp = await r.json();
      const msgs = resp?.conversation || [];
      if (msgs.length > 0) {
        this._isFirstTurn = false;
        for (const m of msgs) {
          this._appendMessage(m.role, m.content, { noProposals: true });
        }
      }
    } catch (_) {
      // Silently ignore — conversation is optional
    }
  }

  // ── Send ──────────────────────────────────────────────────────────────────

  async _sendMessage() {
    const text = this._inputEl.value.trim();
    if (!text || this._isStreaming) return;

    this._inputEl.value = "";
    this._appendMessage("user", text);

    this._isStreaming = true;
    this._sendBtn.disabled = true;
    this._sendBtn.textContent = "…";

    const assistantMsgEl = this._appendMessage("assistant", "", { streaming: true });
    let fullText = "";

    this._abortController = new AbortController();

    try {
      const response = await fetch(
        `/api/pipelines/${this._pipelineId}/assistant/turn`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, is_first_turn: this._isFirstTurn }),
          signal: this._abortController.signal,
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          if (!part.trim()) continue;
          const eventLine = part.split("\n").find((l) => l.startsWith("event: "));
          const dataLine  = part.split("\n").find((l) => l.startsWith("data: "));
          if (!eventLine || !dataLine) continue;
          const evType = eventLine.slice(7).trim();
          let payload;
          try { payload = JSON.parse(dataLine.slice(6)); } catch { continue; }
          this._handleSseEvent(evType, payload, assistantMsgEl, (t) => { fullText += t; });
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        this._appendErrMsg(`Error: ${err.message}`);
      }
    } finally {
      this._isStreaming = false;
      this._sendBtn.disabled = false;
      this._sendBtn.textContent = "Send";
      this._isFirstTurn = false;
      // Remove streaming indicator
      assistantMsgEl?.classList.remove("pai-msg--streaming");
    }
  }

  _handleSseEvent(evType, payload, assistantMsgEl, onToken) {
    switch (evType) {
      case "assistant_token": {
        const delta = payload.delta || "";
        onToken(delta);
        // Strip proposal sentinels from visible text
        const visibleDelta = delta
          .replace(/PROPOSAL_BEGIN[\s\S]*?PROPOSAL_END/g, "")
          .replace(/PROPOSAL_BEGIN[\s\S]*/g, ""); // partial
        if (visibleDelta && assistantMsgEl) {
          const textEl = assistantMsgEl.querySelector(".pai-msg-text");
          if (textEl) textEl.textContent += visibleDelta;
        }
        break;
      }
      case "proposal_parsed": {
        if (payload && payload.kind) {
          this._renderProposal(payload, assistantMsgEl);
        }
        break;
      }
      case "self_improvement": {
        this._renderSelfImprovementHint(payload);
        break;
      }
      case "error": {
        this._appendErrMsg(payload.message || "Unknown error");
        break;
      }
      case "heartbeat":
      case "turn_start":
      case "turn_complete":
        break;
    }
  }

  // ── Message rendering ─────────────────────────────────────────────────────

  _appendMessage(role, content, opts = {}) {
    const el = document.createElement("div");
    el.className = `pai-msg pai-msg--${role}${opts.streaming ? " pai-msg--streaming" : ""}`;
    if (role === "assistant") {
      el.innerHTML = `<span class="pai-msg-icon">✦</span>
        <div class="pai-msg-body"><div class="pai-msg-text">${opts.noProposals ? _esc(content) : ""}</div></div>`;
    } else {
      el.innerHTML = `<div class="pai-msg-body"><div class="pai-msg-text">${_esc(content)}</div></div>`;
    }
    this._msgListEl.appendChild(el);
    this._scrollToBottom();
    return el;
  }

  _appendErrMsg(msg) {
    const el = document.createElement("div");
    el.className = "pai-msg pai-msg--error";
    el.innerHTML = `<div class="pai-msg-body pai-msg-err">${_esc(msg)}</div>`;
    this._msgListEl.appendChild(el);
    this._scrollToBottom();
  }

  _renderProposal(proposal, parentMsgEl) {
    const pid = proposal.id || `prop_${Date.now()}`;
    this._pendingProposals.set(pid, { ...proposal, id: pid });

    const el = document.createElement("div");
    el.className = "pai-proposal";
    el.dataset.proposalId = pid;
    el.innerHTML = `
      <div class="pai-proposal-kind">${_esc(proposal.kind.replace(/_/g, " "))}</div>
      <div class="pai-proposal-explanation">${_esc(proposal.explanation || "")}</div>
      <div class="pai-proposal-actions">
        <button class="pbtn pbtn-p pai-proposal-accept" data-action="accept" data-proposal-id="${_esc(pid)}">Accept</button>
        <button class="pbtn pbtn-g pai-proposal-reject" data-action="reject" data-proposal-id="${_esc(pid)}">Reject</button>
      </div>
    `;

    const body = parentMsgEl?.querySelector(".pai-msg-body");
    if (body) body.appendChild(el);
    else this._msgListEl.appendChild(el);
    this._scrollToBottom();
  }

  _renderSelfImprovementHint(hint) {
    const el = document.createElement("div");
    el.className = "pai-self-improvement";
    el.innerHTML = `
      <span class="pai-si-icon">⚡</span>
      <div class="pai-si-body">
        <div class="pai-si-label">Self-improvement suggestion</div>
        <div class="pai-si-text">${_esc(hint.suggestion || "")}</div>
      </div>
    `;
    this._msgListEl.appendChild(el);
    this._scrollToBottom();
  }

  // ── Proposal Accept / Reject ───────────────────────────────────────────────

  _acceptProposal(proposalId, proposalEl) {
    const proposal = this._pendingProposals.get(proposalId);
    if (!proposal) return;

    const { nodes, edges } = this._getCanvasState();

    let updatedNodes = nodes;
    let updatedEdges = edges;
    try {
      [updatedNodes, updatedEdges] = _applyProposal(proposal, nodes, edges);
    } catch (err) {
      this._appendErrMsg(`Cannot apply: ${err.message}`);
      return;
    }

    this._pendingProposals.delete(proposalId);
    if (proposalEl) {
      proposalEl.classList.add("pai-proposal--accepted");
      proposalEl.querySelector(".pai-proposal-actions").innerHTML =
        '<span class="pai-proposal-status">Accepted</span>';
    }

    if (this._onProposalAccept) {
      this._onProposalAccept(proposal, updatedNodes, updatedEdges);
    }
  }

  _rejectProposal(proposalId, proposalEl) {
    this._pendingProposals.delete(proposalId);
    if (proposalEl) {
      proposalEl.classList.add("pai-proposal--rejected");
      proposalEl.querySelector(".pai-proposal-actions").innerHTML =
        '<span class="pai-proposal-status">Rejected</span>';
    }
  }

  // ── Clear conversation ─────────────────────────────────────────────────────

  async _clearConversation() {
    this._msgListEl.innerHTML = "";
    this._pendingProposals.clear();
    this._isFirstTurn = true;
    try {
      await fetch(`/api/pipelines/${this._pipelineId}/assistant/conversation`, {
        method: "DELETE",
        credentials: "include",
      });
    } catch (_) { /* ignore */ }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  _scrollToBottom() {
    if (this._msgListEl) {
      this._msgListEl.scrollTop = this._msgListEl.scrollHeight;
    }
  }
}

// ── Client-side proposal apply (mirrors Python apply_proposal) ────────────────

function _applyProposal(proposal, nodes, edges) {
  nodes = nodes.map((n) => ({ ...n }));
  edges = edges.map((e) => ({ ...e }));
  const nodeIndex = new Map(nodes.map((n, i) => [n.id, i]));

  switch (proposal.kind) {
    case "add_node": {
      const nn = { ...proposal.payload.node };
      if (!nn.id) nn.id = `node_${Date.now().toString(36)}`;
      if (!nn.position) nn.position = { x: 400, y: 200 };
      nodes.push(nn);
      break;
    }
    case "remove_node": {
      const nid = proposal.payload.node_id;
      if (!nodeIndex.has(nid)) throw new Error(`Node '${nid}' not found`);
      nodes = nodes.filter((n) => n.id !== nid);
      edges = edges.filter((e) => e.source_node_id !== nid && e.target_node_id !== nid);
      break;
    }
    case "add_edge": {
      const { source_node_id: src, target_node_id: tgt, condition } = proposal.payload;
      if (!nodeIndex.has(src)) throw new Error(`Source node '${src}' not found`);
      if (!nodeIndex.has(tgt)) throw new Error(`Target node '${tgt}' not found`);
      const dup = edges.some((e) => e.source_node_id === src && e.target_node_id === tgt);
      if (!dup) {
        edges.push({
          id: `edge_${Date.now().toString(36)}`,
          source_node_id: src,
          target_node_id: tgt,
          condition: condition ?? null,
          data_shape: null,
        });
      }
      break;
    }
    case "remove_edge": {
      const { source_node_id: src, target_node_id: tgt } = proposal.payload;
      edges = edges.filter((e) => !(e.source_node_id === src && e.target_node_id === tgt));
      break;
    }
    case "update_node_config": {
      const nid = proposal.payload.node_id;
      if (!nodeIndex.has(nid)) throw new Error(`Node '${nid}' not found`);
      const idx = nodeIndex.get(nid);
      nodes[idx] = {
        ...nodes[idx],
        config: { ...(nodes[idx].config || {}), ...proposal.payload.config_patch },
      };
      break;
    }
    default:
      throw new Error(`Unknown proposal kind: ${proposal.kind}`);
  }

  return [nodes, edges];
}

// ── Escape helper ─────────────────────────────────────────────────────────────

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
