"""CC4 — qualifier + content tools tests.

Covers the unblock-the-chain behaviour: signal read/transition, the Gate-1 signal
brief, ruleset versioning (lossless), the districts stub, and registry completeness.

Transition verification uses a FRESH ``SessionLocal`` session after commit to prove
the write is durable (not just visible in the open session).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.marketing.models import CampaignStateTransition, Ruleset, SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.districts import _factory as districts_factory
from artemis.tools.registry import known_tool_names
from artemis.tools.ruleset_storage import (
    _get_active_factory,
    _write_new_factory,
)
from artemis.tools.signal_briefs import _write_factory as signal_briefs_write_factory
from artemis.tools.signal_queue_ops import (
    _find_by_dc_factory,
    _get_factory,
    _update_status_factory,
)

_QUALIFIER = "marketing.qualifier.cross_reference"
_BRIEF_COMPOSER = "marketing.qualifier.brief_composer"
_RULESET_MANAGER = "marketing.qualifier.ruleset_manager"
_SCOUT = "marketing.scout.regional_news"


def _ctx(session: AsyncSession, agent_id: str) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-test-cc4",
        pipeline_run_id=None,
    )


async def _seed_signal(
    session: AsyncSession,
    *,
    district_id: str | None = "TX-001",
    reason_codes: list[Any] | None = None,
    family: str = "obc",
) -> int:
    row = SignalQueue(
        source_type="news_article",
        headline="District seeks new literacy vendor",
        summary="Board approved an RFP for a K-3 reading screener.",
        campaign_family=family,
        urgency_tier="standard",
        discovered_by="regional_news",
        district_id=district_id,
        state="TX",
        reason_codes=reason_codes
        if reason_codes is not None
        else [{"code": "VENDOR_DISSATISFACTION"}],
        signal_status="pending_qualification",
    )
    session.add(row)
    await session.flush()
    return row.id


# ── #1 update_status transition + permission ────────────────────────────────────


@pytest.mark.asyncio
async def test_update_status_qualifier_transitions_signal(db_session: AsyncSession) -> None:
    """Qualifier transitions pending_qualification -> qualified; durable in a fresh session."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session, _QUALIFIER))
    result = await impl(
        {"signalId": signal_id, "newStatus": "qualified", "reason": "passes filters"}
    )
    data = json.loads(result)
    assert data["signal_status"] == "qualified"
    await db_session.commit()

    async with _db.SessionLocal() as fresh:
        row = await fresh.get(SignalQueue, signal_id)
        assert row is not None
        assert row.signal_status == "qualified"
        # Audit row written atomically by transition().
        transitions = (
            (
                await fresh.execute(
                    select(CampaignStateTransition).where(
                        CampaignStateTransition.entity_type == "signal",
                        CampaignStateTransition.entity_id == signal_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(transitions) == 1
        assert transitions[0].to_state == "qualified"
        assert transitions[0].actor == _QUALIFIER


@pytest.mark.asyncio
async def test_update_status_non_qualifier_denied(db_session: AsyncSession) -> None:
    """Non-qualifier agent → PERMISSION_DENIED, no transition."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session, _SCOUT))
    result = await impl({"signalId": signal_id, "newStatus": "qualified"})
    assert result.startswith("PERMISSION_DENIED")
    await db_session.commit()
    async with _db.SessionLocal() as fresh:
        row = await fresh.get(SignalQueue, signal_id)
        assert row is not None
        assert row.signal_status == "pending_qualification"


@pytest.mark.asyncio
async def test_update_status_illegal_transition(db_session: AsyncSession) -> None:
    """An illegal target status returns ILLEGAL_TRANSITION, not a raise."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session, _QUALIFIER))
    result = await impl({"signalId": signal_id, "newStatus": "approved"})
    assert result.startswith("ILLEGAL_TRANSITION")


# ── #2 get + find_by_district_and_code ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_seeded_row(db_session: AsyncSession) -> None:
    signal_id = await _seed_signal(db_session)
    _, impl = _get_factory(_ctx(db_session, _QUALIFIER))
    data = json.loads(await impl({"signalId": signal_id}))
    assert data["id"] == signal_id
    assert data["campaign_family"] == "obc"
    assert data["signal_status"] == "pending_qualification"


@pytest.mark.asyncio
async def test_get_not_found(db_session: AsyncSession) -> None:
    _, impl = _get_factory(_ctx(db_session, _QUALIFIER))
    assert (await impl({"signalId": 999999})).startswith("NOT_FOUND")


@pytest.mark.asyncio
async def test_find_by_district_and_code_filters(db_session: AsyncSession) -> None:
    match_id = await _seed_signal(
        db_session, district_id="TX-001", reason_codes=[{"code": "BUDGET_CYCLE"}]
    )
    # Different district — must not match.
    await _seed_signal(db_session, district_id="CA-009", reason_codes=[{"code": "BUDGET_CYCLE"}])
    # Same district, different code — must not match.
    await _seed_signal(db_session, district_id="TX-001", reason_codes=[{"code": "OTHER_CODE"}])

    _, impl = _find_by_dc_factory(_ctx(db_session, _BRIEF_COMPOSER))
    data = json.loads(await impl({"districtId": "TX-001", "reasonCode": "BUDGET_CYCLE"}))
    ids = [s["id"] for s in data["signals"]]
    assert ids == [match_id]


# ── #3 signal_briefs.write ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_briefs_write_populates_gate_fields(db_session: AsyncSession) -> None:
    """brief_composer writes a brief onto the signal with approval-card preview fields."""
    signal_id = await _seed_signal(db_session, reason_codes=[{"code": "VENDOR_DISSATISFACTION"}])
    _, impl = signal_briefs_write_factory(_ctx(db_session, _BRIEF_COMPOSER))
    result = await impl(
        {
            "signalId": signal_id,
            "preview": "TX district wants a new screener",
            "body": "Full brief body with evidence and recommendation.",
            "evidenceQuote": "Board approved an RFP.",
            "recommendedFamilies": ["obc"],
        }
    )
    assert json.loads(result)["status"] == "written"
    await db_session.commit()

    async with _db.SessionLocal() as fresh:
        row = await fresh.get(SignalQueue, signal_id)
        assert row is not None
        brief = (row.qualification_json or {})["brief"]
        assert brief["preview"] == "TX district wants a new screener"
        assert brief["evidence_quote"] == "Board approved an RFP."
        assert brief["recommended_families"] == ["obc"]
        assert brief["reason_codes"] == [{"code": "VENDOR_DISSATISFACTION"}]
        assert brief["districts"] == ["TX-001"]


@pytest.mark.asyncio
async def test_signal_briefs_write_non_composer_denied(db_session: AsyncSession) -> None:
    signal_id = await _seed_signal(db_session)
    _, impl = signal_briefs_write_factory(_ctx(db_session, _QUALIFIER))
    result = await impl({"signalId": signal_id, "preview": "x", "body": "y"})
    assert result.startswith("PERMISSION_DENIED")


# ── #4 ruleset_storage get_active + write_new_version (lossless) ─────────────────


@pytest.mark.asyncio
async def test_ruleset_get_active_and_write_new_version_is_lossless(
    db_session: AsyncSession,
) -> None:
    # Seed an active v1 directly.
    v1 = Ruleset(family="obc", version_tag="v1", state="active")
    db_session.add(v1)
    await db_session.flush()

    _, get_active = _get_active_factory(_ctx(db_session, _RULESET_MANAGER))
    active = json.loads(await get_active({"family": "obc"}))
    assert active["version_tag"] == "v1"
    assert active["state"] == "active"

    # Append a new draft v2 — must NOT touch v1.
    _, write_new = _write_new_factory(_ctx(db_session, _RULESET_MANAGER))
    new_result = json.loads(
        await write_new({"family": "obc", "versionTag": "v2", "hardFilters": [{"k": "v"}]})
    )
    assert new_result["state"] == "draft"
    await db_session.commit()

    async with _db.SessionLocal() as fresh:
        rows = (await fresh.execute(select(Ruleset).where(Ruleset.family == "obc"))).scalars().all()
        by_tag = {r.version_tag: r for r in rows}
        # v1 still present and still active — lossless.
        assert by_tag["v1"].state == "active"
        assert by_tag["v2"].state == "draft"


@pytest.mark.asyncio
async def test_ruleset_write_new_version_non_manager_denied(db_session: AsyncSession) -> None:
    _, impl = _write_new_factory(_ctx(db_session, _BRIEF_COMPOSER))
    result = await impl({"family": "obc", "versionTag": "v9"})
    assert result.startswith("PERMISSION_DENIED")


# ── #5 districts.get stub ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_districts_get_stub_shape(db_session: AsyncSession) -> None:
    _, impl = districts_factory(_ctx(db_session, _BRIEF_COMPOSER))
    data = json.loads(await impl({"districtId": "TX-001"}))
    assert data == {"district_id": "TX-001", "known": False}


# ── #6 registry completeness ────────────────────────────────────────────────────


def test_registry_includes_all_cc4_tools() -> None:
    import artemis.tools  # noqa: F401 — ensure all submodules imported / registered

    names = set(known_tool_names())
    expected = {
        "signal_queue.get",
        "signal_queue.update_status",
        "signal_queue.find_by_district_and_code",
        "signal_queue.find_recent_qualification_results",
        "signal_briefs.write",
        "signal_briefs.get_approval_history",
        "campaign_brief.read",
        "ruleset_storage.get_active",
        "ruleset_storage.get_version",
        "ruleset_storage.write_new_version",
        "ruleset_storage.activate",
        "ruleset_storage.get_hit_rate",
        "districts.get",
    }
    missing = expected - names
    assert not missing, f"missing tools: {sorted(missing)}"
