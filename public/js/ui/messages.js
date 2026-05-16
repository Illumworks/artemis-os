// Message rendering
import { escapeHtml, getToolDetail, scrollToBottom } from '../core/utils.js';
import { renderMarkdown, highlightCodeBlocks, addCopyButtons, renderMermaidBlocks } from './formatting.js';
import { renderDiffView, renderAdditionsView } from './diff.js';
import { getState, setState } from '../core/store.js';
import { $ } from '../core/dom.js';
import { getPane } from './parallel.js';

export function showChatEmptyState(pane) {
  pane = pane || getPane(null);
  removeChatEmptyState(pane);
  const el = document.createElement("div");
  el.className = "artemis-chat-empty";
  el.innerHTML = `
    <div class="artemis-chat-empty-card">
      <div class="artemis-chat-empty-eyebrow">Chat workspace</div>
      <div class="artemis-chat-empty-topline">
        <img src="/icons/artemis-mark.png" alt="Artemis" draggable="false">
        <div class="artemis-chat-empty-copy">
          <div class="artemis-chat-empty-text">Start with a question, a plan, or a repo task.</div>
          <div class="artemis-chat-empty-hint">Dashboard is for triage. Chat is where the real work happens.</div>
        </div>
      </div>
      <div class="artemis-chat-empty-meta">
        <span>Use the composer below to keep work moving.</span>
        <span>Files, Git, and Memory stay available when you need them.</span>
      </div>
    </div>
  `;
  pane.messagesDiv.appendChild(el);
}

export function removeChatEmptyState(pane) {
  pane = pane || getPane(null);
  const existing = pane.messagesDiv.querySelector(".artemis-chat-empty");
  if (existing) existing.remove();
}

function getOrCreateTurn(pane) {
  if (!pane.currentTurn) {
    const turn = document.createElement("div");
    turn.className = "msg-assistant-turn";
    turn.dataset.turnLabel = "ARTEMIS";
    pane.messagesDiv.appendChild(turn);
    pane.currentTurn = turn;
  }
  return pane.currentTurn;
}

function updateTurnChrome(turn) {
  if (!turn) return;

  const textLength = [...turn.querySelectorAll(".text-content")]
    .reduce((total, node) => total + (node.dataset.raw || node.textContent || "").length, 0);
  const stepCount = turn.querySelectorAll(".tool-indicator, .cli-output, .diff-view, .additions-view").length;
  const hasAssistantText = Boolean(turn.querySelector(".msg-assistant"));
  const isLongTurn = textLength >= 1400 || stepCount >= 4;

  const labelParts = ["ARTEMIS"];
  if (stepCount > 0) {
    labelParts.push(`${stepCount} step${stepCount === 1 ? "" : "s"}`);
  } else if (hasAssistantText) {
    labelParts.push(isLongTurn ? "Long response" : "Response");
  }

  turn.dataset.turnLabel = labelParts.join(" · ");
  turn.dataset.turnSteps = String(stepCount);
  turn.classList.toggle("turn-has-tools", stepCount > 0);
  turn.classList.toggle("turn-response-only", hasAssistantText && stepCount === 0);
  turn.classList.toggle("turn-long", isLongTurn);
}

export function addUserMessage(text, pane, images = [], filePaths = []) {
  pane = pane || getPane(null);
  removeChatEmptyState(pane);
  pane.currentAssistantMsg = null;
  pane.currentTurn = null;
  const div = document.createElement("div");
  div.className = "msg msg-user";

  const label = document.createElement("span");
  label.className = "msg-user-label";
  label.textContent = "YOU";

  div.appendChild(label);

  if (filePaths && filePaths.length > 0) {
    const filesDiv = document.createElement("div");
    filesDiv.className = "msg-user-files";
    for (const fp of filePaths) {
      const fileTag = document.createElement("span");
      fileTag.className = "msg-user-file-tag";
      fileTag.textContent = fp;
      fileTag.title = fp;
      filesDiv.appendChild(fileTag);
    }
    div.appendChild(filesDiv);
  }

  const body = document.createElement("span");
  body.className = "msg-user-body";
  body.textContent = text;

  div.appendChild(body);

  if (images && images.length > 0) {
    renderChatImages(images, div);
  }

  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane, { force: true });
}

