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

        # me_only=True keeps the brief scoped to Jon's own tickets regardless
        # of any team_members configuration on the board.
        result = await jira_overview(session=session, _=None, me_only=True)
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


async def _safe_engagement_weights(session: AsyncSession) -> dict[str, float]:
    """Return brief-item engagement weights from memory observations.

    Reads ``brief_reaction:*`` observations from the ``agent:floating-artemis``
    scope — the same scope that ``record_reaction`` writes to.  Returns an
    empty dict (neutral, no effect) on any failure.
    """
    try:
        from artemis.proactivity.brief_reactions import read_engagement_weights

        return await read_engagement_weights(session)
    except Exception:
        logger.debug("Engagement weights unavailable", exc_info=True)
        return {}


async def _safe_brief_exclusions(session: AsyncSession) -> set[str]:
    """Return the set of Jira ticket keys Jon has asked to suppress from the brief.

    Reads memory observations in the ``agent:floating-artemis`` scope — the same
    scope that ``set_brief_exclusion`` / ``clear_brief_exclusion`` write to.
    A ticket is excluded if there is a ``brief_exclusion:<KEY>`` observation
    with no later ``brief_exclusion:<KEY> cleared`` observation.
    """
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        scope = [Scope(scope_kind="agent", scope_id="floating-artemis")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query="brief_exclusion",
            limit=50,
            modes=["fts"],
        )

        # Build the net exclusion set: the last write per key wins.
        # observations come back newest-first from FTS; iterate to find the state.
        seen: dict[str, str] = {}  # ticket_key -> "excluded" | "cleared"
        _PREFIX = "brief_exclusion:"
        for obs in results:
            content = obs.content or ""
            if not content.startswith(_PREFIX):
                continue
            rest = content[len(_PREFIX):]
            parts = rest.split(None, 1)
            ticket_key = parts[0].upper() if parts else ""
            if not ticket_key:
                continue
            state = "cleared" if (len(parts) > 1 and "cleared" in parts[1]) else "excluded"
            # Only record the first (newest) observation per key.
            if ticket_key not in seen:
                seen[ticket_key] = state

        return {k for k, v in seen.items() if v == "excluded"}
    except Exception:
        logger.debug("Brief exclusions unavailable", exc_info=True)
        return set()


async def gather_sources(session: AsyncSession) -> dict[str, Any]:
    """Gather all data sources concurrently. Missing sources return None / [].

    Each session-using source opens its OWN session via ``_own``: a single
    AsyncSession is NOT safe for the concurrent reads ``asyncio.gather`` issues
    (it raises "This session is provisioning a new connection; concurrent
    operations are not permitted", silently dropping sources). The ``session``
    arg is retained for API compatibility but is not shared across the gathered
    coroutines.
    """
    import artemis.db as _db

    async def _own(fn: Any) -> Any:
        async with _db.SessionLocal() as own_session:
            return await fn(own_session)

    (
        jira,
        calendar,
        slack,
        okr,
        sessions,
        memory,
        previous_brief,
        brief_exclusions,
        engagement_weights,
    ) = await asyncio.gather(
        _own(_safe_jira),
        _own(_safe_calendar),
        _safe_slack_signals(),
        _own(_safe_okr),
        _safe_sessions(),
        _own(_safe_memory),
        _own(_safe_previous_brief),
        _own(_safe_brief_exclusions),
        _own(_safe_engagement_weights),
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
        # Private key consumed by _build_context_string — not passed to LLM directly.
        "_excluded_ticket_keys": _unwrap(brief_exclusions, set()),
        # Private key consumed by generator — not passed to LLM directly.
        "_engagement_weights": _unwrap(engagement_weights, {}),
    }
