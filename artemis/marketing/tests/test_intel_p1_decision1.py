"""Phase 1 Decision-1 enrichment — trendContext in initiation-proposal endpoint.

Covers:
  - Trend context shape: trendContext key present with momentum / comparables /
    decisionHistory subkeys and expected types.
  - Decision history read: seeded memory observations surface in priorApproves /
    priorRejects and topMatches.
  - Missing primary signal: trendContext.resolved == False, rest of response intact.
  - Determinism: two sequential calls yield identical trendContext.momentum.delta_ratio.
  - Disjoint from deliverables path: existing response keys unchanged in structure.

Fixture strategy:
  - Reuses marketing conftest db_session + client fixtures.
  - Adds a clean_session fixture that additionally TRUNCATEs memory tables so
    memory reads don't bleed across tests.
  - Seeds memory observations directly via _multi_scope_observation_write (the
    same path used by real carryover helpers) to mirror production writes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401
import artemis.memory.models  # noqa: F401
import artemis.pipelines.models  # noqa: F401
from artemis.marketing.models import District, SignalQueue
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_signal,
    save_initiation_proposal,
)
from artemis.memory.models import MemoryObservation, MemoryObservationScope
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.seeds.marketing_pipeline import AGENT_IDS, seed_marketing_pipeline

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Memory TRUNCATE — run in the same connection as db_session to avoid locks
# ---------------------------------------------------------------------------

_TRUNCATE_MEMORY = text(
    "TRUNCATE memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_observation_scopes, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def clean_session(db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Wrap marketing db_session and additionally truncate memory tables."""
    async with db_session.begin():
        await db_session.execute(_TRUNCATE_MEMORY)
    yield db_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_district(
    session: AsyncSession,
    *,
    name: str = "Riverside ISD",
    state: str = "TX",
    on_skip_list: bool = False,
) -> District:
    d = District(
        name=name,
        state=state,
        enrollment=8000,
        tier="D2",
        supported=True,
        on_skip_list=on_skip_list,
        classification_source="manual",
    )
    session.add(d)
    await session.flush()
    await session.refresh(d)
    return d


async def _make_signal(
    session: AsyncSession,
    *,
    headline: str,
    district_id: int | None,
    campaign_family: str = "obc",
    state: str = "TX",
    signal_status: str = "qualified",
    urgency_tier: str = "standard",
    created_at: datetime | None = None,
) -> SignalQueue:
    sig = await create_signal(
        session,
        headline=headline,
        campaign_family=campaign_family,
        source_type="manual",
        summary=f"Summary for {headline}",
        urgency_tier=urgency_tier,
        discovered_by="test",
        reason_codes=["TEST"],
        resolved_district_id=district_id,
        state=state,
    )
    sig.signal_status = signal_status
    if created_at is not None:
        sig.created_at = created_at
    await session.flush()
    return sig


async def _make_gate_run(session: AsyncSession) -> str:
    """Seed agents + pipeline + run — required for marketing test harness."""
    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        ),
        [{"agent_id": agent_id} for agent_id in AGENT_IDS],
    )
    await session.commit()
    await seed_marketing_pipeline(session)
    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Decision1 Test Pipeline",
        nodes=[
            {
                "id": "gate_campaign_initiation",
                "type": "human_gate",
                "label": "Campaign Initiation Confirm",
                "config": {
                    "approval_kind": "campaign_initiation",
                    "approvers": ["test@example.com"],
                    "timeout_hours": 72,
                },
                "position": {"x": 0.0, "y": 0.0},
            }
        ],
        edges=[],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="test",
    )
    return run.id


async def _seed_candidate(
    session: AsyncSession,
    *,
    district_name: str = "Riverside ISD",
    state: str = "TX",
    campaign_family: str = "obc",
    with_district: bool = True,
    run_id: str | None = None,
) -> int:
    """Seed a minimal candidate with a proposal and return candidate_id."""
    district_id: int | None = None
    if with_district:
        district = await _make_district(session, name=district_name, state=state)
        district_id = district.id

    sig = await _make_signal(
        session,
        headline="Superintendent transition",
        district_id=district_id,
        campaign_family=campaign_family,
        state=state,
        created_at=_NOW - timedelta(days=10),
    )
    if run_id is not None:
        sig.pipeline_run_id = run_id
        await session.flush()

    candidate = await cluster_or_create_candidate(session, sig)
    await save_initiation_proposal(
        session,
        candidate.id,
        {
            "name": "Riverside Follow-Up",
            "objective": "Engage the district following the leadership change.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": [state]},
            "rationale": "Strong signal cluster warrants outreach.",
        },
    )
    return candidate.id


