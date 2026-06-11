"""DB-backed test for the find_by_keyword marketing tool (callie-w3-keyword-search).

Seeds a signal whose headline contains a bill number (HB27) and a campaign
candidate whose name contains it, then asserts:
  - the tool returns both for that keyword
  - the tool returns nothing for an unrelated keyword
  - the tool errors gracefully on an empty query
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, SignalQueue
from artemis.marketing.repository import (
    find_signals_and_candidates_by_keyword,
)

# ── helpers ───────────────────────────────────────────────────────────────────


async def _seed_signal(session: AsyncSession, headline: str) -> SignalQueue:
    sig = SignalQueue(
        headline=headline,
        campaign_family="obc",
        source_type="manual",
        summary="",
        urgency_tier="standard",
        discovered_by="manual",
        reason_codes=[],
        signal_status="pending_qualification",
    )
    session.add(sig)
    await session.flush()
    await session.refresh(sig)
    return sig


async def _seed_candidate(session: AsyncSession, name: str) -> CampaignCandidate:
    cand = CampaignCandidate(
        campaign_family="obc",
        name=name,
        stage="human_gate_1",
        decision_state="in_inbox",
        workspace_state="pending_content",
    )
    session.add(cand)
    await session.flush()
    await session.refresh(cand)
    return cand


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_by_keyword_returns_signal_and_campaign(db_session: AsyncSession) -> None:
    """Seeded HB27 signal + campaign both surface for keyword 'HB27'."""
    async with db_session.begin_nested():
        sig = await _seed_signal(
            db_session,
            "Texas Personal Financial Literacy Course Requirement (HB27)",
        )
        cand = await _seed_candidate(
            db_session,
            "Texas HB27 Personal Financial Literacy Mandate Outreach",
        )
        # Unrelated decoy rows that must NOT appear
        await _seed_signal(db_session, "Unrelated district budget update")
        await _seed_candidate(db_session, "Unrelated outreach campaign")

    result = await find_signals_and_candidates_by_keyword(db_session, "HB27")

    signal_ids = [s[0] for s in result.signals]
    assert sig.id in signal_ids, "Expected seeded HB27 signal in results"
    assert all("HB27" in s[3].upper() for s in result.signals), (
        "All returned signals should contain HB27 in headline"
    )

    candidate_ids = [c[0] for c in result.candidates]
    assert cand.id in candidate_ids, "Expected seeded HB27 campaign in results"
    assert all("HB27" in c[1].upper() for c in result.candidates), (
        "All returned campaigns should contain HB27 in name"
    )


@pytest.mark.asyncio
async def test_find_by_keyword_case_insensitive(db_session: AsyncSession) -> None:
    """Lowercase 'hb27' matches uppercase headline and name."""
    async with db_session.begin_nested():
        sig = await _seed_signal(
            db_session, "Texas Personal Financial Literacy Course Requirement (HB27)"
        )
        cand = await _seed_candidate(
            db_session, "Texas HB27 Personal Financial Literacy Mandate Outreach"
        )

    result = await find_signals_and_candidates_by_keyword(db_session, "hb27")

    assert sig.id in [s[0] for s in result.signals]
    assert cand.id in [c[0] for c in result.candidates]


@pytest.mark.asyncio
async def test_find_by_keyword_no_match_returns_empty(db_session: AsyncSession) -> None:
    """Keyword with no matches returns empty signal + candidate lists."""
    async with db_session.begin_nested():
        await _seed_signal(
            db_session, "Texas Personal Financial Literacy Course Requirement (HB27)"
        )
        await _seed_candidate(db_session, "Texas HB27 Mandate Outreach")

    result = await find_signals_and_candidates_by_keyword(db_session, "ZZZNOTAREALTERM9999")

    assert result.signals == []
    assert result.candidates == []


@pytest.mark.asyncio
async def test_find_by_keyword_empty_query_raises(db_session: AsyncSession) -> None:
    """Empty query string raises ValueError rather than returning everything."""
    with pytest.raises(ValueError, match="non-empty"):
        await find_signals_and_candidates_by_keyword(db_session, "")


@pytest.mark.asyncio
async def test_find_by_keyword_result_fields(db_session: AsyncSession) -> None:
    """Result tuples contain the documented fields in the right positions."""
    async with db_session.begin_nested():
        sig = await _seed_signal(
            db_session, "Texas Personal Financial Literacy Course Requirement (HB27)"
        )
        cand = await _seed_candidate(
            db_session, "Texas HB27 Personal Financial Literacy Mandate Outreach"
        )

    result = await find_signals_and_candidates_by_keyword(db_session, "HB27")

    # Signal tuple: (id, urgency_tier, signal_status, headline)
    sig_row = next(r for r in result.signals if r[0] == sig.id)
    assert sig_row[0] == sig.id
    assert isinstance(sig_row[1], str)  # urgency_tier
    assert isinstance(sig_row[2], str)  # signal_status
    assert "HB27" in sig_row[3]  # headline

    # Candidate tuple: (id, name, decision_state)
    cand_row = next(r for r in result.candidates if r[0] == cand.id)
    assert cand_row[0] == cand.id
    assert "HB27" in cand_row[1]  # name
    assert isinstance(cand_row[2], str)  # decision_state


@pytest.mark.asyncio
async def test_find_by_keyword_tool_wrapper_happy_path(db_session: AsyncSession) -> None:
    """End-to-end: the _find_by_keyword tool fn returns formatted text for a hit."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from artemis.floating_artemis.tools.marketing import _find_by_keyword
    from artemis.marketing.repository import KeywordSearchResult

    mock_result = KeywordSearchResult(
        signals=[
            (
                624,
                "standard",
                "pending_qualification",
                "Texas Personal Financial Literacy Course Requirement (HB27)",
            )
        ],
        candidates=[(18, "Texas HB27 Personal Financial Literacy Mandate Outreach", "in_inbox")],
    )

    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.repository.find_signals_and_candidates_by_keyword",
            new=AsyncMock(return_value=mock_result),
        ),
    ):
        output = await _find_by_keyword({"query": "HB27"})

    assert "signals:" in output
    assert "#624" in output
    assert "HB27" in output
    assert "campaigns:" in output
    assert "#18" in output


@pytest.mark.asyncio
async def test_find_by_keyword_tool_wrapper_no_results(db_session: AsyncSession) -> None:
    """Tool wrapper returns a friendly 'no matches' message for empty result."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from artemis.floating_artemis.tools.marketing import _find_by_keyword
    from artemis.marketing.repository import KeywordSearchResult

    mock_result = KeywordSearchResult(signals=[], candidates=[])

    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.repository.find_signals_and_candidates_by_keyword",
            new=AsyncMock(return_value=mock_result),
        ),
    ):
        output = await _find_by_keyword({"query": "NOMATCH"})

    assert "No signals or campaigns matched" in output


@pytest.mark.asyncio
async def test_find_by_keyword_tool_requires_query() -> None:
    """Tool wrapper rejects empty query without hitting the DB."""
    from artemis.floating_artemis.tools.marketing import _find_by_keyword

    output = await _find_by_keyword({"query": ""})
    assert "Error" in output or "required" in output.lower()


def test_find_by_keyword_registered_as_layer_1() -> None:
    """find_by_keyword must be registered at layer 1 (read-only, no confirmation)."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.floating_artemis.tools.marketing import register_marketing_tools

    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    entry = reg.get("find_by_keyword")
    assert entry is not None, "find_by_keyword not registered"
    assert entry.layer == 1, "find_by_keyword must be layer 1"
    assert "[surface:marketing-os]" in entry.tool.description
    assert "[layer:1]" in entry.tool.description