function renderChatImages(images, container) {
  const strip = document.createElement("div");
  strip.className = "chat-image-strip";

  for (const img of images) {
    const imgEl = document.createElement("img");
    imgEl.className = "chat-image-thumb";
    imgEl.src = `data:${img.mimeType};base64,${img.data}`;
    imgEl.alt = img.name || "attached image";
    imgEl.title = img.name || "attached image";
    imgEl.addEventListener("click", () => {
      const overlay = document.createElement("div");
      overlay.className = "chat-image-overlay";
      const fullImg = document.createElement("img");
      fullImg.src = imgEl.src;
      overlay.appendChild(fullImg);
      overlay.addEventListener("click", () => overlay.remove());
      document.body.appendChild(overlay);
    });
    strip.appendChild(imgEl);
  }

  container.appendChild(strip);
}

export function appendAssistantText(text, pane) {
  pane = pane || getPane(null);
  if (!pane.currentAssistantMsg) {
    const turn = getOrCreateTurn(pane);
    const div = document.createElement("div");
    div.className = "msg msg-assistant";
    const content = document.createElement("div");
    content.className = "text-content";
    div.appendChild(content);
    turn.appendChild(div);
    pane.currentAssistantMsg = content;
  }
  pane.currentAssistantMsg.innerHTML = renderMarkdown(
    (pane.currentAssistantMsg.dataset.raw || "") + text
  );
  pane.currentAssistantMsg.dataset.raw =
    (pane.currentAssistantMsg.dataset.raw || "") + text;
  highlightCodeBlocks(pane.currentAssistantMsg);
  addCopyButtons(pane.currentAssistantMsg);
  renderMermaidBlocks(pane.currentAssistantMsg);
  updateTurnChrome(pane.currentTurn || pane.currentAssistantMsg.closest(".msg-assistant-turn"));
  scrollToBottom(pane);

  // Update streaming token counter
  let count = getState("streamingCharCount") + text.length;
  setState("streamingCharCount", count);
  const tokenEst = Math.round(count / 4);
  if ($.streamingTokens) {
    if ($.streamingTokensValue) $.streamingTokensValue.textContent = `~${tokenEst} tokens`;
    $.streamingTokens.classList.remove("hidden");
    if ($.streamingTokensSep) $.streamingTokensSep.classList.remove("hidden");
  }
}

export function appendToolIndicator(name, input, pane, toolId, isLive = true) {
  pane = pane || getPane(null);
  const div = document.createElement("div");
  div.className = "msg";

  // Diff view for Edit tool
  if (name === "Edit" && input && input.old_string != null && input.new_string != null) {
    const diffEl = renderDiffView(input.old_string, input.new_string, input.file_path);
    if (diffEl) {
      div.appendChild(diffEl);
      const turn = getOrCreateTurn(pane);
      turn.appendChild(div);
      pane.currentAssistantMsg = null;
      updateTurnChrome(turn);
      scrollToBottom(pane);
      return;
    }
  }

  // Additions view for Write tool
  if (name === "Write" && input && input.content != null) {
    const addEl = renderAdditionsView(input.content, input.file_path);
    if (addEl) {
      div.appendChild(addEl);
      const turn = getOrCreateTurn(pane);
      turn.appendChild(div);
      pane.currentAssistantMsg = null;
      updateTurnChrome(turn);
      scrollToBottom(pane);
      return;
    }
  }

  // Default tool indicator — show spinner only for live streaming tools
  const indicator = document.createElement("div");
  indicator.className = isLive ? "tool-indicator tool-running" : "tool-indicator";
  if (toolId) indicator.dataset.toolId = toolId;
  indicator.innerHTML = `
    <span class="tool-spinner" ${!isLive ? 'style="display:none;"' : ""}></span>
    <span class="tool-status-icon" style="display:none;"></span>
    <span class="tool-name">${escapeHtml(name)}</span>
    <span class="tool-detail">${getToolDetail(name, input)}</span>
    <div class="tool-body">${escapeHtml(JSON.stringify(input, null, 2))}</div>
    <div class="tool-result-preview" style="display:none;"></div>
  `;
  indicator.addEventListener("click", () => {
    indicator.classList.toggle("expanded");
  });

  div.appendChild(indicator);
  const turn = getOrCreateTurn(pane);
  turn.appendChild(div);
  pane.currentAssistantMsg = null;
  updateTurnChrome(turn);
  scrollToBottom(pane);
}

