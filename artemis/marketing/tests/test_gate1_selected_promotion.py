"""Tests for operator-selected Gate-1 promotion (Change 2 + 3).

Covers:
1. promote_selected_signals_for_run: only selected signals become candidates;
   unselected remain qualified (lossless).
2. get_signal_ids_for_cluster_keys: cluster_key expansion.
3. Approvals route — selected_cluster_keys path: POST decision with a cluster_key
   creates only that cluster's candidate.
4. Approvals route — backward compat: POST decision with no selection → legacy
   all-qualified path.

Worker A test DB: artemis_test_worker_a
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import NullPool
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.marketing.models import (
    Approval,
    District,
    SignalQueue,
)
from artemis.marketing.repository import (
    get_signal_ids_for_cluster_keys,
    list_run_candidates,
    promote_selected_signals_for_run,
)
from artemis.pipelines import repository as pipeline_repo

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")

pytestmark = pytest.mark.asyncio

# ── Truncate helpers (both marketing + pipeline tables) ───────────────────────

_TRUNCATE_MARKETING = _text(
    "TRUNCATE campaign_candidate_signals, campaign_candidates, approvals, "
    "signal_queue, district_tier_bands, districts RESTART IDENTITY CASCADE"
)
_TRUNCATE_PIPELINES = _text("TRUNCATE pipeline_runs, pipelines RESTART IDENTITY CASCADE")


async def _reset(session: AsyncSession) -> None:
    await session.execute(_TRUNCATE_MARKETING)
    await session.execute(_TRUNCATE_PIPELINES)


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_district(session: AsyncSession, nces: str = "NCESA") -> int:
    d = District(
        nces_id=nces,
        name=f"District {nces}",
        state="CA",
        enrollment=5000,
        tier="D2",
        supported=True,
        on_skip_list=False,
        classification_source="manual",
        classified_at=datetime.now(UTC),
    )
    session.add(d)
    await session.flush()
    await session.refresh(d)
    return d.id


async def _seed_run(session: AsyncSession) -> Any:
    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Selection Test Pipeline",
        nodes=[
            {
                "id": "gate_1",
                "type": "human_gate",
                "config": {"approval_kind": "signal_brief", "approvers": ["t@example.com"]},
                "label": "Gate 1",
            }
        ],
        edges=[],
        status="active",
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="test",
    )
    await pipeline_repo.update_pipeline_run(
        session,
        run.id,
        node_states={
            "gate_1": {
                "status": "suspended",
                "started_at": datetime.now(UTC).isoformat(),
                "cost_usd": 0.0,
            }
        },
    )
    await session.flush()
    return run


async def _seed_signals(
    session: AsyncSession,
    run_id: str,
    *,
    count: int,
    resolved_district_id: int | None = None,
    campaign_family: str = "marketing",
) -> list[Any]:
    signals = []
    for i in range(count):
        sig = SignalQueue(
            headline=f"Signal {i}",
            summary="",
            campaign_family=campaign_family,
            signal_status="qualified",
            discovered_by="test",
            pipeline_run_id=run_id,
            resolved_district_id=resolved_district_id,
        )
        session.add(sig)
        signals.append(sig)
    await session.flush()
    for s in signals:
        await session.refresh(s)
    return signals


async def _create_approval(session: AsyncSession, run_id: str, node_id: str = "gate_1") -> Any:
    approval = Approval(
        kind="signal_brief",
        subject_id=f"{run_id}:{node_id}",
        status="pending",
        decision_payload={
            "run_id": run_id,
            "node_id": node_id,
            "approvers": ["t@example.com"],
        },
        pipe4_context={
            "pipeline_run_id": run_id,
            "pipeline_name": "Selection Test Pipeline",
            "node_id": node_id,
            "node_label": "Gate 1",
            "context": {},
        },
    )
    session.add(approval)
    await session.flush()
    await session.refresh(approval)
    return approval


# ── Test 1: promote_selected_signals_for_run ──────────────────────────────────


async def test_selected_promotion_only_creates_selected_candidates(
    db_session: AsyncSession,
) -> None:
    """Only the selected signal IDs get promoted; unselected stay qualified (lossless)."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        dist_a = await _seed_district(db_session, "NCESA1")
        dist_b = await _seed_district(db_session, "NCESB1")
        run = await _seed_run(db_session)
        run_id = run.id

        # 2 signals in cluster A, 1 signal in cluster B
        sig_a1, sig_a2 = await _seed_signals(
            db_session, run_id, count=2, resolved_district_id=dist_a
        )
        (sig_b1,) = await _seed_signals(db_session, run_id, count=1, resolved_district_id=dist_b)

    # Select only the cluster-B signal
    async with db_session.begin():
        results = await promote_selected_signals_for_run(db_session, run_id, [sig_b1.id])

    # 1 result — only cluster B signal promoted
    assert len(results) == 1
    assert results[0].signal.id == sig_b1.id
    assert results[0].candidate is not None

    # Verify cluster-A signals remain qualified (NOT approved, NOT deleted)
    async with db_session.begin():
        sig_a1_db = await db_session.get(SignalQueue, sig_a1.id)
        sig_a2_db = await db_session.get(SignalQueue, sig_a2.id)
        sig_b1_db = await db_session.get(SignalQueue, sig_b1.id)

        assert sig_a1_db is not None and sig_a1_db.signal_status == "qualified", (
            f"sig_a1 should remain qualified; got {sig_a1_db.signal_status if sig_a1_db else 'None'}"
        )
        assert sig_a2_db is not None and sig_a2_db.signal_status == "qualified", (
            f"sig_a2 should remain qualified; got {sig_a2_db.signal_status if sig_a2_db else 'None'}"
        )
        assert sig_b1_db is not None and sig_b1_db.signal_status == "approved", (
            f"sig_b1 should be approved; got {sig_b1_db.signal_status if sig_b1_db else 'None'}"
        )

    # Verify exactly 1 candidate created (the cluster-B one)
    async with db_session.begin():
        candidates = await list_run_candidates(db_session, run_id, initiated_only=False)

    assert len(candidates) == 1, f"Expected 1 candidate (cluster B only); got {len(candidates)}"


