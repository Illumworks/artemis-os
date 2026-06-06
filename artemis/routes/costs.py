"""Cost summary endpoint — Phase 2 visibility dashboard.

GET /api/costs/summary

Returns a unified rollup of cost_events for the requested time window plus a
same-duration prior-period comparison.  All queries are read-only on cost_events.

Auth: Depends(require_token) — same as every other dashboard endpoint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/costs",
    tags=["costs"],
    dependencies=[Depends(require_token)],  # noqa: B008
)


# ── Pydantic response models ──────────────────────────────────────────────────


class WindowSchema(BaseModel):
    from_: datetime
    to: datetime


class TotalsSchema(BaseModel):
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cache_savings_usd: float
    calls: int


class PriorTotalsSchema(BaseModel):
    cost_usd: float
    calls: int


class TodaySchema(BaseModel):
    cost_usd: float
    avg_daily_cost_usd: float


class ByFeatureRow(BaseModel):
    feature_tag: str
    cost_usd: float
    share: float
    calls: int
    input_tokens: int
    output_tokens: int


class ByModelRow(BaseModel):
    provider: str
    model: str
    cost_usd: float
    share: float
    calls: int


class ByProviderPathRow(BaseModel):
    provider_path: str
    cost_usd: float
    share: float


class DailyRow(BaseModel):
    date: str
    cost_usd: float


class TopCallRow(BaseModel):
    id: int
    created_at: datetime
    feature_tag: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostSummaryResponse(BaseModel):
    window: dict[str, str]
    prior_window: dict[str, str]
    totals: TotalsSchema
    prior_totals: PriorTotalsSchema
    today: TodaySchema
    by_feature: list[ByFeatureRow]
    by_model: list[ByModelRow]
    by_provider_path: list[ByProviderPathRow]
    daily: list[DailyRow]
    top_calls: list[TopCallRow]


# ── Helper: build WHERE clause from filters ───────────────────────────────────


def _window_and_filter(
    from_dt: datetime,
    to_dt: datetime,
    feature_tags: list[str] | None,
    providers: list[str] | None,
    models: list[str] | None,
) -> tuple[str, dict[str, Any]]:
    """Return (WHERE clause fragment, params dict) for the given filters."""
    clauses = ["created_at >= :from_dt", "created_at < :to_dt"]
    params: dict[str, Any] = {"from_dt": from_dt, "to_dt": to_dt}

    if feature_tags:
        clauses.append("feature_tag = ANY(:feature_tags)")
        params["feature_tags"] = feature_tags
    if providers:
        clauses.append("provider = ANY(:providers)")
        params["providers"] = providers
    if models:
        clauses.append("model = ANY(:models)")
        params["models"] = models

    return " AND ".join(clauses), params


# ── Cache-savings math ────────────────────────────────────────────────────────
# cache_savings_usd = SUM(
#   cache_read_input_tokens * (input_rate_per_million - cache_read_rate_per_million)
# ) / 1_000_000
#
# This is the dollar difference between what would have been paid at full input
# rate vs what was actually paid at the cache-read rate.

_CACHE_SAVINGS_EXPR = """
    COALESCE(SUM(
        cache_read_input_tokens::float
        * (input_rate_per_million - cache_read_rate_per_million)
        / 1000000.0
    ), 0.0)