export function appendToolResult(toolUseId, content, isError, pane) {
  pane = pane || getPane(null);

  // Try to find the matching tool indicator and update it in-place
  const existing = toolUseId
    ? pane.messagesDiv.querySelector(`.tool-indicator[data-tool-id="${toolUseId}"]`)
    : null;

  if (existing) {
    // Update the existing indicator: stop spinner, show status icon + result
    existing.classList.remove("tool-running");
    existing.classList.add(isError ? "tool-error" : "tool-done");

    const spinner = existing.querySelector(".tool-spinner");
    if (spinner) spinner.style.display = "none";

    const statusIcon = existing.querySelector(".tool-status-icon");
    if (statusIcon) {
      statusIcon.style.display = "";
      statusIcon.style.color = isError ? "var(--error)" : "var(--success)";
      statusIcon.innerHTML = isError ? "&#10007;" : "&#10003;";
    }

    // Show result preview inline
    const resultPreview = existing.querySelector(".tool-result-preview");
    if (resultPreview && content) {
      const preview = typeof content === "string" ? content.slice(0, 150) : "";
      resultPreview.textContent = preview;
      resultPreview.style.display = "";
      resultPreview.className = "tool-result-preview" + (isError ? " error" : "");
    }

    // Append full result to tool-body
    const body = existing.querySelector(".tool-body");
    if (body && content) {
      body.innerHTML += "\n\n─── Result ───\n" + escapeHtml(content || "");
    }

    updateTurnChrome(existing.closest(".msg-assistant-turn"));
    scrollToBottom(pane);
    return;
  }

  // Fallback: create a standalone result element (for old messages without tool IDs)
  const div = document.createElement("div");
  div.className = "msg";

  const indicator = document.createElement("div");
  indicator.className = "tool-indicator " + (isError ? "tool-error" : "tool-done");
  const preview = typeof content === "string" ? content.slice(0, 120) : "";
  const iconColor = isError ? "var(--error)" : "var(--success)";
  const icon = isError ? "&#10007;" : "&#10003;";
  indicator.innerHTML = `
    <span class="tool-status-icon" style="color: ${iconColor};">${icon}</span>
    <span class="tool-name">${isError ? "Error" : "Result"}</span>
    <span class="tool-detail">${escapeHtml(preview)}</span>
    <div class="tool-body">${escapeHtml(content || "")}</div>
  `;
  indicator.addEventListener("click", () => {
    indicator.classList.toggle("expanded");
  });

  div.appendChild(indicator);
  const turn = getOrCreateTurn(pane);
  turn.appendChild(div);
  pane.currentAssistantMsg = null;
  updateTurnChrome(turn);
  scrollToBottom(pane);
}

export function showThinking(label, pane) {
  pane = pane || getPane(null);
  removeThinking(pane);
  const div = document.createElement("div");
  div.className = "thinking-bar";
  div.dataset.thinkingBar = "true";
  div.innerHTML = `
    <div class="thinking-dot-container">
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
    </div>
    <span class="thinking-label">${escapeHtml(label)}</span>
  `;
  pane.messagesDiv.appendChild(div);
  if (pane.statusEl) {
    pane.statusEl.textContent = "streaming";
    pane.statusEl.className = "chat-pane-status streaming";
  }
  scrollToBottom(pane, { force: true });
}

export function removeThinking(pane) {
  pane = pane || getPane(null);
  const el = pane.messagesDiv.querySelector('[data-thinking-bar="true"]');
  if (el) el.remove();
}

