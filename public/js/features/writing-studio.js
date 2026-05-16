import { escapeHtml } from "../core/utils.js";
import {
  composeWritingDraftApi,
  createWritingDraftApi,
  createWritingDraftFromGoogleDocApi,
  createWritingDraftVersionApi,
  deleteWritingDraftApi,
  deleteWritingFolderApi,
  createWritingFolderApi,
  createWritingTrainingCandidateApi,
  decideWritingTrainingCandidateApi,
  exportWritingDraftToGoogleDocApi,
  exportWritingStudioSyncApi,
  fetchWritingDraft,
  fetchGoogleOverviewApi,
  fetchWritingStudioOverview,
  importWritingDraftFromGoogleDocApi,
  inspectWritingStudioSyncApi,
  reconcileWritingStudioSyncApi,
  importWritingStudioSyncApi,
  importWritingSeedApi,
  regenerateDraftApi,
  getDraftEditHistoryApi,
  submitDraftForReviewApi,
  unlinkWritingDraftGoogleDocApi,
  updateWritingExampleApi,
  updateWritingDraftApi,
  updateWritingFolderApi,
  updateWritingRuleApi,
  updateWritingSourceApi,
} from "../core/api.js";
import { getState, on as onState } from "../core/store.js";
import { WRITING_STUDIO_VIEW, normalizeAppView } from "../core/navigation.js";
import { PROVIDER_LABELS, PROVIDER_PICKERS } from "../ui/model-selector.js";

const SHELL_CONTENT_SELECTOR = "#app-shell-content";
const DEFAULT_DRAFT_CONTENT = "Paste a rough draft here, or start with the goal, audience, and format.";
const WRITING_STUDIO_HANDOFF_KEY = "artemis-writing-studio-handoff";
const WRITING_STUDIO_SYNC_KEY = "artemis-writing-studio-sync";
const WRITING_SYNC_SUMMARY_LABELS = {
  export: "Last export",
  import: "Last import",
  inspect: "Last inspection",
};
const WRITING_SYNC_PREVIEW_PAGE_SIZE = 4;
const WRITING_SYNC_PREVIEW_GROUPS = ["repoOnly", "localOnly", "conflicts"];
const WRITING_SYNC_PREVIEW_FILTERS = [
  { value: "all", label: "All changes" },
  { value: "repoOnly", label: "Repo only" },
  { value: "localOnly", label: "Local only" },
  { value: "conflicts", label: "Conflicts" },
];

let writingLoadToken = 0;
let writingLibraryDragBound = false;
let _writingModalPortal = null;
let writingState = {
  overview: null,
  selectedDraft: null,
  activePanel: "draft",
  activePopover: null,
  activeModal: null,
  filters: {
    folderId: null,
    campaignId: "",
  },
  draftContent: "",
  chatHistory: [],
  attachments: [],
  dragActive: false,
  memoryEditor: null,
  error: null,
  status: "",
  isComposing: false,
  collapsedFolderIds: [],
  rootCollapsed: false,
  folderComposerParentId: null,
  dragPayload: null,
  syncForm: {
    rootDir: "",
    machineLabel: "",
    autoSync: false,
  },
  syncSummary: null,
  syncPreview: createWritingSyncPreviewState(),
  googleOverview: null,
};

const esc = (value) => escapeHtml(value ?? "");
const WRITING_ENGINE_OPTIONS = ["claude-code", "codex", "gemini", "openrouter"];

export function renderWritingStudioLoading() {
  return `
    <section class="page-hero writing-studio-hero" aria-busy="true">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Writing Studio</span>
          <span class="page-hero-status" data-tone="setup">Loading</span>
        </div>
        <h1>Writing Studio</h1>
        <p class="page-hero-lede">Pulling drafts, training candidates, and approved voice rules into the shared writing workspace.</p>
      </div>
    </section>
  `;
}

export async function loadWritingStudio({ selectedDraftId = null } = {}) {
  const loadToken = ++writingLoadToken;
  const mount = document.querySelector(SHELL_CONTENT_SELECTOR);
  if (!mount) return;
  mount.innerHTML = renderWritingStudioLoading();

  try {
    const [overview, googleOverview] = await Promise.all([
      fetchWritingStudioOverview(),
      fetchGoogleOverviewApi().catch(() => null),
    ]);
    if (loadToken !== writingLoadToken || normalizeAppView(getState("view")) !== WRITING_STUDIO_VIEW) return;

    let selectedDraft = null;
    const drafts = overview.drafts || [];
    const handoff = readWritingStudioHandoff();
    const nextFilters = resolveWritingFilters({
      handoff,
      filters: writingState.filters,
      drafts,
      folders: overview.folders || [],
      campaigns: overview.campaigns || [],
    });
    const visibleDrafts = filterWritingDrafts(drafts, nextFilters);
    const targetId = selectedDraftId
      || handoff?.draftId
      || (handoff?.campaignId ? visibleDrafts.find((draft) => draft.campaign_id === handoff.campaignId)?.id : null)
      || writingState.selectedDraft?.id
      || visibleDrafts[0]?.id
      || drafts[0]?.id;
    if (targetId) {
      selectedDraft = await fetchWritingDraft(targetId);
      if (loadToken !== writingLoadToken || normalizeAppView(getState("view")) !== WRITING_STUDIO_VIEW) return;
    }
    if (selectedDraft && !matchesWritingFilters(selectedDraft, nextFilters)) {
      const fallbackId = visibleDrafts[0]?.id || null;
      selectedDraft = fallbackId ? await fetchWritingDraft(fallbackId) : null;
      if (loadToken !== writingLoadToken || normalizeAppView(getState("view")) !== WRITING_STUDIO_VIEW) return;
    }

    const persistedSyncForm = normalizeWritingSyncForm(readWritingSyncPreferences());

    writingState = {
      overview,
      selectedDraft,
      activePanel: writingState.activePanel === "memory-bank" ? "memory-bank" : writingState.activePanel === "version-history" ? "version-history" : "draft",
      activePopover: null,
      activeModal: null,
      filters: nextFilters,
      draftContent: latestDraftContent(selectedDraft),
      chatHistory: Array.isArray(selectedDraft?.threadMessages) ? selectedDraft.threadMessages : [],
      attachments: [],
      dragActive: false,
      memoryEditor: null,
      error: null,
      status: "",
      isComposing: false,
      collapsedFolderIds: Array.isArray(writingState.collapsedFolderIds) ? writingState.collapsedFolderIds : [],
      folderComposerParentId: writingState.folderComposerParentId ?? nextFilters.folderId ?? null,
      syncForm: normalizeWritingSyncForm(writingState.syncForm?.rootDir || writingState.syncForm?.machineLabel || writingState.syncForm?.autoSync
        ? writingState.syncForm
        : persistedSyncForm),
      syncSummary: writingState.syncSummary || null,
      syncPreview: normalizeWritingSyncPreviewState(writingState.syncPreview),
      googleOverview,
      editHistoryMap: writingState.editHistoryMap || {},
    };
    renderWritingStudio();
  } catch (error) {
    writingState = { ...writingState, error, status: "" };
    mount.innerHTML = renderWritingStudioError(error);
  }
}

export function handleWritingStudioInput(target) {
  if (!target) return false;
  if (target.matches("[data-writing-field='draft-content']")) {
    writingState.draftContent = target.value;
    const counter = document.querySelector("[data-writing-count]");
    if (counter) counter.textContent = `${target.value.length.toLocaleString()} chars`;
    return true;
  }
  if (target.matches("[data-writing-input='new-provider']")) {
    syncWritingModelOptions("new");
    return true;
  }
  if (target.matches("[data-writing-input='draft-provider']")) {
    syncWritingModelOptions("draft");
    return true;
  }
  if (target.matches("[data-writing-input='folder-filter']")) {
    closeWritingToolbarMenus();
    void applyWritingStudioFilters({
      ...writingState.filters,
      folderId: readFolderFilterValue(target.value),
    });
    return true;
  }
  if (target.matches("[data-writing-input='campaign-filter']")) {
    closeWritingToolbarMenus();
    void applyWritingStudioFilters({
      ...writingState.filters,
      campaignId: target.value || "",
    });
    return true;
  }
  if (target.matches("[data-writing-input='sync-root-dir']")) {
    writingState.syncForm = normalizeWritingSyncForm({
      ...writingState.syncForm,
      rootDir: target.value,
      autoSync: target.value.trim() ? writingState.syncForm?.autoSync !== false : false,
    });
    persistWritingSyncPreferences();
    syncWritingSyncCardDom();
    return true;
  }
  if (target.matches("[data-writing-input='sync-machine-label']")) {
    writingState.syncForm = normalizeWritingSyncForm({
      ...writingState.syncForm,
      machineLabel: target.value,
    });
    persistWritingSyncPreferences();
    return true;
  }
  if (target.matches("[data-writing-input='sync-auto-sync']")) {
    writingState.syncForm = normalizeWritingSyncForm({
      ...writingState.syncForm,
      autoSync: Boolean(target.checked),
    });
    persistWritingSyncPreferences();
    syncWritingSyncCardDom();
    return true;
  }
  return false;
}

export function handleWritingStudioAction(button) {
  const action = button?.dataset?.writingAction;
  if (!action) return false;
  if (action === "writing-compose-request") {
    void applyWritingChatPrompt();
    return true;
  }
  if (action === "writing-open-file-picker") {
    document.querySelector("[data-writing-input='attachment-picker']")?.click();
    return true;
  }
  if (action === "writing-open-memory-bank") {
    writingState.activePanel = "memory-bank";
    writingState.activePopover = null;
    renderWritingStudio();
    return true;
  }
  if (action === "writing-open-version-history") {
    writingState.activePanel = "version-history";
    writingState.activePopover = null;
    renderWritingStudio();
    // Kick off edit-history fetch; re-render when data arrives if still on this panel
    const histDraftId = writingState.selectedDraft?.id;
    if (histDraftId && !writingState.editHistoryMap?.[histDraftId]) {
      getDraftEditHistoryApi(histDraftId).then((history) => {
        writingState.editHistoryMap = { ...writingState.editHistoryMap, [histDraftId]: history };
        if (writingState.activePanel === "version-history") renderWritingStudio();
      }).catch(() => {});
    }
    return true;
  }
  if (action === "writing-open-proposed-modal") {
    writingState.activeModal = "proposed-review";
    syncWritingModalPortal();
    return true;
  }
  if (action === "writing-close-modal") {
    writingState.activeModal = null;
    syncWritingModalPortal();
    return true;
  }
  if (action === "writing-open-draft-panel") {
    writingState.activePanel = "draft";
    writingState.activePopover = null;
    renderWritingStudio();
    return true;
  }
  if (action === "writing-clear-draft") {
    writingState.selectedDraft = null;
    writingState.activePanel = "draft";
    writingState.chatHistory = [];
    writingState.attachments = [];
    renderWritingStudio();
    return true;
  }
  if (action === "writing-remove-attachment") {
    removeWritingAttachment(button?.dataset?.writingAttachmentId);
    return true;
  }
  if (action === "writing-save-chat-version") {
    void saveWritingChatReplyAsVersion(button?.dataset?.writingEntryId);
    return true;
  }
  if (action === "writing-edit-memory") {
    const id = Number(button.dataset.writingMemoryId);
    const kind = button.dataset.writingMemoryKind;
    if (!id || !kind) return true;
    writingState.memoryEditor = { id, kind };
    renderWritingStudio();
    return true;
  }
  if (action === "writing-cancel-memory-edit") {
    writingState.memoryEditor = null;
    renderWritingStudio();
    return true;
  }
  if (action === "writing-toggle-brief") {
    const dropdown = document.querySelector(".writing-ctx-brief-dropdown");
    if (dropdown) dropdown.hidden = !dropdown.hidden;
    return true;
  }
  if (action === "writing-toggle-propose") {
    const dropdown = document.querySelector(".writing-propose-dropdown");
    if (dropdown) dropdown.hidden = !dropdown.hidden;
    return true;
  }
  if (action === "writing-toggle-rules") {
    writingState.activePopover = writingState.activePopover === "rules" ? null : "rules";
    renderWritingStudio();
    return true;
  }
  if (action === "writing-select-folder-filter") {
    const nextFolderId = readFolderFilterValue(button?.dataset?.writingFolderId);
    const isSameFolder = Number(nextFolderId || 0) === Number(writingState.filters.folderId || 0);
    void applyWritingStudioFilters({
      ...writingState.filters,
      folderId: isSameFolder ? null : nextFolderId,
    });
    return true;
  }
  if (action === "writing-toggle-root-collapse") {
    writingState.rootCollapsed = !writingState.rootCollapsed;
    syncWritingLibraryDom();
    return true;
  }
  if (action === "writing-toggle-folder-collapse") {
    toggleWritingFolderCollapse(button?.dataset?.writingFolderId);
    return true;
  }
  if (action === "writing-start-subfolder") {
    writingState.folderComposerParentId = readFolderFilterValue(button?.dataset?.writingFolderId);
    syncWritingLibraryDom();
    requestAnimationFrame(() => document.querySelector("[data-writing-input='inline-folder-name']")?.focus());
    return true;
  }
  if (action === "writing-show-root-inline-folder") {
    writingState.folderComposerParentId = "root";
    syncWritingLibraryDom();
    requestAnimationFrame(() => document.querySelector("[data-writing-input='inline-folder-name']")?.focus());
    return true;
  }
  if (action === "writing-cancel-inline-folder") {
    writingState.folderComposerParentId = null;
    syncWritingLibraryDom();
    return true;
  }
  if (action === "writing-sync-preview-filter") {
    const filter = button?.dataset?.writingSyncFilter || "all";
    writingState.syncPreview = {
      ...normalizeWritingSyncPreviewState(writingState.syncPreview),
      filter: WRITING_SYNC_PREVIEW_FILTERS.some((option) => option.value === filter) ? filter : "all",
    };
    renderWritingStudio();
    return true;
  }
  if (action === "writing-sync-toggle-group") {
    const group = button?.dataset?.writingSyncGroup;
    if (!WRITING_SYNC_PREVIEW_GROUPS.includes(group)) return true;
    const previewState = normalizeWritingSyncPreviewState(writingState.syncPreview);
    writingState.syncPreview = {
      ...previewState,
      expandedGroups: {
        ...previewState.expandedGroups,
        [group]: !previewState.expandedGroups[group],
      },
    };
    renderWritingStudio();
    return true;
  }
  if (action === "writing-sync-show-more") {
    const group = button?.dataset?.writingSyncGroup;
    if (!WRITING_SYNC_PREVIEW_GROUPS.includes(group)) return true;
    const previewState = normalizeWritingSyncPreviewState(writingState.syncPreview);
    const visibleCounts = {
      ...previewState.visibleCounts,
    };
    const currentCount = Number(visibleCounts[group] || WRITING_SYNC_PREVIEW_PAGE_SIZE);
    visibleCounts[group] = currentCount > WRITING_SYNC_PREVIEW_PAGE_SIZE
      ? WRITING_SYNC_PREVIEW_PAGE_SIZE
      : currentCount + WRITING_SYNC_PREVIEW_PAGE_SIZE;
    writingState.syncPreview = {
      ...previewState,
      visibleCounts,
    };
    renderWritingStudio();
    return true;
  }
  if (action === "writing-sync-reconcile") {
    void applyWritingSyncReconcile(button);
    return true;
  }
  void runWritingAction(action, button);
  return true;
}

async function refreshWritingStudioDraftState(draftId, { preserveChatHistory = true } = {}) {
  const [overview, selectedDraft] = await Promise.all([
    fetchWritingStudioOverview(),
    fetchWritingDraft(draftId),
  ]);
  writingState = {
    ...writingState,
    overview,
    selectedDraft,
    filters: resolveWritingFilters({
      filters: writingState.filters,
      drafts: overview.drafts || [],
      folders: overview.folders || [],
      campaigns: overview.campaigns || [],
    }),
    draftContent: latestDraftContent(selectedDraft),
    chatHistory: preserveChatHistory
      ? (Array.isArray(selectedDraft?.threadMessages) && selectedDraft.threadMessages.length
          ? selectedDraft.threadMessages
          : writingState.chatHistory)
      : (Array.isArray(selectedDraft?.threadMessages) ? selectedDraft.threadMessages : []),
    error: null,
  };
}

async function applyWritingStudioFilters(nextFilters) {
  try {
    const drafts = writingState.overview?.drafts || [];
    const folders = writingState.overview?.folders || [];
    const campaigns = writingState.overview?.campaigns || [];
    const resolvedFilters = resolveWritingFilters({
      filters: nextFilters,
      drafts,
      folders,
      campaigns,
    });
    writingState = {
      ...writingState,
      filters: resolvedFilters,
      activePopover: null,
      error: null,
    };
    syncWritingLibraryDom();
  } catch (error) {
    console.error("Writing Studio filter update failed:", error);
    setWritingStatus(error.message || "Could not apply Writing Studio filters.", true);
  }
}

