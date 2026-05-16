// Telegram notification settings — UI for configuring bot token, chat ID, and notification preferences
import { $ } from '../core/dom.js';
import { registerCommand } from '../ui/commands.js';

const NOTIFY_MAP = {
  sessionComplete: 'tgNotifySession',
  workflowComplete: 'tgNotifyWorkflow',
  chainComplete: 'tgNotifyChain',
  agentComplete: 'tgNotifyAgent',
  orchestratorComplete: 'tgNotifyOrchestrator',
  dagComplete: 'tgNotifyDag',
  errors: 'tgNotifyErrors',
  permissionRequests: 'tgNotifyPermissions',
  taskStart: 'tgNotifyStart',
};

async function loadConfig() {
  try {
    const res = await fetch("/api/telegram/config");
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

function hasTelegramUi() {
  return Boolean(
    $.telegramBtn &&
    $.telegramModal &&
    $.telegramClose &&
    $.telegramEnabled &&
    $.telegramBotToken &&
    $.telegramChatId &&
    $.telegramAfkTimeout &&
    $.telegramSaveBtn &&
    $.telegramTestBtn &&
    $.telegramLabel &&
    $.telegramStatus
  );
}

function showStatus(msg, isError) {
  if (!$.telegramStatus) return;
  $.telegramStatus.textContent = msg;
  $.telegramStatus.className = `telegram-status ${isError ? "error" : "success"}`;
  $.telegramStatus.classList.remove("hidden");
  setTimeout(() => $.telegramStatus?.classList?.add("hidden"), 4000);
}

function updateLabel(enabled) {
  if (!$.telegramLabel) return;
  $.telegramLabel.textContent = enabled ? "Telegram (on)" : "Telegram";
}

async function openModal() {
  if (!hasTelegramUi()) return;
  const config = await loadConfig();
  if (config) {
    $.telegramEnabled.checked = config.enabled;
    $.telegramBotToken.value = config.botToken || "";
    $.telegramChatId.value = config.chatId || "";
    $.telegramAfkTimeout.value = config.afkTimeoutMinutes || 15;
    updateLabel(config.enabled);

    // Load notification preferences
    if (config.notify) {
      for (const [key, domKey] of Object.entries(NOTIFY_MAP)) {
        if ($[domKey]) {
          $[domKey].checked = config.notify[key] !== false;
        }
      }
    }
  }
  $.telegramModal.classList.remove("hidden");
}

function closeModal() {
  if (!$.telegramModal) return;
  $.telegramModal.classList.add("hidden");
}

function collectNotifyPrefs() {
  const notify = {};
  for (const [key, domKey] of Object.entries(NOTIFY_MAP)) {
    if ($[domKey]) {
      notify[key] = $[domKey].checked;
    }
  }
  return notify;
}

async function save() {
  if (!hasTelegramUi()) return;
  const enabled = $.telegramEnabled.checked;
  const botToken = $.telegramBotToken.value.trim();
  const chatId = $.telegramChatId.value.trim();
  const afkTimeoutMinutes = parseInt($.telegramAfkTimeout.value, 10) || 15;
  const notify = collectNotifyPrefs();

  if (enabled && (!botToken || !chatId)) {
    showStatus("Bot token and chat ID are required", true);
    return;
  }

  try {
    const res = await fetch("/api/telegram/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, botToken, chatId, afkTimeoutMinutes, notify }),
    });
    if (!res.ok) throw new Error((await res.json()).error);
    showStatus("Settings saved", false);
    updateLabel(enabled);
  } catch (err) {
    showStatus(`Save failed: ${err.message}`, true);
  }
}

async function test() {
  if (!$.telegramTestBtn) return;
  $.telegramTestBtn.disabled = true;
  $.telegramTestBtn.textContent = "Sending...";
  try {
    const res = await fetch("/api/telegram/test", { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).error || "Send failed");
    showStatus("Test message sent — check Telegram", false);
  } catch (err) {
    showStatus(`Test failed: ${err.message}`, true);
  } finally {
    $.telegramTestBtn.disabled = false;
    $.telegramTestBtn.textContent = "Send Test";
  }
}

if ($.telegramBtn) $.telegramBtn.addEventListener("click", openModal);
if ($.telegramClose) $.telegramClose.addEventListener("click", closeModal);
if ($.telegramModal) {
  $.telegramModal.addEventListener("click", (e) => {
    if (e.target === $.telegramModal) closeModal();
  });
}
if ($.telegramSaveBtn) $.telegramSaveBtn.addEventListener("click", save);
if ($.telegramTestBtn) $.telegramTestBtn.addEventListener("click", test);

// Register slash command
registerCommand("telegram", {
  category: "settings",
  description: "Open Telegram notification settings",
  execute() { openModal(); },
});

// Load initial state
loadConfig().then(config => {
  if (config) updateLabel(config.enabled);
});
