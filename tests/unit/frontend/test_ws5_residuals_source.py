from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSER_JS = ROOT / "public" / "js" / "features" / "composer-v5.js"
COLLAB_VENDOR = ROOT / "public" / "vendor" / "prosemirror" / "prosemirror-collab.mjs"


def test_composer_closes_collab_socket_on_page_unload_and_destroy() -> None:
    src = COMPOSER_JS.read_text()
    assert 'window.addEventListener("pagehide", handleCollabPageUnload);' in src
    assert 'window.addEventListener("beforeunload", handleCollabPageUnload);' in src
    assert 'window.removeEventListener("pagehide", handleCollabPageUnload);' in src
    assert 'window.removeEventListener("beforeunload", handleCollabPageUnload);' in src
    assert "function closeCollabSocket()" in src
    assert "closeCollabSocket();" in src


def test_vendor_collab_marks_remote_steps_out_of_history() -> None:
    src = COLLAB_VENDOR.read_text()
    assert "historyPreserveItems:!0" in src
    assert '.setMeta("addToHistory",!1)' in src
