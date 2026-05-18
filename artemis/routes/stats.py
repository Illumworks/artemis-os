"""Stats router — /api/stats.

Endpoints:
  GET /api/stats/analytics  — V1 stub (zero-filled overview)
  GET /api/stats/providers  — real data from integrations.repository
"""

from __future__ import annotations

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

router = APIRouter(prefix="/api/stats", tags=["stats"])

_PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}


async def _provider_is_configured(session: AsyncSession, provider_id: str) -> bool:
    """A provider counts as configured if the resolver can produce a key —
    either from the DB-stored credential_configs row or from the env var
    (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, etc.).
    """
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
