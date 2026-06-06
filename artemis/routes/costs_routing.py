"""Cost Phase 3 — routing opportunities endpoint.

GET /api/costs/routing-opportunities

Reads cost_events (read-only) and computes what-if savings for each
feature_tag if tokens were routed to a cheaper provider.  Filters out
alternatives where monthly savings < $1 to avoid noise.  Availability-
aware: every alternative carries availability + setup_hint.

Auth: same require_token dependency as /api/costs/summary.
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
from artemis.costs.pricing import PRICING, get_rates
from artemis.costs.routing_candidates import (
    CANDIDATES,
    FEATURE_TIER,
    SETUP_HINTS,
    TRADEOFF_NOTES,
)
from artemis.marketing.routes._auth import require_token
from artemis.providers.health import probe_all_providers

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/costs",
    tags=["costs"],
    dependencies=[Depends(require_token)],  # noqa: B008
)

# Number of days to extrapolate to a full month
_MONTH_DAYS = 30.0

# Known providers (must match health module + pricing registry)
_KNOWN_PROVIDERS = set(PRICING.keys())


# ── Pydantic response models ──────────────────────────────────────────────────


class CascadeStep(BaseModel):
    provider: str
    model: str | None = None


class AlternativeSchema(BaseModel):
    provider: str
    model: str
    monthly_pace_usd: float
    savings_usd: float
    availability: str  # "available" | "setup_required"
    tradeoff_note: str
    apply_cascade: list[CascadeStep]
    setup_hint: str | None = None


class CurrentSchema(BaseModel):
    provider: str
    model: str
    cost_usd_in_window: float
    monthly_pace_usd: float


class OpportunitySchema(BaseModel):
    feature_tag: str
    current: CurrentSchema
    current_routing_is_override: bool
    alternatives: list[AlternativeSchema]


class MonthlyPaceSchema(BaseModel):
    current_total_usd: float
    projected_savings_usd_if_all_available_applied: float
    projected_total_usd: float


class RoutingOpportunitiesResponse(BaseModel):
    window: dict[str, str]
    monthly_pace: MonthlyPaceSchema
    opportunities: list[OpportunitySchema]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _days_in_window(from_dt: datetime, to_dt: datetime) -> float:
    """Elapsed days in the window, minimum 1 to avoid div-by-zero."""
    return max(1.0, (to_dt - from_dt).total_seconds() / 86400.0)


def _monthly_pace(cost_in_window: float, window_days: float) -> float:
    """Extrapolate cost to a full 30-day month."""
    return cost_in_window * (_MONTH_DAYS / window_days)


def _compute_alt_cost(
    input_tokens: int,
    output_tokens: int,
    alt_provider: str,
    alt_model: str,
    window_days: float,
) -> float:
    """Compute monthly-paced cost for an alternative provider/model."""
    try:
        rates = get_rates(alt_provider, alt_model)
    except KeyError:
        # lm-studio / codex have zero rates — get_rates handles them; fall back to zero.
        return 0.0
    cost_in_window = (
        input_tokens * rates["input"] / 1_000_000 + output_tokens * rates["output"] / 1_000_000
    )
    return _monthly_pace(cost_in_window, window_days)


def _build_apply_cascade(
    primary_provider: str,
    primary_model: str,
    tier: str,
    current_provider: str,
    current_model: str,
    available_providers: set[str],
) -> list[CascadeStep]:
    """Build a ≥2-step cascade for the Apply button.

    Structure: primary → (a good available fallback) → current routing.
    The current routing is always the final fallback (hard constraint).
    """
    steps: list[CascadeStep] = [CascadeStep(provider=primary_provider, model=primary_model)]

    # Pick a secondary fallback (different from primary, preferably available)
    candidates_for_tier = CANDIDATES.get(tier, [])
    for prov, model in candidates_for_tier:
        if prov == primary_provider:
            continue
        if prov in available_providers or prov == "anthropic":
            steps.append(CascadeStep(provider=prov, model=model))
            break
    else:
        # No suitable secondary found — use claude-code haiku as universal fallback
        steps.append(CascadeStep(provider="claude-code", model="claude-haiku-4-5-20251001"))

    # Final step: current routing (lossless fallback)
    final = CascadeStep(provider=current_provider, model=current_model)
    if not any(s.provider == current_provider for s in steps):
        steps.append(final)

    # Guard: must be at least 2 steps
    if len(steps) < 2:
        steps.append(final)

    return steps


# ── Main endpoint ─────────────────────────────────────────────────────────────


@router.get("/routing-opportunities", response_model=RoutingOpportunitiesResponse)
async def get_routing_opportunities(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> RoutingOpportunitiesResponse:
    """Compute routing alternatives for each feature_tag in the window.

    Returns the top 10 opportunities (by top-alternative savings) with
    availability filtering so every Apply button is grounded.
    """
    now_utc = datetime.now(UTC)

    # Parse or default the window (default: last 30 days)
    if from_:
        from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00"))
    else:
        from_dt = datetime.fromtimestamp(now_utc.timestamp() - 30 * 86400, tz=UTC)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now_utc

    window_days = _days_in_window(from_dt, to_dt)

    # ── Step 1: aggregate cost_events by (feature_tag, provider, model) ──────
    agg_sql = text("""
        SELECT
            feature_tag,
            provider,
            model,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cost_usd), 0.0)    AS cost_usd
        FROM cost_events
        WHERE created_at >= :from_dt AND created_at < :to_dt
        GROUP BY feature_tag, provider, model
        ORDER BY SUM(cost_usd) DESC
    """)
    rows = (await session.execute(agg_sql, {"from_dt": from_dt, "to_dt": to_dt})).all()

    # ── Step 2: probe provider health once (parallelised inside the module) ───
    health_records = await probe_all_providers()
    available_providers: set[str] = {h["provider"] for h in health_records if h.get("available")}

    # ── Step 3: check which features have active overrides ────────────────────
    override_result = await session.execute(
        text("SELECT feature_tag FROM feature_routing_overrides WHERE active = true")
    )
    overridden_features: set[str] = {r[0] for r in override_result.all()}

    # ── Step 4: build per-feature aggregates ──────────────────────────────────
    # Group rows by feature_tag; for each feature pick the dominant model
    # (highest cost_usd in window) as the "current" routing.
    feature_data: dict[str, dict[str, Any]] = {}
    for row in rows:
        tag = row.feature_tag
        if tag not in feature_data:
            feature_data[tag] = {
                "provider": row.provider,
                "model": row.model,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": float(row.cost_usd),
            }
        else:
            # Accumulate tokens/cost from secondary (provider, model) combos
            feature_data[tag]["input_tokens"] += int(row.input_tokens)
            feature_data[tag]["output_tokens"] += int(row.output_tokens)
            feature_data[tag]["cost_usd"] += float(row.cost_usd)

    # ── Step 5: compute alternatives for each feature_tag ────────────────────
    opportunities: list[OpportunitySchema] = []

    for tag, data in feature_data.items():
        tier = FEATURE_TIER.get(tag, "low_stakes")
        candidate_list = CANDIDATES.get(tier, [])
        current_monthly = _monthly_pace(data["cost_usd"], window_days)

        alternatives: list[AlternativeSchema] = []
        for alt_provider, alt_model in candidate_list:
            # Skip if same as current dominant provider+model
            if alt_provider == data["provider"] and alt_model == data["model"]:
                continue

            alt_monthly = _compute_alt_cost(
                data["input_tokens"],
                data["output_tokens"],
                alt_provider,
                alt_model,
                window_days,
            )
            savings = current_monthly - alt_monthly

            # Filter noise: skip if savings < $1/mo
            if savings < 1.0:
                continue

            is_available = alt_provider in available_providers
            availability = "available" if is_available else "setup_required"
            setup_hint = None if is_available else SETUP_HINTS.get(alt_provider)
            tradeoff = TRADEOFF_NOTES.get((alt_provider, alt_model), "")

            apply_cascade = _build_apply_cascade(
                primary_provider=alt_provider,
                primary_model=alt_model,
                tier=tier,
                current_provider=data["provider"],
                current_model=data["model"],
                available_providers=available_providers,
            )

            alternatives.append(
                AlternativeSchema(
                    provider=alt_provider,
                    model=alt_model,
                    monthly_pace_usd=round(alt_monthly, 4),
                    savings_usd=round(savings, 4),
                    availability=availability,
                    tradeoff_note=tradeoff,
                    apply_cascade=apply_cascade,
                    setup_hint=setup_hint,
                )
            )

        if not alternatives:
            continue

        # Sort alternatives by savings descending
        alternatives.sort(key=lambda a: a.savings_usd, reverse=True)

        opportunities.append(
            OpportunitySchema(
                feature_tag=tag,
                current=CurrentSchema(
                    provider=data["provider"],
                    model=data["model"],
                    cost_usd_in_window=round(data["cost_usd"], 4),
                    monthly_pace_usd=round(current_monthly, 4),
                ),
                current_routing_is_override=tag in overridden_features,
                alternatives=alternatives,
            )
        )

    # ── Step 6: sort by top alternative savings, cap at 10 ───────────────────
    opportunities.sort(
        key=lambda o: o.alternatives[0].savings_usd if o.alternatives else 0,
        reverse=True,
    )
    opportunities = opportunities[:10]

    # ── Step 7: build monthly pace summary ───────────────────────────────────
    total_monthly = sum(o.current.monthly_pace_usd for o in opportunities)
    savings_available = sum(
        o.alternatives[0].savings_usd
        for o in opportunities
        if o.alternatives and o.alternatives[0].availability == "available"
    )

    return RoutingOpportunitiesResponse(
        window={
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        monthly_pace=MonthlyPaceSchema(
            current_total_usd=round(total_monthly, 4),
            projected_savings_usd_if_all_available_applied=round(savings_available, 4),
            projected_total_usd=round(total_monthly - savings_available, 4),
        ),
        opportunities=opportunities,
    )