async def _seed_decision_observations(
    session: AsyncSession,
    *,
    theme: str = "obc",
    state: str = "TX",
) -> tuple[int, int]:
    """Seed one approve + one reject observation in workspace:marketing scope.

    Mirrors the real write path via _multi_scope_observation_write so the same
    retrieval stack (FTS + recency) picks them up in search_observations.

    Returns (approve_obs_id, reject_obs_id).
    """
    from artemis.builder.memory_carryover import _multi_scope_observation_write

    # Use brand scope as a secondary — "brand" is a valid ScopeKind and serves
    # as a campaign-family proxy without requiring a new ScopeKind literal.
    approve_id = await _multi_scope_observation_write(
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scope_kinds=["brand"],
        additional_scope_ids=[theme],
        content=(
            f"Gate-1 approved signal for {theme} campaign in {state}. "
            "Operator reviewed and confirmed outreach worthwhile."
        ),
        category="signal_gate1_decision",
        confidence_origin="test_seed",
        source_quality=0.85,
    )

    reject_id = await _multi_scope_observation_write(
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scope_kinds=["brand"],
        additional_scope_ids=[theme],
        content=(
            f"Gate-1 rejected signal for {theme} campaign in {state}. "
            "Operator declined; district already engaged this quarter."
        ),
        category="pipeline_gate_decision",
        confidence_origin="test_seed",
        source_quality=0.85,
    )

    return approve_id, reject_id


