from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSER_JS = ROOT / "public" / "js" / "features" / "composer-v5.js"
COMPOSER_CSS = ROOT / "public" / "css" / "features" / "composer-v5.css"


def test_composer_picker_renders_drag_sources_and_drop_targets() -> None:
    src = COMPOSER_JS.read_text()
    assert 'data-cv5-drag-type="draft"' in src
    assert 'data-cv5-drag-type="folder"' in src
    assert 'data-cv5-drop-target="folder"' in src
    assert 'data-cv5-drop-target="root"' in src


def test_composer_picker_includes_folder_cycle_guard() -> None:
    src = COMPOSER_JS.read_text()
    assert "collectPickerFolderDescendants" in src
    assert "return !descendants.has(targetInfo.folderId);" in src
    assert "parent_folder_id" in src


def test_composer_picker_css_has_drop_target_affordance() -> None:
    src = COMPOSER_CSS.read_text()
    assert ".cv5-picker-drop-target-active" in src
    assert ".cv5-drafts-picker.is-dragging" in src
