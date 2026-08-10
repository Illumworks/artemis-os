"""Unit tests for the opt-in commitments Phase 1 core.

These tests mock the DB session / repo so they do NOT require a live database.
They verify the logic in commitments.py and repository.py without depending on
the test DB being available in the worktree environment.

Tests cover:
1. Owner+deadline gate:
   - owned by Jon + has due  → proposed commitment created, observation written.
   - not owned by Jon        → NO commitment, observation still written.
   - owned by Jon, no due    → NO commitment, observation still written.
2. Follow-ups: list_commitment_followup_candidates filters status='active' only —
   proposed commitments are excluded.
3. approve_commitment → status='active', decision row inserted with decision='approve'.
4. dismiss_commitment → status='dismissed', decision row inserted with decision='dismiss'.
5. upsert_commitment status param defaults to 'active' (back-compat).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.proactivity.commitments import (
    _build_decision_features,
    approve_commitment,
    dismiss_commitment,
    ingest_meeting_commitments,
)
from artemis.proactivity.models import Commitment


# ─── helpers ────────────────────────────────────────────────────────────────

_OWNER_ID = 42
_NOW = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
_DUE = datetime(2026, 6, 20, 21, 0, tzinfo=UTC)


def _make_commitment(
    *,
    id: int = 1,
    status: str = "proposed",
    owner_user_id: int | None = _OWNER_ID,
    due: datetime | None = _DUE,
    sensitivity: str = "personal_ops",
    source_type: str = "granola_meeting",
    source_id: str = "g-123",
) -> Commitment:
    c = Commitment()
    c.id = id
    c.status = status
    c.owner_user_id = owner_user_id
    c.due = due
    c.sensitivity = sensitivity
    c.source_type = source_type
    c.source_id = source_id
    c.text = "Test action item"
    c.created_at = _NOW
    c.updated_at = _NOW
    return c


def _user_mock(user_id: int) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.last_seen_at = _NOW
    return u


def _result_mock(val: Any) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=val)
    return r


def _make_ingest_session(
    *,
    canonical_owner_id: int | None,
    item_owner_id: int | None,
    dismissed: bool = False,
) -> AsyncMock:
    """Build a mock AsyncSession for ingest_meeting_commitments tests.

    execute() call order inside ingest_meeting_commitments:
      1. _resolve_canonical_owner_user_id (before the loop)
      2. Per-item: MeetingActionItemDismissal check
      3. Per-item: _resolve_owner_user_id (item owner label)
    """
    session = AsyncMock()

    seq = [
        # 1) canonical owner lookup
        _result_mock(_user_mock(canonical_owner_id) if canonical_owner_id else None),
        # 2) dismissal check
        _result_mock(MagicMock() if dismissed else None),
        # 3) item owner lookup
        _result_mock(_user_mock(item_owner_id) if item_owner_id else None),
    ]
    call_count = [0]

    async def _execute(stmt, *a, **kw):
        idx = min(call_count[0], len(seq) - 1)
        call_count[0] += 1
        return seq[idx]

    session.execute = _execute
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    return session


# ─── gate tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_owner_and_due_creates_proposed_commitment() -> None:
    """owner=Jon + due present → proposed commitment created, observation written."""
    commitment = _make_commitment(status="proposed")
    session = _make_ingest_session(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OWNER_ID,
    )

    with (
        patch(
            "artemis.proactivity.commitments.repo.upsert_commitment",
            new=AsyncMock(return_value=(commitment, True)),
        ) as mock_upsert,
        patch(
            "artemis.proactivity.commitments.write_observation",
            new=AsyncMock(),
        ) as mock_obs,
    ):
        summary = await ingest_meeting_commitments(
            session,
            granola_id="g-test-1",
            title="Weekly sync",
            action_items=[{"text": "Send the report", "owner": "Jon", "due": "2026-06-20"}],
            now=_NOW,
        )

    assert summary.seen == 1
    assert summary.inserted == 1
    assert summary.deduped == 0

    mock_upsert.assert_awaited_once()
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["status"] == "proposed", "Gate must pass status='proposed'"
    assert call_kwargs["source_type"] == "granola_meeting"

    mock_obs.assert_awaited_once()


@pytest.mark.asyncio
async def test_gate_non_owner_no_commitment_but_observation_written() -> None:
    """owner != Jon → NO commitment created, observation still written (lossless)."""
    _OTHER_ID = 99
    session = _make_ingest_session(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OTHER_ID,
    )

    with (
        patch(
            "artemis.proactivity.commitments.repo.upsert_commitment",
            new=AsyncMock(),
        ) as mock_upsert,
        patch(
            "artemis.proactivity.commitments.write_observation",
            new=AsyncMock(),
        ) as mock_obs,
    ):
        summary = await ingest_meeting_commitments(
            session,
            granola_id="g-test-2",
            title="Weekly sync",
            action_items=[{"text": "Prepare the slides", "owner": "Alice", "due": "2026-06-20"}],
            now=_NOW,
        )

    assert summary.seen == 1
    assert summary.inserted == 0  # No commitment created
    assert summary.deduped == 0

    mock_upsert.assert_not_awaited()  # Gate blocked commitment creation
    mock_obs.assert_awaited_once()  # Observation still written (lossless)


@pytest.mark.asyncio
async def test_gate_owner_no_due_no_commitment_but_observation_written() -> None:
    """owner=Jon + no due → NO commitment, observation still written (lossless)."""
    session = _make_ingest_session(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OWNER_ID,
    )

    with (
        patch(
            "artemis.proactivity.commitments.repo.upsert_commitment",
            new=AsyncMock(),
        ) as mock_upsert,
        patch(
            "artemis.proactivity.commitments.write_observation",
            new=AsyncMock(),
        ) as mock_obs,
    ):
        summary = await ingest_meeting_commitments(
            session,
            granola_id="g-test-3",
            title="Weekly sync",
            action_items=[{"text": "Review the doc", "owner": "Jon", "due": None}],
            now=_NOW,
        )

    assert summary.seen == 1
    assert summary.inserted == 0  # No commitment (no due)
    mock_upsert.assert_not_awaited()
    mock_obs.assert_awaited_once()  # Observation still written


@pytest.mark.asyncio
async def test_gate_dismissed_item_skipped_entirely() -> None:
    """Dismissed item is skipped (seen=0) — pre-existing behavior preserved."""
    session = _make_ingest_session(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OWNER_ID,
        dismissed=True,
    )

    with (
        patch(
            "artemis.proactivity.commitments.repo.upsert_commitment", new=AsyncMock()
        ) as mock_upsert,
        patch("artemis.proactivity.commitments.write_observation", new=AsyncMock()) as mock_obs,
    ):
        summary = await ingest_meeting_commitments(
            session,
            granola_id="g-test-4",
            title="Weekly sync",
            action_items=[{"text": "Dismissed item", "owner": "Jon", "due": "2026-06-20"}],
            now=_NOW,
        )

    assert summary.seen == 0
    mock_upsert.assert_not_awaited()
    mock_obs.assert_not_awaited()


# ─── follow-up gate test ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followup_candidates_status_active_filter() -> None:
    """list_commitment_followup_candidates SQL query must include status='active'.

    We verify by compiling the SQLAlchemy query with literal_binds=True so
    parameter values appear as literals in the string.  This confirms that
    'proposed' commitments are structurally excluded.
    """
    from sqlalchemy.dialects import postgresql
    from artemis.proactivity.repository import list_commitment_followup_candidates

    active_c = _make_commitment(id=2, status="active")

    captured_queries: list[str] = []
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[active_c])
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)

    session = AsyncMock()

    async def capture_execute(stmt, *a, **kw):
        compiled = stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        captured_queries.append(str(compiled))
        return result_mock

    session.execute = capture_execute

    due_cutoff = _NOW + timedelta(hours=48)
    renotify_cutoff = _NOW - timedelta(hours=24)
    results = await list_commitment_followup_candidates(
        session,
        now=_NOW,
        due_soon_cutoff=due_cutoff,
        renotify_cutoff=renotify_cutoff,
    )

    assert results == [active_c]
    assert len(captured_queries) == 1
    query_str = captured_queries[0]
    # The WHERE clause must contain a literal 'active' filter
    assert "'active'" in query_str, f"Expected 'active' in compiled query but got:\n{query_str}"
    # 'proposed' must NOT appear — it is not a value searched for
    assert "'proposed'" not in query_str, (
        "Query must not reference 'proposed' — it should be excluded by the 'active' filter"
    )


# ─── approve / dismiss + decision capture ───────────────────────────────────


@pytest.mark.asyncio
async def test_approve_commitment_sets_active_and_records_decision() -> None:
    """approve_commitment → status='active', approve_commitment called with features."""
    commitment = _make_commitment(status="proposed")

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_commitment",
            new=AsyncMock(return_value=commitment),
        ),
        patch(
            "artemis.proactivity.commitments.repo.approve_commitment",
            new=AsyncMock(return_value=_make_commitment(status="active")),
        ) as mock_approve,
        patch(
            "artemis.proactivity.commitments._resolve_canonical_owner_user_id",
            new=AsyncMock(return_value=_OWNER_ID),
        ),
    ):
        session = AsyncMock()
        result = await approve_commitment(session, commitment_id=1)

    assert result is not None
    assert result.status == "active"
    mock_approve.assert_awaited_once()
    call_kwargs = mock_approve.call_args.kwargs
    assert call_kwargs["commitment_id"] == 1
    features = call_kwargs["features"]
    assert features["owner_is_owner"] is True  # commitment.owner_user_id == _OWNER_ID
    assert features["had_due"] is True  # commitment.due is set
    assert features["sensitivity"] == "personal_ops"
    assert features["source_type"] == "granola_meeting"
    assert features["source_id"] == "g-123"


@pytest.mark.asyncio
async def test_dismiss_commitment_sets_dismissed_and_records_decision() -> None:
    """dismiss_commitment → status='dismissed', dismiss_commitment_with_decision called."""
    commitment = _make_commitment(status="proposed")

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_commitment",
            new=AsyncMock(return_value=commitment),
        ),
        patch(
            "artemis.proactivity.commitments.repo.dismiss_commitment_with_decision",
            new=AsyncMock(return_value=_make_commitment(status="dismissed")),
        ) as mock_dismiss,
        patch(
            "artemis.proactivity.commitments._resolve_canonical_owner_user_id",
            new=AsyncMock(return_value=_OWNER_ID),
        ),
    ):
        session = AsyncMock()
        result = await dismiss_commitment(session, commitment_id=1)

    assert result is not None
    assert result.status == "dismissed"
    mock_dismiss.assert_awaited_once()
    call_kwargs = mock_dismiss.call_args.kwargs
    assert call_kwargs["commitment_id"] == 1
    features = call_kwargs["features"]
    assert features["owner_is_owner"] is True
    assert features["had_due"] is True


@pytest.mark.asyncio
async def test_approve_commitment_not_found_returns_none() -> None:
    """approve_commitment returns None when the commitment doesn't exist."""
    with patch(
        "artemis.proactivity.commitments.repo.get_commitment",
        new=AsyncMock(return_value=None),
    ):
        session = AsyncMock()
        result = await approve_commitment(session, commitment_id=9999)

    assert result is None


