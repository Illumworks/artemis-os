"""Tests for the per-campaign cost rollup endpoint.

Covers:
  - 404 for unknown candidate
  - Direct rows: byStage sums by feature_tag
  - Backward compat: NULL campaign_candidate_id rows excluded
  - Stage mapping: marketing_brief → brief, writing_studio_compose → content
  - Unknown feature_tag (still tagged to candidate) folds into content
  - Scout allocation math: per-signal share × seeding count
  - districtsBasis: target_audience when no sends, recipients when sends exist
  - costPerDistrict math + null when 0 districts
  - Isolation: two campaigns don't bleed into each other
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.events import record_cost_event
from artemis.marketing.models import (
    CampaignCandidate,
    CampaignCandidateSignal,
    District,
    SignalQueue,
)


async def _seed_districts(session: AsyncSession, ids: list[int]) -> None:
    """Insert District rows so target-audience resolution finds them."""
    for d_id in ids:
        session.add(District(id=d_id, name=f"District {d_id}", supported=True))
    await session.flush()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _seed_candidate(
    session: AsyncSession,
    *,
    family: str = "test-family",
    target_scope: dict[str, Any] | None = None,
) -> CampaignCandidate:
    candidate = CampaignCandidate(
        campaign_family=family,
        stage="initiation_proposed",
        decision_state="created",
        workspace_state="pending_content",
        target_scope_json=target_scope or {"mode": "all_districts"},
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def _seed_signal(session: AsyncSession, *, family: str = "test-family") -> SignalQueue:
    signal = SignalQueue(
        source_type="manual",
        headline="test signal",
        summary="",
        campaign_family=family,
    )
    session.add(signal)
    await session.flush()
    return signal


async def _attach_signal(
    session: AsyncSession, candidate: CampaignCandidate, signal: SignalQueue
) -> None:
    session.add(
        CampaignCandidateSignal(
            candidate_id=candidate.id, signal_id=signal.id, is_primary=True
        )
    )
    await session.flush()


async def _add_cost(
    session: AsyncSession,
    *,
    feature_tag: str,
    cost_input_tokens: int = 10_000,
    cost_output_tokens: int = 5_000,
    campaign_candidate_id: int | None = None,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    created_at: datetime | None = None,
) -> None:
    event = await record_cost_event(
        session,
        provider=provider,
        model=model,
        provider_path="api",
        feature_tag=feature_tag,
        input_tokens=cost_input_tokens,
        output_tokens=cost_output_tokens,
        campaign_candidate_id=campaign_candidate_id,
    )
    # Override created_at if the test needs a specific window placement
    if created_at is not None:
        event.created_at = created_at
        await session.flush()


@pytest.fixture(autouse=True)
async def _truncate_cost_and_campaign_tables(db_session: AsyncSession):
    """The marketing conftest's TRUNCATE list doesn't include cost_events or
    campaign_candidates. Wipe them per-test to keep these tests isolated."""
    await db_session.execute(
        text(
            "TRUNCATE cost_events, campaign_candidates, "
            "campaign_candidate_signals, campaign_sends RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()
    yield


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_404_for_unknown_candidate(client: AsyncClient) -> None:
    res = await client.get("/api/marketing/campaigns/99999999/cost")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_byStage_sums_direct_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    candidate = await _seed_candidate(db_session, target_scope={"mode": "all_districts"})
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=1_000_000,
        cost_output_tokens=200_000,
        campaign_candidate_id=candidate.id,
    )
    await _add_cost(
        db_session,
        feature_tag="writing_studio_compose",
        cost_input_tokens=500_000,
        cost_output_tokens=100_000,
        campaign_candidate_id=candidate.id,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    assert res.status_code == 200
    body = res.json()

    # brief: 1M*$3 + 200K*$15 = $3 + $3 = $6
    assert body["byStage"]["brief"] == pytest.approx(6.0, abs=0.0001)
    # content: 500K*$3 + 100K*$15 = $1.5 + $1.5 = $3
    assert body["byStage"]["content"] == pytest.approx(3.0, abs=0.0001)
    assert body["byStage"]["sends"] == 0.0
    assert body["totalUsd"] == pytest.approx(9.0, abs=0.0001)


@pytest.mark.asyncio
async def test_null_campaign_id_rows_excluded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Historical rows with campaign_candidate_id=NULL must not bleed in."""
    candidate = await _seed_candidate(db_session)
    # Tagged row
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=1_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=candidate.id,
    )
    # Untagged historical row — same feature_tag, but no campaign attribution
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=10_000_000,  # would dwarf the tagged one
        cost_output_tokens=0,
        campaign_candidate_id=None,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    assert body["byStage"]["brief"] == pytest.approx(3.0, abs=0.0001)  # 1M*$3
    assert body["totalUsd"] == pytest.approx(3.0, abs=0.0001)