function syncWritingLibraryDom() {
  const card = document.querySelector("[data-writing-library-card]");
  if (!card) return;
  const overview = writingState.overview || {};
  card.innerHTML = renderWritingOrganizationRail(
    overview.folders || [],
    overview.campaigns || [],
    overview.drafts || [],
    writingState.selectedDraft?.id || null,
  );
}

async function runWritingAction(action, button) {
  setWritingBusy(true);
  try {
    if (action === "writing-new-draft" || action === "writing-new-draft-blank") {
      const rawTitle = readValue("[data-writing-input='new-title']");
      const title = rawTitle || "Untitled draft";
      const assetType = readValue("[data-writing-input='new-asset-type']") || "one-off doc";
      const audience = readValue("[data-writing-input='new-audience']");
      const channel = readValue("[data-writing-input='new-channel']");
      const campaignId = readValue("[data-writing-input='new-campaign-id']");
      const folderId = readFolderField("[data-writing-input='new-folder-id']");
      const brief = readValue("[data-writing-input='new-brief']");
      const engine = readWritingEngine("new");
      const googleDocUrl = readValue("[data-writing-input='new-google-doc-url']");
      const draft = googleDocUrl
        ? await createWritingDraftFromGoogleDocApi({
            url: googleDocUrl,
            title: rawTitle || null,
            assetType,
            folderId,
            campaignId: campaignId || null,
            audience: audience || null,
            channel: channel || null,
            metadata: {
              ...engine,
              brief,
            },
          })
        : await createWritingDraftApi({
            title,
            assetType,
            folderId,
            campaignId: campaignId || null,
            audience: audience || null,
            channel: channel || null,
            content: brief || DEFAULT_DRAFT_CONTENT,
            source: "manual",
            metadata: {
              ...engine,
              brief,
            },
          });
      await loadWritingStudio({ selectedDraftId: draft.id });
      if (action === "writing-new-draft" && !googleDocUrl) {
        maybeAutoSyncWritingStudio();
        await autoComposeNewDraft(draft.id, buildNewDraftRequest({ assetType, audience, channel, brief }), engine);
      } else {
        const syncResult = await maybeAutoSyncWritingStudio();
        setWritingStatus(formatWritingAutoSyncStatus(googleDocUrl ? "Draft created from Google Doc." : "Draft created.", syncResult), !syncResult.ok && syncResult.enabled);
      }
      return;
    }

    if (action === "writing-select-draft") {
      const id = Number(button.dataset.writingDraftId);
      if (id) await loadWritingStudio({ selectedDraftId: id });
      return;
    }

    if (action === "writing-save-version") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      const status = readValue("[data-writing-input='draft-status']");
      const audience = readValue("[data-writing-input='draft-audience']");
      const channel = readValue("[data-writing-input='draft-channel']");
      const campaignId = readValue("[data-writing-input='draft-campaign-id']");
      const folderId = readFolderField("[data-writing-input='draft-folder-id']");
      const brief = readValue("[data-writing-input='draft-brief']");
      const engine = readWritingEngine("draft");
      await createWritingDraftVersionApi(draftId, {
        content: writingState.draftContent || "",
        changeNote: readValue("[data-writing-input='change-note']") || "Manual save from Draft Canvas",
        source: "manual",
      });
      await updateWritingDraftApi(draftId, {
        status: status || undefined,
        folderId,
        campaignId,
        audience: audience || null,
        channel: channel || null,
        metadata: {
          ...parseWritingMetadata(writingState.selectedDraft),
          ...engine,
          brief,
        },
      });
      await loadWritingStudio({ selectedDraftId: draftId });
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Version saved.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-submit-for-review") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      const submitBtn = button;
      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Submitting…"; }
      const result = await submitDraftForReviewApi(draftId);
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus(result.alreadyPending ? "Approval already pending." : "Submitted for review.");
      return;
    }

    if (action === "writing-regenerate-from-feedback") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      if (button) { button.disabled = true; button.textContent = "Regenerating…"; }
      const result = await regenerateDraftApi(draftId);
      if (result.status === "failed") {
        if (button) { button.disabled = false; button.textContent = "Regenerate from feedback"; }
        setWritingStatus(`Regeneration failed: ${result.error || "unknown error"}`, true);
        return;
      }
      // Invalidate cached edit history so the next open fetches fresh data
      if (writingState.editHistoryMap?.[draftId]) {
        const { [draftId]: _removed, ...rest } = writingState.editHistoryMap;
        writingState.editHistoryMap = rest;
      }
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus("Regenerated. Status returned to Draft — submit for review when ready.");
      return;
    }

    if (action === "writing-restore-version") {
      const draftId = writingState.selectedDraft?.id;
      const versionId = Number(button.dataset.writingVersionId);
      if (!draftId || !versionId) return;
      const version = writingState.selectedDraft?.versions?.find((v) => v.id === versionId);
      if (!version) return;
      if (!window.confirm(`Restore version ${version.version_number}? Your current draft content will be replaced.`)) return;
      await updateWritingDraftApi(draftId, { content: version.content });
      await createWritingDraftVersionApi(draftId, {
        content: version.content,
        changeNote: `Restored from version ${version.version_number}`,
        source: "restore",
      });
      await loadWritingStudio({ selectedDraftId: draftId });
      writingState.activePanel = "draft";
      renderWritingStudio();
      setWritingStatus(`Restored to version ${version.version_number}.`);
      return;
    }

    if (action === "writing-export-google-doc") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      if (!writingState.googleOverview?.connected) {
        setWritingStatus("Connect Google first to export this draft to Docs.", true);
        return;
      }
      if (!writingState.googleOverview?.docsExportReady) {
        setWritingStatus("Reconnect Google to grant Google Docs export access.", true);
        return;
      }
      const existingDoc = resolveGoogleDocLink(writingState.selectedDraft);
      const result = await exportWritingDraftToGoogleDocApi(draftId, {
        title: writingState.selectedDraft?.title || "",
        content: writingState.draftContent || "",
      });
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus(
        result.created || !existingDoc?.documentId
          ? "Draft exported to a new Google Doc."
          : "Linked Google Doc updated.",
      );
      return;
    }

    if (action === "writing-import-google-doc") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      if (!writingState.googleOverview?.connected) {
        setWritingStatus("Connect Google first to attach or import from Docs.", true);
        return;
      }
      if (!writingState.googleOverview?.docsImportReady) {
        setWritingStatus("Reconnect Google to grant Google Docs import access.", true);
        return;
      }
      const existingDoc = resolveGoogleDocLink(writingState.selectedDraft);
      const googleDocUrl = readValue("[data-writing-input='draft-google-doc-url']");
      if (!existingDoc?.documentId && !googleDocUrl) {
        setWritingStatus("Paste a Google Doc link before importing it into this draft.", true);
        return;
      }
      const result = await importWritingDraftFromGoogleDocApi(draftId, {
        url: googleDocUrl || undefined,
      });
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus(
        result.linked || !existingDoc?.documentId
          ? "Google Doc attached and imported into this draft."
          : "Linked Google Doc imported into this draft as a new version.",
      );
      return;
    }

    if (action === "writing-change-google-doc-link") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      if (!writingState.googleOverview?.connected) {
        setWritingStatus("Connect Google first to change the linked Google Doc.", true);
        return;
      }
      if (!writingState.googleOverview?.docsImportReady) {
        setWritingStatus("Reconnect Google to grant Google Docs import access.", true);
        return;
      }
      const existingDoc = resolveGoogleDocLink(writingState.selectedDraft);
      const nextUrl = window.prompt("Paste the Google Doc link to attach to this draft", existingDoc?.url || "")?.trim();
      if (!nextUrl) return;
      const result = await importWritingDraftFromGoogleDocApi(draftId, { url: nextUrl });
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus(
        result.linked
          ? "Google Doc link updated and imported into this draft."
          : "Linked Google Doc refreshed into this draft.",
      );
      return;
    }

    if (action === "writing-remove-google-doc-link") {
      const draftId = writingState.selectedDraft?.id;
      if (!draftId) return;
      if (!window.confirm("Remove the linked Google Doc from this draft? Existing draft versions will stay in Writing Studio.")) return;
      await unlinkWritingDraftGoogleDocApi(draftId);
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus("Google Doc link removed from this draft.");
      return;
    }

    if (action === "writing-create-inline-folder") {
      const input = document.querySelector("[data-writing-input='inline-folder-name']");
      const name = input?.value?.trim();
      if (!name) {
        setWritingStatus("Enter a folder name first.", true);
        input?.focus();
        return;
      }
      const rawParent = writingState.folderComposerParentId;
      const parentFolderId = (rawParent === "root" || !rawParent) ? null : Number(rawParent);
      writingState.folderComposerParentId = null;
      await createWritingFolderApi({ name, parentFolderId });
      await loadWritingStudio();
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Folder created.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-create-folder") {
      const name = readValue("[data-writing-input='new-folder-name']");
      if (!name) {
        setWritingStatus("Name the folder before creating it.", true);
        return;
      }
      const parentFolderId = readFolderField("[data-writing-input='new-folder-parent-id']");
      const campaignId = readValue("[data-writing-input='new-folder-campaign-id']");
      const description = readValue("[data-writing-input='new-folder-description']");
      await createWritingFolderApi({
        name,
        parentFolderId,
        campaignId: campaignId || null,
        description: description || null,
      });
      writingState.folderComposerParentId = null;
      await loadWritingStudio();
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Folder created.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-edit-folder") {
      const folderId = Number(button?.dataset?.writingFolderId);
      if (!folderId) return;
      const currentName = button?.dataset?.writingFolderName || "";
      const nextName = window.prompt("Rename folder", currentName)?.trim();
      if (!nextName || nextName === currentName) return;
      await updateWritingFolderApi(folderId, {
        name: nextName,
        campaignId: button?.dataset?.writingFolderCampaignId || null,
        description: button?.dataset?.writingFolderDescription || null,
      });
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Folder renamed.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-delete-folder") {
      const folderId = Number(button?.dataset?.writingFolderId);
      const folderName = button?.dataset?.writingFolderName || "this folder";
      if (!folderId) return;
      if (!window.confirm(`Delete ${folderName}? Drafts will remain available in All drafts.`)) return;
      await deleteWritingFolderApi(folderId);
      if (Number(writingState.filters.folderId || 0) === folderId) {
        writingState.filters = {
          ...writingState.filters,
          folderId: null,
        };
      }
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Folder deleted.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-edit-draft") {
      const draftId = Number(button?.dataset?.writingDraftId);
      const draft = (writingState.overview?.drafts || []).find((entry) => Number(entry.id) === draftId);
      if (!draftId || !draft) return;
      const nextTitle = window.prompt("Rename draft", draft.title || "")?.trim();
      if (!nextTitle || nextTitle === draft.title) return;
      await updateWritingDraftApi(draftId, { title: nextTitle });
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || draftId });
      setWritingStatus("Draft renamed.");
      return;
    }

    if (action === "writing-delete-draft") {
      const draftId = Number(button?.dataset?.writingDraftId);
      const drafts = writingState.overview?.drafts || [];
      const draft = drafts.find((entry) => Number(entry.id) === draftId);
      if (!draftId || !draft) return;
      if (!window.confirm(`Delete ${draft.title}? This removes its saved versions too.`)) return;
      const remainingDrafts = drafts.filter((entry) => Number(entry.id) !== draftId);
      const fallbackDraftId = remainingDrafts.find((entry) => matchesWritingFilters(entry, writingState.filters))?.id
        || remainingDrafts[0]?.id
        || null;
      await deleteWritingDraftApi(draftId);
      await loadWritingStudio({ selectedDraftId: fallbackDraftId });
      const syncResult = await maybeAutoSyncWritingStudio();
      setWritingStatus(formatWritingAutoSyncStatus("Draft deleted.", syncResult), !syncResult.ok && syncResult.enabled);
      return;
    }

    if (action === "writing-toggle-brief") {
      const dropdown = document.querySelector(".writing-ctx-brief-dropdown");
      if (dropdown) dropdown.hidden = !dropdown.hidden;
      return;
    }

    if (action === "writing-toggle-propose") {
      const dropdown = document.querySelector(".writing-propose-dropdown");
      if (dropdown) dropdown.hidden = !dropdown.hidden;
      return;
    }

    if (action === "writing-propose-learning") {
      const draftId = writingState.selectedDraft?.id ?? null;
      const proposedText = readValue("[data-writing-input='learning-text']");
      if (!proposedText) {
        setWritingStatus("Add a learning before proposing it.", true);
        return;
      }
      await createWritingTrainingCandidateApi({
        draftId,
        proposedText,
        candidateType: readValue("[data-writing-input='learning-type']") || "rule",
        rationale: "Proposed from Writing Studio review.",
        scope: {
          assetType: writingState.selectedDraft?.asset_type || null,
          channel: writingState.selectedDraft?.channel || null,
        },
      });
      await loadWritingStudio({ selectedDraftId: draftId });
      setWritingStatus("Learning candidate added for review.");
      return;
    }

    if (action === "writing-candidate-decision") {
      const id = Number(button.dataset.writingCandidateId);
      const decision = button.dataset.writingDecision;
      if (!id || !decision) return;
      await decideWritingTrainingCandidateApi(id, decision);
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      const remaining = (writingState.overview?.trainingCandidates || []).filter((c) => c.status === "proposed");
      if (remaining.length === 0) writingState.activeModal = null;
      renderWritingStudio();
      setWritingStatus(`Candidate marked ${decision.replaceAll("_", " ")}.`);
      return;
    }

    if (action === "writing-import-seed") {
      const result = await importWritingSeedApi();
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      setWritingStatus(`Imported ${result.sourcesUpserted || 0} seed sources, ${result.rulesUpserted || 0} rules, and ${result.examplesUpserted || 0} examples.`);
      return;
    }

    if (action === "writing-sync-export") {
      const rootDir = readWritingSyncRootDir();
      if (!rootDir) {
        setWritingStatus("Paste the repo-backed sync path before exporting.", true);
        return;
      }
      const machineLabel = readValue("[data-writing-input='sync-machine-label']");
      const result = await exportWritingStudioSyncApi({
        rootDir,
        machineLabel: machineLabel || undefined,
      });
      writingState.syncForm = normalizeWritingSyncForm({
        ...writingState.syncForm,
        rootDir,
        machineLabel,
      });
      persistWritingSyncPreferences();
      writingState.syncSummary = {
        action: "export",
        result,
      };
      renderWritingStudio();
      setWritingStatus(`Exported Writing Studio sync files to ${rootDir}.`);
      return;
    }

    if (action === "writing-sync-inspect") {
      const rootDir = readWritingSyncRootDir();
      if (!rootDir) {
        setWritingStatus("Paste the repo-backed sync path before inspecting.", true);
        return;
      }
      const result = await inspectWritingStudioSyncApi({ rootDir });
      writingState.syncForm = normalizeWritingSyncForm({
        ...writingState.syncForm,
        rootDir,
      });
      persistWritingSyncPreferences();
      writingState.syncSummary = {
        action: "inspect",
        result,
      };
      writingState.syncPreview = createWritingSyncPreviewState();
      renderWritingStudio();
      setWritingStatus(`Loaded Writing Studio repo snapshot from ${rootDir}.`);
      return;
    }

    if (action === "writing-sync-import") {
      const rootDir = readWritingSyncRootDir();
      if (!rootDir) {
        setWritingStatus("Paste the repo-backed sync path before importing.", true);
        return;
      }
      const result = await importWritingStudioSyncApi({ rootDir });
      writingState.syncForm = normalizeWritingSyncForm({
        ...writingState.syncForm,
        rootDir,
      });
      persistWritingSyncPreferences();
      writingState.syncSummary = {
        action: "import",
        result,
      };
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      setWritingStatus(`Imported Writing Studio sync files from ${rootDir}.`);
      return;
    }

    if (action === "writing-save-memory-edit") {
      if (!writingState.memoryEditor) return;
      const { kind, id } = writingState.memoryEditor;
      const body = readValue("[data-writing-input='memory-body']");
      if (!body) {
        setWritingStatus("Add content before saving the memory module.", true);
        return;
      }
      if (kind === "source") {
        await updateWritingSourceApi(id, { body });
      } else if (kind === "rule") {
        await updateWritingRuleApi(id, { body });
      } else if (kind === "example") {
        await updateWritingExampleApi(id, { body });
      }
      writingState.memoryEditor = null;
      await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
      writingState.activePanel = "memory-bank";
      renderWritingStudio();
      setWritingStatus(`${kind[0].toUpperCase()}${kind.slice(1)} updated.`);
    }
  } catch (error) {
    console.error("Writing Studio action failed:", error);
    setWritingStatus(error.message || "Writing Studio action failed.", true);
  } finally {
    setWritingBusy(false);
  }
}

