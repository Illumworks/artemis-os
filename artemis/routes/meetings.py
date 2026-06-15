"""Meetings router — /api/meetings.

Endpoints:
  GET  /api/meetings/overview                     — yesterday's + today's past meetings
  GET  /api/meetings/list                         — date-range list
  GET  /api/meetings/{id}                         — full transcript + attendees
  POST /api/meetings/{id}/actions/jira            — route action item to Jira issue
  POST /api/meetings/{id}/actions/okr             — route action item to OKR activity
  POST /api/meetings/{id}/actions/slack           — route action item to Slack reminder
  POST /api/meetings/{id}/actions/todo            — save action item as personal todo
  POST /api/meetings/{id}/actions/dismiss         — lossless dismiss ("drop it / not relevant")
  POST /api/meetings/{id}/ask                     — AI Q&A over transcript
  GET  /api/meetings/{id}/routings                — list persisted action routings

All return {"status": "not_connected"} if no active Granola integration.
Granola integration is provided by J6a — GranolaClient + GranolaProvider.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.granola.client import GranolaAPIError, GranolaClient
from artemis.marketing.routes._auth import require_owner, require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/meetings",
    tags=["meetings"],
    dependencies=[Depends(require_token), Depends(require_owner)],
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

    Lazy backfill: if summary exists but transcript is NULL, fetches the
    transcript from Granola and persists it so the next call is instant.
    Idempotent: only fills when transcript IS NULL.
    """
    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from artemis.meetings.models import MeetingSummary

    result = await session.execute(
        sa_select(MeetingSummary).where(MeetingSummary.granola_id == granola_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "summary_not_found", "granola_id": granola_id},
        )

    # Lazy backfill: fetch transcript from Granola when NULL.
    if row.transcript is None:
        granola = await _get_granola_client(session)
        if granola is not None:
            try:
                detail = await granola.get_meeting(granola_id)
                if detail:
                    transcript_text: str | None = None
                    if "transcript" in detail:
                        transcript_text = str(detail["transcript"])
                    elif "notes" in detail:
                        transcript_text = str(detail["notes"])
                    if transcript_text:
                        await session.execute(
                            sa_update(MeetingSummary)
                            .where(MeetingSummary.granola_id == granola_id)
                            .values(transcript=transcript_text)
                        )
                        await session.commit()
                        row.transcript = transcript_text
            except Exception:
                logger.warning(
                    "Lazy transcript backfill failed for granola_id=%s", granola_id, exc_info=True
                )

    return {
        "granola_id": row.granola_id,
        "gcal_event_id": row.gcal_event_id,
        "title": row.title,
        "summary": row.summary,
        "action_items": row.action_items or [],
        "transcript": row.transcript,
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


# ── J6c — Action routing helpers ──────────────────────────────────────────────


async def _get_routing(
    session: AsyncSession, meeting_id: str, action_text: str, routed_to: str
) -> dict[str, Any] | None:
    """Return an existing routing row or None."""
    result = await session.execute(
        text(
            "SELECT id, target_id, target_url FROM meeting_action_routings "
            "WHERE meeting_id = :mid AND action_text = :at AND routed_to = :rt"
        ).bindparams(mid=meeting_id, at=action_text, rt=routed_to)
    )
    row = result.fetchone()
    if row is None:
        return None
    return {"id": row[0], "target_id": row[1], "target_url": row[2]}


async def _insert_routing(
    session: AsyncSession,
    *,
    meeting_id: str,
    action_text: str,
    routed_to: str,
    target_id: str | None = None,
    target_url: str | None = None,
) -> None:
    """Insert into meeting_action_routings; silently no-ops if the UNIQUE row exists."""
    await session.execute(
        text(
            "INSERT INTO meeting_action_routings "
            "  (meeting_id, action_text, routed_to, target_id, target_url) "
            "VALUES (:mid, :at, :rt, :tid, :turl) "
            "ON CONFLICT (meeting_id, action_text, routed_to) DO NOTHING"
        ).bindparams(
            mid=meeting_id,
            at=action_text,
            rt=routed_to,
            tid=target_id,
            turl=target_url,
        )
    )
    await session.commit()


# ── J6c — POST /api/meetings/{meeting_id}/actions/jira ───────────────────────


@router.post("/{meeting_id}/actions/jira")
async def route_action_to_jira(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a Jira issue from a meeting action item; idempotent."""
    action_text = str(body.get("action_text", "")).strip()
    if not action_text:
        raise HTTPException(status_code=422, detail={"error": "action_text required"})

    # Idempotency: return existing routing if already done
    existing = await _get_routing(session, meeting_id, action_text, "jira")
    if existing:
        return {
            "ok": True,
            "already_routed": True,
            "key": existing["target_id"],
            "url": existing["target_url"],
        }

    # Resolve Jira credentials
    from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_jira_config
    from artemis.integrations.jira.client import JiraAPIError, JiraClient

    try:
        jira_cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503, detail={"error": str(exc), "code": "jira_not_configured"}
        ) from exc

    client = JiraClient(
        site_url=jira_cfg.site_url,
        email=jira_cfg.email,
        api_token=jira_cfg.api_token,
    )

    # Fetch meeting title for description context
    granola = await _get_granola_client(session)
    meeting_title = ""
    meeting_date = ""
    if granola:
        try:
            detail = await granola.get_meeting(meeting_id)
            if detail:
                meeting_title = str(detail.get("title", ""))
                meeting_date = str(detail.get("date", ""))
        except Exception:
            pass

    description = f"From meeting: {meeting_title}\n{meeting_date}\n\nAction: {action_text}"
    project_key = jira_cfg.project_key or "MT"

    try:
        result = await client.create_issue(
            project_key=project_key,
            summary=action_text[:255],
            description=description,
        )
    except JiraAPIError as exc:
        raise HTTPException(
            status_code=502, detail={"error": str(exc), "code": "jira_error"}
        ) from exc

    issue_key = result["key"]
    issue_url = f"{jira_cfg.site_url.rstrip('/')}/browse/{issue_key}"

    await _insert_routing(
        session,
        meeting_id=meeting_id,
        action_text=action_text,
        routed_to="jira",
        target_id=issue_key,
        target_url=issue_url,
    )

    return {"ok": True, "already_routed": False, "key": issue_key, "url": issue_url}


# ── J6c — POST /api/meetings/{meeting_id}/actions/okr ────────────────────────


@router.post("/{meeting_id}/actions/okr")
async def route_action_to_okr(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Append an OKR activity evidence row for a meeting action item; idempotent."""
    action_text = str(body.get("action_text", "")).strip()
    kr_id_raw = body.get("kr_id")
    if not action_text:
        raise HTTPException(status_code=422, detail={"error": "action_text required"})
    if kr_id_raw is None:
        raise HTTPException(status_code=422, detail={"error": "kr_id required"})

    try:
        kr_id = int(kr_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail={"error": "kr_id must be an integer"}) from exc

    existing = await _get_routing(session, meeting_id, action_text, "okr")
    if existing:
        return {"ok": True, "already_routed": True, "kr_id": kr_id}

    # Verify KR exists
    from artemis.okr.models import OkrActivity, OkrKeyResult

    kr_row = await session.get(OkrKeyResult, kr_id)
    if kr_row is None:
        raise HTTPException(status_code=404, detail={"error": "key result not found"})

    activity = OkrActivity(
        text=f"Meeting action: {action_text}",
        kr_id=kr_id,
        kr_label=kr_row.title,
        raw_text=f"source: meeting:{meeting_id}",
        mapping_confidence=1.0,
    )
    session.add(activity)
    await session.flush()
    await session.refresh(activity)

    await _insert_routing(
        session,
        meeting_id=meeting_id,
        action_text=action_text,
        routed_to="okr",
        target_id=str(kr_id),
        target_url=None,
    )

    return {"ok": True, "already_routed": False, "kr_id": kr_id, "activity_id": activity.id}


# ── J6c — POST /api/meetings/{meeting_id}/actions/slack ──────────────────────


@router.post("/{meeting_id}/actions/slack")
async def route_action_to_slack(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Send a Slack DM reminder for a meeting action item; idempotent."""
    action_text = str(body.get("action_text", "")).strip()
    when_str = str(body.get("when", "")).strip()
    if not action_text:
        raise HTTPException(status_code=422, detail={"error": "action_text required"})

    existing = await _get_routing(session, meeting_id, action_text, "slack")
    if existing:
        return {"ok": True, "already_routed": True}

    # Resolve Slack token from active integration row
    rows = await repo.list_active(session, provider="slack")
    if not rows:
        raise HTTPException(
            status_code=503, detail={"error": "Slack not connected", "code": "slack_not_connected"}
        )

    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail={"error": "Cannot decrypt Slack credentials"}
        ) from exc

    token = str(creds.get("access_token", ""))
    if not token:
        raise HTTPException(
            status_code=503,
            detail={"error": "No Slack access_token", "code": "slack_not_configured"},
        )

    from artemis.integrations.slack.client import SlackAPIError, SlackClient

    client = SlackClient(token=token)

    # Resolve the Slack recipient.
    # Preference order:
    #   1. authed_user / user_id in the stored OAuth payload
    #   2. bot_user_id on the integration row (the bot itself; DM is opened via conversations.open)
    #   3. incoming_webhook_channel_id from the payload
    authed_user = str(creds.get("authed_user", creds.get("user_id", "")))
    if not authed_user:
        authed_user = str(integration.bot_user_id or "")
    if not authed_user:
        channel = str(creds.get("incoming_webhook_channel_id", ""))
        if not channel:
            raise HTTPException(
                status_code=503, detail={"error": "Cannot determine Slack recipient"}
            )
    else:
        channel = authed_user

    when_label = when_str or "now"
    text_msg = f":bell: *Meeting reminder* — {action_text}"
    if when_str:
        try:
            dt = datetime.fromisoformat(when_str)
            when_label = dt.strftime("%b %-d at %-I:%M %p")
            text_msg += f"\n_Scheduled for {when_label}_"
        except ValueError:
            pass

    try:
        await client.post_message(channel=channel, text=text_msg)
    except SlackAPIError as exc:
        raise HTTPException(
            status_code=502, detail={"error": str(exc), "code": "slack_error"}
        ) from exc

    await _insert_routing(
        session,
        meeting_id=meeting_id,
        action_text=action_text,
        routed_to="slack",
        target_id=None,
        target_url=None,
    )

    return {"ok": True, "already_routed": False, "when": when_label}


# ── J6c — POST /api/meetings/{meeting_id}/actions/todo ───────────────────────


@router.post("/{meeting_id}/actions/todo")
async def route_action_to_todo(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Save a meeting action item as a personal todo; idempotent."""
    action_text = str(body.get("action_text", "")).strip()
    if not action_text:
        raise HTTPException(status_code=422, detail={"error": "action_text required"})

    existing = await _get_routing(session, meeting_id, action_text, "todo")
    if existing:
        return {"ok": True, "already_routed": True, "id": existing["target_id"]}

    result = await session.execute(
        text(
            "INSERT INTO personal_todos (text, source) VALUES (:text, :source) RETURNING id"
        ).bindparams(text=action_text, source=f"meeting:{meeting_id}")
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail={"error": "Failed to create todo"})
    todo_id = row[0]
    await session.commit()

    await _insert_routing(
        session,
        meeting_id=meeting_id,
        action_text=action_text,
        routed_to="todo",
        target_id=str(todo_id),
        target_url=None,
    )

    return {"ok": True, "already_routed": False, "id": todo_id}


# ── P3 — POST /api/meetings/{meeting_id}/actions/dismiss ─────────────────────


@router.post("/{meeting_id}/actions/dismiss")
async def dismiss_action_item(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Losslessly dismiss a meeting action item as irrelevant ("drop it").

    Records a permanent dismissal row keyed by content hash so the item never
    reappears on re-ingest or re-summarisation.  Also closes the linked
    commitment (if any) with a 'dismissed' terminal state — distinct from
    'done' — so no further DM nags are sent.

    Body JSON:
      action_text  (str, required) — exact text of the action item to dismiss.

    Returns:
      ok                (bool)  — true on success
      already_dismissed (bool)  — true if already recorded (idempotent)
      commitment_id     (int|null) — id of the closed commitment, if one existed
    """
    from sqlalchemy import select as sa_select

    from artemis.meetings.models import MeetingActionItemDismissal, MeetingSummary
    from artemis.proactivity import repository as prepo
    from artemis.proactivity.commitments import _normalize_text, action_item_key

    action_text_raw = str(body.get("action_text", "")).strip()
    if not action_text_raw:
        raise HTTPException(status_code=422, detail={"error": "action_text required"})

    normalized = _normalize_text(action_text_raw)
    item_key = action_item_key(normalized)

    # Resolve the meeting_summaries row so we have a FK target.
    summary_result = await session.execute(
        sa_select(MeetingSummary).where(MeetingSummary.granola_id == meeting_id)
    )
    summary = summary_result.scalar_one_or_none()
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "meeting_summary_not_found", "granola_id": meeting_id},
        )

    # Idempotency check.
    existing = await session.execute(
        sa_select(MeetingActionItemDismissal).where(
            MeetingActionItemDismissal.meeting_summary_id == summary.id,
            MeetingActionItemDismissal.action_item_key == item_key,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return {"ok": True, "already_dismissed": True, "commitment_id": None}

    # Write the dismissal record.
    from datetime import UTC, datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.now(UTC)
    dismissal_stmt = (
        pg_insert(MeetingActionItemDismissal.__table__)  # type: ignore[arg-type]
        .values(
            meeting_summary_id=summary.id,
            action_item_key=item_key,
            granola_id=meeting_id,
            action_item_text=normalized,
            dismissed_at=now,
        )
        .on_conflict_do_nothing(
            constraint="uq_meeting_action_item_dismissals",
        )
    )
    await session.execute(dismissal_stmt)

    # Close the linked commitment (source_type="granola_meeting", source_id=granola_id, text=text).
    commitment = await prepo.find_commitment_by_source_and_text(
        session,
        source_type="granola_meeting",
        source_id=meeting_id,
        text=normalized,
    )
    commitment_id: int | None = None
    if commitment is not None:
        await prepo.dismiss_commitment(session, commitment_id=commitment.id, now=now)
        commitment_id = commitment.id

    await session.commit()
    return {"ok": True, "already_dismissed": False, "commitment_id": commitment_id}


# ── J6c — POST /api/meetings/{meeting_id}/ask ────────────────────────────────


@router.post("/{meeting_id}/ask")
async def ask_about_meeting(
    meeting_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Answer a question about a meeting transcript using the LLM chain."""
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=422, detail={"error": "question required"})

    # Fetch transcript from Granola
    granola = await _get_granola_client(session)
    if granola is None:
        raise HTTPException(status_code=503, detail={"error": "Granola not connected"})

    try:
        detail = await granola.get_meeting(meeting_id)
    except GranolaAPIError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail={"error": "Meeting not found"}) from exc
        raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    if not detail:
        raise HTTPException(status_code=404, detail={"error": "Meeting not found"})

    transcript = str(detail.get("transcript", "")).strip()
    summary = str(detail.get("summary", detail.get("notes", ""))).strip()
    context_text = transcript or summary
    if not context_text:
        return {"answer": "No transcript or notes available for this meeting.", "citations": []}

    # Use the existing provider chain (claude-code → codex → lm-studio → anthropic)
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock
    from artemis.providers import get_adapter
    from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

    system_prompt = (
        "Answer the user's question about this meeting transcript. "
        "Quote relevant lines verbatim. If the answer isn't in the transcript, say so."
    )
    user_message = f"Transcript:\n{context_text}\n\nQuestion: {question}"

    adapter = None
    for candidate in ("claude-code", "codex", "lm-studio", "anthropic"):
        try:
            adapter = get_adapter(candidate)
            break
        except (MissingApiKeyError, UnknownProviderError):
            continue
        except Exception:
            continue

    if adapter is None:
        raise HTTPException(status_code=503, detail={"error": "No LLM provider available"})

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=user_message)])],
        system=system_prompt,
        max_tokens=1024,
    )

    try:
        response = await adapter.complete(request)
    except Exception as exc:
        logger.warning("ask_about_meeting LLM error: %s", exc)
        raise HTTPException(
            status_code=502, detail={"error": "LLM call failed", "detail": str(exc)}
        ) from exc

    # Extract text from response
    answer_text = ""
    for block in response.message.content:
        if isinstance(block, TextBlock):
            answer_text += block.text

    return {"answer": answer_text, "citations": []}