@pytest.mark.asyncio
async def test_unknown_feature_tag_folds_into_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A row with campaign_candidate_id set but unknown tag is genuine campaign
    cost — fold into content rather than dropping it from totalUsd."""
    candidate = await _seed_candidate(db_session)
    await _add_cost(
        db_session,
        feature_tag="some_future_feature",  # not in BRIEF/CONTENT/SENDS taxonomy
        cost_input_tokens=1_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=candidate.id,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    assert body["byStage"]["content"] == pytest.approx(3.0, abs=0.0001)
    assert body["totalUsd"] == pytest.approx(3.0, abs=0.0001)


@pytest.mark.asyncio
async def test_scout_allocation_per_signal_share(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Allocation formula: (total_scout_cost / total_signals) * seeding_signals.

    Two scout cost_events totaling $10. Five signals in window. This candidate
    has 2 of them as seeding signals. Expected discovery: $10/5 * 2 = $4.
    """
    candidate = await _seed_candidate(db_session)
    seed_signals = [await _seed_signal(db_session) for _ in range(2)]
    # 3 more signals in window that are NOT seeding signals for this candidate
    other_signals = [await _seed_signal(db_session) for _ in range(3)]
    for sig in seed_signals:
        await _attach_signal(db_session, candidate, sig)
    # 2 scout cost_events summing to $10 (5M output tokens at sonnet $15/M = $75 — too much; use $5 + $5 instead)
    # Sonnet output rate = $15/M. To get $5, need 333_333 output tokens (5/15 * 1M)
    await _add_cost(
        db_session,
        feature_tag="marketing_scout",
        cost_input_tokens=0,
        cost_output_tokens=333_333,  # $5.00
    )
    await _add_cost(
        db_session,
        feature_tag="marketing_scout",
        cost_input_tokens=0,
        cost_output_tokens=333_333,  # another $5.00 → total $10.00
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    # 5 signals total in window, 2 seeding → per_signal = $10/5 = $2, allocated = $2 * 2 = $4
    assert body["byStage"]["scouting"] == pytest.approx(4.0, abs=0.001)
    # Quiet unused-variable warning for the non-seeding signals (we only seeded
    # them so the window total = 5).
    assert len(other_signals) == 3


@pytest.mark.asyncio
async def test_scout_allocation_returns_null_when_no_seeding_signals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    candidate = await _seed_candidate(db_session)
    # Scout cost exists but candidate has zero seeding signals → scouting=null
    await _add_cost(
        db_session,
        feature_tag="marketing_scout",
        cost_input_tokens=0,
        cost_output_tokens=333_333,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    assert body["byStage"]["scouting"] is None


@pytest.mark.asyncio
async def test_two_campaigns_isolated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A row tagged to candidate A must not appear in candidate B's rollup."""
    a = await _seed_candidate(db_session)
    b = await _seed_candidate(db_session)
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=1_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=a.id,
    )
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=2_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=b.id,
    )
    await db_session.commit()

    res_a = (await client.get(f"/api/marketing/campaigns/{a.id}/cost")).json()
    res_b = (await client.get(f"/api/marketing/campaigns/{b.id}/cost")).json()
    assert res_a["byStage"]["brief"] == pytest.approx(3.0, abs=0.0001)  # 1M*$3
    assert res_b["byStage"]["brief"] == pytest.approx(6.0, abs=0.0001)  # 2M*$3


@pytest.mark.asyncio
async def test_districts_basis_target_audience_when_no_sends(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No campaign_sends row → fall back to target_audience district count."""
    await _seed_districts(db_session, [1, 2, 3])
    candidate = await _seed_candidate(
        db_session, target_scope={"mode": "named_districts", "district_ids": [1, 2, 3]}
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    assert body["districtsBasis"] == "target_audience"
    # The target_scope has 3 named districts; resolve_district_ids returns those
    # without supported-filter for named_districts mode, so all 3 land.
    assert body["districtsContacted"] == 3


@pytest.mark.asyncio
async def test_cost_per_district_math(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """costPerDistrict = totalUsd / districtsContacted, null when 0 districts."""
    await _seed_districts(db_session, [10, 20])
    candidate = await _seed_candidate(
        db_session, target_scope={"mode": "named_districts", "district_ids": [10, 20]}
    )
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=1_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=candidate.id,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    # totalUsd = $3, districts = 2 → $1.50/district
    assert body["totalUsd"] == pytest.approx(3.0, abs=0.0001)
    assert body["districtsContacted"] == 2
    assert body["costPerDistrict"] == pytest.approx(1.5, abs=0.0001)


@pytest.mark.asyncio
async def test_cost_per_district_null_when_zero_districts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No districts → costPerDistrict is null even if totalUsd > 0."""
    candidate = await _seed_candidate(
        db_session, target_scope={"mode": "named_districts", "district_ids": []}
    )
    await _add_cost(
        db_session,
        feature_tag="marketing_brief",
        cost_input_tokens=1_000_000,
        cost_output_tokens=0,
        campaign_candidate_id=candidate.id,
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    assert body["totalUsd"] == pytest.approx(3.0, abs=0.0001)
    assert body["districtsContacted"] == 0
    assert body["costPerDistrict"] is None


@pytest.mark.asyncio
async def test_scout_cost_outside_window_excluded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A scout cost row older than 30 days must not contribute to allocation."""
    candidate = await _seed_candidate(db_session)
    sig = await _seed_signal(db_session)
    await _attach_signal(db_session, candidate, sig)
    # Old scout cost (40 days ago) — outside window
    await _add_cost(
        db_session,
        feature_tag="marketing_scout",
        cost_input_tokens=0,
        cost_output_tokens=333_333,
        created_at=datetime.now(UTC) - timedelta(days=40),
    )
    await db_session.commit()

    res = await client.get(f"/api/marketing/campaigns/{candidate.id}/cost")
    body = res.json()
    # Only 1 in-window signal, but no in-window scout cost → scouting = None
    assert body["byStage"]["scouting"] is None
