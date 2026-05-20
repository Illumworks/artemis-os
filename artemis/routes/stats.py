"""Stats router — /api/stats.

Endpoints:
  GET /api/stats/analytics  — V1 stub (zero-filled overview)
  GET /api/stats/providers  — real data from integrations.repository
"""

from __future__ import annotations

import asyncio
import os

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
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

_LM_STUDIO_MODELS_URL = os.environ.get(
    "LM_STUDIO_BASE_URL", "http://localhost:1234/v1"
) + "/models"


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
    agents: list[dict] = []
    byType: list[dict] = []
    daily: list[dict] = []
    recent: list[dict] = []


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
async def stats_agent_metrics() -> AgentMetricsOut:
    """Return empty-state agent metrics stub.

    Shape matches frontend consumer in public/js/features/agent-monitor.js.
    # TODO(J11-followup): wire real metrics from agent_runs table.
    """
    return AgentMetricsOut(overview=AgentMetricsOverview())


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
