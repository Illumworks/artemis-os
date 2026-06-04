"""Phase 1 marketing intelligence — deterministic trend substrate.

All functions are pure async SQL/SQLAlchemy aggregations. No LLM. No new tables.
Snapshots are persisted to memory_observations via the established multi-scope
helper in artemis.builder.memory_carryover.

SCHEMA GAP — time-sensitivity deadline field:
  signal_queue.provenance is a free-form JSONB blob written by scouts. The only
  date-like fields observed in production are 'source_published_at' (a string
  timestamp in some provenance blobs, not universally present) and signal
  'created_at'. There is NO structured deadline column anywhere in signal_queue
  or districts. The spec notes "board votes / budget cycles already in signals"
  but this field does not exist in the current schema.

  Fallback used: signals are ranked by urgency_tier (critical > high > elevated >
  standard) and then by recency (created_at DESC). The 'deadline_proxy' returned
  is created_at, and 'deadline_source' is 'created_at_urgency_proxy'.

  Downstream workers: if a structured deadline field is added later (e.g.
  signal_queue.deadline_date TIMESTAMPTZ), replace the proxy logic in
  compute_time_sensitivity with a direct filter on that column.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.intel.schemas import (
    BucketCount,
    ComparablesResult,
    DistrictStub,
    DistrictVelocityRow,
    MomentumResult,
    TimeSensitiveSignalRow,
    TrendSnapshot,
    UrgencyBreakdown,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical urgency weights (must match urgency_tier values in models.py)
# ---------------------------------------------------------------------------
URGENCY_WEIGHTS: dict[str, float] = {
    "standard": 1.0,
    "elevated": 2.0,
    "high": 3.0,
    "critical": 5.0,
}

# Ordered list for time-sensitivity sorting (highest urgency first)
_URGENCY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "elevated": 2,
    "standard": 3,
}

# Status values that count as "active" signals for trend analysis
_ACTIVE_STATUSES = ("qualified", "approved")

# Default cap for comparable-district samples returned
_COMPARABLES_SAMPLE_LIMIT = 10


# ---------------------------------------------------------------------------
# Helper: build a DistrictStub from a row that has id/name/state/tier columns
# ---------------------------------------------------------------------------


def _stub(district_id: int, name: str, state: str | None, tier: str | None) -> DistrictStub:
    return DistrictStub(district_id=district_id, name=name, state=state, tier=tier)


# ---------------------------------------------------------------------------
# 1. compute_momentum
# ---------------------------------------------------------------------------


async def compute_momentum(
    session: AsyncSession,
    *,
    theme: str,
    region: str | None,
    as_of: datetime,
    window_days: int = 90,
    bucket_days: int = 7,
) -> MomentumResult:
    """Count signals in signal_queue grouped by week-bucket for a theme × region.

    Returns a time-series of buckets + period-over-period delta comparing the
    most-recent window_days against the prior equal-length window.

    Only 'qualified' and 'approved' signals are counted.
    Bucketing is by created_at (UTC, truncated to bucket_days boundaries).
    """
    window_start = as_of - timedelta(days=window_days)
    prior_start = as_of - timedelta(days=2 * window_days)

    # Raw SQL for date_trunc-style bucketing (arbitrary bucket_days via
    # FLOOR + epoch arithmetic so we don't require a calendar table)
    #   bucket_start = prior_start + floor((created_at - prior_start) / interval) * interval
    bucket_interval = f"{bucket_days} days"

    base_where = (
        "signal_status = ANY(:statuses) "
        "AND campaign_family = :theme "
        "AND created_at >= :prior_start "
        "AND created_at < :as_of "
    )
    region_clause = "AND state = :region " if region is not None else ""

    sql = text(
        f"""
        SELECT
            CAST(:prior_start AS timestamptz)
            + (FLOOR(EXTRACT(EPOCH FROM (created_at - CAST(:prior_start AS timestamptz)))
               / EXTRACT(EPOCH FROM INTERVAL '{bucket_interval}'))
               * INTERVAL '{bucket_interval}') AS bucket_start,
            COUNT(*) AS cnt
        FROM signal_queue
        WHERE {base_where}{region_clause}
        GROUP BY 1
        ORDER BY 1
        """
    )

    params: dict[str, Any] = {
        "statuses": list(_ACTIVE_STATUSES),
        "theme": theme,
        "prior_start": prior_start,
        "as_of": as_of,
    }
    if region is not None:
        params["region"] = region

    rows = (await session.execute(sql, params)).fetchall()

    # Build per-bucket counts
    buckets: list[BucketCount] = []
    current_window_count = 0
    prior_window_count = 0

    for row in rows:
        b_start: datetime = row[0]
        if b_start.tzinfo is None:
            b_start = b_start.replace(tzinfo=UTC)
        b_end = b_start + timedelta(days=bucket_days)
        count = int(row[1])
        buckets.append(BucketCount(bucket_start=b_start, bucket_end=b_end, count=count))
        if b_start >= window_start:
            current_window_count += count
        else:
            prior_window_count += count

    delta_ratio: float | None = None
    if prior_window_count > 0:
        delta_ratio = current_window_count / prior_window_count

    return MomentumResult(
        theme=theme,
        region=region,
        as_of=as_of,
        window_days=window_days,
        bucket_days=bucket_days,
        buckets=buckets,
        current_window_count=current_window_count,
        prior_window_count=prior_window_count,
        delta_ratio=delta_ratio,
    )


# ---------------------------------------------------------------------------
# 2. count_comparable_districts
# ---------------------------------------------------------------------------


async def count_comparable_districts(
    session: AsyncSession,
    *,
    theme: str,
    region: str | None,
    as_of: datetime,
    window_days: int = 90,
) -> ComparablesResult:
    """Count + sample districts with ≥1 active signal for the theme in the window.

    Excludes districts where on_skip_list=True or supported=False.
    Joins signal_queue to districts via resolved_district_id.
    Returns total count + up to _COMPARABLES_SAMPLE_LIMIT sample stubs.
    """
    window_start = as_of - timedelta(days=window_days)

    region_filter = "AND d.state = :region " if region is not None else ""

    sql = text(
        f"""
        SELECT DISTINCT d.id, d.name, d.state, d.tier
        FROM signal_queue sq
        JOIN districts d ON d.id = sq.resolved_district_id
        WHERE sq.signal_status = ANY(:statuses)
          AND sq.campaign_family = :theme
          AND sq.created_at >= :window_start
          AND sq.created_at < :as_of
          AND d.on_skip_list = FALSE
          AND d.supported = TRUE
          {region_filter}
        ORDER BY d.name
        """
    )

    params: dict[str, Any] = {
        "statuses": list(_ACTIVE_STATUSES),
        "theme": theme,
        "window_start": window_start,
        "as_of": as_of,
    }
    if region is not None:
        params["region"] = region

    rows = (await session.execute(sql, params)).fetchall()

    stubs = [_stub(r[0], r[1], r[2], r[3]) for r in rows]
    sample = stubs[:_COMPARABLES_SAMPLE_LIMIT]

    return ComparablesResult(
        theme=theme,
        region=region,
        as_of=as_of,
        window_days=window_days,
        comparable_count=len(stubs),
        sample_districts=sample,
    )


# ---------------------------------------------------------------------------
# 3. compute_velocity_ranking
# ---------------------------------------------------------------------------


async def compute_velocity_ranking(
    session: AsyncSession,
    *,
    as_of: datetime,
    window_days: int = 30,
    limit: int = 20,
) -> list[DistrictVelocityRow]:
    """Rank districts by (new signals in window) × urgency weight.

    Urgency weights from URGENCY_WEIGHTS constant (standard=1, elevated=2,
    high=3, critical=5). Only qualified/approved signals in window counted.
    Returns ranked rows (rank 1 = highest weighted score).
    """
    window_start = as_of - timedelta(days=window_days)

    # Embed weights as literals to avoid asyncpg type-inference issues with
    # float params inside a CASE/WHEN expression (see SQLAlchemy/asyncpg quirk:
    # SUM(CASE ... THEN $1 ...) fails when $1 has ambiguous type context).
    w_std = URGENCY_WEIGHTS["standard"]
    w_elev = URGENCY_WEIGHTS["elevated"]
    w_high = URGENCY_WEIGHTS["high"]
    w_crit = URGENCY_WEIGHTS["critical"]

    sql = text(
        f"""
        SELECT
            d.id,
            d.name,
            d.state,
            d.tier,
            COUNT(*) AS raw_count,
            SUM(CASE sq.urgency_tier
                    WHEN 'standard'  THEN {w_std}
                    WHEN 'elevated'  THEN {w_elev}
                    WHEN 'high'      THEN {w_high}
                    WHEN 'critical'  THEN {w_crit}
                    ELSE {w_std}
                END) AS weighted_score,
            SUM(CASE WHEN sq.urgency_tier = 'standard'  THEN 1 ELSE 0 END) AS cnt_standard,
            SUM(CASE WHEN sq.urgency_tier = 'elevated'  THEN 1 ELSE 0 END) AS cnt_elevated,
            SUM(CASE WHEN sq.urgency_tier = 'high'      THEN 1 ELSE 0 END) AS cnt_high,
            SUM(CASE WHEN sq.urgency_tier = 'critical'  THEN 1 ELSE 0 END) AS cnt_critical
        FROM signal_queue sq
        JOIN districts d ON d.id = sq.resolved_district_id
        WHERE sq.signal_status = ANY(:statuses)
          AND sq.created_at >= :window_start
          AND sq.created_at < :as_of
          AND d.supported = TRUE
          AND d.on_skip_list = FALSE
        GROUP BY d.id, d.name, d.state, d.tier
        ORDER BY weighted_score DESC, raw_count DESC, d.name ASC
        LIMIT :limit
        """
    )

    params: dict[str, Any] = {
        "statuses": list(_ACTIVE_STATUSES),
        "window_start": window_start,
        "as_of": as_of,
        "limit": limit,
    }

    rows = (await session.execute(sql, params)).fetchall()

    result: list[DistrictVelocityRow] = []
    for rank, row in enumerate(rows, start=1):
        d_id, d_name, d_state, d_tier = row[0], row[1], row[2], row[3]
        raw_count = int(row[4])
        weighted_score = float(row[5])
        cnt_std = int(row[6])
        cnt_elev = int(row[7])
        cnt_high = int(row[8])
        cnt_crit = int(row[9])
        result.append(
            DistrictVelocityRow(
                rank=rank,
                district=_stub(d_id, d_name, d_state, d_tier),
                raw_signal_count=raw_count,
                weighted_score=weighted_score,
                urgency_mix=UrgencyBreakdown(
                    standard=cnt_std,
                    elevated=cnt_elev,
                    high=cnt_high,
                    critical=cnt_crit,
                ),
            )
        )
    return result


# ---------------------------------------------------------------------------
# 4. compute_time_sensitivity
# ---------------------------------------------------------------------------


async def compute_time_sensitivity(
    session: AsyncSession,
    *,
    as_of: datetime,
    horizon_days: int = 60,
    limit: int = 20,
) -> list[TimeSensitiveSignalRow]:
    """Return signals whose urgency/recency suggest a near-term time window.

    SCHEMA GAP: signal_queue has no structured deadline field. provenance is a
    free-form JSONB blob; 'source_published_at' appears in some blobs but is not
    guaranteed present. No column stores board-vote or budget-cycle dates.

    Fallback: rank active signals by urgency_tier (critical first) then by
    recency (created_at DESC) within the horizon_days window. The 'deadline_proxy'
    field in results is set to created_at; 'deadline_source' is
    'created_at_urgency_proxy'. horizon_days is applied as a minimum age filter
    (created_at >= as_of - horizon_days) so only recently-flagged signals appear.

    If a structured deadline column is added to signal_queue in a future migration,
    replace this proxy logic with a direct filter on that column.
    """
    window_start = as_of - timedelta(days=horizon_days)

    sql = text(
        """
        SELECT
            sq.id,
            sq.headline,
            sq.campaign_family,
            sq.urgency_tier,
            sq.created_at,
            sq.provenance,
            d.id    AS d_id,
            d.name  AS d_name,
            d.state AS d_state,
            d.tier  AS d_tier
        FROM signal_queue sq
        LEFT JOIN districts d ON d.id = sq.resolved_district_id
        WHERE sq.signal_status = ANY(:statuses)
          AND sq.created_at >= :window_start
          AND sq.created_at < :as_of
        ORDER BY
            CASE sq.urgency_tier
                WHEN 'critical' THEN 0
                WHEN 'high'     THEN 1
                WHEN 'elevated' THEN 2
                ELSE 3
            END ASC,
            sq.created_at DESC
        LIMIT :limit
        """
    )

    params: dict[str, Any] = {
        "statuses": list(_ACTIVE_STATUSES),
        "window_start": window_start,
        "as_of": as_of,
        "limit": limit,
    }

    rows = (await session.execute(sql, params)).fetchall()

    result: list[TimeSensitiveSignalRow] = []
    for row in rows:
        sig_id = int(row[0])
        headline = str(row[1])
        campaign_family = str(row[2])
        urgency_tier = str(row[3])
        created_at: datetime = row[4]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        provenance_raw = row[5]
        d_id = row[6]
        d_name = row[7]
        d_state = row[8]
        d_tier = row[9]

        district_stub: DistrictStub | None = None
        if d_id is not None:
            district_stub = _stub(int(d_id), str(d_name), d_state, d_tier)

        # Attempt to extract provenance snippet for context
        prov_snippet: dict[str, Any] | None = None
        if isinstance(provenance_raw, dict):
            prov_snippet = {
                k: provenance_raw[k]
                for k in ("why_flagged", "source_published_at", "source_title")
                if k in provenance_raw
            } or None

        result.append(
            TimeSensitiveSignalRow(
                signal_id=sig_id,
                headline=headline,
                campaign_family=campaign_family,
                urgency_tier=urgency_tier,
                district=district_stub,
                deadline_proxy=created_at,
                deadline_source="created_at_urgency_proxy",
                provenance_snippet=prov_snippet,
            )
        )
    return result


# ---------------------------------------------------------------------------
# 5. persist_trend_snapshot
# ---------------------------------------------------------------------------


async def persist_trend_snapshot(
    session: AsyncSession,  # noqa: ARG001 — not used directly; _multi_scope_observation_write opens its own session
    *,
    snapshot: TrendSnapshot,
    primary_scope_kind: str,
    primary_scope_id: str,
    additional_scopes: list[tuple[str, str]],
) -> int:
    """Persist a TrendSnapshot as a memory observation via the multi-scope write path.

    Follows the same pattern as MC1–MC5 in memory_carryover.py.
    category='trend_snapshot', confidence_origin='deterministic_aggregation',
    source_quality=0.85.

    The content field combines the human-readable summary line + the full JSON
    payload so both FTS and semantic retrieval work on the observation.

    The session parameter is accepted for interface consistency but the actual
    write opens its own SessionLocal session (same as MC1–MC5).

    Returns the new observation_id.
    """
    from artemis.builder.memory_carryover import _multi_scope_observation_write

    payload_json = json.dumps(snapshot.payload, default=str, sort_keys=True)
    content = f"{snapshot.content_summary}\n\nPayload: {payload_json}"

    additional_scope_kinds = [k for k, _ in additional_scopes]
    additional_scope_ids = [i for _, i in additional_scopes]

    obs_id = await _multi_scope_observation_write(
        primary_scope_kind=primary_scope_kind,
        primary_scope_id=primary_scope_id,
        additional_scope_kinds=additional_scope_kinds,
        additional_scope_ids=additional_scope_ids,
        content=content,
        category="trend_snapshot",
        confidence_origin="deterministic_aggregation",
        source_quality=0.85,
    )
    logger.info(
        "trend_snapshot persisted: obs_id=%s kind=%s region=%s theme=%s",
        obs_id,
        snapshot.snapshot_kind,
        snapshot.region,
        snapshot.theme,
    )
    return obs_id
