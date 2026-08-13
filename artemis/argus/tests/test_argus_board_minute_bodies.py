"""Tests for ARGUS-3 -- fetching agenda-item BODIES for the relevant subset
of a district's board-minutes titles, not every item and not none.

All tests are UNIT tests -- no DB, no real HTTP. ``_fetch_board_minutes`` is
exercised with a caller-supplied ``signal={"boarddocs_url": ...}`` so it
never needs ``_resolve_district_row`` / the database (mirrors
``test_fetch_board_minutes_signal_supplied_url_wins_over_stored`` in
test_argus_district_identity.py).

Two mocking strategies are used, matched to what each test is actually
checking:
  - Tests about research.py's OWN orchestration (which items get selected,
    the cap) mock ``fetch_boarddocs_bodies`` itself and inspect what it was
    called with -- the cap and the relevance filter are research.py's job,
    not client.py's.
  - Tests about what happens WHILE bodies are being fetched (budget
    exhaustion, one item failing) let the REAL ``fetch_boarddocs_bodies``
    run and fake only the lowest-level network call
    (``fetch_agenda_item_body``) -- these are the behaviours
    ``fetch_boarddocs_bodies`` itself is responsible for, and faking it away
    would test nothing.

Required-by-brief coverage (artemis/argus tests, see
briefs/argus-3-board-minute-bodies.md):
  - only is_argus_relevant-passing titles get bodies fetched (count, driven by a
    mixed stub list)
  - the body cap is honoured when more items than the cap are relevant
  - budget exhaustion returns the bodies already fetched PLUS the remaining
    titles, and does not raise
  - one body fetch failing does not lose the others
  - body text reaches the synthesis input, trimmed
  - zero relevant titles yields the title-only behaviour and does not raise

Plus one test this package considers load-bearing, not optional: without it
the whole fix is a no-op in practice (see research.py's own docstring on
_fetch_board_minutes) --
  - relevant/enriched items are ordered BEFORE the remaining title-only
    items, ahead of the existing ``[:10]`` trim, so a relevant item that is
    NOT among the first 10 in raw agenda order still reaches synthesis.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from artemis.argus.research import _build_synthesis_prompt, _fetch_board_minutes
from artemis.config import settings

_BOARDDOCS_URL = "https://go.boarddocs.com/tx/testdistrict/Board.nsf/Public"
_SIGNAL: dict[str, Any] = {"boarddocs_url": _BOARDDOCS_URL}

_RELEVANT_TITLE_A = (
    "Board Meeting Agenda and Notice — Consider and Take Possible Action to Approve "
    "Reading Curriculum Adoption for Elementary Campuses"
)
_RELEVANT_TITLE_B = (
    "Board Meeting Agenda and Notice — Vendor Review for Lexia Renewal (Districtwide)"
)
_IRRELEVANT_TITLE_A = "Board Meeting Agenda and Notice — Parking Lot Resurfacing at Central High"
_IRRELEVANT_TITLE_B = "Board Meeting Agenda and Notice — Approval of Prior Meeting Minutes"


def _item(title: str, item_unique: str, *, text: str | None = None) -> dict[str, Any]:
    """A title-only agenda item as fetch_boarddocs (fetch_bodies=False) returns it."""
    return {
        "title": title,
        "date": "2026-02-26",
        "source_url": f"{_BOARDDOCS_URL.rsplit('/Public', 1)[0]}/goto?open&id={item_unique}",
        "text": text if text is not None else title,
        "speaker_attribution": None,
        "item_unique": item_unique,
        "committee_id": "C1",
    }


def _patch_client(**overrides: Any) -> Any:
    """Patch artemis.scouts.board_minutes.client attributes for the duration of a `with` block.

    _fetch_board_minutes does a deferred `from ...client import (...)` INSIDE the
    function body on every call, so patching the module attribute (not a local
    binding) is what existing ARGUS-2 tests already rely on -- same pattern here.
    """
    return patch.multiple("artemis.scouts.board_minutes.client", **overrides)


# ===========================================================================
# 1. Only is_argus_relevant-passing titles get their bodies fetched
# ===========================================================================


async def test_only_relevant_titles_get_body_fetched() -> None:
    items = [
        _item(_IRRELEVANT_TITLE_A, "U1"),
        _item(_RELEVANT_TITLE_A, "U2"),
        _item(_IRRELEVANT_TITLE_B, "U3"),
        _item(_RELEVANT_TITLE_B, "U4"),
    ]
    mock_fetch_boarddocs = AsyncMock(return_value=items)
    mock_fetch_bodies = AsyncMock(return_value=0)

    with _patch_client(
        fetch_boarddocs=mock_fetch_boarddocs, fetch_boarddocs_bodies=mock_fetch_bodies
    ):
        await _fetch_board_minutes("11331", signal=_SIGNAL)

    mock_fetch_bodies.assert_awaited_once()
    passed_items = mock_fetch_bodies.call_args.args[1]
    assert {it["item_unique"] for it in passed_items} == {"U2", "U4"}, (
        "only the two relevant items (U2, U4) should have been handed to "
        "fetch_boarddocs_bodies -- got "
        f"{[it['item_unique'] for it in passed_items]}"
    )


# ===========================================================================
# 2. The body cap is honoured when more items than the cap are relevant
# ===========================================================================


async def test_body_cap_is_honoured_when_more_items_than_cap_are_relevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "argus_board_minutes_body_cap", 2)

    items = [_item(_RELEVANT_TITLE_A, f"U{i}") for i in range(5)]
    mock_fetch_boarddocs = AsyncMock(return_value=items)
    mock_fetch_bodies = AsyncMock(return_value=0)

    with _patch_client(
        fetch_boarddocs=mock_fetch_boarddocs, fetch_boarddocs_bodies=mock_fetch_bodies
    ):
        await _fetch_board_minutes("11331", signal=_SIGNAL)

    mock_fetch_bodies.assert_awaited_once()
    passed_items = mock_fetch_bodies.call_args.args[1]
    assert len(passed_items) == 2, (
        f"5 relevant items exist but the cap is 2 -- fetch_boarddocs_bodies should "
        f"receive exactly 2, got {len(passed_items)}"
    )


# ===========================================================================
# 3. Budget exhaustion: already-fetched bodies + remaining titles, no raise
# ===========================================================================


async def test_body_budget_exhausted_returns_partial_bodies_and_remaining_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real fetch_boarddocs_bodies runs; only the network call is faked.

    Item A returns instantly. Item SLOW sleeps far longer than the budget --
    its per-item asyncio.wait_for times out, so it keeps its title-only
    text. By the time that timeout fires the budget is already spent, so
    item B is never even attempted and also stays title-only. Nothing here
    should raise, and the two title-only items must still be present in the
    final result (partial success, not a lost request).
    """
    monkeypatch.setattr(settings, "argus_board_minutes_body_budget_s", 0.05)
    monkeypatch.setattr(settings, "argus_board_minutes_body_cap", 20)

    items = [
        _item(_RELEVANT_TITLE_A, "A"),
        _item(_RELEVANT_TITLE_A, "SLOW"),
        _item(_RELEVANT_TITLE_A, "B"),
    ]
    mock_fetch_boarddocs = AsyncMock(return_value=items)

    async def fake_body(base_url: str, item_unique: str, committee_id: str, http: Any) -> str:
        if item_unique == "SLOW":
            await asyncio.sleep(2.0)
            return "should never be seen"
        return f"real body text for {item_unique}"

    with _patch_client(fetch_boarddocs=mock_fetch_boarddocs, fetch_agenda_item_body=fake_body):
        result = await _fetch_board_minutes("11331", signal=_SIGNAL)  # must not raise

    by_unique = {it["source_url"].rsplit("id=", 1)[-1]: it for it in result}
    assert by_unique["A"]["text"] == "real body text for A"
    # SLOW timed out (kept its title-only text); B was never attempted (budget
    # already gone) and also kept its title-only text. Both are still present.
    assert by_unique["SLOW"]["text"] == _RELEVANT_TITLE_A
    assert by_unique["B"]["text"] == _RELEVANT_TITLE_A


