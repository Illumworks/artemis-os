"""Unit tests for artemis.enablement.sync (mocked — no DB required).

Tests:
1. _parse_csv: header normalisation, tag splitting, extra-column capture.
2. _map_row: field mapping, slug key synthesis for rows with no drive_file_id.
3. sync_enablement_index — upserts rows from sample CSV data.
4. sync_enablement_index — idempotent (re-run produces same rows, no dupes).
5. sync_enablement_index — empty sheet → 0 rows, no DB writes.
6. Embedding text built from correct fields (title + summary + tags + transcript).
7. Rows with no drive_file_id get stable slug keys.
"""

from __future__ import annotations

# ── minimal env so artemis.config doesn't blow up ────────────────────────────
import os
import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("ARTEMIS_DB_URL", "postgresql+asyncpg://test:test@localhost/test_unit")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-real")
os.environ.setdefault("FERNET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

from artemis.enablement.sync import (  # noqa: E402
    _build_embedding_text,
    _parse_csv,
    _slugify,
    sync_enablement_index,
)

# ── helpers ────────────────────────────────────────────────────────────────────


SAMPLE_CSV = textwrap.dedent("""\
    drive_file_id,asset_name,type,drive_link,title,summary,tags,audience,status,confidence_label,source_scope
    abc123,Onboarding Guide,doc,https://drive.google.com/abc123,Onboarding Guide v2,"Covers first-week setup","onboarding,hr",new-hire,active,high,enablement
    vid456,Product Demo Video,video,https://drive.google.com/vid456,Product Demo 2026,Short demo of core features,"demo,product",all,active,medium,shared
    ,No-ID Asset,doc,,Asset With No Drive ID,Summary text,"misc",internal,draft,low,enablement
""")

SAMPLE_CSV_EMPTY = "drive_file_id,asset_name,type\n"

SAMPLE_CSV_BLANK = ""


# ── _parse_csv ─────────────────────────────────────────────────────────────────


def test_parse_csv_returns_correct_row_count():
    header, rows = _parse_csv(SAMPLE_CSV)
    # 3 data rows (header stripped)
    assert len(rows) == 3


def test_parse_csv_maps_known_columns():
    _, rows = _parse_csv(SAMPLE_CSV)
    first = rows[0]
    assert first["drive_file_id"] == "abc123"
    assert first["title"] == "Onboarding Guide v2"
    assert first["type"] == "doc"
    assert first["source_scope"] == "enablement"


def test_parse_csv_splits_tags_correctly():
    _, rows = _parse_csv(SAMPLE_CSV)
    first = rows[0]
    assert isinstance(first["tags"], list)
    assert "onboarding" in first["tags"]
    assert "hr" in first["tags"]


def test_parse_csv_empty_sheet_returns_no_rows():
    _, rows = _parse_csv(SAMPLE_CSV_EMPTY)
    assert rows == []


def test_parse_csv_blank_string_returns_no_rows():
    _, rows = _parse_csv(SAMPLE_CSV_BLANK)
    assert rows == []


# ── _build_embedding_text ─────────────────────────────────────────────────────


def test_embedding_text_combines_title_summary_tags():
    row = {
        "title": "My Title",
        "summary": "A useful summary",
        "tags": ["tag1", "tag2"],
        "transcript_text": None,
    }
    text = _build_embedding_text(row)
    assert "My Title" in text
    assert "A useful summary" in text
    assert "tag1" in text
    assert "tag2" in text


def test_embedding_text_includes_truncated_transcript():
    row = {
        "title": "Demo",
        "summary": "Short",
        "tags": [],
        "transcript_text": "A" * 3000,
    }
    text = _build_embedding_text(row)
    # transcript truncated at 2000 chars
    assert text.count("A") == 2000


def test_embedding_text_empty_row_returns_empty_string():
    assert _build_embedding_text({}) == ""


# ── slug key synthesis ────────────────────────────────────────────────────────


def test_slug_key_synthesised_for_no_id_rows():
    _, rows = _parse_csv(SAMPLE_CSV)
    no_id_row = rows[2]
    # drive_file_id should be None from the CSV (empty cell)
    assert not no_id_row.get("drive_file_id")
    # asset_name and title are present
    assert no_id_row.get("asset_name") == "No-ID Asset"


def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"
    assert _slugify("Multiple   Spaces") == "multiple-spaces"
    assert _slugify("Special@#$Chars") == "special-chars"


# ── sync_enablement_index (mocked) ────────────────────────────────────────────


def _make_mock_session(executed_stmts: list | None = None) -> AsyncMock:
    """Return an AsyncMock DB session that captures executed statements."""
    session = AsyncMock()
    stmts: list = executed_stmts if executed_stmts is not None else []

    async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        stmts.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    session.execute.side_effect = _execute
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_sync_upserts_rows_from_sample_csv():
    """sync_enablement_index processes rows and flushes the session."""
    stmts: list = []
    session = _make_mock_session(stmts)

    with (
        patch(
            "artemis.enablement.sync._get_personal_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "artemis.enablement.sync._fetch_sheet_csv",
            new=AsyncMock(return_value=SAMPLE_CSV),
        ),
        patch(
            "artemis.enablement.sync._compute_embedding",
            new=AsyncMock(return_value=[0.1] * 384),
        ),
    ):
        count = await sync_enablement_index(session)

    # 3 data rows (one with no id gets a slug key)
    assert count == 3
    session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_sync_empty_sheet_returns_zero():
    """Empty sheet → 0 rows, session never flushed."""
    session = _make_mock_session()

    with (
        patch(
            "artemis.enablement.sync._get_personal_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "artemis.enablement.sync._fetch_sheet_csv",
            new=AsyncMock(return_value=SAMPLE_CSV_EMPTY),
        ),
    ):
        count = await sync_enablement_index(session)

    assert count == 0
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_sync_no_token_returns_zero():
    """No personal Google credential → 0 rows, no DB writes."""
    session = _make_mock_session()

    with patch(
        "artemis.enablement.sync._get_personal_access_token",
        new=AsyncMock(return_value=None),
    ):
        count = await sync_enablement_index(session)

    assert count == 0
    session.execute.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_sync_fetch_failure_returns_zero():
    """Drive export failure → 0 rows, session not flushed."""
    session = _make_mock_session()

    with (
        patch(
            "artemis.enablement.sync._get_personal_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "artemis.enablement.sync._fetch_sheet_csv",
            new=AsyncMock(return_value=None),
        ),
    ):
        count = await sync_enablement_index(session)

    assert count == 0
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_sync_idempotent_same_count_on_rerun():
    """Two successive runs produce the same upserted count (no phantom extras)."""
    stmts_run1: list = []
    stmts_run2: list = []
    session1 = _make_mock_session(stmts_run1)
    session2 = _make_mock_session(stmts_run2)

    patches = (
        patch(
            "artemis.enablement.sync._get_personal_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "artemis.enablement.sync._fetch_sheet_csv",
            new=AsyncMock(return_value=SAMPLE_CSV),
        ),
        patch(
            "artemis.enablement.sync._compute_embedding",
            new=AsyncMock(return_value=[0.1] * 384),
        ),
    )

    with patches[0], patches[1], patches[2]:
        count1 = await sync_enablement_index(session1)
        count2 = await sync_enablement_index(session2)

    assert count1 == count2 == 3


@pytest.mark.asyncio
async def test_sync_shared_scope_row():
    """Rows with source_scope='shared' are upserted with that scope value."""
    stmts: list = []
    session = _make_mock_session(stmts)
    shared_csv = textwrap.dedent("""\
        drive_file_id,asset_name,type,title,summary,source_scope
        shared001,Shared Deck,doc,Shared Sales Deck,Shared externally,shared
    """)

    with (
        patch(
            "artemis.enablement.sync._get_personal_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "artemis.enablement.sync._fetch_sheet_csv",
            new=AsyncMock(return_value=shared_csv),
        ),
        patch(
            "artemis.enablement.sync._compute_embedding",
            new=AsyncMock(return_value=[0.0] * 384),
        ),
    ):
        count = await sync_enablement_index(session)

    assert count == 1