@pytest.mark.asyncio
async def test_dismiss_commitment_not_found_returns_none() -> None:
    """dismiss_commitment returns None when the commitment doesn't exist."""
    with patch(
        "artemis.proactivity.commitments.repo.get_commitment",
        new=AsyncMock(return_value=None),
    ):
        session = AsyncMock()
        result = await dismiss_commitment(session, commitment_id=9999)

    assert result is None


@pytest.mark.asyncio
async def test_decision_features_owner_not_owner_flag() -> None:
    """_build_decision_features sets owner_is_owner=False for non-owner commitments."""
    commitment = _make_commitment(owner_user_id=99, due=None)  # not owner, no due

    with patch(
        "artemis.proactivity.commitments._resolve_canonical_owner_user_id",
        new=AsyncMock(return_value=_OWNER_ID),  # canonical = 42, commitment owner = 99
    ):
        session = AsyncMock()
        features = await _build_decision_features(session, commitment)

    assert features["owner_is_owner"] is False
    assert features["had_due"] is False
    assert features["sensitivity"] == "personal_ops"


@pytest.mark.asyncio
async def test_upsert_commitment_status_param_defaults_active() -> None:
    """upsert_commitment defaults status='active' — no breaking change for existing callers."""
    from artemis.proactivity import repository as prepo

    commitment = _make_commitment(status="active")
    # Simulate conflict (on_conflict_do_nothing returns no row), then SELECT
    inserted_id_result = MagicMock()
    inserted_id_result.scalar_one_or_none = MagicMock(return_value=None)

    select_result = MagicMock()
    select_result.scalar_one = MagicMock(return_value=commitment)

    session = AsyncMock()
    call_count = [0]

    async def execute_side(stmt, *a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return inserted_id_result  # INSERT on_conflict → no row (conflict)
        return select_result  # SELECT to fetch existing row

    session.execute = execute_side
    session.get = AsyncMock(return_value=None)

    # No status= kwarg — testing default
    row, created = await prepo.upsert_commitment(
        session,
        source_type="test",
        source_id="t-1",
        text="Do the thing",
        owner_user_id=None,
        due=None,
        sensitivity="personal_ops",
    )
    assert row.status == "active"
    assert created is False  # Deduped path


@pytest.mark.asyncio
async def test_upsert_commitment_passes_status_proposed() -> None:
    """upsert_commitment accepts status='proposed' and passes it in the INSERT."""
    from artemis.proactivity import repository as prepo

    commitment = _make_commitment(status="proposed")
    # Simulate INSERT succeeds (returns inserted id)
    inserted_id_result = MagicMock()
    inserted_id_result.scalar_one_or_none = MagicMock(return_value=1)  # inserted_id = 1

    session = AsyncMock()
    session.execute = AsyncMock(return_value=inserted_id_result)
    session.get = AsyncMock(return_value=commitment)

    row, created = await prepo.upsert_commitment(
        session,
        source_type="granola_meeting",
        source_id="g-new",
        text="New item",
        owner_user_id=_OWNER_ID,
        due=_DUE,
        sensitivity="personal_ops",
        status="proposed",
    )
    assert created is True
    assert row.status == "proposed"