# ===========================================================================
# 4. A body fetch failing for one item does not lose the others
# ===========================================================================


async def test_one_body_fetch_failure_does_not_lose_the_others() -> None:
    items = [
        _item(_RELEVANT_TITLE_A, "A"),
        _item(_RELEVANT_TITLE_A, "FAIL"),
        _item(_RELEVANT_TITLE_A, "B"),
    ]
    mock_fetch_boarddocs = AsyncMock(return_value=items)

    async def fake_body(base_url: str, item_unique: str, committee_id: str, http: Any) -> str:
        if item_unique == "FAIL":
            raise RuntimeError("BoardDocs blew up for this one item")
        return f"real body text for {item_unique}"

    with _patch_client(fetch_boarddocs=mock_fetch_boarddocs, fetch_agenda_item_body=fake_body):
        result = await _fetch_board_minutes("11331", signal=_SIGNAL)  # must not raise

    assert len(result) == 3, "no item should be dropped because one body fetch raised"
    by_unique = {it["source_url"].rsplit("id=", 1)[-1]: it for it in result}
    assert by_unique["A"]["text"] == "real body text for A"
    assert by_unique["B"]["text"] == "real body text for B"
    assert by_unique["FAIL"]["text"] == _RELEVANT_TITLE_A  # kept its title-only text


