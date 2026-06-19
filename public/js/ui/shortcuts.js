// Keyboard shortcuts
import { $ } from '../core/dom.js';
import { getState, setState } from '../core/store.js';
import { CHAT_IDS } from '../core/constants.js';
import { panes } from './parallel.js';
import { registerCommand } from './commands.js';
import { emit } from '../core/events.js';
import { toggleTipsFeed } from '../panels/tips-feed.js';

const DEV_PROJECT_FILES_FOCUS_STORAGE_KEY = 'artemis-dev-project-files-focus';

function closeAllModals() {
  document.querySelectorAll(".modal-overlay:not([data-persistent])").forEach((m) => m.classList.add("hidden"));
}

// Shortcuts modal ref — rendered by <artemis-shortcuts-modal> web component
const shortcutsModal = document.getElementById("shortcuts-modal");

document.addEventListener("keydown", (e) => {
  const isMeta = e.metaKey || e.ctrlKey;

  if (e.key === "Escape") {
    closeAllModals();
    return;
  }

  const tag = document.activeElement?.tagName;
  if (!isMeta && (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT")) return;

  if (isMeta && e.key === "k") {
    e.preventDefault();
    $.sessionSearchInput?.focus();
    return;
  }

  if (isMeta && e.key === "n") {
    e.preventDefault();
    $.newSessionBtn?.click();
    return;
  }

  if (isMeta && e.key === "/") {
    e.preventDefault();
    shortcutsModal?.classList.toggle("hidden");
    return;
  }

  // Cmd+Shift+E — Open Dev Projects files
  if (isMeta && e.shiftKey && e.key === "E") {
    e.preventDefault();
    localStorage.setItem(DEV_PROJECT_FILES_FOCUS_STORAGE_KEY, "1");
    setState("view", "chat");
    emit("dev-project-files:focus");
    return;
  }

  // Cmd+Shift+A — Go to Dashboard
  if (isMeta && e.shiftKey && e.key === "A") {
    e.preventDefault();
    setState("view", "command-center");
    setState("sessionId", null);
    return;
  }

  // Cmd+Shift+T — Toggle Tips Feed
  if (isMeta && e.shiftKey && e.key === "T") {
    e.preventDefault();
    toggleTipsFeed();
    return;
  }

  if (isMeta && getState("parallelMode") && e.key >= "1" && e.key <= "4") {
    e.preventDefault();
    const idx = parseInt(e.key) - 1;
    const chatId = CHAT_IDS[idx];
    const pane = panes.get(chatId);
    if (pane && pane.messageInput) {
      pane.messageInput.focus();
    }
    return;
  }
});

registerCommand("shortcuts", {
  category: "app",
  description: "Show keyboard shortcuts",
  execute() {
    shortcutsModal?.classList.remove("hidden");
  },
});

// Register /files and /git slash commands
registerCommand("files", {
  category: "app",
  description: "Focus Forge files",
  execute() {
    localStorage.setItem(DEV_PROJECT_FILES_FOCUS_STORAGE_KEY, "1");
    setState("view", "chat");
    emit("dev-project-files:focus");
  },
});

