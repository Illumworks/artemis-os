from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSER_JS = ROOT / "public" / "js" / "features" / "composer-v5.js"
API_JS = ROOT / "public" / "js" / "core" / "api.js"


def test_composer_actions_menu_includes_ready_for_review() -> None:
    src = COMPOSER_JS.read_text()
    assert 'data-cv5-action="ready-for-review"' in src
    assert "Ready for review" in src


def test_composer_ready_for_review_picker_uses_campaign_approver_fallback() -> None:
    src = COMPOSER_JS.read_text()
    assert "Use campaign approver (fallback Angela)" in src
    assert "markDraftReadyForReviewApi(draft.id" in src
    assert "Sent to ${result.reviewerEmail || \"the reviewer\"} for review." in src


def test_ready_for_review_api_calls_new_endpoint() -> None:
    src = API_JS.read_text()
    assert "export async function markDraftReadyForReviewApi" in src
    assert "/ready-for-review" in src
    assert "reviewerEmail" in src
