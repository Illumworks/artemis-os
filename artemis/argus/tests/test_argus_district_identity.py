"""Tests for ARGUS-2 -- connecting districts to the board-minutes sources
that already know them.

Two groups:
  - Pure-data tests on ``artemis.argus.board_minutes_backfill`` (no DB): the
    hand-verified peer_scout -> districts.id mapping is correct, unique, and
    accounts for all 27 peer_scout watch-list entries (mapped or explicitly
    unmapped -- never silently dropped).
  - DB-backed tests (real Postgres, via ``conftest.py``'s ARTEMIS_TEST_DB_URL
    guard) on ``_resolve_district_row`` / ``_fetch_board_minutes`` /
    ``_resolve_search_term``: the runtime resolution seam, exercised against
    seeded ``districts`` rows rather than the real backfill data (which
    depends on the full 13,466-row NCES load that test databases don't
    carry -- see the ARGUS-2 report).

Required-by-brief coverage:
  - back-fill maps Dallas ISD to the disd URL and not any other TX district
    (assert the specific row)                         -- pure-data group.
  - a district with no stored URL still yields [] and does not raise
                                                        -- DB group.
  - a signal-supplied boarddocs_url still wins over the stored one
                                                        -- DB group.
  - the stored URL is used when the signal has none (the case that was
    broken before ARGUS-2)                             -- DB group.
  - _fetch_board_minutes and _resolve_search_term resolve the same district
    key to the same district row                       -- DB group.
  - ambiguous peer_scout entries are left unmapped rather than guessed
                                                        -- pure-data group
    (OH_cleveland).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text as sql_text

import artemis.db as _db
from artemis.argus.board_minutes_backfill import BOARD_MINUTES_BACKFILL, BOARD_MINUTES_UNMAPPED
from artemis.argus.research import _fetch_board_minutes, _resolve_district_row, _resolve_search_term
from artemis.scouts.board_minutes.peer_scout import _DEFAULT_PEER_WATCH_LIST

# ── Pure-data: the hand-verified backfill mapping ──────────────────────────────


def test_dallas_backfill_maps_to_the_correct_row_and_not_another_tx_district() -> None:
    """The brief's own required assertion: Dallas ISD -> the disd URL, specifically."""
    dallas = next(e for e in BOARD_MINUTES_BACKFILL if e.peer_scout_district_id == "TX_dallas")
    assert dallas.districts_id == 11331
    assert dallas.expected_name == "DALLAS ISD"
    assert dallas.boarddocs_url == "https://go.boarddocs.com/tx/disd/Board.nsf/Public"

    # No OTHER entry (in TX or otherwise) shares Dallas's id or URL -- a
    # mis-mapping here is exactly "publishes another district's board into
    # Dallas's dossier" or vice versa.
    others = [e for e in BOARD_MINUTES_BACKFILL if e.peer_scout_district_id != "TX_dallas"]
    assert all(e.districts_id != dallas.districts_id for e in others)
    assert all(e.boarddocs_url != dallas.boarddocs_url for e in others)


def test_backfill_entries_map_to_unique_districts() -> None:
    """Two peer_scout entries must never resolve to the same district row."""
    ids = [e.districts_id for e in BOARD_MINUTES_BACKFILL]
    assert len(ids) == len(set(ids)), f"duplicate districts_id in backfill: {ids}"


def test_cleveland_is_left_unmapped_rather_than_guessed() -> None:
    """OH_cleveland has 4 same-state name collisions with no exact-match
    disambiguator -- ARGUS-2's real example of 'leave it unmapped'."""
    mapped_ids = {e.peer_scout_district_id for e in BOARD_MINUTES_BACKFILL}
    assert "OH_cleveland" not in mapped_ids

    unmapped_ids = {entry_id for entry_id, _reason in BOARD_MINUTES_UNMAPPED}
    assert "OH_cleveland" in unmapped_ids


