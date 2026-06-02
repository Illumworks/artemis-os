"""Source gathering for daily brief generation.

Mirrors Node's _gatherSources — collects data from all available integrations
using asyncio.gather with return_exceptions=True so one failure doesn't tank
the whole brief.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.brief import repository
from artemis.brief.models import BriefSnapshot

logger = logging.getLogger(__name__)


async def _safe_jira(session: AsyncSession) -> dict[str, Any] | None:
    try:
        from artemis.routes.jira import jira_overview

        result = await jira_overview(session=session, _=None)
        return dict(result) if result else None
    except Exception:
        logger.debug("Jira source unavailable", exc_info=True)
        return None


async def _safe_calendar(session: AsyncSession) -> dict[str, Any] | None:
    try:
        from artemis.routes.calendar import get_calendar_overview

        result = await get_calendar_overview(session=session)
        return dict(result) if result else None
    except Exception:
        logger.debug("Calendar source unavailable", exc_info=True)
        return None


async def _safe_okr(session: AsyncSession) -> dict[str, Any] | None:
    try:
        from artemis.routes.okr import get_overview

        result = await get_overview(session=session)
        return dict(result) if result else None
    except Exception:
        logger.debug("OKR source unavailable", exc_info=True)
        return None


async def _safe_sessions() -> list[Any]:
    # Sessions route is a stub returning []. That's fine — return empty list.
    return []


async def _safe_memory(session: AsyncSession) -> list[Any]:
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        # Search with a global scope wildcard — use a broad scope that captures
        # general observations. We try a known scope kind first; if nothing
        # matches, the list is simply empty.
        scope = [Scope(scope_kind="global", scope_id="*")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query="work priorities focus today",
            limit=6,
            modes=["fts"],
        )
        return list(results)
    except Exception:
        logger.debug("Memory source unavailable", exc_info=True)
        return []


async def _safe_previous_brief(session: AsyncSession) -> BriefSnapshot | None:
    try:
        return await repository.get_latest_brief_snapshot(session)
    except Exception:
        logger.debug("Previous brief unavailable", exc_info=True)
        return None


async def _safe_slack_signals() -> dict[str, Any] | None:
    # J8 (Slack signals) runs in parallel — fall back gracefully if not shipped.
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:8000/api/slack/signals")
            if resp.status_code == 200:
                data = resp.json()
                return dict(data) if data else None
    except Exception:
        pass
    return None


async def gather_sources(session: AsyncSession) -> dict[str, Any]:
    """Gather all data sources concurrently. Missing sources return None / []."""
    (
        jira,
        calendar,
        slack,
        okr,
        sessions,
        memory,
        previous_brief,
    ) = await asyncio.gather(
        _safe_jira(session),
        _safe_calendar(session),
        _safe_slack_signals(),
        _safe_okr(session),
        _safe_sessions(),
        _safe_memory(session),
        _safe_previous_brief(session),
        return_exceptions=True,
    )

    def _unwrap(val: Any, default: Any) -> Any:
        if isinstance(val, BaseException):
            return default
        return val

    return {
        "jira": _unwrap(jira, None),
        "calendar": _unwrap(calendar, None),
        "slack": _unwrap(slack, None),
        "okr": _unwrap(okr, None),
        "sessions": _unwrap(sessions, []),
        "memory": _unwrap(memory, []),
        "previousBrief": _unwrap(previous_brief, None),
    }
