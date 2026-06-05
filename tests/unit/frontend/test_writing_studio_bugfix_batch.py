"""Writing Studio frontend smoke checks for the bug-fix batch.

Pattern matches the existing frontend smoke suite: inspect or lightly exercise
production JS source without introducing a browser runtime dependency.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WRITING_JS = ROOT / "public" / "js" / "features" / "writing-studio.js"


def _read_source() -> str:
    return WRITING_JS.read_text()


def test_writing_studio_normalizes_overview_compat_keys() -> None:
    src = _read_source()
    assert "trainingCandidates: overview.trainingCandidates || overview.training_candidates || []" in src
    assert "activeProfile: overview.activeProfile || overview.active_profile || null" in src


def test_writing_studio_chat_renderer_accepts_text_alias_or_legacy_content() -> None:
    src = _read_source()
    assert 'renderWritingRichText(entry.text || entry.content || "")' in src


def test_writing_studio_folder_row_no_longer_uses_campaign_id_as_subtitle() -> None:
    src = _read_source()
    block = src.split("function renderFolderBrowserRow(folder) {", 1)[1].split(
        "function renderInlineFolderInput",
        1,
    )[0]
    assert "Campaign folder" in block
    assert "folder.campaign_id || null" not in block


def test_writing_studio_folder_counts_are_derived_from_visible_drafts() -> None:
    src = _read_source()
    block = src.split("function renderWritingOrganizationRail(folders, campaigns, drafts, selectedDraftId) {", 1)[1].split(
        "function renderWritingSyncCard",
        1,
    )[0]
    assert "const draftCountsByFolder = new Map();" in block
    assert "drafts.forEach((draft) => {" in block
    assert "draftCount: draftCountsByFolder.get(Number(folder.id)) || 0" in block
