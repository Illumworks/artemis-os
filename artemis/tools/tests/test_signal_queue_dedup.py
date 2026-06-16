"""CC9 — Source-URL-based dedup fallback for null-district signals.

Tests that signal_queue.write deduplicates on source_url when district_id
is null (e.g. federal_funding grants.gov signals), while preserving the
district-based dedup path for non-null districts and leaving the null-source_url
path unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.models import SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.signal_queue import _factory

_SCOUT_AGENT_ID = "marketing.scout.federal_funding"
_OTHER_AGENT_ID = "marketing.scout.regional_news"


def _ctx(session: AsyncSession, agent_id: str = _SCOUT_AGENT_ID) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-cc9-test",
        pipeline_run_id=None,
    )


def _federal_payload(**overrides: Any) -> dict[str, Any]:
    """Minimal valid payload for a federal-funding signal (null district)."""
    spec = parse_spec()
    scout_slug = _SCOUT_AGENT_ID.rsplit(".", 1)[-1]
    codes = reason_codes_for_scout(spec, scout_slug)
    first_code = codes[0].code if codes else "FEDERAL_FUNDING_OPPORTUNITY"
    base: dict[str, Any] = {
        "sourceType": "state_doe",
        "headline": "Comprehensive Centers — Literacy for Students with Disabilities",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Published on grants.gov",
        "sourceUrl": "https://www.grants.gov/web/grants/view-opportunity.html?oppId=12345",
        # No districtId — federal signals have no district
    }
    base.update(overrides)
    return base


def _regional_payload(**overrides: Any) -> dict[str, Any]:
    """Minimal valid payload for a regional-news signal with district."""
    spec = parse_spec()
    scout_slug = _OTHER_AGENT_ID.rsplit(".", 1)[-1]
    codes = reason_codes_for_scout(spec, scout_slug)
    first_code = codes[0].code if codes else "VENDOR_DISSATISFACTION"
    base: dict[str, Any] = {
        "sourceType": "news_article",
        "headline": "District signs new contract",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Announced publicly.",
        "sourceUrl": "https://example.com/district-article",
        "districtId": "TX-001",
    }
    base.update(overrides)
    return base


# ── Test 1: First write lands ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_federal_signal_written(db_session: AsyncSession) -> None:
    """First write with null district + source_url inserts a new row."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)
    result = await impl(_federal_payload())
    data = json.loads(result)
    assert data["status"] == "written", f"Expected 'written', got: {data}"
    row = await db_session.get(SignalQueue, data["signal_id"])
    assert row is not None
    assert row.district_id is None
    assert row.signal_status == "pending_qualification"


# ── Test 2: Duplicate write dedups within 30 days ─────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_source_url_null_district_deduped(db_session: AsyncSession) -> None:
    """Second write with same source_url within 30 days returns deduplicated."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    # First write
    first = json.loads(await impl(_federal_payload()))
    assert first["status"] == "written"
    original_id = first["signal_id"]

    # Second write — same URL, same scout
    second = json.loads(await impl(_federal_payload()))
    assert second["status"] == "deduplicated"
    assert second["signal_id"] == original_id
    assert second["duplicate_of"] == original_id

    # Confirm only one row exists
    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"


# ── Test 3: Cross-scout dedup (different agent_id, same URL) ─────────────────


@pytest.mark.asyncio
async def test_cross_scout_dedup_same_url(db_session: AsyncSession) -> None:
    """Different scout writing same source_url within 30 days is still deduped."""
    ctx_a = _ctx(db_session, agent_id=_SCOUT_AGENT_ID)
    _, impl_a = _factory(ctx_a)

    # First write by federal_funding scout
    first = json.loads(await impl_a(_federal_payload()))
    assert first["status"] == "written"
    original_id = first["signal_id"]

    # Now a different scout tries to write the same URL
    # regional_news scout also needs a valid payload for itself
    ctx_b = _ctx(db_session, agent_id=_OTHER_AGENT_ID)
    _, impl_b = _factory(ctx_b)
    # Use the same source_url but regional_news-valid payload shape
    regional_with_same_url = _regional_payload(
        sourceUrl=_federal_payload()["sourceUrl"],
        districtId=None,  # strip district to keep URL as dedup key
    )
    second = json.loads(await impl_b(regional_with_same_url))
    assert second["status"] == "deduplicated"
    assert second["signal_id"] == original_id

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 1


# ── Test 4: Stale (>30 days) allows re-emit ───────────────────────────────────


@pytest.mark.asyncio
async def test_stale_source_url_allows_new_row(db_session: AsyncSession) -> None:
    """After 30 days, the same source_url is allowed through again."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    # Write a row and then back-date its created_at to 31 days ago
    first = json.loads(await impl(_federal_payload()))
    assert first["status"] == "written"
    old_id = first["signal_id"]
    await db_session.execute(
        text(
            "UPDATE signal_queue SET created_at = now() - interval '31 days' WHERE id = :id"
        ).bindparams(id=old_id)
    )
    await db_session.flush()

    # Now write again — should land as a new row
    second = json.loads(await impl(_federal_payload()))
    assert second["status"] == "written", f"Expected 'written' after stale window, got: {second}"
    assert second["signal_id"] != old_id

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 2