def test_backfill_accounts_for_every_peer_scout_entry() -> None:
    """Every one of peer_scout's 27 watch-list entries is either mapped or
    explicitly recorded as unmapped -- none silently dropped, and this fails
    loudly if peer_scout's list is ever edited without updating the backfill."""
    peer_ids = {d["district_id"] for d in _DEFAULT_PEER_WATCH_LIST}
    assert len(peer_ids) == 27, f"expected 27 peer_scout entries, found {len(peer_ids)}"

    mapped_ids = {e.peer_scout_district_id for e in BOARD_MINUTES_BACKFILL}
    unmapped_ids = {entry_id for entry_id, _reason in BOARD_MINUTES_UNMAPPED}

    assert mapped_ids.isdisjoint(unmapped_ids), "an entry cannot be both mapped and unmapped"
    assert mapped_ids | unmapped_ids == peer_ids, (
        f"backfill does not account for all peer_scout entries: "
        f"missing={peer_ids - (mapped_ids | unmapped_ids)} "
        f"extra={(mapped_ids | unmapped_ids) - peer_ids}"
    )
    assert len(mapped_ids) == 26
    assert len(unmapped_ids) == 1


def test_backfill_urls_match_peer_scouts_exactly() -> None:
    """Every backfilled boarddocs_url is transcribed verbatim from peer_scout --
    a typo here would attach a district to a URL peer_scout never listed."""
    peer_by_id = {d["district_id"]: d["boarddocs_url"] for d in _DEFAULT_PEER_WATCH_LIST}
    for entry in BOARD_MINUTES_BACKFILL:
        assert entry.boarddocs_url == peer_by_id[entry.peer_scout_district_id], (
            f"{entry.peer_scout_district_id}: backfill URL != peer_scout's URL"
        )


# ── DB-backed: the runtime resolution seam ─────────────────────────────────────


