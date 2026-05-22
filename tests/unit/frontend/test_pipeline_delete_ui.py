"""Source checks for pipeline archive/delete UI wiring."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
PIPELINES_JS = REPO / "public" / "js" / "features" / "pipelines.js"
PIPELINES_CSS = REPO / "public" / "css" / "features" / "pipelines.css"
API_JS = REPO / "public" / "js" / "core" / "api.js"


def test_archive_filter_persists_to_expected_local_storage_key():
    src = PIPELINES_JS.read_text()

    assert "artemis.pipelines.archived-filter" in src
    assert "localStorage.setItem(ARCHIVED_FILTER_KEY, _archivedFilter)" in src
    assert "localStorage.getItem(ARCHIVED_FILTER_KEY)" in src


def test_archive_filter_has_three_states():
    src = PIPELINES_JS.read_text()

    for state in [
        'data-archive-filter="default"',
        'data-archive-filter="include"',
        'data-archive-filter="only"',
    ]:
        assert state in src


def test_kebab_menu_exposes_archive_restore_and_permanent_delete():
    src = PIPELINES_JS.read_text()

    for token in ["pkebab", "Archive", "Restore", "Permanently delete"]:
        assert token in src


def test_permanent_delete_dialog_requires_typed_pipeline_name():
    src = PIPELINES_JS.read_text()

    assert 'type === "permanent"' in src
    assert "_confirm.typed !== _confirm.pipeline.name" in src
    assert "Type pipeline name to confirm" in src


def test_permanent_delete_api_uses_permanent_route():
    src = API_JS.read_text()

    assert "permanentDeletePipelineApi" in src
    assert "/permanent" in src


def test_delete_ui_css_selectors_exist():
    css = PIPELINES_CSS.read_text()

    for selector in [".pkebab", ".pmenu-list", ".pmodal-backdrop", ".pconfirm-name"]:
        assert selector in css