export function addResultSummary(msg, pane) {
  pane = pane || getPane(null);
  const parts = [];
  if (msg.model) parts.push(msg.model);
  if (msg.num_turns != null) parts.push(`${msg.num_turns} turn${msg.num_turns !== 1 ? "s" : ""}`);
  if (msg.duration_ms != null) {
    const secs = (msg.duration_ms / 1000).toFixed(1);
    parts.push(`${secs}s`);
  }
  if (msg.cost_usd != null) parts.push(`$${msg.cost_usd.toFixed(4)}`);
  const inTok = msg.input_tokens || 0;
  const outTok = msg.output_tokens || 0;
  if (inTok > 0 || outTok > 0) {
    const fmtTok = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    parts.push(`${fmtTok(inTok)} in / ${fmtTok(outTok)} out`);
  }
  if (msg.stop_reason && msg.stop_reason !== "success") {
    parts.push(`[${msg.stop_reason}]`);
  }
  if (parts.length > 0) {
    addStatus(parts.join(" \u00b7 "), false, pane);
  }
}

export function addStatus(text, isError, pane) {
  pane = pane || getPane(null);
  const div = document.createElement("div");
  div.className = "status" + (isError ? " error" : "");
  div.textContent = text;
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane, { force: true });
}

export function addSkillUsedMessage(skillName, skillDescription, pane) {
  pane = pane || getPane(null);
  const div = document.createElement("div");
  div.className = "skill-used-message";
  div.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg><span><span class="skill-used-name">Skill used: ${escapeHtml(skillName)}</span>${skillDescription ? `<span class="skill-used-desc"> — ${escapeHtml(skillDescription)}</span>` : ""}</span>`;
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane);
}

export function appendCliOutput(data, pane) {
  pane = pane || getPane(null);
  const div = document.createElement("div");
  div.className = "msg";

  const block = document.createElement("div");
  block.className = "cli-output";

  const isOk = data.exitCode === 0;
  block.innerHTML = `
    <div class="cli-output-header">
      <span class="cli-icon ${isOk ? "success" : "error"}">${isOk ? "&#10003;" : "&#10007;"}</span>
      <span class="cli-cmd">${escapeHtml(data.command)}</span>
      <span class="cli-exit">exit ${data.exitCode}</span>
    </div>
    <div class="cli-output-body">
      ${data.stdout ? `<pre>${escapeHtml(data.stdout)}</pre>` : ""}
      ${data.stderr ? `<pre class="cli-output-stderr">${escapeHtml(data.stderr)}</pre>` : ""}
      ${!data.stdout && !data.stderr ? `<pre>(no output)</pre>` : ""}
    </div>
  `;

  div.appendChild(block);
  const turn = getOrCreateTurn(pane);
  turn.appendChild(div);
  pane.currentAssistantMsg = null;
  updateTurnChrome(turn);
  scrollToBottom(pane);
}

export function renderMessagesIntoPane(messages, pane) {
  pane.messagesDiv.innerHTML = "";
  pane.currentAssistantMsg = null;
  pane.currentTurn = null;
  // Reset streaming counter — we're loading saved messages, not streaming
  setState("streamingCharCount", 0);
  if (!messages || messages.length === 0) {
    showChatEmptyState(pane);
    return;
  }
  // Track last assistant message ID for fork button placement
  let lastAssistantMsgEl = null;
  let lastAssistantMsgId = null;

  for (const msg of messages) {
    const data = JSON.parse(msg.content);
    switch (msg.role) {
      case "user": {
        // Finalize previous assistant block with fork button
        if (lastAssistantMsgEl && lastAssistantMsgId) {
          addForkButton(lastAssistantMsgEl, lastAssistantMsgId);
          lastAssistantMsgEl = null;
          lastAssistantMsgId = null;
        }
        // Extract file paths from saved <file path="..."> blocks
        const filePathMatches = (data.text || "").match(/<file path="([^"]+)">/g);
        const savedFilePaths = filePathMatches
          ? filePathMatches.map(m => m.match(/<file path="([^"]+)">/)[1])
          : [];
        // Show only the user's actual text, not the file content blocks
        const cleanText = savedFilePaths.length > 0
          ? (data.text || "").replace(/<file path="[^"]*">[\s\S]*?<\/file>\s*/g, "").trim()
          : (data.text || "");
        addUserMessage(cleanText, pane, data.images || [], savedFilePaths);
        break;
      }
      case "assistant":
        appendAssistantText(data.text, pane);
        lastAssistantMsgEl = pane.currentTurn || lastAssistantMsgEl;
        lastAssistantMsgId = msg.id;
        break;
      case "tool":
        if (data.name === "Skill" && data.input?.skill) {
          addSkillUsedMessage(data.input.skill, data.input.description || "", pane);
        }
        appendToolIndicator(data.name, data.input, pane, data.id, false);
        lastAssistantMsgEl = pane.currentTurn || lastAssistantMsgEl;
        lastAssistantMsgId = msg.id;
        break;
      case "tool_result":
        appendToolResult(data.toolUseId, data.content, data.isError, pane);
        lastAssistantMsgEl = pane.currentTurn || lastAssistantMsgEl;
        lastAssistantMsgId = msg.id;
        break;
      case "result":
        addResultSummary(data, pane);
        if (lastAssistantMsgEl && lastAssistantMsgId) {
          addForkButton(lastAssistantMsgEl, lastAssistantMsgId);
        }
        lastAssistantMsgEl = null;
        lastAssistantMsgId = null;
        pane.currentTurn = null;
        break;
      case "error": {
        const errorParts = [];
        if (data.subtype) errorParts.push(`[${data.subtype}]`);
        if (data.error) errorParts.push(data.error);
        if (data.cost_usd != null) errorParts.push(`$${data.cost_usd.toFixed(4)}`);
        if (data.model) errorParts.push(data.model);
        addStatus(errorParts.join(" \u00b7 ") || "Error", true, pane);
        break;
      }
      case "skill":
        addSkillUsedMessage(data.skill || data.name || "", data.description || "", pane);
        break;
      case "aborted":
        addStatus("Aborted", true, pane);
        break;
    }
  }

  // Add fork button to last assistant message if conversation ends with one
  if (lastAssistantMsgEl && lastAssistantMsgId) {
    addForkButton(lastAssistantMsgEl, lastAssistantMsgId);
  }

  pane.currentAssistantMsg = null;
  pane.currentTurn = null;
  // Hide token counter and reset — loading saved messages shouldn't show streaming stats
  setState("streamingCharCount", 0);
  if ($.streamingTokens) $.streamingTokens.classList.add("hidden");
  if ($.streamingTokensSep) $.streamingTokensSep.classList.add("hidden");
  highlightCodeBlocks(pane.messagesDiv);
  addCopyButtons(pane.messagesDiv);
  renderMermaidBlocks(pane.messagesDiv);
}

function addForkButton(msgEl, messageId) {
  if (!msgEl || msgEl.querySelector(".fork-btn")) return;
  const btn = document.createElement("button");
  btn.className = "fork-btn";
  btn.dataset.messageId = messageId;
  btn.title = "Fork conversation from here";
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>`;
  msgEl.appendChild(btn);
}