async def test_selected_promotion_empty_list_returns_empty(db_session: AsyncSession) -> None:
    """Calling promote_selected_signals_for_run with [] returns []."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        run = await _seed_run(db_session)
        await _seed_signals(db_session, run.id, count=2)

    async with db_session.begin():
        results = await promote_selected_signals_for_run(db_session, run.id, [])

    assert results == []


# ── Test 2: get_signal_ids_for_cluster_keys ───────────────────────────────────


async def test_cluster_key_expansion(db_session: AsyncSession) -> None:
    """get_signal_ids_for_cluster_keys returns only the IDs in the given cluster."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        dist_a = await _seed_district(db_session, "NCESKEYSA")
        dist_b = await _seed_district(db_session, "NCESKEYSB")
        run = await _seed_run(db_session)
        run_id = run.id
        sig_a1, sig_a2 = await _seed_signals(
            db_session, run_id, count=2, resolved_district_id=dist_a
        )
        (sig_b1,) = await _seed_signals(db_session, run_id, count=1, resolved_district_id=dist_b)

    # Compute cluster_key for dist_b
    cluster_key_b = f"{dist_b}|marketing"

    async with db_session.begin():
        ids = await get_signal_ids_for_cluster_keys(db_session, run_id, [cluster_key_b])

    assert set(ids) == {sig_b1.id}, f"Expected only sig_b1 ({sig_b1.id}); got {ids}"