function renderWritingStudio() {
  const mount = document.querySelector(SHELL_CONTENT_SELECTOR);
  if (!mount) return;
  const { overview, selectedDraft, draftContent } = writingState;
  const drafts = filterWritingDrafts(overview?.drafts || [], writingState.filters);
  const folders = overview?.folders || [];
  const campaigns = overview?.campaigns || [];
  const candidates = overview?.trainingCandidates || [];
  const rules = overview?.rules || [];
  const sources = overview?.sources || [];
  const activeProfile = overview?.activeProfile;
  const googleOverview = writingState.googleOverview;
  const reviewCount = countStatus(candidates, "proposed");

  mount.innerHTML = `
    <div class="writing-status ${writingState.status ? "visible" : ""}" data-writing-status>${esc(writingState.status)}</div>

    <section class="page-canvas writing-studio-page">
      <aside class="writing-sidebar col-span-3">
          ${selectedDraft
            ? renderActiveDraftCard(
                selectedDraft,
                resolveWritingEngine(selectedDraft),
                resolveWritingBrief(selectedDraft),
                activeProfile,
                googleOverview,
              )
            : ""}

          <article class="writing-card writing-library-card" data-writing-library-card>
            ${renderWritingOrganizationRail(folders, campaigns, drafts, selectedDraft?.id)}
          </article>

          ${renderMemorySidebarCard()}
          <section class="writing-sync-hidden-panel" hidden aria-hidden="true">
            ${renderWritingSyncCard()}
          </section>
      </aside>

      <main class="writing-canvas col-span-9">
          ${writingState.activePanel === "memory-bank"
            ? renderVoiceMemoryBank(selectedDraft)
            : (selectedDraft ? renderDraftCanvas(selectedDraft, draftContent) : renderNewDraftCanvas())}
      </main>
    </section>
  `;
  bindWritingStudioInteractions();
  syncWritingModalPortal();
  if (writingState.chatHistory.length) {
    queueWritingChatScrollToBottom();
  }
}

function renderDraftRow(draft, selectedId) {
  const isSelected = Number(draft.id) === Number(selectedId);
  const orgMeta = [draft.asset_type || "draft", draft.status || "draft"].filter(Boolean).join(" · ");
  const detailMeta = [draft.folder_name, draft.campaign_id].filter(Boolean).join(" · ");
  return `
    <div class="writing-browser-row writing-browser-row-file ${isSelected ? "selected" : ""}" draggable="true" data-writing-drag-type="draft" data-writing-drag-id="${draft.id}">
      <button type="button" class="writing-browser-row-main writing-draft-row ${isSelected ? "selected" : ""}" data-writing-action="writing-select-draft" data-writing-draft-id="${draft.id}">
        <span class="writing-browser-row-icon writing-browser-row-icon-file" aria-hidden="true">
          <svg width="14" height="16" viewBox="0 0 20 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 1.75h6.75l4.5 4.5v13.5A2.25 2.25 0 0 1 14 22H5A2.25 2.25 0 0 1 2.75 19.75V4A2.25 2.25 0 0 1 5 1.75Z"></path>
            <path d="M11.75 1.75v4.5h4.5"></path>
            <path d="M6.5 13h7"></path>
            <path d="M6.5 16.5h5.25"></path>
          </svg>
        </span>
        <span class="writing-browser-row-copy">
          <strong>${esc(draft.title)}</strong>
          <span>${esc(orgMeta)}</span>
          ${detailMeta ? `<small>${esc(detailMeta)}</small>` : ""}
        </span>
      </button>
      <div class="writing-browser-row-actions">
        <span class="writing-drag-handle" aria-hidden="true">
          <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
            <circle cx="3" cy="2.5" r="1.3"/><circle cx="7" cy="2.5" r="1.3"/>
            <circle cx="3" cy="7" r="1.3"/><circle cx="7" cy="7" r="1.3"/>
            <circle cx="3" cy="11.5" r="1.3"/><circle cx="7" cy="11.5" r="1.3"/>
          </svg>
        </span>
        <button type="button" class="writing-row-action" data-writing-action="writing-edit-draft" data-writing-draft-id="${draft.id}" aria-label="Rename draft ${escAttr(draft.title)}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 20h9"></path>
            <path d="m16.5 3.5 4 4L7 21H3v-4z"></path>
          </svg>
        </button>
        <button type="button" class="writing-row-action writing-row-action-danger" data-writing-action="writing-delete-draft" data-writing-draft-id="${draft.id}" aria-label="Delete draft ${escAttr(draft.title)}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18"></path>
            <path d="M8 6V4h8v2"></path>
            <path d="M19 6l-1 14H6L5 6"></path>
          </svg>
        </button>
      </div>
    </div>
  `;
}

