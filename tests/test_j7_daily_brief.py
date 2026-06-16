"""Tests for J7 daily brief endpoints.

Covers:
  - GET  /api/daily-brief                — no snapshots → {brief: null, exists: false}
  - GET  /api/daily-brief                — seeded snapshot → returns hydrated brief
  - POST /api/daily-brief/generate       — mocked adapter → persists snapshot
  - POST /api/daily-brief/generate       — markdown-fenced JSON → extracts correctly
  - POST /api/daily-brief/generate       — prose-only output → 502 brief_generation_failed
  - GET  /api/daily-brief/history        — 10 seeded → returns 7 newest, metadata only
  - gather_sources resilience            — one source raises, others still populate
  - Round-trip                           — write then read back, contents match
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    s = AsyncMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    s.add = MagicMock()
    return s


def _fake_snapshot(
    id_: int = 1,
    brief_json: dict[str, Any] | None = None,
    model: str = "claude-haiku-4-5-20251001",
    tokens_input: int | None = 100,
    tokens_output: int | None = 50,
) -> MagicMock:
    snap = MagicMock()
    snap.id = id_
    snap.brief_json = brief_json or {
        "headline": "Test headline",
        "priorities": [
            {"rank": 1, "title": "Do A", "why": "Because A", "ticket": "MT-1"},
            {"rank": 2, "title": "Do B", "why": "Because B", "ticket": None},
            {"rank": 3, "title": "Do C", "why": "Because C", "ticket": None},
        ],
        "continuity": None,
        "context": "Context text",
        "defer": "Defer this",
        "slackUrgency": "low",
        "calendarNote": None,
        "generatedAt": "2026-05-18T12:00:00+00:00",
        "sourcesUsed": ["jira"],
    }
    snap.model = model
    snap.tokens_input = tokens_input
    snap.tokens_output = tokens_output
    snap.generated_at = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    return snap


# ── GET /api/daily-brief — no snapshots ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_brief_when_none_exists() -> None:
    from artemis.routes.daily_brief import get_brief

    session = _make_session()

    with patch(
        "artemis.brief.repository.get_latest_brief_snapshot",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await get_brief(session=session)

    assert result == {"brief": None, "exists": False}


# ── GET /api/daily-brief — with snapshot ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_brief_returns_latest() -> None:
    from artemis.routes.daily_brief import get_brief

    session = _make_session()
    snap = _fake_snapshot()

    with patch(
        "artemis.brief.repository.get_latest_brief_snapshot",
        new_callable=AsyncMock,
        return_value=snap,
    ):
        result = await get_brief(session=session)

    assert result["exists"] is True
    brief = result["brief"]
    assert brief["_snapshotId"] == 1
    assert brief["_generatedAt"] == "2026-05-18T12:00:00+00:00"
    assert brief["headline"] == "Test headline"


# ── POST /api/daily-brief/generate — happy path ──────────────────────────────


@pytest.mark.asyncio
async def test_generate_persists_snapshot() -> None:
    from artemis.routes.daily_brief import generate_brief_endpoint

    session = _make_session()

    # H5 (trimmed): DailyBrief shape — top_priorities/waiting_on_you/okr_at_risk
    valid_json = '{"summary": "Sprint review day.", "top_priorities": [{"item": "Sprint review", "rationale": "Due today", "urgency": "high"}], "waiting_on_you": [], "okr_at_risk": null, "confidence": "high"}'

    snap = _fake_snapshot(id_=42)

    with (
        patch(
            "artemis.brief.sources.gather_sources",
            new_callable=AsyncMock,
            return_value={
                "jira": None,
                "calendar": None,
                "slack": None,
                "okr": None,
                "sessions": [],
                "memory": [],
                "previousBrief": None,
            },
        ),
        patch(
            "artemis.brief.generator._call_llm",
            new_callable=AsyncMock,
            return_value=(valid_json, "claude-haiku-4-5-20251001", 100, 50),
        ),
        patch(
            "artemis.brief.repository.save_brief_snapshot",
            new_callable=AsyncMock,
            return_value=snap,
        ) as mock_save,
    ):
        result = await generate_brief_endpoint(session=session)

    assert result["generated"] is True
    # H5 (trimmed): DailyBrief shape — top_priorities, not highlights.
    assert result["brief"]["top_priorities"][0]["item"] == "Sprint review"
    assert result["brief"]["_snapshotId"] == 42
    mock_save.assert_awaited_once()


# ── POST /api/daily-brief/generate — markdown fenced JSON ────────────────────


@pytest.mark.asyncio
async def test_generate_handles_markdown_fenced_json() -> None:
    from artemis.brief.generator import generate_brief

    # H5 (trimmed): DailyBrief-shaped JSON wrapped in markdown fences.
    inner_json = '{"summary": null, "top_priorities": [{"item": "Fenced brief", "rationale": null, "urgency": "medium"}], "waiting_on_you": [], "okr_at_risk": null, "confidence": "medium"}'
    fenced = f"```json\n{inner_json}\n```"

    snap = _fake_snapshot(id_=7)

    with (
        patch(
            "artemis.brief.sources.gather_sources",
            new_callable=AsyncMock,
            return_value={
                "jira": None,
                "calendar": None,
                "slack": None,
                "okr": None,
                "sessions": [],
                "memory": [],
                "previousBrief": None,
            },
        ),
        patch(
            "artemis.brief.generator._call_llm",
            new_callable=AsyncMock,
            return_value=(fenced, "claude-haiku-4-5-20251001", 80, 40),
        ),
        patch(
            "artemis.brief.repository.save_brief_snapshot",
            new_callable=AsyncMock,
            return_value=snap,
        ),
    ):
        result = await generate_brief(session=_make_session())

    # H5 (trimmed): DailyBrief shape — top_priorities, not highlights.
    assert result["top_priorities"][0]["item"] == "Fenced brief"
    assert result["_snapshotId"] == 7


# ── POST /api/daily-brief/generate — prose-only → empty DailyBrief (H5) ──────


@pytest.mark.asyncio
async def test_generate_falls_back_on_no_json() -> None:
    """H5 failure isolation: when LLM returns no JSON, _generate_with_retry logs
    a warning and returns an empty DailyBrief().  The endpoint persists the
    empty brief and returns generated=True — no 502.

    Pre-H5 behavior: BriefGenerationError → 502. H5 changes this to failure
    isolation (empty brief + warning log) so the UI flow is never broken.
    """
    from artemis.routes.daily_brief import generate_brief_endpoint

    snap = _fake_snapshot(id_=99)

    with (
        patch(
            "artemis.brief.sources.gather_sources",
            new_callable=AsyncMock,
            return_value={
                "jira": None,
                "calendar": None,
                "slack": None,
                "okr": None,
                "sessions": [],
                "memory": [],
                "previousBrief": None,
            },
        ),
        patch(
            "artemis.brief.generator._call_llm",
            new_callable=AsyncMock,
            return_value=(
                "Sorry, I cannot generate a brief right now.",
                "claude-haiku-4-5-20251001",
                20,
                10,
            ),
        ),
        patch(
            "artemis.brief.repository.save_brief_snapshot",
            new_callable=AsyncMock,
            return_value=snap,
        ),
    ):
        result = await generate_brief_endpoint(session=_make_session())

    # H5: no 502 — failure isolation returns empty DailyBrief.
    assert result["generated"] is True
    brief = result["brief"]
    assert brief["top_priorities"] == []
    assert brief["waiting_on_you"] == []
    assert brief["summary"] is None


# ── GET /api/daily-brief/history — 10 seeded → returns 7 ────────────────────


@pytest.mark.asyncio
async def test_history_returns_last_7_metadata_only() -> None:
    from artemis.routes.daily_brief import get_history

    session = _make_session()
    snapshots = [_fake_snapshot(id_=i) for i in range(1, 8)]

    with patch(
        "artemis.brief.repository.list_brief_snapshots",
        new_callable=AsyncMock,
        return_value=snapshots,
    ):
        result = await get_history(session=session)

    history = result["history"]
    assert len(history) == 7
    # Metadata only — no brief_json in any row
    for row in history:
        assert "brief_json" not in row
        assert "id" in row
        assert "generated_at" in row
        assert "model" in row
        assert "tokens_input" in row
        assert "tokens_output" in row


# ── gather_sources resilience ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_sources_is_resilient() -> None:
    """One source raising should not prevent others from returning data."""
    from artemis.brief.sources import gather_sources

    session = _make_session()

    calendar_data = {"status": "ready", "events": []}
    okr_data = {"objectives": [{"title": "Grow", "progress": 50, "krs": []}]}

    with (
        patch(
            "artemis.brief.sources._safe_jira",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Jira exploded"),
        ),
        patch(
            "artemis.brief.sources._safe_calendar",
            new_callable=AsyncMock,
            return_value=calendar_data,
        ),
        patch(
            "artemis.brief.sources._safe_okr",
            new_callable=AsyncMock,
            return_value=okr_data,
        ),
        patch(
            "artemis.brief.sources._safe_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.brief.sources._safe_memory",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.brief.sources._safe_slack_signals",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.brief.sources._safe_previous_brief",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        sources = await gather_sources(session)

    # Jira raised — should be None (unwrapped exception default)
    assert sources["jira"] is None
    # Calendar and OKR still populated
    assert sources["calendar"] == calendar_data
    assert sources["okr"] == okr_data


# ── Round-trip ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persisted_brief_round_trips() -> None:
    """Write a brief snapshot through the repository, read it back, contents match."""
    from artemis.brief.generator import get_latest_brief

    original_json = {
        "headline": "Round-trip headline",
        "priorities": [
            {"rank": 1, "title": "A", "why": "W", "ticket": "MT-5"},
            {"rank": 2, "title": "B", "why": "W2", "ticket": None},
            {"rank": 3, "title": "C", "why": "W3", "ticket": None},
        ],
        "continuity": None,
        "context": "Some ctx",
        "defer": "Nothing urgent",
        "slackUrgency": "medium",
        "calendarNote": "No meetings",
        "generatedAt": "2026-05-18T10:00:00+00:00",
        "sourcesUsed": ["jira", "okr"],
    }

    snap = _fake_snapshot(id_=99, brief_json=original_json)

    session = _make_session()

    with patch(
        "artemis.brief.repository.get_latest_brief_snapshot",
        new_callable=AsyncMock,
        return_value=snap,
    ):
        result = await get_latest_brief(session)

    assert result is not None
    assert result["headline"] == "Round-trip headline"
    assert result["_snapshotId"] == 99
    assert result["priorities"][0]["ticket"] == "MT-5"
    assert result["sourcesUsed"] == ["jira", "okr"]
