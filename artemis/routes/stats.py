"""Stats router — /api/stats.

Endpoints:
  GET /api/stats/analytics      — V1 stub (zero-filled overview)
  GET /api/stats/agent-metrics  — real aggregation from agent_runs table
  GET /api/stats/providers      — real data from integrations.repository
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import (
    MissingProviderConfigError,
    resolve_anthropic_config,
    resolve_gemini_config,
    resolve_openai_config,
)
from artemis.marketing.routes._auth import require_token
from artemis.providers import list_providers
from artemis.providers._bin_path import find_cli_binary

router = APIRouter(prefix="/api/stats", tags=["stats"])

_PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "claude-code": "Claude Code CLI",
    "codex": "Codex CLI",
    "gemini": "Gemini",
    "lm-studio": "LM Studio",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}

_LM_STUDIO_MODELS_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1") + "/models"


async def _lm_studio_is_reachable() -> bool:
    """Return True if the LM Studio local server responds within 500 ms."""
    base_url = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    url = f"{base_url}/models"
    try:
        async with httpx.AsyncClient() as client:
            resp = await asyncio.wait_for(
                client.get(url, timeout=0.5),
                timeout=1.0,
            )
        return resp.is_success
    except Exception:
        return False


async def _provider_is_configured(session: AsyncSession, provider_id: str) -> bool:
    """A provider counts as configured if the resolver can produce a key —
    either from the DB-stored credential_configs row or from the env var
    (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, etc.).

    CLI providers (claude-code, codex) are configured if the binary is found.
    LM Studio is configured if the local server is reachable.
    """
    # CLI subscription providers — binary presence = configured
    if provider_id == "claude-code":
        return find_cli_binary("claude") is not None
    if provider_id == "codex":
        return find_cli_binary("codex") is not None
    # LM Studio — local server reachability = configured
    if provider_id == "lm-studio":
        return await _lm_studio_is_reachable()

    try:
        if provider_id == "anthropic":
            await resolve_anthropic_config(session)
            return True
        if provider_id == "gemini":
            await resolve_gemini_config(session)
            return True
        if provider_id == "openai":
            await resolve_openai_config(session)
            return True
    except MissingProviderConfigError:
        return False
    # Fallback for providers without a dedicated resolver: just check DB.
    config = await repo.get_provider_config(session, provider_id)
    return bool(config and any(v and str(v).strip() for v in config.values()))


# ── Response models ───────────────────────────────────────────────────────────


class AnalyticsOverview(BaseModel):
    sessions: int
    messages: int
    tokens: int


class AnalyticsOut(BaseModel):
    overview: AnalyticsOverview
    tokens_today: int
    cost_today_usd: float
    runs_today: int


class ProviderStatusOut(BaseModel):
    provider_id: str
    name: str
    configured: bool
    healthy: bool | None


class AgentMetricsOverview(BaseModel):
    total_runs: int = 0
    completed: int = 0
    total_cost: float = 0.0
    avg_duration: float = 0.0
    avg_turns: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class AgentMetricsOut(BaseModel):
    overview: AgentMetricsOverview
    agents: list[dict[str, Any]] = []
    # Preserve the Node-compatible JSON wire shape for /api/stats/agent-metrics.
    byType: list[dict[str, Any]] = []  # noqa: N815
    daily: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/analytics", response_model=AnalyticsOut)
async def stats_analytics(
    project_path: str | None = Query(default=None),
) -> AnalyticsOut:
    """Return zero-filled analytics stub (V1)."""
    return AnalyticsOut(
        overview=AnalyticsOverview(sessions=0, messages=0, tokens=0),
        tokens_today=0,
        cost_today_usd=0.0,
        runs_today=0,
    )


@router.get("/agent-metrics", response_model=AgentMetricsOut)
async def stats_agent_metrics(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> AgentMetricsOut:
    """Aggregate agent run health metrics from the agent_runs table.

    Design notes:
    - Ephemeral runs (is_ephemeral = true) are EXCLUDED from all aggregates.
      These are internal/tooling runs that inflate counts without reflecting real
      agent health.  The exclusion mirrors list_agent_runs() in builders/repository.py.
    - Success status is 'completed' (not 'succeeded'); confirmed from executor.py.
    - avg_cost / total_cost are estimated via Sonnet-4.6 blended rates
      ($3/M input, $15/M output).  The agent_runs table does not store the model
      name, so we use the project default.  Rates defined in artemis/builders/_cost.py.
    - avg_duration is MILLISECONDS (float) — formatDuration() in the JS consumer expects ms
      (value<1000 → "Nms", else /1000 → seconds/minutes/hours).
    - recent[] returns the 50 most recent non-ephemeral runs, oldest-first within
      that window so the frontend .filter().slice(0,3) gets the 3 newest per agent.
    - last_run_at is included per-agent row for the JS consumer at
      operations-shell.js:1075.
    """
    # ── per-agent aggregate ───────────────────────────────────────────────────
    # Cost estimate constants: Sonnet-4.6 rates ($3/M input, $15/M output)
    # $3/1_000_000 = 0.000003  /  $15/1_000_000 = 0.000015
    agents_sql = sa_text(
        """
        SELECT
            ar.agent_id,
            COALESCE(a.name, ar.agent_id)           AS agent_title,
            COUNT(*)                                  AS runs,
            COUNT(*) FILTER (WHERE ar.status = 'completed')
                                                      AS successes,
            AVG(
                EXTRACT(EPOCH FROM (ar.completed_at - ar.started_at)) * 1000
            ) FILTER (WHERE ar.completed_at IS NOT NULL)
                                                      AS avg_duration,
            AVG(
                ar.cost_input_tokens  * 0.000003
              + ar.cost_output_tokens * 0.000015
            )                                         AS avg_cost,
            SUM(
                ar.cost_input_tokens  * 0.000003
              + ar.cost_output_tokens * 0.000015
            )                                         AS total_cost,
            SUM(ar.cost_input_tokens)                 AS total_input_tokens,
            SUM(ar.cost_output_tokens)                AS total_output_tokens,
            MAX(ar.started_at)                        AS last_run_at
        FROM agent_runs ar
        LEFT JOIN agents a ON a.agent_id = ar.agent_id
        WHERE ar.is_ephemeral = false
        GROUP BY ar.agent_id, a.name
        ORDER BY runs DESC
        """
    )

    agents_result = await session.execute(agents_sql)
    agents_rows = agents_result.fetchall()

    agents: list[dict[str, Any]] = [
        {
            "agent_id": row.agent_id,
            "agent_title": row.agent_title,
            "runs": int(row.runs),
            "successes": int(row.successes),
            "avg_duration": float(row.avg_duration) if row.avg_duration is not None else None,
            "avg_cost": float(row.avg_cost) if row.avg_cost is not None else None,
            "total_cost": float(row.total_cost) if row.total_cost is not None else 0.0,
            "total_input_tokens": int(row.total_input_tokens) if row.total_input_tokens else 0,
            "total_output_tokens": int(row.total_output_tokens) if row.total_output_tokens else 0,
            "last_run_at": (
                row.last_run_at.isoformat()
                if row.last_run_at is not None
                else None
            ),
        }
        for row in agents_rows
    ]

    # ── overview ──────────────────────────────────────────────────────────────
    total_runs = sum(r["runs"] for r in agents)
    completed = sum(r["successes"] for r in agents)
    total_cost = sum(r["total_cost"] for r in agents)
    total_input_tokens = sum(r["total_input_tokens"] for r in agents)
    total_output_tokens = sum(r["total_output_tokens"] for r in agents)

    # Weighted average duration across all agents that have duration data
    duration_rows = [r for r in agents if r["avg_duration"] is not None]
    if duration_rows:
        # Weight by run count for a true mean
        weighted_duration = sum(r["avg_duration"] * r["runs"] for r in duration_rows)
        duration_weight = sum(r["runs"] for r in duration_rows)
        avg_duration_overall = weighted_duration / duration_weight if duration_weight else 0.0
    else:
        avg_duration_overall = 0.0

    overview = AgentMetricsOverview(
        total_runs=total_runs,
        completed=completed,
        total_cost=total_cost,
        avg_duration=avg_duration_overall,
        avg_turns=0.0,  # turns not tracked in agent_runs
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
    )

    # ── byType — group by the agent_id prefix (e.g. "marketing.scout") ────────
    # Derive a simple type label from the first two dot-segments of agent_id.
    by_type_map: dict[str, dict[str, Any]] = {}
    for row in agents:
        aid = row["agent_id"] or ""
        parts = aid.split(".")
        type_key = ".".join(parts[:2]) if len(parts) >= 2 else aid
        if type_key not in by_type_map:
            by_type_map[type_key] = {"type": type_key, "runs": 0, "successes": 0}
        by_type_map[type_key]["runs"] += row["runs"]
        by_type_map[type_key]["successes"] += row["successes"]
    by_type = sorted(by_type_map.values(), key=lambda x: x["runs"], reverse=True)

    # ── daily — last 30 days run counts ──────────────────────────────────────
    daily_sql = sa_text(
        """
        SELECT
            DATE(started_at AT TIME ZONE 'UTC') AS day,
            COUNT(*)                             AS runs,
            COUNT(*) FILTER (WHERE status = 'completed') AS successes
        FROM agent_runs
        WHERE is_ephemeral = false
          AND started_at >= NOW() - INTERVAL '30 days'
        GROUP BY day
        ORDER BY day ASC
        """
    )
    daily_result = await session.execute(daily_sql)
    daily: list[dict[str, Any]] = [
        {
            "day": str(row.day),
            "runs": int(row.runs),
            "successes": int(row.successes),
        }
        for row in daily_result.fetchall()
    ]

    # ── recent — last 50 non-ephemeral runs (newest first) ──────────────────
    # The JS consumer filters by agent_id then slices to 3, so include enough
    # rows to cover all active agents.  50 is a reasonable upper bound.
    recent_sql = sa_text(
        """
        SELECT
            ar.run_id,
            ar.agent_id,
            COALESCE(a.name, ar.agent_id)  AS agent_title,
            ar.status,
            EXTRACT(EPOCH FROM ar.started_at)::bigint AS started_at,
            EXTRACT(EPOCH FROM ar.completed_at - ar.started_at) AS duration_seconds,
            ar.error
        FROM agent_runs ar
        LEFT JOIN agents a ON a.agent_id = ar.agent_id
        WHERE ar.is_ephemeral = false
        ORDER BY ar.started_at DESC
        LIMIT 50
        """
    )
    recent_result = await session.execute(recent_sql)
    recent: list[dict[str, Any]] = [
        {
            "run_id": row.run_id,
            "agent_id": row.agent_id,
            "agent_title": row.agent_title,
            "status": row.status,
            "started_at": int(row.started_at) if row.started_at is not None else None,
            "duration_seconds": (
                float(row.duration_seconds) if row.duration_seconds is not None else None
            ),
            "error": row.error,
        }
        for row in recent_result.fetchall()
    ]

    return AgentMetricsOut(
        overview=overview,
        agents=agents,
        byType=by_type,
        daily=daily,
        recent=recent,
    )


@router.get("/providers", response_model=list[ProviderStatusOut])
async def stats_providers(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
    _: None = Depends(require_token),  # noqa: B008
) -> list[ProviderStatusOut]:
    """Return LLM provider configuration status from DB."""
    results: list[ProviderStatusOut] = []
    for provider_id in list_providers():
        configured = await _provider_is_configured(session, provider_id)
        results.append(
            ProviderStatusOut(
                provider_id=provider_id,
                name=_PROVIDER_NAMES.get(provider_id, provider_id.title()),
                configured=configured,
                healthy=None,
            )
        )
    return results


@router.get("/alerts")
async def stats_alerts() -> dict[str, object]:
    """Return active alert list (stub — alert system not yet implemented)."""
    return {"alerts": [], "count": 0}