function renderFolderBrowserRow(folder) {
  const isCollapsed = isFolderCollapsed(folder.id);
  const subtitle = [
    folder.campaign_id || null,
    folder.childFolderCount ? `${folder.childFolderCount} subfolder${folder.childFolderCount === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(" · ") || "No campaign";
  return `
    <div class="writing-browser-row writing-browser-row-folder" draggable="true" data-writing-drag-type="folder" data-writing-drag-id="${folder.id}" data-writing-drop-target="folder" data-writing-drop-folder-id="${folder.id}">
      <button type="button" class="writing-browser-row-main writing-folder-row" data-writing-action="writing-toggle-folder-collapse" data-writing-folder-id="${folder.id}" aria-expanded="${isCollapsed ? "false" : "true"}" aria-label="${isCollapsed ? "Expand" : "Collapse"} ${escAttr(folder.name)}">
        <span class="writing-folder-chevron" aria-hidden="true">
          <svg width="9" height="9" viewBox="0 0 9 9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.5 1.5l3 3-3 3"/>
          </svg>
        </span>
        <span class="writing-folder-row-icon writing-browser-row-icon" aria-hidden="true">
          <svg width="18" height="14" viewBox="0 0 24 18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.75 4.75a2 2 0 0 1 2-2h4.4l1.7 1.95h8.4a2 2 0 0 1 2 2V13.25a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2z"/>
          </svg>
        </span>
        <span class="writing-browser-row-copy writing-folder-row-copy">
          <strong>${esc(folder.name)}</strong>
          <span>${esc(subtitle)}</span>
        </span>
        <small class="writing-browser-row-count writing-folder-row-count">${Number(folder.draftCount || 0)}</small>
      </button>
      <div class="writing-browser-row-actions">
        <span class="writing-drag-handle" aria-hidden="true">
          <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
            <circle cx="3" cy="2.5" r="1.3"/><circle cx="7" cy="2.5" r="1.3"/>
            <circle cx="3" cy="7" r="1.3"/><circle cx="7" cy="7" r="1.3"/>
            <circle cx="3" cy="11.5" r="1.3"/><circle cx="7" cy="11.5" r="1.3"/>
          </svg>
        </span>
        <button type="button" class="writing-row-action" data-writing-action="writing-start-subfolder" data-writing-folder-id="${folder.id}" data-writing-folder-name="${escAttr(folder.name)}" aria-label="New subfolder in ${escAttr(folder.name)}" title="New subfolder">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7.5a2 2 0 0 1 2-2h4l1.6 1.8H19a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <path d="M12 10.5v6"/><path d="M9 13.5h6"/>
          </svg>
        </button>
        <button type="button" class="writing-row-action" data-writing-action="writing-edit-folder" data-writing-folder-id="${folder.id}" data-writing-folder-name="${escAttr(folder.name)}" data-writing-folder-campaign-id="${escAttr(folder.campaign_id || "")}" data-writing-folder-description="${escAttr(folder.description || "")}" aria-label="Rename ${escAttr(folder.name)}" title="Rename">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 20h9"/><path d="m16.5 3.5 4 4L7 21H3v-4z"/>
          </svg>
        </button>
        <button type="button" class="writing-row-action writing-row-action-danger" data-writing-action="writing-delete-folder" data-writing-folder-id="${folder.id}" data-writing-folder-name="${escAttr(folder.name)}" aria-label="Delete ${escAttr(folder.name)}" title="Delete folder">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>
          </svg>
        </button>
      </div>
    </div>
  `;
}

function renderInlineFolderInput(parentId) {
  return `
    <div class="writing-inline-folder-row">
      <span class="writing-folder-row-icon writing-browser-row-icon" aria-hidden="true">
        <svg width="18" height="14" viewBox="0 0 24 18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2.75 4.75a2 2 0 0 1 2-2h4.4l1.7 1.95h8.4a2 2 0 0 1 2 2V13.25a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2z"/>
        </svg>
      </span>
      <input
        class="writing-inline-folder-input"
        data-writing-input="inline-folder-name"
        data-writing-inline-parent-id="${parentId ?? ""}"
        placeholder="Folder name"
        autocomplete="off"
        autofocus
      >
      <div class="writing-inline-folder-actions">
        <button type="button" class="writing-row-action" data-writing-action="writing-create-inline-folder" aria-label="Create folder">
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M1.5 6.5l3 3 6-7"/>
          </svg>
        </button>
        <button type="button" class="writing-row-action writing-row-action-danger" data-writing-action="writing-cancel-inline-folder" aria-label="Cancel">
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M2 2l8 8M10 2l-8 8"/>
          </svg>
        </button>
      </div>
    </div>
  `;
}

function renderFolderDraftGroup(folder, drafts, selectedDraftId, foldersByParent, depth = 0) {
  const isCollapsed = isFolderCollapsed(folder.id);
  const childFolders = foldersByParent.get(Number(folder.id)) || [];
  const showInlineComposer = writingState.folderComposerParentId === Number(folder.id);
  return `
    <div class="writing-browser-group" style="--writing-folder-depth:${depth};" data-writing-drop-target="folder" data-writing-drop-folder-id="${folder.id}">
      ${renderFolderBrowserRow(folder)}
      ${showInlineComposer ? renderInlineFolderInput(folder.id) : ""}
      ${isCollapsed ? "" : `
        <div class="writing-browser-children">
          ${childFolders.map((childFolder) => {
            const childDrafts = drafts.filter((draft) => Number(draft.folder_id) === Number(childFolder.id));
            return renderFolderDraftGroup(childFolder, childDrafts, selectedDraftId, foldersByParent, depth + 1);
          }).join("")}
          ${drafts.length
            ? drafts.map((draft) => renderDraftRow(draft, selectedDraftId)).join("")
            : (!childFolders.length ? `<p class="writing-browser-empty writing-browser-empty-nested">No drafts in this folder yet.</p>` : "")}
        </div>
      `}
    </div>
  `;
}

function renderRootBrowserRow(drafts, folders) {
  const isCollapsed = writingState.rootCollapsed;
  return `
    <div class="writing-browser-row writing-browser-root-row" data-writing-drop-target="root">
      <button type="button" class="writing-browser-row-main writing-folder-row writing-folder-row-root"
        data-writing-action="writing-toggle-root-collapse"
        aria-expanded="${isCollapsed ? "false" : "true"}">
        <span class="writing-folder-chevron" aria-hidden="true">
          <svg width="9" height="9" viewBox="0 0 9 9" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.5 1.5l3 3-3 3"/>
          </svg>
        </span>
        <span class="writing-folder-row-icon writing-browser-row-icon" aria-hidden="true">
          <svg width="18" height="14" viewBox="0 0 24 18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.75 4.75a2 2 0 0 1 2-2h4.4l1.7 1.95h8.4a2 2 0 0 1 2 2V13.25a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2z"/>
          </svg>
        </span>
        <span class="writing-browser-row-copy writing-folder-row-copy">
          <strong>All drafts</strong>
          <span>${folders.length ? `${folders.length} folder${folders.length === 1 ? "" : "s"}` : "No folders yet"}</span>
        </span>
        <small class="writing-browser-row-count writing-folder-row-count">${drafts.length}</small>
      </button>
      <div class="writing-browser-row-actions">
        <button type="button" class="writing-row-action" data-writing-action="writing-show-root-inline-folder" aria-label="New folder" title="New folder">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 7.5a2 2 0 0 1 2-2h4l1.6 1.8H19a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <path d="M12 10.5v6"/><path d="M9 13.5h6"/>
          </svg>
        </button>
      </div>
    </div>
  `;
}

function renderWritingOrganizationRail(folders, campaigns, drafts, selectedDraftId) {
  const foldersByParent = buildWritingFolderParentMap(folders);
  const unfiledDrafts = drafts.filter((draft) => !draft.folder_id);
  const rootFolders = folders.filter((folder) => {
    if (!folder.parent_folder_id) return true;
    return !folders.some((candidate) => Number(candidate.id) === Number(folder.parent_folder_id));
  });
  const showRootInlineComposer = writingState.folderComposerParentId === "root";
  return `
    <div class="writing-organization-card">
      <div class="writing-organization-browser writing-browser-panel">
        <div class="writing-organization-browser-head writing-browser-panel-head">
          <div class="writing-browser-head-copy">
            <span class="shell-eyebrow">Library</span>
          </div>
          <button type="button" class="writing-library-new-folder-link" data-writing-action="writing-show-root-inline-folder" aria-label="New folder">+ Folder</button>
        </div>
        <div class="writing-browser-list">
          ${renderRootBrowserRow(drafts, folders)}
          ${!writingState.rootCollapsed ? `
            <div class="writing-browser-children writing-browser-children-root" data-writing-drop-target="root">
              ${unfiledDrafts.map((draft) => renderDraftRow(draft, selectedDraftId)).join("")}
            </div>
          ` : ""}
          ${showRootInlineComposer ? renderInlineFolderInput(null) : ""}
          ${rootFolders.map((folder) => {
            const folderDrafts = drafts.filter((draft) => Number(draft.folder_id) === Number(folder.id));
            return renderFolderDraftGroup(folder, folderDrafts, selectedDraftId, foldersByParent);
          }).join("")}
        </div>
        ${!rootFolders.length && !drafts.length ? `<p class="writing-browser-empty">No drafts or folders yet.</p>` : ""}
      </div>
    </div>
  `;
}

function renderWritingSyncCard() {
  const rootDir = writingState.syncForm?.rootDir || "";
  const machineLabel = writingState.syncForm?.machineLabel || "";
  const autoSync = isWritingAutoSyncEnabled();
  return `
    <div class="writing-sync-card">
      <div class="writing-sync-inline-status">
        <span class="writing-meta-pill" data-writing-sync-state>${autoSync ? "Auto on" : "Auto off"}</span>
        <span class="writing-browser-caption">Sync saved work to a repo folder.</span>
      </div>
      <input data-writing-input="sync-root-dir" class="writing-input" value="${escAttr(rootDir)}" placeholder="/absolute/path/to/repo/writing-studio">
      <input data-writing-input="sync-machine-label" class="writing-input" value="${escAttr(machineLabel)}" placeholder="Machine label for export (optional)">
      <label class="writing-setup-card writing-sync-toggle-card">
        <span>Autosync saved work</span>
        <input data-writing-input="sync-auto-sync" type="checkbox" class="writing-sync-checkbox" ${autoSync ? "checked" : ""}>
      </label>
      <div class="writing-sync-actions">
        <button type="button" class="writing-button writing-button-ghost" data-writing-action="writing-sync-inspect">Inspect repo</button>
        <button type="button" class="writing-button" data-writing-action="writing-sync-export">Export</button>
        <button type="button" class="writing-button" data-writing-action="writing-sync-import">Import</button>
      </div>
      <p class="writing-sync-note" data-writing-sync-note>${autoSync
        ? "Autosync only touches durable Writing Studio records after explicit save actions. Transient thread turns stay local until you save or promote them."
        : "Transient thread turns stay out of sync until you explicitly save or promote them into version history."}</p>
      ${renderWritingSyncSummary()}
    </div>
  `;
}

function renderWritingSyncSummary() {
  const summary = writingState.syncSummary;
  if (!summary?.result) return "";
  const label = WRITING_SYNC_SUMMARY_LABELS[summary.action] || "Last sync";
  const rootDir = summary.result.rootDir || writingState.syncForm?.rootDir || "";
  const counts = summary.result.counts || {};
  const conflicts = Array.isArray(summary.result.conflicts) ? summary.result.conflicts : [];
  const folders = Array.isArray(summary.result.folders) ? summary.result.folders : [];
  const drafts = Array.isArray(summary.result.drafts) ? summary.result.drafts : [];
  const comparison = summary.result.comparison || null;
  const repoOnly = Array.isArray(comparison?.repoOnly) ? comparison.repoOnly : [];
  const localOnly = Array.isArray(comparison?.localOnly) ? comparison.localOnly : [];
  const diffConflicts = Array.isArray(comparison?.conflicts) ? comparison.conflicts : [];
  const diffCounts = comparison?.counts || {};
  const previewState = normalizeWritingSyncPreviewState(writingState.syncPreview);
  return `
    <div class="writing-sync-summary">
      <strong>${esc(label)}</strong>
      ${rootDir ? `<span>${esc(rootDir)}</span>` : ""}
      ${summary.result.exportedAt ? `<span>${esc(formatWritingSyncSnapshotMeta(summary.result))}</span>` : ""}
      <div class="writing-sync-counts">
        ${Object.entries(counts).length
          ? Object.entries(counts).map(([key, value]) => `
            <span class="writing-ctx-chip writing-ctx-chip-muted">${esc(formatWritingSyncCount(key, value))}</span>
          `).join("")
          : `<span class="writing-empty">No sync counts reported.</span>`}
      </div>
      ${folders.length || drafts.length ? `
        <div class="writing-sync-snapshot">
          ${folders.length ? `
            <div>
              <strong>Folders</strong>
              ${folders.map((folder) => `<p>${esc(formatWritingSyncFolder(folder))}</p>`).join("")}
            </div>
          ` : ""}
          ${drafts.length ? `
            <div>
              <strong>Drafts</strong>
              ${drafts.map((draft) => `<p>${esc(formatWritingSyncDraft(draft))}</p>`).join("")}
            </div>
          ` : ""}
        </div>
      ` : ""}
      ${comparison ? `
        <div class="writing-sync-diff">
          <strong>Preview</strong>
          <div class="writing-sync-counts">
            ${Object.entries(diffCounts)
              .filter(([, value]) => Number(value) > 0)
              .map(([key, value]) => `
                <span class="writing-ctx-chip writing-ctx-chip-muted">${esc(formatWritingSyncCount(key, value))}</span>
              `).join("") || `<span class="writing-empty">Repo and local Writing Studio state match on the compared sync fields.</span>`}
          </div>
          ${renderWritingSyncPreviewToolbar(previewState, { repoOnly, localOnly, diffConflicts })}
          <div class="writing-sync-preview-groups">
            ${renderWritingSyncPreviewGroup({
              key: "repoOnly",
              title: "Repo only",
              items: repoOnly,
              previewState,
            })}
            ${renderWritingSyncPreviewGroup({
              key: "localOnly",
              title: "Local only",
              items: localOnly,
              previewState,
            })}
            ${renderWritingSyncPreviewGroup({
              key: "conflicts",
              title: "Conflict candidates",
              items: diffConflicts,
              previewState,
            })}
          </div>
        </div>
      ` : ""}
      ${conflicts.length ? `
        <div class="writing-sync-conflicts">
          <strong>Conflicts</strong>
          ${conflicts.slice(0, 3).map((conflict) => renderWritingSyncConflictRow(conflict)).join("")}
          ${conflicts.length > 3 ? `<p>${esc(`${conflicts.length - 3} more conflict${conflicts.length - 3 === 1 ? "" : "s"} reported.`)}</p>` : ""}
        </div>
      ` : ""}
    </div>
  `;
}

function renderDraftCanvas(draft, content) {
  if (writingState.activePanel === "memory-bank") {
    return renderVoiceMemoryBank(draft);
  }
  if (writingState.activePanel === "version-history") {
    return renderVersionHistory(draft);
  }
  const engine = resolveWritingEngine(draft);
  const brief = resolveWritingBrief(draft);
  const candidates = writingState.overview?.trainingCandidates || [];
  const rules = writingState.overview?.rules || [];
  const reviewCount = countStatus(candidates, "proposed");
  const rulesPopoverOpen = writingState.activePopover === "rules";
  return `
    <article class="writing-card writing-canvas-card writing-canvas-card-chat">
      <div class="writing-canvas-header writing-canvas-header-minimal">
        <div class="writing-canvas-titleblock">
          <span class="shell-eyebrow">Writing room</span>
          <h3>${esc(draft.title)}</h3>
        </div>
        <div class="writing-canvas-actions">
          ${_draftStatusPill(draft)}
          ${(draft.status === "changes_requested" && parseWritingMetadata(draft).review?.note) ? `<span class="writing-changes-note" title="${escAttr(parseWritingMetadata(draft).review.note)}">${esc(parseWritingMetadata(draft).review.note.slice(0, 80))}${parseWritingMetadata(draft).review.note.length > 80 ? "…" : ""}</span>` : ""}
          ${draft.status === "changes_requested" ? `<button type="button" class="writing-button writing-button-regenerate" data-writing-action="writing-regenerate-from-feedback" title="Generate a new version using the reviewer feedback. Status returns to Draft — you must submit for review again.">Regenerate from feedback</button>` : ""}
          ${(!draft.status || draft.status === "draft" || draft.status === "changes_requested") ? `<button type="button" class="writing-button writing-button-submit-review" data-writing-action="writing-submit-for-review">Submit for Review</button>` : ""}
          ${reviewCount > 0 ? `<button type="button" class="writing-meta-pill writing-meta-pill-alert writing-meta-pill-btn" data-writing-action="writing-open-proposed-modal">${reviewCount} proposed</button>` : ""}
          <div class="writing-rules-pill-wrap">
            <button type="button" class="writing-meta-pill ${rules.length > 0 ? "writing-meta-pill-active" : ""} writing-rules-pill-button" data-writing-action="writing-toggle-rules" aria-expanded="${rulesPopoverOpen ? "true" : "false"}">${rules.length} rules</button>
            ${rulesPopoverOpen ? `
              <div class="writing-rules-popover">
                <div class="writing-rule-strip-head">
                  <span class="shell-eyebrow">Rules in play</span>
                  <button type="button" class="writing-mini-button writing-rule-strip-button" data-writing-action="writing-open-memory-bank">Open memory bank</button>
                </div>
                <div class="writing-rule-strip-list">
                  ${rules.length ? rules.map((rule) => `
                    <div class="writing-rule-chip">
                      <strong>${esc(rule.title || "Untitled rule")}</strong>
                      <span>${esc(rule.rule_type || "approved rule")}</span>
                    </div>
                  `).join("") : `<span class="writing-empty">Approved rules will appear here after review.</span>`}
                </div>
              </div>
            ` : ""}
          </div>
          <button type="button" class="writing-button" data-writing-action="writing-open-version-history">History</button>
          <button type="button" class="writing-button writing-button-rdf" data-writing-action="writing-save-version">Save Version</button>
        </div>
      </div>

      <section class="writing-chat-shell ${writingState.dragActive ? "is-dragging" : ""}" aria-label="Draft composer" data-writing-dropzone>
        ${renderContextStrip(draft, engine, brief)}
        <div class="writing-chat-thread" data-writing-chat-thread>
          ${renderWritingChatThread(draft, content, brief)}
        </div>

        <div class="writing-composer-dock">
          <input type="file" data-writing-input="attachment-picker" class="writing-hidden-input" multiple>
          <div class="writing-composer-attachments" data-writing-attachments>
            ${renderWritingAttachmentChips()}
          </div>
          <div class="writing-chat-composer-card">
            <textarea data-writing-input="draft-request" class="writing-compose-input writing-compose-input-chat" placeholder="Ask for a first draft, paste a brief, request a rewrite, or drop in source notes."></textarea>
            <div class="writing-chat-composer-bar">
              <div class="writing-chat-composer-left">
                <button type="button" class="writing-chat-attach-trigger" data-writing-action="writing-open-file-picker" aria-label="Attach files">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <line x1="12" y1="5" x2="12" y2="19"></line>
                    <line x1="5" y1="12" x2="19" y2="12"></line>
                  </svg>
                </button>
                <span class="writing-chat-attach-label">Attach file</span>
                ${writingState.attachments.length ? `<span class="writing-chat-attach-count">${writingState.attachments.length}</span>` : ""}
              </div>
              <button type="button" class="writing-chat-send-button" data-writing-action="writing-compose-request" aria-label="Send">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </section>

    </article>
  `;
}

function renderMemorySidebarCard() {
  const candidates = writingState.overview?.trainingCandidates || [];
  const rules = writingState.overview?.rules || [];
  const sources = writingState.overview?.sources || [];
  const examplesCount = Number(writingState.overview?.counts?.examples || 0);
  const hasInstalledBaseMemory = sources.length > 0 || rules.length > 0 || examplesCount > 0;
  const activeProfile = writingState.overview?.activeProfile;
  return `
    <article class="writing-card writing-memory-rail-card">
      <div class="writing-memory-rail-head">
        <span class="shell-eyebrow">Memory</span>
        <button type="button" class="writing-library-new-folder-link" data-writing-action="writing-open-memory-bank">Open →</button>
      </div>
      ${activeProfile
        ? `<p class="writing-memory-rail-profile">${esc(activeProfile.name)}</p>`
        : (!hasInstalledBaseMemory
          ? `<button type="button" class="writing-button writing-button-primary writing-seed-button" data-writing-action="writing-import-seed">Install Base Memory</button>`
          : "")
      }
    </article>
  `;
}

function syncWritingModalPortal() {
  const candidates = writingState.overview?.trainingCandidates || [];
  if (writingState.activeModal !== "proposed-review") {
    if (_writingModalPortal) {
      _writingModalPortal.remove();
      _writingModalPortal = null;
    }
    return;
  }
  if (!_writingModalPortal) {
    _writingModalPortal = document.createElement("div");
    _writingModalPortal.addEventListener("click", (e) => {
      const button = e.target.closest("[data-writing-action]");
      if (button) handleWritingStudioAction(button);
    });
    document.body.appendChild(_writingModalPortal);
  }
  _writingModalPortal.innerHTML = renderProposedModal(candidates);
}

function renderProposedModal(candidates) {
  const proposed = candidates.filter((c) => c.status === "proposed");
  return `
    <div class="writing-modal-overlay" data-writing-action="writing-close-modal" role="dialog" aria-modal="true" aria-label="Proposed training rules">
      <div class="writing-modal" role="document">
        <div class="writing-modal-head">
          <div>
            <span class="shell-eyebrow">Training review</span>
            <h3>Proposed rules</h3>
          </div>
          <button type="button" class="writing-modal-close" data-writing-action="writing-close-modal" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <p class="writing-modal-description">These voice patterns were detected during your draft session. Approve to add them as permanent rules, or reject to dismiss.</p>
        <div class="writing-modal-body">
          ${proposed.length === 0
            ? `<p class="writing-empty">No pending proposals right now.</p>`
            : proposed.map((c) => `
              <div class="writing-candidate writing-candidate-modal">
                <div class="writing-candidate-top">
                  <span class="writing-pill">${esc(c.candidate_type || "rule")}</span>
                  ${c.draft_title ? `<small>from "${esc(c.draft_title)}"</small>` : ""}
                </div>
                <p>${esc(c.proposed_text)}</p>
                <div class="writing-candidate-actions">
                  <button type="button" class="writing-mini-button writing-mini-button-approve" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${c.id}" data-writing-decision="approved">Approve</button>
                  <button type="button" class="writing-mini-button" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${c.id}" data-writing-decision="one_time">One-time</button>
                  <button type="button" class="writing-mini-button writing-mini-button-reject" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${c.id}" data-writing-decision="rejected">Reject</button>
                </div>
              </div>
            `).join("")}
        </div>
      </div>
    </div>
  `;
}

function renderVoiceMemoryBank(draft) {
  const rules = writingState.overview?.rules || [];
  const sources = writingState.overview?.sources || [];
  const examples = writingState.overview?.examples || [];
  const examplesCount = Number(writingState.overview?.counts?.examples || examples.length || 0);
  const activeProfile = writingState.overview?.activeProfile;
  const totalAssets = sources.length + rules.length + examplesCount;
  return `
    <article class="writing-card writing-canvas-card writing-memory-bank-card">
      <div class="writing-canvas-header writing-memory-bank-header">
        <div class="writing-canvas-titleblock">
          <span class="shell-eyebrow">Voice memory bank</span>
          <h3>${esc(activeProfile?.name || "Amira Marketing Voice")}</h3>
          <div class="writing-memory-bank-meta-row">
            ${draft ? `<span>${esc(draft.title)}</span>` : ""}
            <span>${totalAssets} memory assets</span>
          </div>
        </div>
        <div class="writing-canvas-actions">
          <button type="button" class="writing-button" data-writing-action="writing-open-draft-panel">Back to Draft</button>
        </div>
      </div>

      <section class="writing-memory-bank-grid">
        <article class="writing-memory-bank-section">
          <div class="writing-memory-bank-section-head writing-memory-bank-section-head-compact">
            <span class="shell-eyebrow">Sources <span class="writing-memory-count">${sources.length}</span></span>
          </div>
          <div class="writing-source-stack">
            ${sources.length ? sources.map(renderSource).join("") : `
              <p class="writing-empty">No source documents imported yet.</p>
            `}
          </div>
        </article>

        <article class="writing-memory-bank-section">
          <div class="writing-memory-bank-section-head writing-memory-bank-section-head-compact">
            <span class="shell-eyebrow">Rules <span class="writing-memory-count">${rules.length}</span></span>
          </div>
          <div class="writing-rule-list">
            ${rules.length ? rules.map(renderRuleCard).join("") : `
              <p class="writing-empty">Approved rules will appear here after review.</p>
            `}
          </div>
        </article>

        <article class="writing-memory-bank-section">
          <div class="writing-memory-bank-section-head writing-memory-bank-section-head-compact">
            <span class="shell-eyebrow">Examples <span class="writing-memory-count">${examplesCount}</span></span>
          </div>
          <div class="writing-example-list">
            ${examples.length ? examples.map(renderExampleCard).join("") : `
              <p class="writing-empty">Imported templates and references will appear here.</p>
            `}
          </div>
        </article>
      </section>
    </article>
  `;
}

function renderActiveDraftCard(draft, engine, brief, activeProfile, googleOverview) {
  const engineLabel = PROVIDER_LABELS[engine.provider] || engine.provider;
  const modelLabel = resolveModelLabel(engine.provider, engine.model);
  const organizationBits = [draft.folder_name, draft.campaign_id].filter(Boolean);
  const googleDoc = resolveGoogleDocLink(draft);
  const googleDocPreview = resolveGoogleDocPreviewText(draft);
  const exportLabel = resolveGoogleDocExportLabel(googleOverview, googleDoc);
  const connectorHint = renderGoogleDocConnectorHint(googleOverview);
  const importLabel = googleDoc?.documentId ? "Refresh from linked Google Doc" : "Attach and import Google Doc";
  const linkedStatus = googleDoc?.documentId ? "Linked Google Doc" : "No linked Google Doc";
  return `
    <article class="writing-card writing-active-card">
      <div class="writing-active-card-header">
        <span class="shell-eyebrow">Active draft</span>
        <button type="button" class="writing-new-link" data-writing-action="writing-clear-draft">+ New</button>
      </div>
      <div class="writing-active-title-row">
        <strong class="writing-active-title">${esc(draft.title || "Untitled draft")}</strong>
        <span class="writing-active-meta">${esc(draft.asset_type || "draft")} · ${esc(draft.status === "ready_for_review" ? "waiting for approval" : draft.status === "changes_requested" ? "changes requested" : draft.status || "draft")}</span>
      </div>
      <div class="writing-active-chips">
        <span class="writing-ctx-chip">${esc(engineLabel)}</span>
        ${engine.model ? `<span class="writing-ctx-chip">${esc(modelLabel)}</span>` : ""}
        <span class="writing-ctx-chip writing-ctx-chip-muted">${esc(activeProfile?.name || "Amira Marketing Voice")}</span>
        ${organizationBits.map((bit) => `<span class="writing-ctx-chip writing-ctx-chip-muted">${esc(bit)}</span>`).join("")}
      </div>
      <div class="writing-active-doc-card" data-linked="${googleDoc?.documentId ? "true" : "false"}">
        <div class="writing-active-doc-head">
          <span class="writing-active-doc-status">${esc(linkedStatus)}</span>
          ${googleDoc?.documentId ? `<span class="writing-active-doc-pill">Attached</span>` : ""}
        </div>
        ${googleDoc?.documentId ? `
          <a class="writing-active-link" href="${escAttr(googleDoc.url)}" target="_blank" rel="noreferrer">${esc(googleDoc.label || googleDoc.title || "Open Google Doc")}</a>
          ${googleDocPreview ? `<p class="writing-active-doc-preview">${esc(googleDocPreview)}</p>` : ""}
          <div class="writing-active-doc-actions">
            <button type="button" class="writing-mini-button writing-button-rdf writing-active-doc-button writing-active-doc-button-primary" data-writing-action="writing-import-google-doc" ${googleOverview?.connected && googleOverview?.docsImportReady ? "" : "disabled"}>Refresh into Draft</button>
            <button type="button" class="writing-mini-button writing-button-rdf-outline writing-active-doc-button writing-active-doc-button-secondary" data-writing-action="writing-export-google-doc" ${googleOverview?.connected && googleOverview?.docsExportReady ? "" : "disabled"}>${esc(exportLabel)}</button>
          </div>
          <div class="writing-active-doc-actions writing-active-doc-actions-secondary">
            <button type="button" class="writing-mini-button writing-button-rdf-outline writing-active-doc-button writing-active-doc-button-quiet" data-writing-action="writing-change-google-doc-link">Change Link</button>
            <button type="button" class="writing-mini-button writing-button-rdf-outline writing-button-rdf-outline-danger writing-active-doc-button writing-active-doc-button-danger" data-writing-action="writing-remove-google-doc-link">Remove Link</button>
          </div>
        ` : `
          <input data-writing-input="draft-google-doc-url" class="writing-input writing-active-google-doc-input" placeholder="Paste a Google Doc link to attach this draft">
          <div class="writing-active-doc-actions">
            <button type="button" class="writing-mini-button writing-button-rdf writing-active-doc-button writing-active-doc-button-primary" data-writing-action="writing-import-google-doc" ${googleOverview?.connected && googleOverview?.docsImportReady ? "" : "disabled"}>${esc(importLabel)}</button>
            <button type="button" class="writing-mini-button writing-button-rdf-outline writing-active-doc-button writing-active-doc-button-secondary" data-writing-action="writing-export-google-doc" ${googleOverview?.connected && googleOverview?.docsExportReady ? "" : "disabled"}>${esc(exportLabel)}</button>
          </div>
        `}
        ${connectorHint ? `<span class="writing-active-action-hint">${esc(connectorHint)}</span>` : ""}
      </div>
    </article>
  `;
}

function renderContextStrip(draft, engine, brief) {
  const engineLabel = PROVIDER_LABELS[engine.provider] || engine.provider;
  const modelLabel = resolveModelLabel(engine.provider, engine.model);
  const audience = draft.audience || "";
  const channel = draft.channel || "";
  const campaignId = draft.campaign_id || "";
  const folderId = draft.folder_id ? String(draft.folder_id) : "";
  const folders = writingState.overview?.folders || [];
  const rules = writingState.overview?.rules || [];
  const hasBrief = brief && brief.trim().length > 0;
  const metaParts = [engineLabel, modelLabel, draft.folder_name || "", campaignId, audience, channel].filter(Boolean);
  return `
    <div class="writing-context-strip">
      <div class="writing-context-meta-row">
        ${metaParts.map((part, i) => `
          <span class="writing-ctx-meta-item">${esc(part)}</span>
          ${i < metaParts.length - 1 ? `<span class="writing-ctx-dot" aria-hidden="true">·</span>` : ""}
        `).join("")}
        <span class="writing-ctx-dot" aria-hidden="true">·</span>
        <div class="writing-ctx-brief-wrap">
          <button type="button" class="writing-ctx-brief-trigger" data-writing-action="writing-toggle-brief">${hasBrief ? "Brief" : "+ Brief"}</button>
          <div class="writing-ctx-brief-dropdown" hidden>
            <textarea data-writing-input="draft-brief" class="writing-learning-input writing-ctx-brief-textarea" placeholder="Goal, angle, audience context, or source facts.">${esc(brief)}</textarea>
            <div class="writing-ctx-brief-meta">
              <select data-writing-input="draft-folder-id" class="writing-select writing-ctx-inline-input">
                <option value="">No folder</option>
                ${folders.map((folder) => `
                  <option value="${folder.id}" ${String(folder.id) === folderId ? "selected" : ""}>${esc(folder.name)}</option>
                `).join("")}
              </select>
              <input data-writing-input="draft-campaign-id" class="writing-input writing-ctx-inline-input" value="${escAttr(campaignId)}" placeholder="Campaign id">
              <input data-writing-input="draft-audience" class="writing-input writing-ctx-inline-input" value="${escAttr(audience)}" placeholder="Audience">
              <input data-writing-input="draft-channel" class="writing-input writing-ctx-inline-input" value="${escAttr(channel)}" placeholder="Channel">
            </div>
          </div>
        </div>
        <div class="writing-ctx-propose-wrap">
          <button type="button" class="writing-ctx-brief-trigger" data-writing-action="writing-toggle-propose">+ Propose</button>
          <div class="writing-propose-dropdown" hidden>
            <div class="writing-propose-form">
              <select data-writing-input="learning-type" class="writing-select">
                <option value="rule">Rule</option>
                <option value="preference">Preference</option>
                <option value="anti_pattern">Anti-pattern</option>
                <option value="example">Example</option>
              </select>
              <textarea data-writing-input="learning-text" class="writing-learning-input" placeholder="Capture a durable voice rule from this draft."></textarea>
              <button type="button" class="writing-button" data-writing-action="writing-propose-learning">Add to Review</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderSource(source) {
  const preview = source.normalized_content || source.original_content || "";
  const isEditing = isEditingMemory("source", source.id);
  return `
    <div class="writing-source-row">
      <span class="writing-source-key">${esc(source.source_key || source.source_type || "source")}</span>
      <div class="writing-source-row-body">
        <strong>${esc(source.title)}</strong>
        ${isEditing ? renderMemoryEditForm("source", preview) : ""}
      </div>
      <div class="writing-memory-row-side">
        ${source.source_type ? `<small>${esc(source.source_type)}</small>` : ""}
        <button type="button" class="writing-mini-button" data-writing-action="writing-edit-memory" data-writing-memory-kind="source" data-writing-memory-id="${source.id}">Edit</button>
      </div>
    </div>
  `;
}

function renderRuleCard(rule) {
  const isEditing = isEditingMemory("rule", rule.id);
  return `
    <div class="writing-rule">
      <div class="writing-memory-card-head">
        <div>
          <strong>${esc(rule.title)}</strong>
          <span>${esc(rule.rule_type || "voice")}</span>
        </div>
        <button type="button" class="writing-mini-button" data-writing-action="writing-edit-memory" data-writing-memory-kind="rule" data-writing-memory-id="${rule.id}">Edit</button>
      </div>
      ${isEditing ? renderMemoryEditForm("rule", rule.body || "") : (rule.body ? `<p>${esc(compactExcerpt(rule.body, 220))}</p>` : "")}
    </div>
  `;
}

function renderExampleCard(example) {
  const isEditing = isEditingMemory("example", example.id);
  return `
    <div class="writing-example-card">
      <div class="writing-example-top">
        <strong>${esc(example.title)}</strong>
        <span>${esc(example.example_type || "reference")}</span>
      </div>
      <div class="writing-memory-card-head writing-memory-card-head-example">
        ${example.asset_type ? `<small>${esc(example.asset_type)}</small>` : "<small>reference</small>"}
        <button type="button" class="writing-mini-button" data-writing-action="writing-edit-memory" data-writing-memory-kind="example" data-writing-memory-id="${example.id}">Edit</button>
      </div>
      ${isEditing ? renderMemoryEditForm("example", example.body || "") : (example.body ? `<p>${esc(compactExcerpt(example.body, 220))}</p>` : "")}
    </div>
  `;
}

function renderMemoryEditForm(kind, body) {
  return `
    <div class="writing-memory-editor">
      <textarea data-writing-input="memory-body" class="writing-learning-input writing-memory-editor-textarea" placeholder="Revise this memory module.">${esc(body)}</textarea>
      <div class="writing-memory-editor-actions">
        <button type="button" class="writing-mini-button" data-writing-action="writing-save-memory-edit">Save</button>
        <button type="button" class="writing-mini-button" data-writing-action="writing-cancel-memory-edit">Cancel</button>
      </div>
    </div>
  `;
}

function renderWritingEngineControls(scope, engine) {
  const provider = engine.provider || "claude-code";
  const models = getWritingModels(provider);
  const currentModel = engine.model || models[0]?.value || "";
  return `
    <div class="writing-engine-controls" data-writing-engine-scope="${scope}">
      <label class="writing-setup-card">
        <span>Writing engine</span>
        <select data-writing-input="${scope}-provider" class="writing-select">
          ${WRITING_ENGINE_OPTIONS.map((providerId) => `
            <option value="${providerId}" ${providerId === provider ? "selected" : ""}>${esc(PROVIDER_LABELS[providerId] || providerId)}</option>
          `).join("")}
        </select>
      </label>
      <label class="writing-setup-card">
        <span>Model</span>
        <select data-writing-input="${scope}-model" class="writing-select">
          ${models.map((model) => `
            <option value="${escAttr(model.value)}" ${model.value === currentModel ? "selected" : ""}>${esc(model.label)}</option>
          `).join("")}
        </select>
      </label>
    </div>
  `;
}

function renderMeta(label, value) {
  return `
    <div class="writing-meta">
      <span>${esc(label)}</span>
      <strong>${esc(value || "Not set")}</strong>
    </div>
  `;
}

function renderVersionHistory(draft) {
  const versions = Array.isArray(draft?.versions) ? draft.versions : [];
  const formatDate = (ts) => {
    if (!ts) return "—";
    const d = new Date(typeof ts === "number" && ts < 1e12 ? ts * 1000 : ts);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) +
      " · " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  };
  const wordCount = (text) => (text || "").trim().split(/\s+/).filter(Boolean).length;
  const SOURCE_LABELS = {
    manual: "Manual save",
    ai: "AI generated",
    restore: "Restored",
    import: "Imported",
    submit_for_review: "Submitted",
    regenerated: "Regenerated",
    invoke: "Programmatic",
    "google-doc": "Google Doc",
    sync_reconcile: "Sync",
  };

  // Edit-history enrichment data (loaded async; may be absent on first render)
  const editHistory = writingState.editHistoryMap?.[draft?.id];
  const historyByVersionId = new Map(
    (editHistory?.versions || []).map((h) => [h.versionId, h])
  );

  return `
    <article class="writing-card writing-canvas-card writing-version-history-card">
      <div class="writing-canvas-header writing-memory-bank-header">
        <div class="writing-canvas-titleblock">
          <span class="shell-eyebrow">Version history</span>
          <h3>${esc(draft?.title || "Draft")}</h3>
          <p>${versions.length} saved version${versions.length === 1 ? "" : "s"}</p>
        </div>
        <div class="writing-canvas-actions">
          <button type="button" class="writing-button" data-writing-action="writing-open-draft-panel">Back to Draft</button>
        </div>
      </div>
      <div class="writing-version-list">
        ${versions.length === 0 ? `
          <div class="writing-version-empty">
            <p>No saved versions yet. Click <strong>Save Version</strong> in the draft toolbar to create one.</p>
          </div>
        ` : versions.map((v) => {
          const words = wordCount(v.content);
          const preview = (v.content || "").replace(/\s+/g, " ").trim().slice(0, 100);
          const label = SOURCE_LABELS[v.source] || v.source || "Saved";
          const isLatest = v.id === versions[0]?.id;

          // Enrichment from edit-history API
          const hist = historyByVersionId.get(v.id);
          const diff = hist?.diff ?? null;
          const approvals = hist?.approvals ?? [];
          const diffBadge = diff && (diff.wordDelta !== 0 || diff.linesAdded !== 0 || diff.linesRemoved !== 0)
            ? `<span class="writing-version-badge writing-version-badge-diff">${diff.wordDelta >= 0 ? "+" : ""}${diff.wordDelta}w</span>`
            : "";
          const approvalBadges = approvals.map((a) => {
            if (a.status === "approved") return `<span class="writing-version-badge writing-version-badge-approved" title="${escAttr(a.reviewer || "")}">Approved</span>`;
            if (a.status === "rejected") return `<span class="writing-version-badge writing-version-badge-rejected" title="${escAttr(a.note || "")}">Rejected</span>`;
            if (a.status === "pending") return `<span class="writing-version-badge writing-version-badge-pending">Pending review</span>`;
            return "";
          }).join("");

          return `
            <div class="writing-version-row">
              <div class="writing-version-meta">
                <div class="writing-version-header">
                  <span class="writing-version-number">v${v.version_number}</span>
                  ${isLatest ? `<span class="writing-version-badge writing-version-badge-latest">Current</span>` : ""}
                  <span class="writing-version-badge">${esc(label)}</span>
                  ${diffBadge}
                  ${approvalBadges}
                </div>
                <span class="writing-version-date">${formatDate(v.created_at)}</span>
                <span class="writing-version-words">${words.toLocaleString()} words</span>
                ${v.change_note ? `<span class="writing-version-note">${esc(v.change_note)}</span>` : ""}
              </div>
              <p class="writing-version-preview">${esc(preview)}${v.content?.length > 100 ? "…" : ""}</p>
              ${isLatest ? "" : `
                <button type="button" class="writing-button writing-version-restore-btn" data-writing-action="writing-restore-version" data-writing-version-id="${v.id}">Restore</button>
              `}
            </div>
          `;
        }).join("")}
      </div>
    </article>
  `;
}

function buildNewDraftRequest({ assetType, audience, channel, brief }) {
  const head = [`Write a ${assetType}`];
  if (audience) head.push(`for ${audience}`);
  if (channel) head.push(`on ${channel}`);
  return brief ? `${head.join(" ")}.\n\n${brief}` : `${head.join(" ")}.`;
}

function renderNewDraftCanvas() {
  const overview = writingState.overview || {};
  const activeProfile = overview.activeProfile;
  const googleOverview = writingState.googleOverview;
  const engine = resolveWritingEngine(null, activeProfile);
  return `
    <article class="writing-card writing-canvas-card writing-new-draft-canvas">
      <div class="writing-new-draft-inner">
        <div class="writing-new-draft-head">
          <span class="shell-eyebrow">Writing Studio</span>
          <h2>New draft</h2>
          ${activeProfile ? `<p class="writing-new-draft-profile-chip">Voice: <strong>${esc(activeProfile.name)}</strong></p>` : ""}
        </div>

        <div class="writing-new-draft-section">
          <p class="writing-new-draft-section-label">The draft</p>
          <div class="writing-new-draft-fields">
            <label class="writing-field-label">
              <span class="writing-field-header">Title</span>
              <input data-writing-input="new-title" class="writing-input writing-input-title" placeholder="Give it a name" autofocus>
            </label>
            <label class="writing-field-label">
              <span class="writing-field-header">Asset type</span>
              <input data-writing-input="new-asset-type" class="writing-input" placeholder="e.g. email sequence, blog post, one-pager">
            </label>
            <label class="writing-field-label">
              <span class="writing-field-header">Brief <span class="writing-field-optional">optional</span></span>
              <textarea data-writing-input="new-brief" class="writing-learning-input writing-brief-input writing-new-draft-brief" placeholder="Paste a brief, rough opening, source copy, or notes — or leave blank for a clean page."></textarea>
            </label>
          </div>
        </div>

        <div class="writing-new-draft-divider"></div>

        <div class="writing-new-draft-section">
          <p class="writing-new-draft-section-label">Setup</p>
          <div class="writing-new-draft-grid">
            <label class="writing-field-label">
              <span class="writing-field-header">Folder</span>
              <select data-writing-input="new-folder-id" class="writing-select">
                <option value="">No folder</option>
                ${(overview.folders || []).map((folder) => `
                  <option value="${folder.id}" ${Number(folder.id) === Number(writingState.filters.folderId) ? "selected" : ""}>${esc(folder.name)}</option>
                `).join("")}
              </select>
            </label>
            <label class="writing-field-label">
              <span class="writing-field-header">Audience <span class="writing-field-optional">optional</span></span>
              <input data-writing-input="new-audience" class="writing-input" placeholder="e.g. SMB marketers">
            </label>
            <label class="writing-field-label">
              <span class="writing-field-header">Channel <span class="writing-field-optional">optional</span></span>
              <input data-writing-input="new-channel" class="writing-input" placeholder="e.g. LinkedIn, email">
            </label>
            <label class="writing-field-label">
              <span class="writing-field-header">Campaign <span class="writing-field-optional">optional</span></span>
              <input data-writing-input="new-campaign-id" class="writing-input" value="${escAttr(writingState.filters.campaignId || "")}" placeholder="Campaign ID">
            </label>
          </div>
          <label class="writing-field-label writing-new-draft-gdoc-row">
            <span class="writing-field-header">Import from Google Doc <span class="writing-field-optional">optional</span></span>
            <input data-writing-input="new-google-doc-url" class="writing-input" placeholder="Paste a Google Doc link">
            <small class="writing-browser-caption">${renderGoogleDocImportHint(googleOverview)}</small>
          </label>
        </div>

        <div class="writing-new-draft-engine-row">
          ${renderWritingEngineControls("new", engine)}
        </div>

        <button type="button" class="writing-button writing-button-primary writing-new-draft-submit" data-writing-action="writing-new-draft">Create Draft</button>
        <button type="button" class="writing-new-draft-blank-link" data-writing-action="writing-new-draft-blank">or start with a blank canvas</button>
      </div>
    </article>
  `;
}

function renderEmptyCanvas() {
  return `
    <article class="writing-card writing-empty-canvas writing-empty-canvas-chat">
      <span class="shell-eyebrow">Writing room</span>
      <h3>Open a draft to start the writing thread.</h3>
      <p>The center column is designed to feel like a dedicated writing chat once a draft is active.</p>
    </article>
  `;
}

function renderWritingChatThread(draft, content, brief) {
  const hasMessages = writingState.chatHistory.length > 0;
  const googleDoc = resolveGoogleDocLink(draft);
  const googleDocPreview = resolveGoogleDocPreviewText(draft);
  const showGoogleDocPreview = googleDoc?.documentId && googleDocPreview && googleDocPreview !== brief;
  const draftBody = typeof content === "string" ? content.trim() : "";
  return `
    ${draftBody ? `
      <article class="writing-chat-message writing-chat-message-draft">
        <div class="writing-chat-bubble writing-chat-bubble-draft">
          <div class="writing-chat-meta">Current draft</div>
          ${renderWritingRichText(draftBody)}
        </div>
      </article>
    ` : ""}
    ${brief ? `
      <article class="writing-chat-message writing-chat-message-user">
        <div class="writing-chat-bubble writing-chat-bubble-user">
          <div class="writing-chat-meta">Brief</div>
          ${renderWritingRichText(brief)}
        </div>
      </article>
    ` : ""}
    ${showGoogleDocPreview ? `
      <article class="writing-chat-message writing-chat-message-user">
        <div class="writing-chat-bubble writing-chat-bubble-user writing-chat-bubble-source">
          <div class="writing-chat-meta">Linked Google Doc</div>
          ${renderWritingRichText(googleDocPreview)}
        </div>
      </article>
    ` : ""}
    ${writingState.chatHistory.map(renderWritingChatEntry).join("")}
    ${!brief && !hasMessages ? `
      <div class="writing-chat-empty">
        <p>Ask for a draft, paste a brief, or request a rewrite. The AI will write directly in this thread.</p>
      </div>
    ` : ""}
  `;
}


function renderWritingChatEntry(entry) {
  const isUser = entry.role === "user";
  const isPending = Boolean(entry.pending);
  const traceSummary = !isUser && entry.trace ? buildWritingTraceSummary(entry.trace, entry.engine) : null;
  return `
    <article class="writing-chat-message ${isUser ? "writing-chat-message-user" : "writing-chat-message-assistant"} ${isPending ? "writing-chat-message-pending" : ""}">
      <div class="writing-chat-bubble ${isUser ? "writing-chat-bubble-user" : ""} ${isPending ? "writing-chat-bubble-pending" : ""}">
        <div class="writing-chat-meta">${esc(entry.label || (isUser ? "You" : "Writing partner"))}</div>
        ${renderWritingRichText(entry.text || "")}
        ${isPending ? `
          <div class="writing-chat-pending-row" aria-live="polite">
            <span class="writing-chat-pending-dot"></span>
            <span class="writing-chat-pending-dot"></span>
            <span class="writing-chat-pending-dot"></span>
          </div>
        ` : ""}
        ${traceSummary ? `
          <div class="writing-trace-card">
            <div class="writing-trace-title">Memory trace</div>
            <div class="writing-trace-actions">
              <button type="button" class="writing-mini-button" data-writing-action="writing-save-chat-version" data-writing-entry-id="${escAttr(entry.id)}">Save as Version</button>
            </div>
            <div class="writing-trace-meta">
              <span>${esc(traceSummary.profile)}</span>
              ${traceSummary.engineLabel ? `<span>${esc(traceSummary.engineLabel)}</span>` : ""}
            </div>
            <div class="writing-trace-meta">
              <span>${esc(traceSummary.rulesLabel)}</span>
              <span>${esc(traceSummary.examplesLabel)}</span>
            </div>
            <p>${esc(traceSummary.contextLabel)}</p>
            ${traceSummary.ruleTitles.length ? `<p>Rules: ${esc(traceSummary.ruleTitles.join(", "))}</p>` : ""}
            ${traceSummary.exampleTitles.length ? `<p>Examples: ${esc(traceSummary.exampleTitles.join(", "))}</p>` : ""}
            ${entry.prompt ? `
              <details class="writing-trace-details">
                <summary>Prompt details</summary>
                <div class="writing-trace-prompt-block">
                  <strong>System prompt</strong>
                  <pre>${esc(entry.prompt.systemPrompt || "")}</pre>
                </div>
                <div class="writing-trace-prompt-block">
                  <strong>User prompt</strong>
                  <pre>${esc(entry.prompt.userPrompt || "")}</pre>
                </div>
              </details>
            ` : ""}
          </div>
        ` : ""}
        ${entry.attachments?.length ? `
          <div class="writing-chat-attachment-stack">
            ${entry.attachments.map((attachment) => `
              <span class="writing-thread-attachment">
                <strong>${esc(attachment.name)}</strong>
                <small>${esc(formatAttachmentMeta(attachment))}</small>
              </span>
            `).join("")}
          </div>
        ` : ""}
      </div>
    </article>
  `;
}

function renderWritingAttachmentChips() {
  if (!writingState.attachments.length) {
    return "";
  }
  return writingState.attachments.map((attachment) => `
    <div class="writing-attachment-chip">
      <div>
        <strong>${esc(attachment.name)}</strong>
        <small>${esc(formatAttachmentMeta(attachment))}</small>
      </div>
      <button type="button" data-writing-action="writing-remove-attachment" data-writing-attachment-id="${escAttr(attachment.id)}" aria-label="Remove ${escAttr(attachment.name)}">×</button>
    </div>
  `).join("");
}

function renderCandidate(candidate) {
  const proposed = candidate.status === "proposed";
  return `
    <div class="writing-candidate">
      <div class="writing-candidate-top">
        <span class="writing-pill">${esc(candidate.candidate_type || "rule")}</span>
        <span>${esc(candidate.status || "proposed")}</span>
      </div>
      <p>${esc(candidate.proposed_text)}</p>
      ${candidate.draft_title ? `<small>From ${esc(candidate.draft_title)}</small>` : ""}
      ${proposed ? `
        <div class="writing-candidate-actions">
          <button type="button" class="writing-mini-button" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${candidate.id}" data-writing-decision="approved">Approve</button>
          <button type="button" class="writing-mini-button" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${candidate.id}" data-writing-decision="one_time">One-time</button>
          <button type="button" class="writing-mini-button" data-writing-action="writing-candidate-decision" data-writing-candidate-id="${candidate.id}" data-writing-decision="rejected">Reject</button>
        </div>
      ` : ""}
    </div>
  `;
}

function renderWritingStudioError(error) {
  return `
    <section class="page-hero writing-studio-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Writing Studio</span>
          <span class="page-hero-status" data-tone="error">Failed</span>
        </div>
        <h1>Could not load Writing Studio.</h1>
        <p class="page-hero-lede">${esc(error?.message || "Unknown error")}</p>
      </div>
    </section>
  `;
}

function latestDraftContent(draft) {
  return draft?.versions?.[0]?.content || "";
}

function readFolderFilterValue(value) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function matchesWritingFilters(draft, filters = writingState.filters) {
  const folderMatch = !filters?.folderId || Number(draft.folder_id) === Number(filters.folderId);
  const campaignMatch = !filters?.campaignId || String(draft.campaign_id || "") === String(filters.campaignId);
  return folderMatch && campaignMatch;
}

function filterWritingDrafts(drafts = [], filters = writingState.filters) {
  return (drafts || []).filter((draft) => matchesWritingFilters(draft, filters));
}

function resolveWritingFilters({ handoff = null, filters = writingState.filters, drafts = [], folders = [], campaigns = [] } = {}) {
  const nextFilters = {
    folderId: readFolderFilterValue(filters?.folderId),
    campaignId: String(filters?.campaignId || handoff?.campaignId || "").trim(),
  };
  if (nextFilters.folderId && !folders.some((folder) => Number(folder.id) === Number(nextFilters.folderId))) {
    nextFilters.folderId = null;
  }
  if (nextFilters.campaignId && !campaigns.some((campaign) => String(campaign.id || "") === nextFilters.campaignId)) {
    nextFilters.campaignId = "";
  }
  return nextFilters;
}

function closeWritingToolbarMenus() {
  document.querySelectorAll(".writing-browser-toolbar-menu[open]").forEach((menu) => {
    menu.removeAttribute("open");
  });
}

function countStatus(items, status) {
  return (items || []).filter((item) => item.status === status).length;
}

function isEditingMemory(kind, id) {
  return writingState.memoryEditor?.kind === kind && Number(writingState.memoryEditor?.id) === Number(id);
}

function readValue(selector) {
  const el = document.querySelector(selector);
  return typeof el?.value === "string" ? el.value.trim() : "";
}

function readWritingSyncRootDir() {
  return readValue("[data-writing-input='sync-root-dir']");
}

function readFolderField(selector) {
  return readFolderFilterValue(readValue(selector));
}

function isFolderCollapsed(folderId) {
  return Array.isArray(writingState.collapsedFolderIds)
    && writingState.collapsedFolderIds.includes(Number(folderId));
}

function toggleWritingFolderCollapse(folderId) {
  const numericId = readFolderFilterValue(folderId);
  if (!numericId) return;
  const collapsed = new Set((writingState.collapsedFolderIds || []).map((id) => Number(id)));
  if (collapsed.has(numericId)) {
    collapsed.delete(numericId);
  } else {
    collapsed.add(numericId);
  }
  writingState = {
    ...writingState,
    collapsedFolderIds: [...collapsed],
  };
  syncWritingLibraryDom();
}

function buildWritingFolderParentMap(folders = []) {
  const parentMap = new Map();
  for (const folder of folders) {
    const parentId = Number(folder.parent_folder_id || 0) || 0;
    const current = parentMap.get(parentId) || [];
    current.push(folder);
    parentMap.set(parentId, current);
  }
  for (const [key, items] of parentMap.entries()) {
    parentMap.set(key, [...items].sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" })));
  }
  return parentMap;
}

function collectWritingFolderDescendants(folderId, foldersByParent, results) {
  const numericId = Number(folderId || 0);
  if (!numericId || results.has(numericId)) return;
  results.add(numericId);
  const children = foldersByParent.get(numericId) || [];
  children.forEach((child) => collectWritingFolderDescendants(child.id, foldersByParent, results));
}

function matchesWritingCampaignTree(folder, campaignId, folders = []) {
  const target = String(campaignId || "");
  if (!target) return true;
  const byId = new Map((folders || []).map((entry) => [Number(entry.id), entry]));
  let current = folder;
  while (current) {
    if (String(current.campaign_id || "") === target) return true;
    const parentId = Number(current.parent_folder_id || 0);
    current = parentId ? byId.get(parentId) || null : null;
  }
  return false;
}

function renderFolderParentOptions(folders = [], selectedId = null) {
  const foldersByParent = buildWritingFolderParentMap(folders);
  const renderBranch = (parentId = 0, depth = 0) => {
    const children = foldersByParent.get(Number(parentId || 0)) || [];
    return children.map((folder) => {
      const prefix = depth ? `${"  ".repeat(depth)}↳ ` : "";
      return `
        <option value="${folder.id}" ${Number(folder.id) === Number(selectedId || 0) ? "selected" : ""}>${esc(prefix + folder.name)}</option>
        ${renderBranch(folder.id, depth + 1)}
      `;
    }).join("");
  };
  return renderBranch(0, 0);
}

function escAttr(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function _draftStatusPill(draft) {
  const s = draft.status || "draft";
  if (s === "ready_for_review") return `<span class="writing-pill writing-pill-pending">Waiting for approval</span>`;
  if (s === "approved") return `<span class="writing-pill writing-pill-approved">Approved</span>`;
  if (s === "changes_requested") return `<span class="writing-pill writing-pill-changes">Changes requested</span>`;
  return `<span class="writing-pill">${esc(s)}</span>`;
}

function parseWritingMetadata(draft) {
  if (!draft?.metadata_json) return {};
  if (typeof draft.metadata_json === "object") return draft.metadata_json;
  try {
    return JSON.parse(draft.metadata_json);
  } catch {
    return {};
  }
}

function resolveWritingEngine(draft, activeProfile = writingState.overview?.activeProfile) {
  const metadata = parseWritingMetadata(draft);
  return {
    provider: metadata.providerId || activeProfile?.default_model_provider || "claude-code",
    model: metadata.modelId || activeProfile?.default_model_id || "",
  };
}

function resolveWritingBrief(draft) {
  const metadata = parseWritingMetadata(draft);
  return typeof metadata.brief === "string" ? metadata.brief : "";
}

function resolveGoogleDocLink(draft) {
  const metadata = parseWritingMetadata(draft);
  const metadataDoc = metadata.googleDoc && typeof metadata.googleDoc === "object" ? metadata.googleDoc : null;
  const link = (draft?.links || []).find((entry) => entry.link_type === "google_doc");
  if (!metadataDoc && !link) return null;
  return {
    documentId: metadataDoc?.documentId || link?.external_id || "",
    title: metadataDoc?.title || link?.label || draft?.title || "Google Doc",
    label: link?.label || metadataDoc?.title || draft?.title || "Google Doc",
    url: metadataDoc?.url || link?.url || "",
  };
}

function resolveGoogleDocPreviewText(draft) {
  const metadata = parseWritingMetadata(draft);
  const preview = metadata.googleDoc && typeof metadata.googleDoc === "object"
    ? metadata.googleDoc.previewText
    : "";
  return typeof preview === "string" ? preview.trim() : "";
}

function renderGoogleDocImportHint(googleOverview) {
  if (!googleOverview?.connected) return "Connect Google first to import from Docs during draft creation.";
  if (!googleOverview?.hasDriveScope) return "Reconnect Google to grant Google Docs import access.";
  return "Paste a doc link here and Create Draft will import the current document text.";
}

function renderGoogleDocConnectorHint(googleOverview) {
  if (!googleOverview?.connected) return "Connect Google to link, import, or export Docs.";
  if (!googleOverview?.docsImportReady) return "Reconnect Google to grant Google Docs import access.";
  if (!googleOverview?.docsExportReady) return "Reconnect Google to grant Google Docs export access.";
  return "";
}

function resolveGoogleDocExportLabel(googleOverview, googleDoc) {
  if (!googleOverview?.connected) return "Connect Google to export";
  if (!googleOverview?.docsExportReady) return "Reconnect Google to export";
  return googleDoc?.documentId ? "Update linked Google Doc" : "Export to Google Doc";
}

function getWritingModels(provider) {
  const picker = PROVIDER_PICKERS[provider] || PROVIDER_PICKERS["claude-code"];
  return Array.isArray(picker?.models) && picker.models.length
    ? picker.models
    : PROVIDER_PICKERS["claude-code"].models;
}

function resolveModelLabel(provider, modelId) {
  const models = getWritingModels(provider);
  const match = models.find((model) => model.value === (modelId || ""));
  return match?.label || "Auto";
}

function readWritingEngine(scope) {
  const providerInput = document.querySelector(`[data-writing-input='${scope}-provider']`);
  const modelInput = document.querySelector(`[data-writing-input='${scope}-model']`);
  const fallback = scope === "draft"
    ? resolveWritingEngine(writingState.selectedDraft)
    : resolveWritingEngine(null, writingState.overview?.activeProfile);
  return {
    providerId: providerInput ? (readValue(`[data-writing-input='${scope}-provider']`) || fallback.provider || "claude-code") : (fallback.provider || "claude-code"),
    modelId: modelInput ? (readValue(`[data-writing-input='${scope}-model']`) || fallback.model || "") : (fallback.model || ""),
  };
}

function syncWritingModelOptions(scope) {
  const provider = readValue(`[data-writing-input='${scope}-provider']`) || "claude-code";
  const modelSelect = document.querySelector(`[data-writing-input='${scope}-model']`);
  if (!modelSelect) return;
  const currentValue = modelSelect.value;
  const models = getWritingModels(provider);
  modelSelect.innerHTML = models.map((model) => `
    <option value="${escAttr(model.value)}">${esc(model.label)}</option>
  `).join("");
  const nextValue = models.some((model) => model.value === currentValue)
    ? currentValue
    : models[0]?.value || "";
  modelSelect.value = nextValue;
}

async function autoComposeNewDraft(draftId, request, engine) {
  const pendingUserId = createWritingStudioId("user");
  const pendingAssistantId = createWritingStudioId("assistant");
  const pendingUserEntry = { id: pendingUserId, role: "user", label: "You", text: request, attachments: [] };
  const pendingAssistantEntry = { id: pendingAssistantId, role: "assistant", label: "Artemis", text: "Drafting…", attachments: [], pending: true };
  writingState.isComposing = true;
  writingState.chatHistory = [...writingState.chatHistory, pendingUserEntry, pendingAssistantEntry];
  syncWritingComposerDom({ scrollToBottom: true });
  setWritingBusy(true);
  setWritingStatus("Drafting your first pass…");
  try {
    const response = await composeWritingDraftApi(draftId, {
      request,
      selectedText: "",
      attachments: [],
      providerId: engine.providerId,
      modelId: engine.modelId,
    });
    const persistedUser = response.persistedMessages?.user || pendingUserEntry;
    const persistedAssistant = response.persistedMessages?.assistant || {
      id: createWritingStudioId("assistant"),
      role: "assistant",
      label: "Artemis",
      text: response.responseText,
      attachments: [],
      trace: response.trace || null,
      engine: response.engine || null,
      prompt: { systemPrompt: response.systemPrompt || "", userPrompt: response.userPrompt || "" },
    };
    writingState.chatHistory = [
      ...writingState.chatHistory.filter((e) => e.id !== pendingUserId && e.id !== pendingAssistantId),
      persistedUser,
      persistedAssistant,
    ];
    syncWritingComposerDom({ scrollToBottom: true });
    if (response.proposedCandidates?.length && writingState.overview) {
      const current = writingState.overview.trainingCandidates || [];
      writingState.overview = { ...writingState.overview, trainingCandidates: [...current, ...response.proposedCandidates] };
      renderWritingStudio();
    }
    setWritingStatus("Drafted with profile, rules, examples, and draft context.");
  } catch (error) {
    console.error("Writing Studio auto-compose failed:", error);
    writingState.chatHistory = writingState.chatHistory.filter((e) => e.id !== pendingAssistantId);
    syncWritingComposerDom({ scrollToBottom: true });
    setWritingStatus(error.message || "Auto-compose failed.", true);
  } finally {
    writingState.isComposing = false;
    setWritingBusy(false);
  }
}

async function applyWritingChatPrompt() {
  const request = readValue("[data-writing-input='draft-request']");
  const attachments = [...writingState.attachments];
  if (!request && !attachments.length) {
    setWritingStatus("Add a message or attach files first.", true);
    return;
  }
  const draftId = writingState.selectedDraft?.id;
  if (!draftId) {
    setWritingStatus("Open a draft before asking the writing room to assemble context.", true);
    return;
  }
  const editor = document.querySelector("[data-writing-field='draft-content']");
  const selectedText = editor ? editor.value.slice(editor.selectionStart || 0, editor.selectionEnd || 0).trim() : "";
  const userText = request || (attachments.length ? "Adding source files for the next pass." : "");
  const engine = readWritingEngine("draft");
  const requestEl = document.querySelector("[data-writing-input='draft-request']");
  const pendingUserId = createWritingStudioId("user");
  const pendingAssistantId = createWritingStudioId("assistant");
  const pendingUserEntry = {
    id: pendingUserId,
    role: "user",
    label: "You",
    text: userText,
    attachments,
  };
  const pendingAssistantEntry = {
    id: pendingAssistantId,
    role: "assistant",
    label: "Artemis",
    text: "Drafting…",
    attachments: [],
    pending: true,
  };
  writingState.isComposing = true;
  writingState.chatHistory = [
    ...writingState.chatHistory,
    pendingUserEntry,
    pendingAssistantEntry,
  ];
  writingState.attachments = [];
  writingState.dragActive = false;
  if (requestEl) requestEl.value = "";
  syncWritingComposerDom({ scrollToBottom: true });
  setWritingBusy(true);
  setWritingStatus("Drafting with Writing Studio memory...");
  try {
    const response = await composeWritingDraftApi(draftId, {
      request,
      selectedText,
      attachments: attachments.map((attachment) => ({
        name: attachment.name,
        type: attachment.type,
        text: attachment.text,
      })),
      providerId: engine.providerId,
      modelId: engine.modelId,
    });
    const persistedUserEntry = response.persistedMessages?.user || pendingUserEntry;
    const persistedAssistantEntry = response.persistedMessages?.assistant || {
      id: createWritingStudioId("assistant"),
      role: "assistant",
      label: "Artemis",
      text: response.responseText,
      attachments: [],
      trace: response.trace || null,
      engine: response.engine || null,
      prompt: {
        systemPrompt: response.systemPrompt || "",
        userPrompt: response.userPrompt || "",
      },
    };
    writingState.chatHistory = [
      ...writingState.chatHistory.filter((entry) => entry.id !== pendingUserId && entry.id !== pendingAssistantId),
      persistedUserEntry,
      persistedAssistantEntry,
    ];
    syncWritingComposerDom({ scrollToBottom: true });
    if (response.proposedCandidates?.length && writingState.overview) {
      const current = writingState.overview.trainingCandidates || [];
      writingState.overview = { ...writingState.overview, trainingCandidates: [...current, ...response.proposedCandidates] };
      renderWritingStudio();
    }
    setWritingStatus(selectedText
      ? "Drafted with the selected Writing Studio memory and passage context."
      : "Drafted with profile, rules, examples, and draft context.");
  } catch (error) {
    console.error("Writing Studio drafting failed:", error);
    writingState.chatHistory = writingState.chatHistory.filter((entry) => entry.id !== pendingAssistantId);
    writingState.attachments = attachments;
    if (requestEl) requestEl.value = request;
    syncWritingComposerDom({ scrollToBottom: true });
    setWritingStatus(error.message || "Writing Studio drafting failed.", true);
  } finally {
    writingState.isComposing = false;
    setWritingBusy(false);
  }
}

function buildWritingTraceSummary(trace, engine) {
  const profile = trace?.profile?.name || "active profile";
  const rules = trace?.rules || [];
  const examples = trace?.examples || [];
  const draft = trace?.draft || {};
  const engineBits = [
    PROVIDER_LABELS[engine?.providerId] || engine?.providerId || "Writing engine",
    engine?.resolvedModelId || engine?.modelId || "",
  ].filter(Boolean).join(" · ");
  const draftBits = [
    draft.title,
    draft.audience ? `audience: ${draft.audience}` : "",
    draft.channel ? `channel: ${draft.channel}` : "",
  ].filter(Boolean).join("; ");
  return {
    profile,
    engineLabel: engineBits,
    rulesLabel: `${rules.length} approved rule${rules.length === 1 ? "" : "s"}`,
    examplesLabel: `${examples.length} relevant example${examples.length === 1 ? "" : "s"}`,
    contextLabel: draftBits || "current draft context",
    ruleTitles: rules.map((rule) => rule.title).filter(Boolean),
    exampleTitles: examples.map((example) => example.title).filter(Boolean),
  };
}

async function saveWritingChatReplyAsVersion(entryId) {
  const draftId = writingState.selectedDraft?.id;
  const entry = writingState.chatHistory.find((item) => item.id === entryId && item.role === "assistant");
  if (!draftId || !entry?.text) {
    setWritingStatus("Could not find that generated reply to save.", true);
    return;
  }
  setWritingBusy(true);
  setWritingStatus("Saving generated reply as a draft version...");
  try {
    await createWritingDraftVersionApi(draftId, {
      content: entry.text,
      changeNote: "Saved generated Writing Studio reply",
      source: "agent",
      metadata: {
        engine: entry.engine || null,
        trace: entry.trace || null,
        prompt: entry.prompt || null,
      },
    });
    await refreshWritingStudioDraftState(draftId, { preserveChatHistory: true });
    renderWritingStudio();
    const syncResult = await maybeAutoSyncWritingStudio();
    setWritingStatus(formatWritingAutoSyncStatus("Generated reply saved as a new draft version.", syncResult), !syncResult.ok && syncResult.enabled);
  } catch (error) {
    console.error("Saving generated reply failed:", error);
    setWritingStatus(error.message || "Saving generated reply failed.", true);
  } finally {
    setWritingBusy(false);
  }
}

function createWritingStudioId(prefix) {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatAttachmentMeta(attachment) {
  const size = Number(attachment?.size || 0);
  const sizeLabel = size >= 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(size / 1024))} KB`;
  return attachment?.type ? `${attachment.type} · ${sizeLabel}` : sizeLabel;
}

function compactExcerpt(text, limit = 220) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  return normalized.length > limit ? `${normalized.slice(0, limit).trim()}…` : normalized;
}

function renderWritingRichText(text) {
  const blocks = parseWritingRichText(text);
  if (!blocks.length) {
    return `<p class="writing-chat-rich-paragraph">${esc(String(text || "").trim())}</p>`;
  }
  return `
    <div class="writing-chat-rich-text">
      ${blocks.map((block) => renderWritingRichBlock(block)).join("")}
    </div>
  `;
}

function renderWritingRichBlock(block) {
  if (!block) return "";
  if (block.type === "heading") {
    const level = Math.min(4, Math.max(1, Number(block.level || 1)));
    return `<h${level} class="writing-chat-rich-heading writing-chat-rich-heading-${level}">${esc(block.text || "")}</h${level}>`;
  }
  if (block.type === "orderedList" || block.type === "unorderedList") {
    const tag = block.type === "orderedList" ? "ol" : "ul";
    return `
      <${tag} class="writing-chat-rich-list writing-chat-rich-list-${block.type === "orderedList" ? "ordered" : "unordered"}">
        ${(block.items || []).map((item) => `
          <li class="writing-chat-rich-list-item" style="--writing-list-depth:${Math.max(0, Number(item.depth || 0))};">${esc(item.text || "")}</li>
        `).join("")}
      </${tag}>
    `;
  }
  return `<p class="writing-chat-rich-paragraph">${esc(block.text || "")}</p>`;
}

function parseWritingRichText(text) {
  const source = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!source) return [];
  const lines = source.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      blocks.push({
        type: "heading",
        level: headingMatch[1].length,
        text: headingMatch[2].trim(),
      });
      index += 1;
      continue;
    }

    const unorderedMatch = line.match(/^(\s*)[-*•]\s+(.+)$/);
    const orderedMatch = line.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
    if (unorderedMatch || orderedMatch) {
      const listType = orderedMatch ? "orderedList" : "unorderedList";
      const items = [];
      while (index < lines.length) {
        const candidate = lines[index];
        const nextUnordered = candidate.match(/^(\s*)[-*•]\s+(.+)$/);
        const nextOrdered = candidate.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
        if (!(nextUnordered || nextOrdered)) break;
        const activeMatch = nextOrdered || nextUnordered;
        const itemText = (nextOrdered ? nextOrdered[3] : nextUnordered[2]).trim();
        if (itemText) {
          items.push({
            text: itemText,
            depth: Math.floor((activeMatch[1] || "").length / 2),
          });
        }
        index += 1;
      }
      blocks.push({ type: listType, items });
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length) {
      const candidate = lines[index];
      if (!candidate.trim()) break;
      if (/^(#{1,6})\s+/.test(candidate)) break;
      if (/^(\s*)[-*•]\s+/.test(candidate)) break;
      if (/^(\s*)(\d+)[.)]\s+/.test(candidate)) break;
      paragraphLines.push(candidate.trim());
      index += 1;
    }
    if (paragraphLines.length) {
      blocks.push({
        type: "paragraph",
        text: paragraphLines.join(" "),
      });
      continue;
    }
    index += 1;
  }

  return blocks;
}

function formatWritingSyncCount(key, value) {
  const label = String(key || "")
    .replaceAll(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replaceAll(/[_-]+/g, " ")
    .trim()
    .toLowerCase();
  return `${value} ${label}`;
}

function formatWritingSyncSnapshotMeta(summary) {
  const machine = summary?.source?.machine ? ` on ${summary.source.machine}` : "";
  return `Exported ${summary.exportedAt}${machine}`;
}

function formatWritingSyncFolder(folder) {
  const campaign = folder?.campaignId ? ` · ${folder.campaignId}` : "";
  return `${folder?.name || folder?.slug || folder?.syncId || "Folder"}${campaign}`;
}

function formatWritingSyncDraft(draft) {
  const status = draft?.status ? ` · ${draft.status}` : "";
  const versionTotal = Number(draft?.versionCount);
  const versions = Number.isFinite(versionTotal)
    ? ` · ${versionTotal} version${versionTotal === 1 ? "" : "s"}`
    : "";
  return `${draft?.title || draft?.slug || draft?.syncId || "Draft"}${status}${versions}`;
}

function formatWritingSyncConflict(conflict) {
  if (typeof conflict === "string") return conflict;
  if (!conflict || typeof conflict !== "object") return "Conflict reported";
  const path = conflict.path || conflict.slug || conflict.syncId || conflict.versionSyncId || "";
  const reason = conflict.reason || conflict.message || conflict.type || "conflict";
  return path ? `${path}: ${reason}` : reason;
}

function formatWritingSyncPreviewItem(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return "Preview item reported";
  const type = item.entityType ? `${item.entityType}: ` : "";
  const label = item.label || item.path || item.syncId || "Item";
  const detail = item.detail ? ` - ${item.detail}` : "";
  return `${type}${label}${detail}`;
}

function resolveWritingSyncReconcileAction(item) {
  if (!item || typeof item !== "object") return null;
  const reason = item.reason || null;
  const entityType = item.entityType || item.kind || null;
  const detail = String(item.detail || "");
  if ((reason === "folder_metadata_mismatch" || (entityType === "folder" && /folder metadata/i.test(detail))) && item.syncId) {
    return {
      action: "accept_repo_folder",
      syncId: item.syncId,
      label: "Apply repo metadata",
    };
  }
  if ((reason === "draft_metadata_mismatch" || (entityType === "draft" && /draft metadata/i.test(detail))) && item.syncId) {
    return {
      action: "accept_repo_draft",
      draftSyncId: item.syncId,
      label: "Apply repo metadata",
    };
  }
  if (reason === "version_content_mismatch" || entityType === "version") {
    const syncId = item.syncId || item.versionSyncId || null;
    const draftSyncId = item.draftSyncId || null;
    const versionNumber = Number(item.versionNumber || 0);
    if (syncId && draftSyncId && Number.isFinite(versionNumber) && versionNumber > 0) {
      return {
        action: "accept_repo_version",
        syncId,
        draftSyncId,
        versionNumber,
        label: "Accept repo copy",
      };
    }
  }
  return null;
}

function renderWritingSyncPreviewRow(item) {
  const action = resolveWritingSyncReconcileAction(item);
  return `
    <div class="writing-sync-preview-row">
      <p>${esc(formatWritingSyncPreviewItem(item))}</p>
      ${action ? renderWritingSyncReconcileButton(action) : ""}
    </div>
  `;
}

function renderWritingSyncConflictRow(conflict) {
  const action = resolveWritingSyncReconcileAction(conflict);
  return `
    <div class="writing-sync-preview-row">
      <p>${esc(formatWritingSyncConflict(conflict))}</p>
      ${action ? renderWritingSyncReconcileButton(action) : ""}
    </div>
  `;
}

function renderWritingSyncReconcileButton(action) {
  return `
    <button
      type="button"
      class="writing-mini-button writing-sync-inline-action"
      data-writing-action="writing-sync-reconcile"
      data-writing-sync-action="${escAttr(action.action)}"
      ${action.syncId ? `data-writing-sync-id="${escAttr(action.syncId)}"` : ""}
      ${action.draftSyncId ? `data-writing-draft-sync-id="${escAttr(action.draftSyncId)}"` : ""}
      ${Number.isFinite(action.versionNumber) ? `data-writing-version-number="${Number(action.versionNumber)}"` : ""}
    >${esc(action.label)}</button>
  `;
}

function createWritingSyncPreviewState() {
  return {
    filter: "all",
    expandedGroups: {
      repoOnly: true,
      localOnly: true,
      conflicts: true,
    },
    visibleCounts: {
      repoOnly: WRITING_SYNC_PREVIEW_PAGE_SIZE,
      localOnly: WRITING_SYNC_PREVIEW_PAGE_SIZE,
      conflicts: WRITING_SYNC_PREVIEW_PAGE_SIZE,
    },
  };
}

function normalizeWritingSyncPreviewState(state) {
  const fallback = createWritingSyncPreviewState();
  if (!state || typeof state !== "object") return fallback;
  return {
    filter: WRITING_SYNC_PREVIEW_FILTERS.some((option) => option.value === state.filter)
      ? state.filter
      : fallback.filter,
    expandedGroups: {
      ...fallback.expandedGroups,
      ...(state.expandedGroups && typeof state.expandedGroups === "object" ? state.expandedGroups : {}),
    },
    visibleCounts: {
      ...fallback.visibleCounts,
      ...(state.visibleCounts && typeof state.visibleCounts === "object" ? state.visibleCounts : {}),
    },
  };
}

function renderWritingSyncPreviewToolbar(previewState, { repoOnly, localOnly, diffConflicts }) {
  const counts = {
    all: repoOnly.length + localOnly.length + diffConflicts.length,
    repoOnly: repoOnly.length,
    localOnly: localOnly.length,
    conflicts: diffConflicts.length,
  };
  return `
    <div class="writing-sync-preview-toolbar" aria-label="Preview filters">
      ${WRITING_SYNC_PREVIEW_FILTERS.map((option) => `
        <button
          type="button"
          class="writing-sync-filter-chip ${previewState.filter === option.value ? "is-active" : ""}"
          data-writing-action="writing-sync-preview-filter"
          data-writing-sync-filter="${option.value}"
        >${esc(option.label)} <span>${counts[option.value]}</span></button>
      `).join("")}
    </div>
  `;
}

function renderWritingSyncPreviewGroup({ key, title, items, previewState }) {
  if (!Array.isArray(items) || !items.length) return "";
  if (previewState.filter !== "all" && previewState.filter !== key) return "";
  const isExpanded = previewState.expandedGroups[key] !== false;
  const visibleLimit = Math.max(WRITING_SYNC_PREVIEW_PAGE_SIZE, Number(previewState.visibleCounts[key] || WRITING_SYNC_PREVIEW_PAGE_SIZE));
  const visibleItems = items.slice(0, visibleLimit);
  const remaining = Math.max(0, items.length - visibleItems.length);
  return `
    <section class="writing-sync-preview-group">
      <div class="writing-sync-preview-head">
        <strong>${esc(title)}</strong>
        <div class="writing-sync-preview-actions">
          <span class="writing-ctx-chip writing-ctx-chip-muted">${items.length}</span>
          <button
            type="button"
            class="writing-mini-button"
            data-writing-action="writing-sync-toggle-group"
            data-writing-sync-group="${key}"
            aria-expanded="${isExpanded ? "true" : "false"}"
          >${isExpanded ? "Collapse" : "Expand"}</button>
        </div>
      </div>
      ${isExpanded ? `
        <div class="writing-sync-preview-list">
          ${visibleItems.map((item) => renderWritingSyncPreviewRow(item)).join("")}
        </div>
      ` : `<p class="writing-sync-preview-collapsed">Hidden to keep the preview compact until you need it.</p>`}
      ${isExpanded && items.length > WRITING_SYNC_PREVIEW_PAGE_SIZE ? `
        <div class="writing-sync-preview-footer">
          <span>${remaining > 0 ? esc(`${remaining} more item${remaining === 1 ? "" : "s"} hidden.`) : "Showing all items."}</span>
          <button
            type="button"
            class="writing-mini-button"
            data-writing-action="writing-sync-show-more"
            data-writing-sync-group="${key}"
          >${remaining > 0 ? "Show more" : "Show less"}</button>
        </div>
      ` : ""}
    </section>
  `;
}

async function applyWritingSyncReconcile(button) {
  const rootDir = readWritingSyncRootDir();
  if (!rootDir) {
    setWritingStatus("Paste the repo-backed sync path before reconciling conflicts.", true);
    return;
  }
  const action = button?.dataset?.writingSyncAction || "";
  const syncId = button?.dataset?.writingSyncId || null;
  const draftSyncId = button?.dataset?.writingDraftSyncId || null;
  const versionNumber = Number(button?.dataset?.writingVersionNumber || 0) || null;
  if (!action) {
    setWritingStatus("Could not determine the selected sync reconcile action.", true);
    return;
  }
  setWritingBusy(true);
  try {
    const result = await reconcileWritingStudioSyncApi({
      rootDir,
      action,
      syncId,
      draftSyncId,
      versionNumber,
    });
    const inspected = await inspectWritingStudioSyncApi({ rootDir });
    writingState.syncForm = normalizeWritingSyncForm({
      ...writingState.syncForm,
      rootDir,
    });
    persistWritingSyncPreferences();
    writingState.syncSummary = {
      action: "inspect",
      result: inspected,
    };
    await loadWritingStudio({ selectedDraftId: writingState.selectedDraft?.id || null });
    setWritingStatus(result.summary || "Writing Studio sync conflict reconciled.");
  } catch (error) {
    console.error("Writing Studio sync reconcile failed:", error);
    setWritingStatus(error.message || "Writing Studio sync reconcile failed.", true);
  } finally {
    setWritingBusy(false);
  }
}

function normalizeWritingSyncForm(value) {
  const raw = value && typeof value === "object" ? value : {};
  const rootDir = String(raw.rootDir || "").trim();
  return {
    rootDir,
    machineLabel: String(raw.machineLabel || "").trim(),
    autoSync: rootDir ? raw.autoSync !== false : false,
  };
}

function readWritingSyncPreferences() {
  try {
    const raw = localStorage.getItem(WRITING_STUDIO_SYNC_KEY);
    return raw ? normalizeWritingSyncForm(JSON.parse(raw)) : normalizeWritingSyncForm();
  } catch {
    return normalizeWritingSyncForm();
  }
}

function persistWritingSyncPreferences() {
  try {
    localStorage.setItem(WRITING_STUDIO_SYNC_KEY, JSON.stringify(normalizeWritingSyncForm(writingState.syncForm)));
  } catch {
    // Ignore storage failures; autosync still works for the current session.
  }
}

function isWritingAutoSyncEnabled() {
  const syncForm = normalizeWritingSyncForm(writingState.syncForm);
  return Boolean(syncForm.rootDir && syncForm.autoSync);
}

function syncWritingSyncCardDom() {
  const statePill = document.querySelector("[data-writing-sync-state]");
  if (statePill) {
    statePill.textContent = isWritingAutoSyncEnabled() ? "Auto on" : "Auto off";
  }
  const note = document.querySelector("[data-writing-sync-note]");
  if (note) {
    note.textContent = isWritingAutoSyncEnabled()
      ? "Autosync only touches durable Writing Studio records after explicit save actions. Transient thread turns stay local until you save or promote them."
      : "Transient thread turns stay out of sync until you explicitly save or promote them into version history.";
  }
}

async function maybeAutoSyncWritingStudio() {
  const syncForm = normalizeWritingSyncForm(writingState.syncForm);
  if (!syncForm.rootDir || !syncForm.autoSync) {
    return { enabled: false, ok: true, result: null, error: null };
  }
  try {
    const result = await exportWritingStudioSyncApi({
      rootDir: syncForm.rootDir,
      machineLabel: syncForm.machineLabel || undefined,
    });
    writingState.syncSummary = {
      action: "export",
      result,
    };
    renderWritingStudio();
    setWritingBusy(true);
    return { enabled: true, ok: true, result, error: null };
  } catch (error) {
    return { enabled: true, ok: false, result: null, error };
  }
}

function formatWritingAutoSyncStatus(message, syncResult) {
  if (!syncResult?.enabled) return message;
  if (syncResult.ok) return `${message} Synced to repo automatically.`;
  return `${message} Saved locally, but autosync failed: ${syncResult.error?.message || "unknown error"}`;
}

function removeWritingAttachment(attachmentId) {
  writingState.attachments = writingState.attachments.filter((attachment) => attachment.id !== attachmentId);
  syncWritingComposerDom();
}

function bindWritingStudioInteractions() {
  const picker = document.querySelector("[data-writing-input='attachment-picker']");
  if (picker) {
    picker.addEventListener("change", handleWritingAttachmentSelection);
  }

  const dropzone = document.querySelector("[data-writing-dropzone]");
  if (dropzone) {
    dropzone.addEventListener("dragover", handleWritingStudioDragover);
    dropzone.addEventListener("dragleave", handleWritingStudioDragleave);
    dropzone.addEventListener("drop", handleWritingStudioDrop);
  }

  if (!writingLibraryDragBound) {
    writingLibraryDragBound = true;
    document.addEventListener("dragstart", handleWritingLibraryDragStart);
    document.addEventListener("dragover", handleWritingLibraryDragOver);
    document.addEventListener("dragleave", handleWritingLibraryDragLeave);
    document.addEventListener("dragend", handleWritingLibraryDragEnd);
    document.addEventListener("drop", handleWritingLibraryDrop);
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".writing-ctx-propose-wrap")) {
      const dropdown = document.querySelector(".writing-propose-dropdown");
      if (dropdown && !dropdown.hidden) dropdown.hidden = true;
    }
    if (!e.target.closest(".writing-ctx-brief-wrap")) {
      const dropdown = document.querySelector(".writing-ctx-brief-dropdown");
      if (dropdown && !dropdown.hidden) dropdown.hidden = true;
    }
    if (!e.target.closest(".writing-rules-pill-wrap") && writingState.activePopover === "rules") {
      writingState.activePopover = null;
      renderWritingStudio();
    }
    if (!e.target.closest(".writing-browser-toolbar-menu")) {
      closeWritingToolbarMenus();
    }
  });

  const composer = document.querySelector("[data-writing-input='draft-request']");
  if (composer) {
    composer.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void applyWritingChatPrompt();
      }
    });
  }
}

async function handleWritingAttachmentSelection(event) {
  const files = Array.from(event?.target?.files || []);
  await addWritingAttachments(files);
  if (event?.target) event.target.value = "";
}

function handleWritingStudioDragover(event) {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  writingState.dragActive = true;
  syncWritingComposerDom();
}

function handleWritingStudioDragleave(event) {
  if (event.currentTarget?.contains(event.relatedTarget)) return;
  writingState.dragActive = false;
  syncWritingComposerDom();
}

async function handleWritingStudioDrop(event) {
  event.preventDefault();
  writingState.dragActive = false;
  const files = Array.from(event.dataTransfer?.files || []);
  await addWritingAttachments(files);
}

function isFolderDescendant(folders, ancestorId, candidateId) {
  if (Number(ancestorId) === Number(candidateId)) return true;
  const children = folders.filter((f) => Number(f.parent_folder_id) === Number(ancestorId));
  return children.some((child) => isFolderDescendant(folders, child.id, candidateId));
}

function handleWritingLibraryDragStart(event) {
  const draggable = event.target.closest("[data-writing-drag-type]");
  if (!draggable) return;
  const type = draggable.dataset.writingDragType;
  const id = Number(draggable.dataset.writingDragId);
  if (!type || !id) return;
  writingState.dragPayload = { type, id };
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", `${type}:${id}`);
}

function handleWritingLibraryDragOver(event) {
  const dropTarget = event.target.closest("[data-writing-drop-target]");
  if (!dropTarget) return;
  const payload = writingState.dragPayload;
  if (!payload) return;
  const targetType = dropTarget.dataset.writingDropTarget;
  if (targetType !== "folder" && targetType !== "root") return;
  if (payload.type === "folder" && targetType === "folder") {
    const targetFolderId = Number(dropTarget.dataset.writingDropFolderId);
    const folders = writingState.overview?.folders || [];
    if (isFolderDescendant(folders, payload.id, targetFolderId)) return;
  }
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".writing-drop-target-active").forEach((el) => el.classList.remove("writing-drop-target-active"));
  dropTarget.classList.add("writing-drop-target-active");
}

function handleWritingLibraryDragLeave(event) {
  const dropTarget = event.target.closest("[data-writing-drop-target]");
  if (!dropTarget) return;
  if (dropTarget.contains(event.relatedTarget)) return;
  dropTarget.classList.remove("writing-drop-target-active");
}

function handleWritingLibraryDragEnd() {
  writingState.dragPayload = null;
  document.querySelectorAll(".writing-drop-target-active").forEach((el) => el.classList.remove("writing-drop-target-active"));
}

async function handleWritingLibraryDrop(event) {
  event.preventDefault();
  const dropTarget = event.target.closest("[data-writing-drop-target]");
  document.querySelectorAll(".writing-drop-target-active").forEach((el) => el.classList.remove("writing-drop-target-active"));
  const payload = writingState.dragPayload;
  writingState.dragPayload = null;
  if (!dropTarget || !payload) return;

  const targetType = dropTarget.dataset.writingDropTarget;
  const targetFolderId = targetType === "folder" ? Number(dropTarget.dataset.writingDropFolderId) : null;

  if (payload.type === "draft") {
    const draft = (writingState.overview?.drafts || []).find((d) => Number(d.id) === payload.id);
    if (draft && (draft.folder_id ? Number(draft.folder_id) : null) === targetFolderId) return;
  }
  if (payload.type === "folder") {
    const folder = (writingState.overview?.folders || []).find((f) => Number(f.id) === payload.id);
    if (folder && (folder.parent_folder_id ? Number(folder.parent_folder_id) : null) === targetFolderId) return;
  }

  try {
    if (payload.type === "draft") {
      await updateWritingDraftApi(payload.id, { folderId: targetFolderId });
      const targetFolder = targetFolderId
        ? (writingState.overview?.folders || []).find((f) => Number(f.id) === targetFolderId)
        : null;
      const prevFolderId = (writingState.overview?.drafts || []).find((d) => Number(d.id) === payload.id)?.folder_id ?? null;
      writingState.overview = {
        ...writingState.overview,
        drafts: (writingState.overview?.drafts || []).map((d) =>
          Number(d.id) === payload.id
            ? { ...d, folder_id: targetFolderId, folder_name: targetFolder?.name ?? null }
            : d
        ),
        folders: (writingState.overview?.folders || []).map((f) => {
          const fid = Number(f.id);
          if (targetFolderId && fid === targetFolderId) return { ...f, draftCount: (Number(f.draftCount) || 0) + 1 };
          if (prevFolderId && fid === Number(prevFolderId)) return { ...f, draftCount: Math.max(0, (Number(f.draftCount) || 0) - 1) };
          return f;
        }),
      };
    } else {
      await updateWritingFolderApi(payload.id, { parentFolderId: targetFolderId });
      writingState.overview = {
        ...writingState.overview,
        folders: (writingState.overview?.folders || []).map((f) =>
          Number(f.id) === payload.id ? { ...f, parent_folder_id: targetFolderId } : f
        ),
      };
    }
    syncWritingLibraryDom();
    setWritingStatus(payload.type === "draft" ? "Draft moved." : "Folder moved.");
  } catch {
    setWritingStatus("Move failed.", true);
  }
}

async function addWritingAttachments(files) {
  if (!files.length) return;
  const mapped = await Promise.all(files.map(async (file) => ({
    id: createWritingStudioId("attachment"),
    name: file.name,
    size: file.size,
    type: file.type || "file",
    text: typeof file.text === "function" ? await file.text().catch(() => "") : "",
  })));
  writingState.attachments = [...writingState.attachments, ...mapped];
  syncWritingComposerDom();
  setWritingStatus(`${mapped.length} file${mapped.length === 1 ? "" : "s"} attached to the writing thread.`);
}

function syncWritingComposerDom({ scrollToBottom = false } = {}) {
  const thread = document.querySelector("[data-writing-chat-thread]");
  if (thread && writingState.selectedDraft) {
    thread.innerHTML = renderWritingChatThread(
      writingState.selectedDraft,
      writingState.draftContent,
      resolveWritingBrief(writingState.selectedDraft),
    );
  }

  const attachments = document.querySelector("[data-writing-attachments]");
  if (attachments) {
    attachments.innerHTML = renderWritingAttachmentChips();
  }

  const dropzone = document.querySelector("[data-writing-dropzone]");
  if (dropzone) {
    dropzone.classList.toggle("is-dragging", Boolean(writingState.dragActive));
  }
  if (scrollToBottom) {
    queueWritingChatScrollToBottom();
  }
}

function queueWritingChatScrollToBottom() {
  const scroll = () => {
    const thread = document.querySelector("[data-writing-chat-thread]");
    if (!thread) return;
    const top = thread.scrollHeight;
    if (typeof thread.scrollTo === "function") {
      thread.scrollTo({ top, behavior: "smooth" });
      return;
    }
    thread.scrollTop = top;
  };
  if (typeof window?.requestAnimationFrame === "function") {
    window.requestAnimationFrame(scroll);
    return;
  }
  setTimeout(scroll, 0);
}

function readWritingStudioHandoff() {
  try {
    const raw = localStorage.getItem(WRITING_STUDIO_HANDOFF_KEY);
    if (!raw) return null;
    localStorage.removeItem(WRITING_STUDIO_HANDOFF_KEY);
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function setWritingBusy(isBusy) {
  document.querySelectorAll("[data-writing-action]").forEach((button) => {
    button.toggleAttribute("disabled", Boolean(isBusy));
  });
}

// Close the modal portal whenever the user navigates away from Writing Studio.
onState("view", (view) => {
  if (normalizeAppView(view) !== WRITING_STUDIO_VIEW && writingState.activeModal) {
    writingState.activeModal = null;
    syncWritingModalPortal();
  }
});

let _writingStatusTimer = null;
function setWritingStatus(message, isError = false) {
  writingState.status = message || "";
  const statusEl = document.querySelector("[data-writing-status]");
  if (!statusEl) return;
  statusEl.textContent = message || "";
  statusEl.classList.toggle("visible", Boolean(message));
  statusEl.classList.toggle("error", Boolean(isError));
  clearTimeout(_writingStatusTimer);
  if (message && !isError) {
    _writingStatusTimer = setTimeout(() => {
      writingState.status = "";
      const el = document.querySelector("[data-writing-status]");
      if (el) { el.textContent = ""; el.classList.remove("visible", "error"); }
    }, 3500);
  }
}
