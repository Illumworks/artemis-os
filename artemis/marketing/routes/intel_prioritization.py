"""Phase 1 Decision-2 — prioritization endpoint.

GET /api/marketing/intel/prioritization

Answers "Where do we point attention this week?" by joining two ranked lists:
  - velocity_ranking: districts with the most urgency-weighted signal activity
    in the look-back window (from compute_velocity_ranking).
  - time_sensitive: signals whose urgency/recency flags a near-term opportunity
    (from compute_time_sensitivity).

The `combined` list surfaces districts in BOTH lists first (intersection ranked
by velocity score), then time-sensitive-only items, then velocity-only items.
No new weighting is invented; the merge is purely positional / set-based.

All numbers come from Worker 1's deterministic module (trends.py). No SQL is
written here. No LLM calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.intel.schemas import (
    DistrictVelocityRow,
    TimeSensitiveSignalRow,
    TrendSnapshot,
)
from artemis.marketing.intel.trends import (
    compute_time_sensitivity,
    compute_velocity_ranking,
    persist_trend_snapshot,
)
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/marketing/intel",
    tags=["marketing-intel"],
    dependencies=[Depends(require_token)],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CombinedPriorityRow(BaseModel):
    """A district entry in the merged priority list.

    Ordering:
      1. Districts appearing in both velocity_ranking AND time_sensitive
         (intersection), ranked by velocity weighted_score DESC.
      2. Districts appearing in time_sensitive only, in their time-sensitive
         order (urgency then recency).
      3. Districts appearing in velocity_ranking only, in velocity order
         (weighted_score DESC).
    """

    district_id: int
    name: str
    state: str | None
    tier: str | None
    velocity_score: float | None
    velocity_rank: int | None
    has_time_sensitive_signal: bool
    earliest_deadline_iso: str | None


class PrioritizationResponse(BaseModel):
    as_of: datetime
    window_days: int
    horizon_days: int
    state_filter: str | None
    velocity_ranking: list[DistrictVelocityRow]
    time_sensitive: list[TimeSensitiveSignalRow]
    combined: list[CombinedPriorityRow]
    persisted_observation_id: int | None


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------


def _build_combined(
    velocity: list[DistrictVelocityRow],
    time_sensitive: list[TimeSensitiveSignalRow],
) -> list[CombinedPriorityRow]:
    """Merge velocity + time-sensitive lists into a deterministic combined ranking.

    Logic (simple, no new weighting):
      1. Build a map: district_id → earliest deadline_proxy from time_sensitive.
      2. Build a map: district_id → DistrictVelocityRow from velocity.
      3. Emit intersection rows (both lists) ordered by velocity_score DESC.
      4. Emit time-sensitive-only rows in their original order.
      5. Emit velocity-only rows in their original order.
    """
    # district_id → earliest deadline proxy ISO string
    ts_deadline_map: dict[int, str] = {}
    ts_district_ids_ordered: list[int] = []  # preserves time-sensitive ordering
    ts_seen: set[int] = set()
    for row in time_sensitive:
        if row.district is None:
            continue
        did = row.district.district_id
        dl_iso = row.deadline_proxy.isoformat()
        if did not in ts_deadline_map:
            ts_deadline_map[did] = dl_iso
        else:
            # keep earliest
            if dl_iso < ts_deadline_map[did]:
                ts_deadline_map[did] = dl_iso
        if did not in ts_seen:
            ts_district_ids_ordered.append(did)
            ts_seen.add(did)

    # district_id → velocity row
    vel_map: dict[int, DistrictVelocityRow] = {}
    for vrow_pre in velocity:
        vel_map[vrow_pre.district.district_id] = vrow_pre

    # Build the three buckets
    intersection: list[CombinedPriorityRow] = []
    ts_only: list[CombinedPriorityRow] = []
    vel_only: list[CombinedPriorityRow] = []

    # Intersection: districts in both — iterate velocity (already ranked by score)
    for vrow in velocity:
        did = vrow.district.district_id
        if did in ts_deadline_map:
            intersection.append(
                CombinedPriorityRow(
                    district_id=did,
                    name=vrow.district.name,
                    state=vrow.district.state,
                    tier=vrow.district.tier,
                    velocity_score=vrow.weighted_score,
                    velocity_rank=vrow.rank,
                    has_time_sensitive_signal=True,
                    earliest_deadline_iso=ts_deadline_map[did],
                )
            )

    # Time-sensitive only — preserve urgency/recency order from time_sensitive list
    for did in ts_district_ids_ordered:
        if did not in vel_map:
            # Need stub info from time_sensitive rows
            for ts_row in time_sensitive:
                if ts_row.district is not None and ts_row.district.district_id == did:
                    d = ts_row.district
                    ts_only.append(
                        CombinedPriorityRow(
                            district_id=did,
                            name=d.name,
                            state=d.state,
                            tier=d.tier,
                            velocity_score=None,
                            velocity_rank=None,
                            has_time_sensitive_signal=True,
                            earliest_deadline_iso=ts_deadline_map[did],
                        )
                    )
                    break

    # Velocity only — preserve velocity ranking order
    intersection_ids = {row.district_id for row in intersection}
    for vrow in velocity:
        did = vrow.district.district_id
        if did not in intersection_ids and did not in ts_deadline_map:
            vel_only.append(
                CombinedPriorityRow(
                    district_id=did,
                    name=vrow.district.name,
                    state=vrow.district.state,
                    tier=vrow.district.tier,
                    velocity_score=vrow.weighted_score,
                    velocity_rank=vrow.rank,
                    has_time_sensitive_signal=False,
                    earliest_deadline_iso=None,
                )
            )

    return intersection + ts_only + vel_only


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/prioritization", response_model=PrioritizationResponse)
async def get_prioritization(
    window_days: Annotated[int, Query(ge=7, le=180)] = 30,
    horizon_days: Annotated[int, Query(ge=7, le=180)] = 60,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    state: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    persist: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PrioritizationResponse:
    """Return a ranked prioritization of districts for this week's attention.

    Combines velocity ranking (urgency-weighted signal count over window_days)
    with time-sensitivity ranking (urgency + recency over horizon_days) into a
    unified `combined` list. Districts in both lists appear first.

    Optional `state` filter (2-letter abbreviation) restricts both lists to one
    state. Optional `persist=true` saves a trend_snapshot observation to memory.
    """
    as_of = datetime.now(UTC)

    velocity = await compute_velocity_ranking(
        session,
        as_of=as_of,
        window_days=window_days,
        limit=limit,
        state=state,
    )

    time_sensitive = await compute_time_sensitivity(
        session,
        as_of=as_of,
        horizon_days=horizon_days,
        limit=limit,
        state=state,
    )

    combined = _build_combined(velocity, time_sensitive)

    obs_id: int | None = None
    if persist:
        # Build a lightweight snapshot payload — full row data would be huge;
        # capture the top-5 combined district IDs + scores for retrieval.
        top5 = [
            {
                "district_id": r.district_id,
                "name": r.name,
                "state": r.state,
                "velocity_score": r.velocity_score,
                "has_time_sensitive_signal": r.has_time_sensitive_signal,
            }
            for r in combined[:5]
        ]
        snapshot = TrendSnapshot(
            as_of=as_of,
            theme=None,
            region=state,
            snapshot_kind="prioritization",
            content_summary=(
                f"Prioritization snapshot as of {as_of.date().isoformat()}"
                + (f" state={state}" if state else "")
                + f": {len(combined)} combined districts"
            ),
            payload={
                "window_days": window_days,
                "horizon_days": horizon_days,
                "state_filter": state,
                "velocity_count": len(velocity),
                "time_sensitive_count": len(time_sensitive),
                "combined_count": len(combined),
                "top5": top5,
            },
        )
        additional_scopes: list[tuple[str, str]] = []
        if state is not None:
            additional_scopes = [("state", state)]
        try:
            obs_id = await persist_trend_snapshot(
                session,
                snapshot=snapshot,
                primary_scope_kind="workspace",
                primary_scope_id="marketing",
                additional_scopes=additional_scopes,
            )
        except Exception:
            logger.exception("persist_trend_snapshot failed — returning response without obs_id")

    return PrioritizationResponse(
        as_of=as_of,
        window_days=window_days,
        horizon_days=horizon_days,
        state_filter=state,
        velocity_ranking=velocity,
        time_sensitive=time_sensitive,
        combined=combined,
        persisted_observation_id=obs_id,
    )