async def _seed_manual_decision_observation(
    *,
    category: str,
    content: str,
) -> int:
    from artemis.builder.memory_carryover import _multi_scope_observation_write

    return await _multi_scope_observation_write(
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scope_kinds=["campaign_family"],
        additional_scope_ids=["obc"],
        content=content,
        category=category,
        confidence_origin="test_seed",
        source_quality=0.85,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trend_context_shape(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Seed a signal + district; assert trendContext present with expected subkeys."""
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "trendContext" in data, (
        "trendContext key must be present in initiation-proposal response"
    )
    tc = data["trendContext"]

    assert tc["resolved"] is True
    assert tc["theme"] == "obc"
    assert tc["region"] == "TX"
    assert "asOf" in tc
    assert "momentum" in tc
    assert "comparables" in tc
    assert "decisionHistory" in tc

    # momentum subkeys
    m = tc["momentum"]
    assert isinstance(m["current_window_count"], int)
    assert isinstance(m["prior_window_count"], int)
    assert isinstance(m["buckets"], list)
    # delta_ratio can be None (no prior signals) or a float
    assert m["delta_ratio"] is None or isinstance(m["delta_ratio"], float)

    # comparables subkeys
    c = tc["comparables"]
    assert isinstance(c["comparable_count"], int)
    assert isinstance(c["sample_districts"], list)

    # decisionHistory subkeys
    dh = tc["decisionHistory"]
    assert isinstance(dh["priorApproves"], int)
    assert isinstance(dh["priorRejects"], int)
    assert isinstance(dh["topMatches"], list)


@pytest.mark.asyncio
async def test_decision_history_reads_seeded_observations(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Seed approve + reject observations; assert they surface in decisionHistory."""
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    # Seed observations via the real write path (uses SessionLocal → test engine)
    approve_id, reject_id = await _seed_decision_observations(clean_session)

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    dh = data["trendContext"]["decisionHistory"]

    assert dh["priorApproves"] + dh["priorRejects"] >= 2, (
        f"Expected ≥2 total decisions, got approves={dh['priorApproves']} "
        f"rejects={dh['priorRejects']}"
    )

    returned_ids = {m["observationId"] for m in dh["topMatches"]}
    # At least one of the seeded observations must appear in top matches
    assert approve_id in returned_ids or reject_id in returned_ids, (
        f"Seeded obs ids {approve_id}, {reject_id} not found in topMatches: {returned_ids}"
    )

    # Each topMatches entry has required fields
    for match in dh["topMatches"]:
        assert "observationId" in match
        assert "category" in match
        assert match["category"] in {"signal_gate1_decision", "pipeline_gate_decision"}
        assert "decision" in match
        assert match["decision"] in {"approved", "rejected"}
        assert "summary" in match
        assert "createdAt" in match


@pytest.mark.asyncio
async def test_decision_history_ignores_not_rejected_false_positive(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Standalone 'not rejected' prose should not be counted as a rejection."""
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    await _seed_decision_observations(clean_session)
    await _seed_manual_decision_observation(
        category="signal_gate1_decision",
        content=(
            "Review note for obc campaign in TX. The district was not rejected in the "
            "previous cycle and there were no rejection concerns."
        ),
    )

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text

    dh = resp.json()["trendContext"]["decisionHistory"]
    assert dh["priorApproves"] == 1
    assert dh["priorRejects"] == 1
    assert all("not rejected" not in match["summary"].lower() for match in dh["topMatches"])


@pytest.mark.asyncio
async def test_missing_primary_signal_returns_resolved_false(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Candidate whose primary signal link is removed: trendContext.resolved==False, no crash.

    We create a candidate normally (so DB constraints are satisfied), then delete
    the campaign_candidate_signals row so get_candidate_primary_signal returns None.
    This exercises the resolved=False path in _build_trend_context without
    violating any DB constraints.
    """
    await _make_gate_run(clean_session)
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    # Remove the primary signal link so get_candidate_primary_signal returns None
    await clean_session.execute(
        text(
            "DELETE FROM campaign_candidate_signals WHERE candidate_id = :cid AND is_primary = TRUE"
        ),
        {"cid": candidate_id},
    )
    await clean_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # trendContext should still be present
    assert "trendContext" in data
    tc = data["trendContext"]
    assert tc["resolved"] is False

    # Verify the rest of the response keys are still present
    for key in ("signalCluster", "districtContext", "defaultTargetScope", "proposal"):
        assert key in data, f"Key '{key}' missing from response when trendContext.resolved=False"


@pytest.mark.asyncio
async def test_trend_context_determinism(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Two sequential calls must yield identical momentum.delta_ratio."""
    run_id = await _make_gate_run(clean_session)

    district = await _make_district(clean_session, name="Determinism ISD")
    did = district.id

    # Seed signals in both current and prior windows so delta_ratio is non-None
    for i in range(3):
        await _make_signal(
            clean_session,
            headline=f"Current signal {i}",
            district_id=did,
            created_at=_NOW - timedelta(days=10 + i),
        )
    for i in range(2):
        await _make_signal(
            clean_session,
            headline=f"Prior signal {i}",
            district_id=did,
            created_at=_NOW - timedelta(days=100 + i),
        )

    # Create candidate on the primary signal
    primary = await _make_signal(
        clean_session,
        headline="Primary signal",
        district_id=did,
        created_at=_NOW - timedelta(days=5),
    )
    primary.pipeline_run_id = run_id
    await clean_session.flush()
    candidate = await cluster_or_create_candidate(clean_session, primary)
    await save_initiation_proposal(
        clean_session,
        candidate.id,
        {
            "name": "Determinism Test",
            "objective": "Test determinism.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": ["TX"]},
            "rationale": "Determinism test.",
        },
    )
    await clean_session.commit()

    url = f"/api/marketing/campaigns/{candidate.id}/initiation-proposal"
    resp1 = await client.get(url)
    resp2 = await client.get(url)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    tc1 = resp1.json()["trendContext"]
    tc2 = resp2.json()["trendContext"]

    assert tc1["resolved"] is True
    assert tc1["momentum"]["delta_ratio"] == tc2["momentum"]["delta_ratio"], (
        "delta_ratio must be byte-equal across two sequential calls "
        f"(got {tc1['momentum']['delta_ratio']!r} vs {tc2['momentum']['delta_ratio']!r})"
    )
    assert tc1["comparables"]["comparable_count"] == tc2["comparables"]["comparable_count"]


@pytest.mark.asyncio
async def test_initiation_proposal_persists_trend_snapshot(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """The Decision-1 enrichment path should persist a trend snapshot with marketing scopes."""
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text

    obs = (
        (
            await clean_session.execute(
                select(MemoryObservation)
                .where(MemoryObservation.category == "trend_snapshot")
                .order_by(MemoryObservation.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert obs is not None

    scopes = (
        (
            await clean_session.execute(
                select(MemoryObservationScope).where(
                    MemoryObservationScope.observation_id == obs.id
                )
            )
        )
        .scalars()
        .all()
    )
    scope_keys = {(scope.scope_kind, scope.scope_id) for scope in scopes}
    assert ("workspace", "marketing") in scope_keys
    assert ("state", "TX") in scope_keys
    assert ("campaign_family", "obc") in scope_keys


@pytest.mark.asyncio
async def test_existing_response_keys_unchanged(
    client: AsyncClient,
    clean_session: AsyncSession,
) -> None:
    """Assert that adding trendContext does not alter existing response keys."""
    run_id = await _make_gate_run(clean_session)
    candidate_id = await _seed_candidate(clean_session, run_id=run_id)
    await clean_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # signalCluster: list of dicts with signalId + headline
    assert isinstance(data["signalCluster"], list)
    assert len(data["signalCluster"]) >= 1
    assert "signalId" in data["signalCluster"][0]
    assert "headline" in data["signalCluster"][0]

    # districtContext: resolved + state
    dc = data["districtContext"]
    assert dc["resolved"] is True
    assert dc["state"] == "TX"
    assert "defaultTargetScope" in dc

    # defaultTargetScope: top-level key also present
    assert "defaultTargetScope" in data
    assert data["defaultTargetScope"] == dc["defaultTargetScope"]

    # proposal: name + target_scope
    proposal = data["proposal"]
    assert proposal["name"] == "Riverside Follow-Up"
    assert "target_scope" in proposal

    # trendContext must be additive (no key removed / renamed)
    assert "trendContext" in data