# ===========================================================================
# 5. Body text reaches the synthesis input, trimmed
# ===========================================================================


async def test_body_text_reaches_synthesis_input_trimmed() -> None:
    long_body = "Vendor named Acme Literacy Co. " * 100  # > 1500 chars
    assert len(long_body) > 1500

    items = [_item(_RELEVANT_TITLE_A, "A")]
    mock_fetch_boarddocs = AsyncMock(return_value=items)

    async def fake_body(base_url: str, item_unique: str, committee_id: str, http: Any) -> str:
        return long_body

    with _patch_client(fetch_boarddocs=mock_fetch_boarddocs, fetch_agenda_item_body=fake_body):
        result = await _fetch_board_minutes("11331", signal=_SIGNAL)

    assert len(result) == 1
    assert result[0]["text"] == long_body[:1500]
    assert len(result[0]["text"]) == 1500

    # And it genuinely reaches what gets sent to the LLM synthesis prompt.
    prompt = _build_synthesis_prompt(
        "11331", ["current_vendor"], {"board_minutes": result}, signal=None
    )
    assert "Acme Literacy Co." in prompt


# ===========================================================================
# 6. Zero relevant titles -> unchanged title-only behaviour, no raise
# ===========================================================================


async def test_zero_relevant_titles_yields_title_only_behaviour_and_does_not_raise() -> None:
    items = [_item(_IRRELEVANT_TITLE_A, "U1"), _item(_IRRELEVANT_TITLE_B, "U2")]
    mock_fetch_boarddocs = AsyncMock(return_value=items)
    mock_fetch_bodies = AsyncMock(return_value=0)

    with _patch_client(
        fetch_boarddocs=mock_fetch_boarddocs, fetch_boarddocs_bodies=mock_fetch_bodies
    ):
        result = await _fetch_board_minutes("11331", signal=_SIGNAL)  # must not raise

    mock_fetch_bodies.assert_not_called()
    assert result == [
        {
            "title": it["title"],
            "date": it["date"],
            "source_url": it["source_url"],
            "text": it["text"][:1500],
        }
        for it in items
    ]


# ===========================================================================
# 7. Relevant/enriched items are ordered ahead of the old [:10] trim
# ===========================================================================


async def test_relevant_items_are_reordered_ahead_of_the_ten_item_trim() -> None:
    """Without this, the fix cannot work in practice.

    A district's handful of relevant items are typically scattered across
    many procedural agenda items (roll call, minutes approval, routine
    consent items). If the two relevant items below sat at positions 12 and
    14 and _fetch_board_minutes still just took items[:10] in raw order, it
    would spend the HTTP budget fetching their bodies and then throw the
    result away before synthesis ever saw it -- worse than doing nothing.
    """
    procedural = [_item(_IRRELEVANT_TITLE_A, f"P{i}") for i in range(12)]
    relevant = [_item(_RELEVANT_TITLE_A, "R1"), _item(_RELEVANT_TITLE_B, "R2")]
    items = procedural + relevant  # relevant items at raw positions 12 and 13
    assert len(items) == 14

    mock_fetch_boarddocs = AsyncMock(return_value=items)

    async def fake_body(base_url: str, item_unique: str, committee_id: str, http: Any) -> str:
        return f"real body for {item_unique}"

    with _patch_client(fetch_boarddocs=mock_fetch_boarddocs, fetch_agenda_item_body=fake_body):
        result = await _fetch_board_minutes("11331", signal=_SIGNAL)

    assert len(result) == 10  # the existing prompt-size cap is unchanged
    returned_uniques = [it["source_url"].rsplit("id=", 1)[-1] for it in result]
    assert returned_uniques[0] in {"R1", "R2"}
    assert returned_uniques[1] in {"R1", "R2"}
    assert {returned_uniques[0], returned_uniques[1]} == {"R1", "R2"}
    # And their real bodies made it through, not just their titles.
    assert result[0]["text"].startswith("real body for")
    assert result[1]["text"].startswith("real body for")
