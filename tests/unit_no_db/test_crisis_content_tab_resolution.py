"""Unit tests for CCA13's tab resolution + test-lane classification.

Pure ``resolve_card_tab_map`` tests -- no database, no real network.
``artemis.crisis_content.tab_resolution`` has no DB import at all, and the
one HTTP call it makes (``_fetch_document``) is monkeypatched at the module
boundary, mirroring the established pattern in
``tests/test_crisis_content_writeback.py`` (which monkeypatches the exact
same shape of ``_fetch_document`` for its own ``locate_card_table``
coverage) rather than mocking ``httpx`` transport-level.

Fixture documents are hand-built in the ``documents.get?includeTabsContent=
true`` JSON shape, verified live on the target doc (see
``briefs/cca13-tab-resolution-and-test-lane.md``): five real tabs, two of
them review-relevant --
``t.cv99t981gtu6`` "Content To Review" and ``t.cv9uq0oh5hzc``
"Content To Review (TESTING)".
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest

from artemis.config import settings
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.tab_resolution import (
    CardTabInfo,
    TabResolutionError,
    resolve_card_tab_map,
)
from artemis.crisis_content.writeback import locate_card_table

# ---------------------------------------------------------------------------
# Fixture builders -- documents.get?includeTabsContent=true shape
# ---------------------------------------------------------------------------


def _para(text_: str, end_index: int = 1) -> dict[str, Any]:
    return {"endIndex": end_index, "paragraph": {"elements": [{"textRun": {"content": text_}}]}}


def _copy_hash(lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _card_table(*, header: str, copy_lines: list[str]) -> dict[str, Any]:
    """A signature-matching review-card table -- header row + status/copy row.

    Mirrors ``tests/test_crisis_content_writeback.py``'s ``_card_table``.
    """
    return {
        "tableRows": [
            {"tableCells": [{"content": [_para(header)]}]},
            {
                "tableCells": [
                    {
                        "content": [
                            _para("Platform:"),
                            _para("Asset for review - LINK"),
                            _para(""),  # asset chip -- opaque, no text
                            _para("Copy review"),
                            _para(""),  # copy chip -- opaque
                        ]
                    },
                    {"content": [_para(line) for line in copy_lines]},
                ]
            },
        ]
    }


def _decoy_table(header: str = "Strategy notes") -> dict[str, Any]:
    """A non-review table -- missing the 'Copy review' marker on purpose."""
    return {
        "tableRows": [
            {"tableCells": [{"content": [_para(header)]}]},
            {"tableCells": [{"content": [_para("Owner:")]}, {"content": [_para("Not a card.")]}]},
        ]
    }


def _tab(
    tab_id: str, title: str, tables: list[dict[str, Any]], child_tabs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    tab: dict[str, Any] = {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {"body": {"content": [{"table": t} for t in tables]}},
    }
    if child_tabs is not None:
        tab["childTabs"] = child_tabs
    return tab


def _document(tabs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documentId": "1IcXikVORzIfzKxsU57zoKTf2jr5rqmIkNHmHP0EAPUw", "tabs": tabs}


def _make_card(
    *,
    header: str = "August 12 - Welcome Back blog",
    platform: str | None = "LinkedIn",
    ordinal: int = 0,
    copy_lines: list[str] | None = None,
) -> ReviewCard:
    lines = copy_lines if copy_lines is not None else ["Default copy body."]
    copy_body = "\n".join(line for line in lines if line)
    return ReviewCard(
        header=header,
        date_text="August 12",
        title="Welcome Back blog",
        platform=platform,
        asset_status="Draft",
        copy_status="Ready",
        asset_url=None,
        copy_body=copy_body,
        identity_key=(header, platform, ordinal),
        copy_hash=hashlib.sha256(copy_body.encode("utf-8")).hexdigest(),
    )


# The verified live tab list from the brief.
_STRATEGY = "t.0"
_CONTENT_PLAN_DRAFT = "t.5b63cccie8xp"
_FRAMEWORK = "t.jfvhnt5wun8g"
_REAL_REVIEW_TAB = "t.cv99t981gtu6"
_TEST_REVIEW_TAB = "t.cv9uq0oh5hzc"


def _live_doc(*, real_tables: list[dict[str, Any]], test_tables: list[dict[str, Any]]) -> dict[str, Any]:
    """The verified live tab shape: 5 tabs, 2 of them review-relevant."""
    return _document(
        [
            _tab(_STRATEGY, "Strategy Plan", [_decoy_table()]),
            _tab(_CONTENT_PLAN_DRAFT, "Content Plan Draft", []),
            _tab(_FRAMEWORK, "Repeatable Framework", []),
            _tab(_REAL_REVIEW_TAB, "Content To Review", real_tables),
            _tab(_TEST_REVIEW_TAB, "Content To Review (TESTING)", test_tables),
        ]
    )


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> list[int]:
    """Stub ``tab_resolution._fetch_document``; returns a call-count list."""
    calls: list[int] = []

    async def fake_fetch(access_token: str, document_id: str) -> dict[str, Any]:
        calls.append(1)
        return doc

    monkeypatch.setattr("artemis.crisis_content.tab_resolution._fetch_document", fake_fetch)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# is_test derives from tab title, real tab -> False
# ─────────────────────────────────────────────────────────────────────────────


async def test_card_on_testing_tab_is_test_true_card_on_real_tab_is_test_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_card = _make_card(header="Real card", copy_lines=["Real copy."])
    test_card = _make_card(header="Test card", copy_lines=["Test copy."])
    doc = _live_doc(
        real_tables=[_card_table(header="Real card", copy_lines=["Real copy."])],
        test_tables=[_card_table(header="Test card", copy_lines=["Test copy."])],
    )
    _patch_fetch(monkeypatch, doc)

    tab_map = await resolve_card_tab_map("token", doc["documentId"], [real_card, test_card])

    assert tab_map[real_card.identity_key] == CardTabInfo(
        tab_id=_REAL_REVIEW_TAB, tab_title="Content To Review", is_test=False
    )
    assert tab_map[test_card.identity_key] == CardTabInfo(
        tab_id=_TEST_REVIEW_TAB, tab_title="Content To Review (TESTING)", is_test=True
    )


# ─────────────────────────────────────────────────────────────────────────────
# Matching is header + copy hash, never platform
# ─────────────────────────────────────────────────────────────────────────────


async def test_matching_ignores_platform_chip_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    """A card whose parsed ``platform`` differs from anything in the live doc
    still matches -- platform is a chip, invisible to ``documents.get`` in
    both directions (this exact mistake was the CCA7 brief's own error,
    caught by a worker; CCA13 must not repeat it).
    """
    # The live table carries no platform info at all (chips are opaque);
    # this ReviewCard's platform is "X" -- utterly irrelevant to matching.
    card = _make_card(header="Cross-platform card", platform="X", copy_lines=["Same copy."])
    doc = _live_doc(
        real_tables=[_card_table(header="Cross-platform card", copy_lines=["Same copy."])],
        test_tables=[],
    )
    _patch_fetch(monkeypatch, doc)

    tab_map = await resolve_card_tab_map("token", doc["documentId"], [card])

    assert tab_map[card.identity_key].tab_id == _REAL_REVIEW_TAB
    assert tab_map[card.identity_key].is_test is False


# ─────────────────────────────────────────────────────────────────────────────
# A new monthly tab with no marker -> real, no configuration needed
# ─────────────────────────────────────────────────────────────────────────────


async def test_new_tab_without_marker_is_not_a_test_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _make_card(header="September post", copy_lines=["September copy."])
    doc = _document(
        [
            _tab(_REAL_REVIEW_TAB, "Content To Review", []),
            _tab(
                "t.newmonth",
                "September 2026",
                [_card_table(header="September post", copy_lines=["September copy."])],
            ),
        ]
    )
    _patch_fetch(monkeypatch, doc)

    tab_map = await resolve_card_tab_map("token", doc["documentId"], [card])

    assert tab_map[card.identity_key].is_test is False
    assert tab_map[card.identity_key].tab_id == "t.newmonth"


def test_marker_matching_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "crisis_content_test_tab_marker", "TESTING")
    from artemis.crisis_content.tab_resolution import _is_test_title

    assert _is_test_title("Content To Review (testing)") is True
    assert _is_test_title("Content To Review (TESTING)") is True
    assert _is_test_title("Content To Review") is False


# ─────────────────────────────────────────────────────────────────────────────
# Exactly one documents.get per call, regardless of card count
# ─────────────────────────────────────────────────────────────────────────────


async def test_exactly_one_fetch_regardless_of_card_count(monkeypatch: pytest.MonkeyPatch) -> None:
    cards = [
        _make_card(header=f"Card {i}", copy_lines=[f"Copy {i}."]) for i in range(5)
    ]
    doc = _live_doc(
        real_tables=[
            _card_table(header=f"Card {i}", copy_lines=[f"Copy {i}."]) for i in range(5)
        ],
        test_tables=[],
    )
    calls = _patch_fetch(monkeypatch, doc)

    tab_map = await resolve_card_tab_map("token", doc["documentId"], cards)

    assert len(calls) == 1
    assert len(tab_map) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Fetch failure -> TabResolutionError, never silently "resolved as real"
# ─────────────────────────────────────────────────────────────────────────────


async def test_fetch_http_error_raises_tab_resolution_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_fetch(access_token: str, document_id: str) -> dict[str, Any]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("artemis.crisis_content.tab_resolution._fetch_document", failing_fetch)

    card = _make_card()
    with pytest.raises(TabResolutionError):
        await resolve_card_tab_map("token", "DOC123", [card])


# ─────────────────────────────────────────────────────────────────────────────
# A single card that can't be positively located is omitted, not guessed
# ─────────────────────────────────────────────────────────────────────────────


async def test_unmatched_card_is_omitted_from_the_map_not_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known_card = _make_card(header="Known card", copy_lines=["Known copy."])
    unknown_card = _make_card(header="Vanished card", copy_lines=["Never existed."])
    doc = _live_doc(
        real_tables=[_card_table(header="Known card", copy_lines=["Known copy."])],
        test_tables=[],
    )
    _patch_fetch(monkeypatch, doc)

    tab_map = await resolve_card_tab_map("token", doc["documentId"], [known_card, unknown_card])

    assert known_card.identity_key in tab_map
    assert unknown_card.identity_key not in tab_map


# ─────────────────────────────────────────────────────────────────────────────
# The doc-line write itself is unaffected by which tab a card lives on
# ─────────────────────────────────────────────────────────────────────────────


def test_write_doc_lines_own_locator_finds_the_testing_tab_card_correctly() -> None:
    """Proves the brief's "doc line still writes, into the test card" claim
    without editing ``writeback.py``: ``write_doc_line`` locates its target
    via this exact, UNMODIFIED ``locate_card_table`` call, keyed only on
    header + copy hash -- the same key ``tab_resolution.py`` uses and never
    on tab or platform. Given a header+hash that is unique to the TESTING
    tab's card, it already finds that card's table correctly today, with no
    CCA13 change required to writeback.py at all.
    """
    real_card_table = _card_table(header="Shared header", copy_lines=["Real body."])
    test_card_table = _card_table(header="Shared header", copy_lines=["Test body -- edited."])
    doc = _live_doc(real_tables=[real_card_table], test_tables=[test_card_table])

    location, total = locate_card_table(
        doc, header="Shared header", copy_hash=_copy_hash(["Test body -- edited."])
    )

    assert location.tab_id == _TEST_REVIEW_TAB
    assert total == 2