// ── Lazy-loading helpers ────────────────────────────────

export function prependOlderMessages(messages, pane) {
  if (!messages || messages.length === 0) return;

  // Render older messages into a detached container using the same rendering logic
  const tempContainer = document.createElement("div");
  const tempPane = { messagesDiv: tempContainer, currentAssistantMsg: null };
  renderMessagesIntoPane(messages, tempPane);

  // Capture scroll position before DOM mutation
  const scrollHeightBefore = pane.messagesDiv.scrollHeight;

  // Move all rendered nodes into the real pane
  const fragment = document.createDocumentFragment();
  while (tempContainer.firstChild) {
    fragment.appendChild(tempContainer.firstChild);
  }

  // Insert after loading indicator (if present) or at the top
  const indicator = pane.messagesDiv.querySelector(".load-more-indicator");
  const insertRef = indicator ? indicator.nextSibling : pane.messagesDiv.firstChild;
  pane.messagesDiv.insertBefore(fragment, insertRef);

  // Restore scroll position so the user's view doesn't jump
  const scrollHeightAfter = pane.messagesDiv.scrollHeight;
  pane.messagesDiv.scrollTop += (scrollHeightAfter - scrollHeightBefore);
}

export function showLoadingIndicator(pane) {
  if (pane.messagesDiv.querySelector(".load-more-indicator")) return;
  const el = document.createElement("div");
  el.className = "load-more-indicator";
  el.innerHTML = '<span class="load-more-spinner"></span> Loading older messages\u2026';
  pane.messagesDiv.prepend(el);
}

export function hideLoadingIndicator(pane) {
  const el = pane.messagesDiv.querySelector(".load-more-indicator");
  if (el) el.remove();
}