"""


# ── Main endpoint ─────────────────────────────────────────────────────────────


@router.get("/summary", response_model=CostSummaryResponse)
async def get_costs_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    feature_tag: str | None = Query(None),
    provider: str | None = Query(None),
    model: str | None = Query(None),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> CostSummaryResponse:
    """Unified cost rollup for the given time window.

    Defaults to current month-to-date vs same prior-month range.
    """
    now_utc = datetime.now(UTC)

    # Parse or default the window
    if from_:
        from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00"))
    else:
        from_dt = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now_utc

    # Prior window: same duration, shifted back by the window length
    window_duration = to_dt - from_dt
    prior_to_dt = from_dt
    prior_from_dt = from_dt - window_duration

    # Parse optional comma-separated filters
    feature_tags = [t.strip() for t in feature_tag.split(",")] if feature_tag else None
    providers = [p.strip() for p in provider.split(",")] if provider else None
    models = [m.strip() for m in model.split(",")] if model else None

    where_clause, base_params = _window_and_filter(from_dt, to_dt, feature_tags, providers, models)
    prior_where, prior_params = _window_and_filter(
        prior_from_dt, prior_to_dt, feature_tags, providers, models
    )

    # ── Totals ────────────────────────────────────────────────────────────────
    totals_sql = text(f"""
        SELECT
            COALESCE(SUM(cost_usd), 0.0)                   AS cost_usd,
            COALESCE(SUM(input_tokens), 0)                  AS input_tokens,
            COALESCE(SUM(output_tokens), 0)                 AS output_tokens,
            COALESCE(SUM(cache_creation_input_tokens), 0)   AS cache_creation_tokens,
            COALESCE(SUM(cache_read_input_tokens), 0)       AS cache_read_tokens,
            {_CACHE_SAVINGS_EXPR}                           AS cache_savings_usd,
            COUNT(*)                                         AS calls
        FROM cost_events
        WHERE {where_clause}
    """)
    row = (await session.execute(totals_sql, base_params)).one()
    totals = TotalsSchema(
        cost_usd=float(row.cost_usd),
        input_tokens=int(row.input_tokens),
        output_tokens=int(row.output_tokens),
        cache_creation_tokens=int(row.cache_creation_tokens),
        cache_read_tokens=int(row.cache_read_tokens),
        cache_savings_usd=float(row.cache_savings_usd),
        calls=int(row.calls),
    )

    # ── Prior totals ──────────────────────────────────────────────────────────
    prior_sql = text(f"""
        SELECT
            COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
            COUNT(*)                      AS calls
        FROM cost_events
        WHERE {prior_where}
    """)
    prior_row = (await session.execute(prior_sql, prior_params)).one()
    prior_totals = PriorTotalsSchema(
        cost_usd=float(prior_row.cost_usd),
        calls=int(prior_row.calls),
    )

    # ── Today ─────────────────────────────────────────────────────────────────
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # Elapsed days in the window (minimum 1 to avoid div-by-zero)
    elapsed_days = max(1, (to_dt - from_dt).days)

    today_sql = text("""
        SELECT COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM cost_events
        WHERE created_at >= :today_start AND created_at < :to_dt
    """)
    today_row = (
        await session.execute(today_sql, {"today_start": today_start, "to_dt": to_dt})
    ).one()
    today_cost = float(today_row.cost_usd)
    avg_daily = totals.cost_usd / elapsed_days

    today_obj = TodaySchema(cost_usd=today_cost, avg_daily_cost_usd=avg_daily)

    # ── By feature ────────────────────────────────────────────────────────────
    feat_sql = text(f"""
        SELECT
            feature_tag,
            COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COUNT(*)                         AS calls
        FROM cost_events
        WHERE {where_clause}
        GROUP BY feature_tag
        ORDER BY SUM(cost_usd) DESC
    """)
    feat_rows = (await session.execute(feat_sql, base_params)).all()
    total_cost_for_share = totals.cost_usd or 1.0  # avoid div-by-zero
    by_feature = [
        ByFeatureRow(
            feature_tag=r.feature_tag,
            cost_usd=float(r.cost_usd),
            share=round(float(r.cost_usd) / total_cost_for_share, 4),
            calls=int(r.calls),
            input_tokens=int(r.input_tokens),
            output_tokens=int(r.output_tokens),
        )
        for r in feat_rows
    ]

    # ── By model ──────────────────────────────────────────────────────────────
    model_sql = text(f"""
        SELECT
            provider,
            model,
            COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
            COUNT(*)                      AS calls
        FROM cost_events
        WHERE {where_clause}
        GROUP BY provider, model
        ORDER BY SUM(cost_usd) DESC
    """)
    model_rows = (await session.execute(model_sql, base_params)).all()
    by_model = [
        ByModelRow(
            provider=r.provider,
            model=r.model,
            cost_usd=float(r.cost_usd),
            share=round(float(r.cost_usd) / total_cost_for_share, 4),
            calls=int(r.calls),
        )
        for r in model_rows
    ]

    # ── By provider path ──────────────────────────────────────────────────────
    path_sql = text(f"""
        SELECT
            provider_path,
            COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM cost_events
        WHERE {where_clause}
        GROUP BY provider_path
        ORDER BY SUM(cost_usd) DESC
    """)
    path_rows = (await session.execute(path_sql, base_params)).all()
    by_provider_path = [
        ByProviderPathRow(
            provider_path=r.provider_path,
            cost_usd=float(r.cost_usd),
            share=round(float(r.cost_usd) / total_cost_for_share, 4),
        )
        for r in path_rows
    ]

    # ── Daily ─────────────────────────────────────────────────────────────────
    daily_sql = text(f"""
        SELECT
            DATE(created_at AT TIME ZONE 'UTC')  AS day,
            COALESCE(SUM(cost_usd), 0.0)         AS cost_usd
        FROM cost_events
        WHERE {where_clause}
        GROUP BY DATE(created_at AT TIME ZONE 'UTC')
        ORDER BY day ASC
    """)
    daily_rows = (await session.execute(daily_sql, base_params)).all()
    daily = [DailyRow(date=str(r.day), cost_usd=float(r.cost_usd)) for r in daily_rows]

    # ── Top calls (top 20 by cost desc) ──────────────────────────────────────
    top_sql = text(f"""
        SELECT
            id,
            created_at,
            feature_tag,
            provider,
            model,
            input_tokens,
            output_tokens,
            cost_usd
        FROM cost_events
        WHERE {where_clause}
        ORDER BY cost_usd DESC
        LIMIT 20
    """)
    top_rows = (await session.execute(top_sql, base_params)).all()
    top_calls = [
        TopCallRow(
            id=int(r.id),
            created_at=r.created_at,
            feature_tag=r.feature_tag,
            provider=r.provider,
            model=r.model,
            input_tokens=int(r.input_tokens),
            output_tokens=int(r.output_tokens),
            cost_usd=float(r.cost_usd),
        )
        for r in top_rows
    ]

    return CostSummaryResponse(
        window={
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        prior_window={
            "from": prior_from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": prior_to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        totals=totals,
        prior_totals=prior_totals,
        today=today_obj,
        by_feature=by_feature,
        by_model=by_model,
        by_provider_path=by_provider_path,
        daily=daily,
        top_calls=top_calls,
    )
