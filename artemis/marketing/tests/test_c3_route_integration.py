"""Phase C3 route integration tests — ≥5 tests.

Tests:
  1. intake auto-qualifies on POST when active rulesets exist
  2. manual qualify endpoint returns real scorer output (not stub)
  3. approve locks ruleset version from qualification_json
  4. brief assembly produces non-stub output
  5. behavioral parity with Node 2026-05-15 smoke path
  (additional edge cases beyond minimum)
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import (
    CampaignCandidate,
    Ruleset,
    SignalQueue,
    TerritoryConfig,
)
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_content_asset,
    create_signal,
    link_content_asset_to_candidate,
)
from artemis.marketing.seeds.reason_codes import seed_reason_codes

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _make_active_ruleset(
    db: AsyncSession, family: str = "obc", version: str = "v1"
) -> Ruleset:
    # Include both DISTRICT_VOTED_YES (used by _make_signal) and
    # DISTRICT_STRATEGIC_LITERACY (used by the smoke-path test's inline payload)
    # so all callers produce a fit-passing signal (score ≥ 0.5 min_fit).
    ruleset = Ruleset(
        family=family,
        version_tag=version,
        state="active",
        hard_filters=[],
        weighted_signals=[
            {"rule_id": "r1", "reason_code": "DISTRICT_VOTED_YES", "weight": 0.7},
            {"rule_id": "r2", "reason_code": "DISTRICT_STRATEGIC_LITERACY", "weight": 0.7},
        ],
        qualitative_rubrics=[],
    )
    db.add(ruleset)
    await db.flush()
    await db.refresh(ruleset)
    return ruleset


async def _make_territory(db: AsyncSession, family: str = "obc") -> TerritoryConfig:
    tc = TerritoryConfig(
        family=family,
        hot_states=["CA", "TX"],
        standard_states=["NY", "FL"],
        unlisted_multiplier=0.85,
    )
    db.add(tc)
    await db.flush()
    await db.refresh(tc)
    return tc


async def _make_signal(db: AsyncSession, **overrides: Any) -> SignalQueue:
    defaults = {
        "headline": "Board approved a resolution",
        "campaign_family": "obc",
        "source_type": "manual",
        "summary": "District voted yes",
        "discovered_by": "manual",
        "reason_codes": [{"code": "DISTRICT_VOTED_YES", "confidence": 1.0}],
        "state": "CA",
    }
    return await create_signal(db, **{**defaults, **overrides})


async def _make_candidate(db: AsyncSession, signal: SignalQueue) -> CampaignCandidate:
    return await create_campaign_candidate_from_signal(
        db,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: intake auto-qualifies when active rulesets exist
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_auto_qualifies_when_active_rulesets_exist(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_reason_codes(db_session)  # M1: registry must be populated for FK check
    await _make_active_ruleset(db_session, family="obc", version="v1")
    await _make_territory(db_session, family="obc")
    await db_session.commit()

    payload = {
        "sourceType": "manual",
        "headline": "District voted yes on measure",
        "campaignFamily": "obc",
        "stateCode": "CA",
        "reasonCodes": [{"code": "DISTRICT_STRATEGIC_LITERACY", "confidence": 1.0}],
    }
    resp = await client.post("/api/signal-queue/intake", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    # After intake auto-qualification, qualificationJson should be set
    qual = data["signal"]["qualificationJson"]
    assert qual is not None
    assert "qualifiedAt" in qual
    assert len(qual["scores"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: manual qualify endpoint returns real scorer output
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qualify_endpoint_returns_real_scorer_output(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_active_ruleset(db_session)
    await _make_territory(db_session)
    signal = await _make_signal(db_session)
    await db_session.commit()

    resp = await client.post(f"/api/signal-queue/{signal.id}/qualify")
    assert resp.status_code == 200
    data = resp.json()
    # Must NOT be the old stub {qualifiedAt, scores: []}
    assert "qualifiedAt" in data
    assert "scores" in data
    assert len(data["scores"]) > 0  # real scorer produced results
    assert "rulesetVersionsUsed" in data
    # Scores should have full fields
    score = data["scores"][0]
    assert "passedHardFilters" in score
    assert "adjustedScore" in score


@pytest.mark.asyncio
async def test_qualify_endpoint_returns_400_when_no_active_rulesets(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signal = await _make_signal(db_session)
    await db_session.commit()

    resp = await client.post(f"/api/signal-queue/{signal.id}/qualify")
    # Returns 400 (bad_request) when no active rulesets exist
    assert resp.status_code == 400
    assert resp.json()["code"] == "no_active_rulesets"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: approve locks ruleset version from qualification_json
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_locks_ruleset_version_from_qualification_json(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_active_ruleset(db_session, version="v99")
    signal = await _make_signal(db_session, signal_status="qualified")
    # Manually set qualification_json with a known version
    signal.qualification_json = {
        "qualifiedAt": "2026-05-16T00:00:00Z",
        "rulesetVersionsUsed": {"obc": "v99"},
        "scores": [],
        "recommendedFamilies": [],
    }
    await db_session.flush()
    await db_session.commit()

    resp = await client.post(f"/api/signal-queue/{signal.id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    # Ruleset version locked from qualification_json, not current active
    assert data["rulesetVersionAtQualification"] == "v99"


@pytest.mark.asyncio
async def test_approve_falls_back_to_active_version_when_no_qual_json(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_active_ruleset(db_session, version="v2")
    signal = await _make_signal(db_session, signal_status="qualified")
    # No qualification_json
    await db_session.commit()

    resp = await client.post(f"/api/signal-queue/{signal.id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    # Falls back to current active version
    assert data["rulesetVersionAtQualification"] == "v2"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: brief assembly produces non-stub output
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brief_assembly_produces_non_stub_output(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signal = await _make_signal(db_session)
    candidate = await _make_candidate(db_session, signal)
    await db_session.commit()

    resp = await client.post(f"/api/campaign-ops/candidates/{candidate.id}/brief/assemble")
    assert resp.status_code == 201
    data = resp.json()
    # Must NOT be the stub {stub: True}
    assert "stub" not in data
    assert "brief" in data
    content = data["brief"]["content"]
    # Real brief shape
    assert content["campaignId"] == candidate.id
    assert "assembledAt" in content
    assert "signal" in content
    assert content["source"] == "campaign_candidate"


@pytest.mark.asyncio
async def test_brief_assembly_includes_linked_assets(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signal = await _make_signal(db_session)
    candidate = await _make_candidate(db_session, signal)
    asset = await create_content_asset(
        db_session,
        asset_type="snippet",
        status="draft",
        summary="A useful content snippet",
    )
    await link_content_asset_to_candidate(
        db_session,
        candidate_id=candidate.id,
        asset_id=asset.id,
        link_role="supporting",
    )
    await db_session.commit()

    resp = await client.post(f"/api/campaign-ops/candidates/{candidate.id}/brief/assemble")
    assert resp.status_code == 201
    content = resp.json()["brief"]["content"]
    assert len(content["linkedAssets"]) == 1
    assert content["linkedAssets"][0]["assetId"] == asset.id


@pytest.mark.asyncio
async def test_brief_assembly_not_found_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post("/api/campaign-ops/candidates/99999/brief/assemble")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Node 2026-05-15 smoke path parity
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_smoke_path_parity(client: AsyncClient, db_session: AsyncSession) -> None:
    """Mirror the Node app's 2026-05-15 smoke path:
    1. Create a signal via intake
    2. Qualify it (scorer runs, score stored)
    3. Approve signal → candidate created with locked version
    4. Assemble brief → non-stub output with signal evidence
    """
    # Setup: reason-code registry (M1 FK enforcement), active ruleset + territory
    await seed_reason_codes(db_session)  # M1: registry must be populated for FK check
    await _make_active_ruleset(db_session, family="obc", version="v1")
    await _make_territory(db_session, family="obc")
    await db_session.commit()

    # Step 1: Intake
    intake_resp = await client.post(
        "/api/signal-queue/intake",
        json={
            "sourceType": "manual",
            "headline": "District 42 board approved new curriculum",
            "campaignFamily": "obc",
            "stateCode": "CA",
            "reasonCodes": [{"code": "DISTRICT_STRATEGIC_LITERACY", "confidence": 0.9}],
        },
    )
    assert intake_resp.status_code == 201
    signal_id = intake_resp.json()["signal"]["id"]

    # Step 2: Qualify
    qual_resp = await client.post(f"/api/signal-queue/{signal_id}/qualify")
    assert qual_resp.status_code == 200
    qual = qual_resp.json()
    assert len(qual["scores"]) > 0

    # Step 3: Approve
    approve_resp = await client.post(f"/api/signal-queue/{signal_id}/approve")
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["rulesetVersionAtQualification"] == "v1"
    candidate_id = approve_data["candidateId"]

    # Step 4: Brief assembly
    brief_resp = await client.post(f"/api/campaign-ops/candidates/{candidate_id}/brief/assemble")
    assert brief_resp.status_code == 201
    brief_content = brief_resp.json()["brief"]["content"]
    assert brief_content["campaignId"] == candidate_id
    assert "signal" in brief_content
    # Signal evidence flows through
    codes = brief_content["signal"]["reasonCodesWithEvidence"]
    assert "DISTRICT_STRATEGIC_LITERACY" in codes
