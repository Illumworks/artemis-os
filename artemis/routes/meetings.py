"""Meetings router — /api/meetings.

Endpoints:
  GET  /api/meetings/overview         — yesterday's + today's past meetings (Focus data)
  GET  /api/meetings/list             — date-range list
  GET  /api/meetings/{id}             — full transcript + attendees

All return {"status": "not_connected"} if no active Granola integration.
Granola integration is provided by J6a — GranolaClient + GranolaProvider.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.granola.client import GranolaAPIError, GranolaClient
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/meetings",
    tags=["meetings"],
    dependencies=[Depends(require_token)],
)

_NOT_CONNECTED: dict[str, Any] = {"status": "not_connected", "provider": "granola"}


async def _get_granola_client(session: AsyncSession) -> GranolaClient | None:
    """Return a configured GranolaClient for the active integration, or None."""
    rows = await repo.list_active(session, provider="granola")
    if not rows:
        return None
    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    except Exception:
        logger.warning("Failed to decrypt Granola credentials")
        return None

    return GranolaClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
    )


def _meeting_to_dict(meeting: Any) -> dict[str, Any]:
    return {
        "id": meeting.id,
        "title": meeting.title,
        "date": meeting.date_raw,
        "date_ms": meeting.date_ms,
        "participants": meeting.participants,
    }


@router.get("/overview")
async def get_meetings_overview(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return yesterday's and today's past meetings with summaries.

    This is the data the Focus page needs: what happened yesterday and
    what has already happened today. Sorted by start time ascending.
    """
    client = await _get_granola_client(session)
    if client is None:
        return _NOT_CONNECTED

    try:
        # Use last_7_days to capture both yesterday and today
        meetings = await client.list_meetings(time_range="last_7_days")
    except GranolaAPIError as exc:
        if exc.status == 401:
            return {"status": "not_connected", "provider": "granola", "reason": "auth_expired"}
        logger.warning("Granola list_meetings error: %s", exc)
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.warning("Granola overview error: %s", exc)
        return {"status": "error", "error": str(exc)}

    now_ms = int(time.time() * 1000)
    today_start = int(
        datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=UTC).timestamp() * 1000
    )
    yesterday_start = today_start - 86_400_000

    # Yesterday: full day; Today: only past meetings (start <= now)
    relevant = [
        m
        for m in meetings
        if (yesterday_start <= m.date_ms < today_start)  # yesterday
        or (today_start <= m.date_ms <= now_ms)  # today so far
    ]
    relevant.sort(key=lambda m: m.date_ms)

    return {
        "status": "connected",
        "provider": "granola",
        "meetings": [_meeting_to_dict(m) for m in relevant],
        "date": date.today().isoformat(),
    }


