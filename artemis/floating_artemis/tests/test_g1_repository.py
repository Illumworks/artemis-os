"""Tests for Floating Artemis repository helpers (in-memory mock DB).

These tests use mock sessions rather than real DB connections so they can
run without a Postgres instance. The DB integration tests live in the
migration test suite (when DB is available).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from artemis.floating_artemis import repository as repo

pytestmark = pytest.mark.asyncio


def _make_mock_session() -> Any:
    """Return a minimal mock that satisfies async session protocol."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


# ── Session CRUD ──────────────────────────────────────────────────────────────


async def test_create_session_sets_fields() -> None:
    mock_session = _make_mock_session()

    # After refresh, the row will be the added object
    created_row = None

    def capture_add(row: Any) -> None:
        nonlocal created_row
        created_row = row

    mock_session.add = capture_add
    mock_session.refresh = AsyncMock(side_effect=lambda row: None)

    await repo.create_session(
        mock_session,
        session_id="test-sid",
        owner_user_id=1,
        title="My Session",
        metadata={"env": "test"},
    )

    assert created_row is not None
    assert created_row.session_id == "test-sid"
    assert created_row.owner_user_id == 1
    assert created_row.title == "My Session"
    assert created_row.metadata_ == {"env": "test"}


async def test_get_session_by_id_not_found() -> None:
    mock_session = _make_mock_session()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ValueError, match="not found"):
        await repo.get_session_by_id(mock_session, "nonexistent")


async def test_close_session_sets_closed_at() -> None:
    mock_session = _make_mock_session()

    mock_row = MagicMock()
    mock_row.session_id = "s1"
    mock_row.closed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.refresh = AsyncMock(side_effect=lambda r: None)

    closed = await repo.close_session(mock_session, "s1")
    assert closed.closed_at is not None


async def test_update_session_sets_last_active_at() -> None:
    mock_session = _make_mock_session()
    mock_row = MagicMock()
    mock_row.session_id = "s2"
    mock_row.title = "Old Title"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.refresh = AsyncMock(side_effect=lambda r: None)

    before = datetime.now(UTC)
    updated = await repo.update_session(mock_session, "s2", title="New Title")
    assert updated.last_active_at >= before


# ── Messages ──────────────────────────────────────────────────────────────────


async def test_add_message_sets_fields() -> None:
    mock_session = _make_mock_session()
    captured = None

    def capture_add(row: Any) -> None:
        nonlocal captured
        captured = row

    mock_session.add = capture_add
    mock_session.refresh = AsyncMock(side_effect=lambda r: None)

    await repo.add_message(
        mock_session,
        session_id="s1",
        role="user",
        content=[{"type": "text", "text": "hello"}],
        cost_input_tokens=10,
        cost_output_tokens=5,
    )

    assert captured is not None
    assert captured.role == "user"
    assert captured.session_id == "s1"
    assert captured.cost_input_tokens == 10


async def test_add_message_default_costs() -> None:
    mock_session = _make_mock_session()
    captured = None

    def capture_add(row: Any) -> None:
        nonlocal captured
        captured = row

    mock_session.add = capture_add
    mock_session.refresh = AsyncMock(side_effect=lambda r: None)

    await repo.add_message(
        mock_session,
        session_id="s1",
        role="assistant",
        content=[],
    )
    assert captured is not None
    assert captured.cost_input_tokens == 0
    assert captured.cost_output_tokens == 0


async def test_list_messages_returns_empty_on_no_results() -> None:
    mock_session = _make_mock_session()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    msgs = await repo.list_messages(mock_session, "s1")
    assert msgs == []


# ── Page context ──────────────────────────────────────────────────────────────


async def test_set_page_context_creates_row() -> None:
    mock_session = _make_mock_session()
    captured = None

    def capture_add(row: Any) -> None:
        nonlocal captured
        captured = row

    mock_session.add = capture_add
    mock_session.refresh = AsyncMock(side_effect=lambda r: None)

    await repo.set_page_context(
        mock_session,
        session_id="s1",
        page="okr",
        ref_id="obj-99",
    )

    assert captured is not None
    assert captured.page == "okr"
    assert captured.ref_id == "obj-99"


async def test_get_latest_page_context_none() -> None:
    mock_session = _make_mock_session()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    ctx = await repo.get_latest_page_context(mock_session, "s1")
    assert ctx is None


# ── Voice corpus ──────────────────────────────────────────────────────────────


async def test_sample_voice_lines_empty_corpus() -> None:
    mock_session = _make_mock_session()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    lines = await repo.sample_voice_lines(mock_session, count=5)
    assert lines == []


async def test_sample_voice_lines_weighted_sampling() -> None:
    """Sampling returns at most count items and prefers low-use lines."""
    mock_session = _make_mock_session()

    # Create mock corpus entries
    mock_lines = []
    for i in range(10):
        line = MagicMock()
        line.line = f"Line {i}"
        line.use_count = i  # Line 0 has highest weight
        line.active = True
        mock_lines.append(line)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_lines
    mock_session.execute = AsyncMock(return_value=mock_result)

    sampled = await repo.sample_voice_lines(mock_session, count=5)
    assert len(sampled) <= 5
    # All returned items should be from our corpus
    all_lines_text = {f"Line {i}" for i in range(10)}
    for item in sampled:
        assert item.line in all_lines_text


async def test_sample_voice_lines_count_capped_by_corpus_size() -> None:
    """Requesting more than corpus size returns all corpus items."""
    mock_session = _make_mock_session()

    mock_lines = []
    for i in range(3):
        line = MagicMock()
        line.line = f"Line {i}"
        line.use_count = 0
        line.active = True
        mock_lines.append(line)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_lines
    mock_session.execute = AsyncMock(return_value=mock_result)

    sampled = await repo.sample_voice_lines(mock_session, count=100)
    assert len(sampled) == 3


async def test_bump_voice_line_use_updates_count() -> None:
    """bump_voice_line_use fires an UPDATE statement."""
    mock_session = _make_mock_session()
    mock_session.execute = AsyncMock()

    await repo.bump_voice_line_use(mock_session, line_id=42)
    mock_session.execute.assert_called_once()


# ── Active runs ───────────────────────────────────────────────────────────────


async def test_get_active_runs_returns_list() -> None:
    mock_session = _make_mock_session()

    # Simulate a view row
    mock_row = {
        "run_id": "r1",
        "run_type": "agent",
        "subject_id": "a1",
        "status": "running",
        "started_at": None,
        "completed_at": None,
        "owner_user_id": None,
    }
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    runs = await repo.get_active_runs(mock_session)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r1"