# ── Test 5: District-based dedup path unaffected ─────────────────────────────


@pytest.mark.asyncio
async def test_district_signal_still_written_independently(db_session: AsyncSession) -> None:
    """Non-null district signals with different URLs write independently (no false dedup)."""
    ctx = _ctx(db_session, agent_id=_OTHER_AGENT_ID)
    _, impl = _factory(ctx)

    first = json.loads(await impl(_regional_payload(sourceUrl="https://example.com/a")))
    assert first["status"] == "written"

    second = json.loads(await impl(_regional_payload(sourceUrl="https://example.com/b")))
    assert second["status"] == "written"

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 2


# ── Test 6: Empty/null source_url falls through (no false-positive dedup) ─────


@pytest.mark.asyncio
async def test_null_source_url_no_false_dedup(db_session: AsyncSession) -> None:
    """Signals without source_url are not deduplicated against each other.

    Uses sourceType='manual' (the only type that does not require sourceUrl)
    with a scout that has at least one valid reason code (regional_news).
    """
    ctx = _ctx(db_session, agent_id=_OTHER_AGENT_ID)
    _, impl = _factory(ctx)

    # manual sourceType does not require sourceUrl
    manual_payload: dict[str, Any] = {
        "sourceType": "manual",
        "headline": "Heard from a contact that district is switching",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": ["VENDOR_DISSATISFACTION"],
        "evidence": "Conversation note.",
        # No sourceUrl — dedup path must not trigger
    }
    first = json.loads(await impl(manual_payload))
    assert first["status"] == "written"

    second = json.loads(await impl(manual_payload))
    assert second["status"] == "written", (
        f"Null-source_url signals should not dedup against each other, got: {second}"
    )

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 2


# ── Test 7: Archived row does not block re-emit ───────────────────────────────


@pytest.mark.asyncio
async def test_archived_row_does_not_block_new_write(db_session: AsyncSession) -> None:
    """An archived or hard-rejected row with same URL should not block a fresh write."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    # Write first row then archive it
    first = json.loads(await impl(_federal_payload()))
    assert first["status"] == "written"
    old_id = first["signal_id"]
    await db_session.execute(
        text("UPDATE signal_queue SET signal_status = 'archived' WHERE id = :id").bindparams(
            id=old_id
        )
    )
    await db_session.flush()

    # Write again — archived row should not trigger dedup
    second = json.loads(await impl(_federal_payload()))
    assert second["status"] == "written", f"Archived row should not block re-emit, got: {second}"
    assert second["signal_id"] != old_id

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 2


# ── Tests for activity-aware dedup (change_hash) ──────────────────────────────
# These three tests cover the legislative-scout fix: same URL + same/different
# change_hash, and the no-change_hash backward-compat path.


@pytest.mark.asyncio
async def test_same_url_same_change_hash_deduped(db_session: AsyncSession) -> None:
    """Same URL + same change_hash → deduplicated (bill has NOT advanced)."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    first = json.loads(await impl(_federal_payload(changeHash="abc123")))
    assert first["status"] == "written"
    original_id = first["signal_id"]

    second = json.loads(await impl(_federal_payload(changeHash="abc123")))
    assert second["status"] == "deduplicated", f"Expected deduplicated, got: {second}"
    assert second["signal_id"] == original_id

    rows_after = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows_after) == 1


@pytest.mark.asyncio
async def test_same_url_different_change_hash_written(db_session: AsyncSession) -> None:
    """Same URL + DIFFERENT change_hash → new write (bill has advanced)."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    first = json.loads(await impl(_federal_payload(changeHash="abc123")))
    assert first["status"] == "written"
    original_id = first["signal_id"]

    second = json.loads(await impl(_federal_payload(changeHash="xyz789")))
    assert second["status"] == "written", f"Expected written for new change_hash, got: {second}"
    assert second["signal_id"] != original_id

    rows_after = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows_after) == 2

    # Confirm new row stores the new change_hash in provenance
    new_row = await db_session.get(SignalQueue, second["signal_id"])
    assert new_row is not None
    assert new_row.provenance is not None
    assert new_row.provenance.get("change_hash") == "xyz789"


@pytest.mark.asyncio
async def test_same_url_no_change_hash_deduped(db_session: AsyncSession) -> None:
    """Same URL + NO change_hash on payload → deduplicated (legacy behavior unchanged)."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)

    first = json.loads(await impl(_federal_payload()))
    assert first["status"] == "written"
    original_id = first["signal_id"]

    # Second write with no changeHash — should still deduplicate as before
    second = json.loads(await impl(_federal_payload()))
    assert second["status"] == "deduplicated", (
        f"No-change_hash path must still dedup on URL alone, got: {second}"
    )
    assert second["signal_id"] == original_id

    rows_after = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows_after) == 1