@router.get("/list")
async def list_meetings(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, le=200),
    time_range: str = Query(default="last_30_days"),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a date-range list of meetings.

    If from/to are not provided, time_range is used (last_30_days, last_7_days, this_week).
    """
    client = await _get_granola_client(session)
    if client is None:
        return _NOT_CONNECTED

    valid_ranges = {"last_30_days", "last_7_days", "this_week"}
    if time_range not in valid_ranges:
        time_range = "last_30_days"

    try:
        meetings = await client.list_meetings(time_range=time_range, limit=limit)
    except GranolaAPIError as exc:
        if exc.status == 401:
            return {"status": "not_connected", "provider": "granola", "reason": "auth_expired"}
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.warning("Granola list_meetings error: %s", exc)
        return {"status": "error", "error": str(exc)}

    # Optional client-side date filtering when from/to supplied
    result = meetings
    if from_date:
        try:
            from_ms = int(datetime.fromisoformat(from_date).timestamp() * 1000)
            result = [m for m in result if m.date_ms >= from_ms]
        except ValueError:
            pass
    if to_date:
        try:
            to_ms = int(datetime.fromisoformat(to_date).timestamp() * 1000)
            result = [m for m in result if m.date_ms <= to_ms]
        except ValueError:
            pass

    return {
        "status": "connected",
        "provider": "granola",
        "meetings": [_meeting_to_dict(m) for m in result],
        "count": len(result),
    }


@router.get("/{granola_id}/summary")
async def get_meeting_summary(
    granola_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the stored post-meeting summary for a Granola meeting ID.

    Returns 404 if no summary has been generated yet. The J6c Actions tab
    reads from here first (instant) and falls back to live extraction if missing.
    """
    from sqlalchemy import select as sa_select

    from artemis.meetings.models import MeetingSummary

    result = await session.execute(
        sa_select(MeetingSummary).where(MeetingSummary.granola_id == granola_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail={"error": "summary_not_found", "granola_id": granola_id},
        )

    return {
        "granola_id": row.granola_id,
        "gcal_event_id": row.gcal_event_id,
        "title": row.title,
        "summary": row.summary,
        "action_items": row.action_items or [],
        "raw_input_id": row.raw_input_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return full transcript and attendees for a single meeting."""
    client = await _get_granola_client(session)
    if client is None:
        return _NOT_CONNECTED

    try:
        detail = await client.get_meeting(meeting_id)
    except GranolaAPIError as exc:
        if exc.status == 401:
            return {"status": "not_connected", "provider": "granola", "reason": "auth_expired"}
        if exc.status == 404:
            return {"status": "not_found", "meeting_id": meeting_id}
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.warning("Granola get_meeting error: %s", exc)
        return {"status": "error", "error": str(exc)}

    if not detail:
        return {"status": "not_found", "meeting_id": meeting_id}

    return {"status": "connected", "provider": "granola", "meeting_id": meeting_id, **detail}


# ── Legacy /api/granola/* compat ──────────────────────────────────────────────
# The frontend (`public/js/core/api.js`) still calls Node-era paths. Rather than
# touch a wide swath of frontend code, expose thin aliases that re-shape J6a
# responses into the `{connected: true, meetings: [...]}` envelope the UI wants.
# Long-term cleanup: migrate frontend to /api/meetings/* + delete this section.

granola_compat_router = APIRouter(
    prefix="/api/granola",
    tags=["granola-compat"],
    dependencies=[Depends(require_token)],
)


def _ok_envelope(meetings_payload: list[dict[str, Any]]) -> dict[str, Any]:
    return {"connected": True, "meetings": meetings_payload}


def _disconnected_envelope(reason: str = "not_connected") -> dict[str, Any]:
    return {"connected": False, "reason": reason, "meetings": []}


@granola_compat_router.get("/meetings")
async def granola_meetings(
    range: str = Query(default="last_30_days", alias="range"),
    limit: int = Query(default=50, le=200),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Legacy alias for /api/meetings/list with the {connected,meetings} shape."""
    client = await _get_granola_client(session)
    if client is None:
        return _disconnected_envelope()

    valid = {"last_30_days", "last_7_days", "this_week"}
    if range not in valid:
        range = "last_30_days"

    try:
        meetings = await client.list_meetings(time_range=range, limit=limit)
    except GranolaAPIError as exc:
        if exc.status == 401:
            return _disconnected_envelope("auth_expired")
        return _disconnected_envelope(f"error: {exc}")
    except Exception as exc:
        logger.warning("Granola meetings compat error: %s", exc)
        return _disconnected_envelope(f"error: {exc}")

    return _ok_envelope([_meeting_to_dict(m) for m in meetings])


@granola_compat_router.get("/transcript/{meeting_id}")
async def granola_transcript(
    meeting_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Legacy alias for /api/meetings/{id}."""
    client = await _get_granola_client(session)
    if client is None:
        return {"connected": False, "reason": "not_connected"}
    try:
        detail = await client.get_meeting(meeting_id)
    except GranolaAPIError as exc:
        if exc.status == 401:
            return {"connected": False, "reason": "auth_expired"}
        if exc.status == 404:
            return {"connected": True, "found": False, "meeting_id": meeting_id}
        return {"connected": True, "error": str(exc)}
    if not detail:
        return {"connected": True, "found": False, "meeting_id": meeting_id}
    return {"connected": True, "found": True, "meeting_id": meeting_id, **detail}


@granola_compat_router.post("/search")
async def granola_search(
    payload: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Semantic search over meetings — returns raw result text with citations."""
    client = await _get_granola_client(session)
    if client is None:
        return {"connected": False, "result": ""}
    query = str(payload.get("query", "")).strip()
    if not query:
        return {"connected": True, "result": ""}
    try:
        text = await client.query_meetings(query)
    except GranolaAPIError as exc:
        if exc.status == 401:
            return {"connected": False, "reason": "auth_expired"}
        return {"connected": True, "error": str(exc), "result": ""}
    return {"connected": True, "result": text}


@granola_compat_router.post("/oauth-disconnect")
async def granola_oauth_disconnect(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Legacy disconnect — deletes the active Granola integration row."""
    rows = await repo.list_active(session, provider="granola")
    for r in rows:
        await session.delete(r)
    await session.commit()
    return {"ok": True, "removed": len(rows)}