async def test_cluster_key_expansion_empty_keys(db_session: AsyncSession) -> None:
    """Empty cluster_keys list → empty result."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        run = await _seed_run(db_session)
        await _seed_signals(db_session, run.id, count=2)

    async with db_session.begin():
        ids = await get_signal_ids_for_cluster_keys(db_session, run.id, [])

    assert ids == []


# ── Test 3: Approvals route — selected_cluster_keys path ─────────────────────


async def test_approvals_route_selected_cluster_keys(
    db_session: AsyncSession,
    client: Any,
) -> None:
    """POST decision with selected_cluster_keys routes through promote_selected_signals_for_run."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        dist_a = await _seed_district(db_session, "NCESROUTEA")
        dist_b = await _seed_district(db_session, "NCESROUTEB")
        run = await _seed_run(db_session)
        run_id = run.id
        sig_a1, sig_a2 = await _seed_signals(
            db_session, run_id, count=2, resolved_district_id=dist_a
        )
        (sig_b1,) = await _seed_signals(db_session, run_id, count=1, resolved_district_id=dist_b)
        approval = await _create_approval(db_session, run_id)

    cluster_key_b = f"{dist_b}|marketing"

    resp = await client.post(
        f"/api/approvals/{approval.id}/decision",
        json={
            "status": "approved",
            "decidedBy": "test@example.com",
            "selectedClusterKeys": [cluster_key_b],
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Use a fresh engine connection to read state committed by the HTTP route,
    # avoiding stale identity-map entries in db_session.
    fresh_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    try:
        async with AsyncSession(fresh_engine, expire_on_commit=False) as fresh:
            async with fresh.begin():
                sig_a1_db = await fresh.get(SignalQueue, sig_a1.id)
                sig_a2_db = await fresh.get(SignalQueue, sig_a2.id)
                sig_b1_db = await fresh.get(SignalQueue, sig_b1.id)

            assert sig_a1_db is not None and sig_a1_db.signal_status == "qualified", (
                f"sig_a1 should stay qualified; got {sig_a1_db.signal_status if sig_a1_db else 'None'}"
            )
            assert sig_a2_db is not None and sig_a2_db.signal_status == "qualified", (
                f"sig_a2 should stay qualified; got {sig_a2_db.signal_status if sig_a2_db else 'None'}"
            )
            assert sig_b1_db is not None and sig_b1_db.signal_status == "approved", (
                f"sig_b1 should be approved; got {sig_b1_db.signal_status if sig_b1_db else 'None'}"
            )

            # Only 1 candidate (cluster B only)
            async with fresh.begin():
                candidates = await list_run_candidates(fresh, run_id, initiated_only=False)

            assert len(candidates) == 1, (
                f"Expected 1 candidate (cluster B only); got {len(candidates)}"
            )
    finally:
        await fresh_engine.dispose()


# ── Test 4: Approvals route — backward compatibility ─────────────────────────


async def test_approvals_route_backward_compat_no_selection(
    db_session: AsyncSession,
    client: Any,
) -> None:
    """POST decision with no selection fields → all qualified signals promoted (legacy path)."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        dist_a = await _seed_district(db_session, "NCESBCKA")
        run = await _seed_run(db_session)
        run_id = run.id
        sigs = await _seed_signals(db_session, run_id, count=3, resolved_district_id=dist_a)
        approval = await _create_approval(db_session, run_id)

    resp = await client.post(
        f"/api/approvals/{approval.id}/decision",
        json={
            "status": "approved",
            "decidedBy": "test@example.com",
            # no selectedSignalIds, no selectedClusterKeys
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Use a fresh engine connection to read state committed by the HTTP route.
    fresh_engine2 = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    try:
        async with AsyncSession(fresh_engine2, expire_on_commit=False) as fresh2:
            # All 3 signals should be approved (legacy all-qualified path)
            async with fresh2.begin():
                for sig in sigs:
                    sig_db = await fresh2.get(SignalQueue, sig.id)
                    assert sig_db is not None and sig_db.signal_status == "approved", (
                        f"Signal {sig.id} should be approved in legacy path; "
                        f"got {sig_db.signal_status if sig_db else 'None'}"
                    )

            # 1 candidate (all 3 signals cluster into one since same district+family)
            async with fresh2.begin():
                candidates = await list_run_candidates(fresh2, run_id, initiated_only=False)

            assert len(candidates) == 1, (
                f"Expected 1 candidate (all signals same cluster); got {len(candidates)}"
            )
    finally:
        await fresh_engine2.dispose()