@pytest.fixture
async def seeded_districts() -> AsyncIterator[dict[str, int]]:
    """Insert a target + decoy districts row; clean up after.

    Mirrors the real risk ARGUS-2 exists to avoid: two same-state rows that
    could be confused for each other under fuzzy matching. The target
    carries a boarddocs_url; the decoy does not and must never be used when
    resolving the target's key (or vice versa).

    districts.id is BigInteger/autoincrement -- artemis_test_a carries no
    NCES load (0 rows), so whatever ids Postgres assigns here are exercised
    directly rather than assumed. Rows are deleted (not superseded) at
    teardown: the CLAUDE.md lossless rule governs memory_observations /
    memory_drawers, not this reference table, and this is a test database.
    """
    async with _db.SessionLocal() as session:
        target_id = (
            await session.execute(
                sql_text(
                    "INSERT INTO districts (name, state, boarddocs_url) "
                    "VALUES (:name, :state, :url) RETURNING id"
                ),
                {
                    "name": "ARGUS2 TEST ISD",
                    "state": "TX",
                    "url": "https://go.boarddocs.com/tx/argus2test/Board.nsf/Public",
                },
            )
        ).scalar_one()
        decoy_id = (
            await session.execute(
                sql_text("INSERT INTO districts (name, state) VALUES (:name, :state) RETURNING id"),
                {"name": "ARGUS2 TEST DECOY ISD", "state": "TX"},
            )
        ).scalar_one()
        await session.commit()

    try:
        yield {"target": target_id, "decoy": decoy_id}
    finally:
        async with _db.SessionLocal() as session:
            await session.execute(
                sql_text("DELETE FROM districts WHERE id IN (:a, :b)"),
                {"a": target_id, "b": decoy_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_resolve_district_row_matches_on_id_and_returns_that_rows_url(
    seeded_districts: dict[str, int],
) -> None:
    row = await _resolve_district_row(str(seeded_districts["target"]))
    assert row is not None
    assert row.id == seeded_districts["target"]
    assert row.name == "ARGUS2 TEST ISD"
    assert row.boarddocs_url == "https://go.boarddocs.com/tx/argus2test/Board.nsf/Public"


@pytest.mark.asyncio
async def test_resolve_district_row_returns_none_for_a_free_text_key(
    seeded_districts: dict[str, int],
) -> None:
    """A key that is a name, not an id (e.g. 'St. Louis Public Schools', 'IL-U46' in
    production), never matches ``id::text = key`` -- returns None, doesn't guess."""
    assert await _resolve_district_row("ARGUS2 TEST ISD") is None
    assert await _resolve_district_row("") is None


@pytest.mark.asyncio
async def test_fetch_board_minutes_uses_the_stored_url_when_signal_has_none(
    seeded_districts: dict[str, int],
) -> None:
    """The case that was broken before ARGUS-2: no signal-supplied URL, but the
    district row has one -- board_minutes must now use it."""
    mock_fetch = AsyncMock(return_value=[{"title": "t", "date": "2026-08-01", "source_url": "u", "text": "x"}])
    with patch("artemis.scouts.board_minutes.client.fetch_boarddocs", new=mock_fetch):
        result = await _fetch_board_minutes(str(seeded_districts["target"]), signal=None)

    assert result, "must return the mocked items once the stored URL is used"
    mock_fetch.assert_awaited_once()
    called_cfg = mock_fetch.call_args.args[0]
    assert called_cfg["boarddocs_url"] == "https://go.boarddocs.com/tx/argus2test/Board.nsf/Public"


@pytest.mark.asyncio
async def test_fetch_board_minutes_signal_supplied_url_wins_over_stored(
    seeded_districts: dict[str, int],
) -> None:
    mock_fetch = AsyncMock(return_value=[])
    signal = {"boarddocs_url": "https://go.boarddocs.com/tx/signal-supplied/Board.nsf/Public"}
    with patch("artemis.scouts.board_minutes.client.fetch_boarddocs", new=mock_fetch):
        await _fetch_board_minutes(str(seeded_districts["target"]), signal=signal)

    mock_fetch.assert_awaited_once()
    called_cfg = mock_fetch.call_args.args[0]
    assert called_cfg["boarddocs_url"] == "https://go.boarddocs.com/tx/signal-supplied/Board.nsf/Public"


@pytest.mark.asyncio
async def test_fetch_board_minutes_no_stored_url_and_no_signal_yields_empty_list_and_does_not_raise(
    seeded_districts: dict[str, int],
) -> None:
    mock_fetch = AsyncMock(return_value=[{"should": "never be reached"}])
    with patch("artemis.scouts.board_minutes.client.fetch_boarddocs", new=mock_fetch):
        result = await _fetch_board_minutes(str(seeded_districts["decoy"]), signal=None)

    assert result == []
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_board_minutes_never_uses_the_decoys_url_for_the_targets_key(
    seeded_districts: dict[str, int],
) -> None:
    """Confidence the lookup landed on the RIGHT row, not merely A row."""
    mock_fetch = AsyncMock(return_value=[])
    with patch("artemis.scouts.board_minutes.client.fetch_boarddocs", new=mock_fetch):
        await _fetch_board_minutes(str(seeded_districts["target"]), signal=None)

    called_cfg = mock_fetch.call_args.args[0]
    assert called_cfg["boarddocs_url"] == "https://go.boarddocs.com/tx/argus2test/Board.nsf/Public"
    assert called_cfg["boarddocs_url"] != "https://go.boarddocs.com/tx/argus2test-decoy/Board.nsf/Public"


@pytest.mark.asyncio
async def test_fetch_board_minutes_and_resolve_search_term_agree_on_the_same_district(
    seeded_districts: dict[str, int],
) -> None:
    """Required test: both lookups must resolve a given district_key to the
    SAME district row -- neither may search on/attach to a different one."""
    key = str(seeded_districts["target"])

    resolved_name = await _resolve_search_term(key)
    assert resolved_name == "ARGUS2 TEST ISD"

    mock_fetch = AsyncMock(return_value=[])
    with patch("artemis.scouts.board_minutes.client.fetch_boarddocs", new=mock_fetch):
        await _fetch_board_minutes(key, signal=None)
    called_cfg = mock_fetch.call_args.args[0]

    # Both calls, given the same key, landed on the target row: the name
    # _resolve_search_term returned belongs to the same row whose
    # boarddocs_url _fetch_board_minutes used.
    row = await _resolve_district_row(key)
    assert row is not None
    assert row.name == resolved_name
    assert row.boarddocs_url == called_cfg["boarddocs_url"]