# ── J6c — GET /api/meetings/{meeting_id}/routings ────────────────────────────


@router.get("/{meeting_id}/routings")
async def get_meeting_routings(
    meeting_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return all persisted action routings for a meeting."""
    result = await session.execute(
        text(
            "SELECT id, action_text, routed_to, target_id, target_url, routed_at "
            "FROM meeting_action_routings WHERE meeting_id = :mid ORDER BY routed_at ASC"
        ).bindparams(mid=meeting_id)
    )
    rows = result.fetchall()
    routings = [
        {
            "id": r[0],
            "action_text": r[1],
            "routed_to": r[2],
            "target_id": r[3],
            "target_url": r[4],
            "routed_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
    return {"meeting_id": meeting_id, "routings": routings}


# ── J6c — GET /api/todos (personal todos list) ───────────────────────────────

todos_router = APIRouter(
    prefix="/api/todos",
    tags=["todos"],
    dependencies=[Depends(require_token), Depends(require_owner)],
)


@todos_router.get("")
async def list_todos(
    include_done: bool = Query(default=False),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return personal todos, newest first."""
    base = "SELECT id, text, source, done, created_at FROM personal_todos"
    where = "" if include_done else " WHERE done = false"
    result = await session.execute(text(base + where + " ORDER BY created_at DESC"))
    rows = result.fetchall()
    return {
        "todos": [
            {
                "id": r[0],
                "text": r[1],
                "source": r[2],
                "done": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    }


@todos_router.post("/{todo_id}/done")
async def mark_todo_done(
    todo_id: int,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Mark a personal todo as done."""
    result = await session.execute(
        text("UPDATE personal_todos SET done = true WHERE id = :id RETURNING id").bindparams(
            id=todo_id
        )
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "Todo not found"})
    await session.commit()
    return {"ok": True, "id": todo_id}


# ── Legacy /api/granola/* compat ──────────────────────────────────────────────
# The frontend (`public/js/core/api.js`) still calls Node-era paths. Rather than
# touch a wide swath of frontend code, expose thin aliases that re-shape J6a
# responses into the `{connected: true, meetings: [...]}` envelope the UI wants.
# Long-term cleanup: migrate frontend to /api/meetings/* + delete this section.

granola_compat_router = APIRouter(
    prefix="/api/granola",
    tags=["granola-compat"],
    dependencies=[Depends(require_token), Depends(require_owner)],
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
