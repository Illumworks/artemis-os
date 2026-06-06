"""Per-campaign cost rollup endpoint.

GET /api/marketing/campaigns/{candidate_id}/cost

Sums campaign-attributed cost_events (tagged with campaign_candidate_id at
write time by brief_assembler / writing_studio compose / run_agent) by stage,
plus an allocated discovery (scouting) line, plus districts contacted and
cost-per-district math.

Response shape (per briefs/campaign-cost-rollup.md):
{
  "totalUsd": <num>,
  "byStage": {
    "scouting": <num|null>,
    "brief":    <num>,
    "content":  <num>,
    "sends":    <num>
  },
  "districtsContacted": <int>,
  "districtsBasis": "recipients" | "target_audience",
  "costPerDistrict": <num|null>
}

Read-only: reuses the rate snapshots already stored on cost_events rows —
no recompute. Lossless: never mutates cost_events.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.models import CostEvent
from artemis.db import get_session
from artemis.marketing.models import (
    CampaignCandidate,
    CampaignCandidateSignal,
    CampaignSend,
    SignalQueue,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.sends import resolve_district_ids_for_candidate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/marketing/campaigns",
    tags=["marketing-campaign-cost"],
    dependencies=[Depends(require_token)],  # noqa: B008
)


# ── Stage taxonomy ────────────────────────────────────────────────────────────
# Map feature_tag on cost_events rows (filtered to a single candidate) to the
# response's byStage keys. Anything not in these sets contributes to totalUsd
# but isn't shown in the breakdown — log a debug message if we hit one.
_BRIEF_TAGS: frozenset[str] = frozenset({"marketing_brief", "campaign_initiation"})
_CONTENT_TAGS: frozenset[str] = frozenset({"writing_studio_compose", "agent_run"})
# Sends today involve no LLM calls — byStage.sends is reserved for when a send
# personalization LLM (subject-line variants, per-recipient framing) lands.
# Until then this is always 0; we keep the key to honor the brief's response
# shape so the Campaign UI can render the row.
_SENDS_TAGS: frozenset[str] = frozenset()

# Scout allocation window — how far back to look when computing average
# cost-per-signal for retrospective attribution.
_SCOUT_WINDOW = timedelta(days=30)


async def _campaign_must_exist(session: AsyncSession, candidate_id: int) -> None:
    row = await session.get(CampaignCandidate, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "campaign not found"})


async def _by_stage_direct(
    session: AsyncSession, candidate_id: int
) -> tuple[dict[str, float], float]:
    """Sum cost_events directly attributed to this candidate, grouped by stage.

    Returns (byStage, sum_of_byStage). The endpoint adds scouting (allocated)
    to the total separately.
    """
    result = await session.execute(
        select(CostEvent.feature_tag, func.sum(CostEvent.cost_usd))
        .where(CostEvent.campaign_candidate_id == candidate_id)
        .group_by(CostEvent.feature_tag)
    )

    by_stage = {"brief": 0.0, "content": 0.0, "sends": 0.0}
    for tag, total in result.all():
        cost = float(total or 0.0)
        if tag in _BRIEF_TAGS:
            by_stage["brief"] += cost
        elif tag in _CONTENT_TAGS:
            by_stage["content"] += cost
        elif tag in _SENDS_TAGS:
            by_stage["sends"] += cost
        else:
            # Cost row tagged to this campaign but unknown stage. Add to total
            # via a sentinel "other" key we'll fold into content for display
            # (anything attributed to the campaign is genuinely campaign cost).
            logger.debug(
                "campaign_cost: cost_events row with campaign_candidate_id=%s has "
                "unknown feature_tag=%r ($%.4f); folding into content",
                candidate_id,
                tag,
                cost,
            )
            by_stage["content"] += cost

    direct_sum = by_stage["brief"] + by_stage["content"] + by_stage["sends"]
    return by_stage, direct_sum


async def _scouting_allocated(session: AsyncSession, candidate_id: int) -> float | None:
    """Per-signal-share allocation of discovery cost.

    avg_cost_per_signal = SUM(scout_cost in window) / COUNT(signals in window)
    discovery_cost      = avg_cost_per_signal * (this candidate's seeding-signal count)

    Returns None when no signals or no scouting spend in the window — the UI
    treats null as "not yet available" rather than a real zero.
    """
    window_start = datetime.now(UTC) - _SCOUT_WINDOW

    seed_count_row = await session.execute(
        select(func.count(CampaignCandidateSignal.id)).where(
            CampaignCandidateSignal.candidate_id == candidate_id
        )
    )
    seed_count = int(seed_count_row.scalar() or 0)
    if seed_count == 0:
        return None

    scout_cost_row = await session.execute(
        select(func.coalesce(func.sum(CostEvent.cost_usd), 0.0)).where(
            CostEvent.feature_tag == "marketing_scout",
            CostEvent.created_at >= window_start,
        )
    )
    total_scout_cost = float(scout_cost_row.scalar() or 0.0)

    signal_count_row = await session.execute(
        select(func.count(SignalQueue.id)).where(SignalQueue.created_at >= window_start)
    )
    total_signals = int(signal_count_row.scalar() or 0)

    if total_signals == 0 or total_scout_cost == 0.0:
        return None

    per_signal = total_scout_cost / total_signals
    return per_signal * seed_count


async def _districts_count(session: AsyncSession, candidate: CampaignCandidate) -> tuple[int, str]:
    """Return (districts_contacted, basis).

    Prefers actual recipients (recipients JSONB on campaign_sends rows) when
    a send has happened, falls back to the resolved target-audience district
    count.
    """
    sends_result = await session.execute(
        select(CampaignSend.recipients).where(CampaignSend.candidate_id == candidate.id)
    )
    recipient_district_ids: set[int] = set()
    for (recipients_json,) in sends_result.all():
        if isinstance(recipients_json, list):
            for r in recipients_json:
                if isinstance(r, dict) and (did := r.get("district_id")) is not None:
                    try:
                        recipient_district_ids.add(int(did))
                    except (TypeError, ValueError):
                        continue
    if recipient_district_ids:
        return len(recipient_district_ids), "recipients"

    # No recipients yet — basis = target_audience.
    district_ids = await resolve_district_ids_for_candidate(session, candidate)
    return len(district_ids), "target_audience"


@router.get("/{candidate_id}/cost")
async def get_campaign_cost(
    candidate_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the per-campaign cost rollup for a single candidate."""
    await _campaign_must_exist(session, candidate_id)
    candidate = await session.get(CampaignCandidate, candidate_id)
    assert candidate is not None  # narrowed by _campaign_must_exist above

    by_stage_direct, direct_sum = await _by_stage_direct(session, candidate_id)
    scouting = await _scouting_allocated(session, candidate_id)

    total_usd = direct_sum + (scouting if scouting is not None else 0.0)

    districts_contacted, basis = await _districts_count(session, candidate)
    cost_per_district = (total_usd / districts_contacted) if districts_contacted > 0 else None

    return {
        "totalUsd": round(total_usd, 6),
        "byStage": {
            "scouting": round(scouting, 6) if scouting is not None else None,
            "brief": round(by_stage_direct["brief"], 6),
            "content": round(by_stage_direct["content"], 6),
            "sends": round(by_stage_direct["sends"], 6),
        },
        "districtsContacted": districts_contacted,
        "districtsBasis": basis,
        "costPerDistrict": round(cost_per_district, 6) if cost_per_district is not None else None,
    }
