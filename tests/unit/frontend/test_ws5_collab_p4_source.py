from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSER_JS = ROOT / "public" / "js" / "features" / "composer-v5.js"


def _read_source() -> str:
    return COMPOSER_JS.read_text()


def test_presence_avatars_dedupe_by_user_email() -> None:
    src = _read_source()
    assert "const deduped = new Map();" in src
    assert 'const emailKey = String(peer.email || "").trim().toLowerCase();' in src
    assert "if (!emailKey || deduped.has(emailKey)) continue;" in src


def test_claim_replace_uses_live_decoration_positions() -> None:
    src = _read_source()
    assert "function resolveClaimDecorationRange(claimId)" in src
    assert "spec?.claimId === claimId" in src
    assert "handleClaimReplace(phrasing, claimId, flagPmFrom, flagPmTo);" in src


def test_collab_remaps_transient_ranges_and_gates_stale_scans() -> None:
    src = _read_source()
    assert "function remapTransientCollabState(mapping, docSize)" in src
    assert "selectionRange = _mapStoredRange(selectionRange, mapping, docSize);" in src
    assert "pendingRewrite = _mapStoredRange(pendingRewrite, mapping, docSize);" in src
    assert "commentAnchorRange = _mapStoredRange(commentAnchorRange, mapping, docSize);" in src
    assert "if (destroyed || requestEpoch !== docEpoch) return;" in src


def test_comment_submit_recomputes_offsets_from_current_pm_range() -> None:
    src = _read_source()
    assert "function _pmRangeToOffsets(from, to)" in src
    assert "const { anchoredText } = _pmRangeToOffsets(from, to);" in src
    assert "const { anchorStart, anchorEnd, anchoredText } = _pmRangeToOffsets(" in src
