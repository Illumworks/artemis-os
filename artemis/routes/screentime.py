"""Screen-Time Watch HTTP routes — internal-only dashboard backend.

Three endpoints power the dedicated "Screen-Time Watch" page (a 50-state heat
map over a searchable signal repository):

  GET  /api/screentime/state-stance  — the per-state rollup for the heat map.
                                        Returns ALL 50 states + DC, filling any
                                        state with no row as ``no_info`` so the
                                        map is honest (gray, not a gap).
  GET  /api/screentime/signals        — filterable, paginated signal repository.
  POST /api/screentime/purge          — owner-only scrub of the screentime_*
                                        tables.

Auth (mirrors the marketing/operations surfaces — internal, NOT customer-facing):
  - The read endpoints are gated by ``require_token`` (the shared bearer / CF
    Access gate any authed internal teammate passes — same as the marketing
    surface). They are NOT public.
  - ``purge`` is additionally gated by ``require_owner`` (owner-only, fail-closed),
    matching the Dev Projects / agent-builder admin pattern.

This module only READS the screentime_* tables (via the repository's purge helper
for the scrub); it never writes signals or stance — the pipeline owns that.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_owner, require_token
from artemis.screentime.models import (
    STANCE_NO_INFO,
    ScreentimeSignal,
    ScreentimeStateStance,
)
from artemis.screentime.repository import purge_screentime_data

_logger = logging.getLogger(__name__)

# The 50 states + DC. The single source of truth for "render every state" so a
# state with no signals shows honest no-info gray, never a gap in the map.
US_STATES: tuple[str, ...] = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
)

# Internal-only gate: any authed teammate may read; the surface is never public.
router = APIRouter(
    prefix="/api/screentime",
    tags=["screentime"],
    dependencies=[Depends(require_token)],
)


@router.get("/state-stance")
async def get_state_stance(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the 50-state (+ DC) rollup for the heat map.

    Every state is present. States with a stored ``screentime_state_stance`` row
    use it; states with no row fall back to ``no_info`` (signal_count 0) so the
    map colors all 50 states honestly.
    """
    rows = (
        await session.execute(
            select(
                ScreentimeStateStance.state,
                ScreentimeStateStance.stance,
                ScreentimeStateStance.rationale,
                ScreentimeStateStance.signal_count,
                ScreentimeStateStance.last_updated,
            )
        )
    ).all()

    by_state: dict[str, dict[str, Any]] = {}
    for state, stance, rationale, signal_count, last_updated in rows:
        by_state[str(state).upper()] = {
            "state": str(state).upper(),
            "stance": stance,
            "rationale": rationale,
            "signal_count": int(signal_count or 0),
            "last_updated": last_updated.isoformat() if last_updated else None,
        }

    states: list[dict[str, Any]] = []
    for code in US_STATES:
        states.append(
            by_state.get(
                code,
                {
                    "state": code,
                    "stance": STANCE_NO_INFO,
                    "rationale": "No signals yet.",
                    "signal_count": 0,
                    "last_updated": None,
                },
            )
        )

    counts: dict[str, int] = {}
    for entry in states:
        counts[entry["stance"]] = counts.get(entry["stance"], 0) + 1

    return {"states": states, "counts": counts, "total_states": len(states)}


@router.get("/signals")
async def list_signals(
    state: str | None = Query(default=None, description="2-letter state code filter"),  # noqa: B008
    level: str | None = Query(default=None, description="state | district"),  # noqa: B008
    status: str | None = Query(default=None, description="proposed|passed|amended|guidance|news"),  # noqa: B008
    stance: str | None = Query(default=None, description="favorable|unfavorable|neutral"),  # noqa: B008
    source_type: str | None = Query(default=None),  # noqa: B008
    since: datetime | None = Query(default=None, description="discovered_at >= since"),  # noqa: B008
    q: str | None = Query(default=None, description="free-text search over title/summary/angle"),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Filterable, paginated signal repository.

    All filters are optional and AND-combine. Returns the page rows + the total
    matching count (for pagination) + the applied filters (for the UI to echo).
    """
    conditions = []
    if state:
        conditions.append(func.upper(ScreentimeSignal.state) == state.strip().upper())
    if level:
        conditions.append(ScreentimeSignal.level == level.strip())
    if status:
        conditions.append(ScreentimeSignal.status == status.strip())
    if stance:
        conditions.append(ScreentimeSignal.stance == stance.strip())
    if source_type:
        conditions.append(ScreentimeSignal.source_type == source_type.strip())
    if since is not None:
        conditions.append(ScreentimeSignal.discovered_at >= since)
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            func.coalesce(ScreentimeSignal.title, "").ilike(needle)
            | func.coalesce(ScreentimeSignal.summary, "").ilike(needle)
            | func.coalesce(ScreentimeSignal.amira_angle, "").ilike(needle)
            | func.coalesce(ScreentimeSignal.district_name, "").ilike(needle)
        )

    base = select(ScreentimeSignal)
    for cond in conditions:
        base = base.where(cond)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(ScreentimeSignal.discovered_at.desc(), ScreentimeSignal.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    signals = [
        {
            "id": row.id,
            "title": row.title,
            "summary": row.summary,
            "state": row.state,
            "level": row.level,
            "district_name": row.district_name,
            "status": row.status,
            "stance": row.stance,
            "amira_angle": row.amira_angle,
            "source_type": row.source_type,
            "source_url": row.source_url,
            "is_real_move": row.is_real_move,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
        }
        for row in rows
    ]

    return {
        "signals": signals,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "filters": {
            "state": state,
            "level": level,
            "status": status,
            "stance": stance,
            "source_type": source_type,
            "since": since.isoformat() if since else None,
            "q": q,
        },
    }


@router.post("/purge", dependencies=[Depends(require_owner)])
async def purge(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Owner-only scrub — TRUNCATE the screentime_* tables and nothing else.

    Hard-bound to ``SCREENTIME_TABLES`` inside the repository helper, so it can
    never touch a marketing / campaign / memory table.
    """
    result = await purge_screentime_data(session)
    await session.commit()
    _logger.info("screentime purge requested via API: %s", result.get("truncated"))
    return {"ok": True, **result}
