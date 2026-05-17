"""Slack Events API receiver — /api/integrations/slack/events.

Handles three Slack callback types:
  - url_verification  : return challenge (no HMAC required)
  - event_callback    : HMAC-verified; dispatch app_mention / message+im to routing
  - everything else   : 200 OK, no-op
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/slack", tags=["slack-events"])

# Regex to strip the leading bot-mention from a message text, e.g. "<@U12345> hello"
_BOT_MENTION_RE = re.compile(r"^<@[A-Z0-9]+>\s*", re.IGNORECASE)


# ── HMAC verification ─────────────────────────────────────────────────────────


def _verify_slack_signature(
    body: bytes, timestamp: str, signature: str, signing_secret: str
) -> bool:
    """Return True iff the Slack HMAC-SHA256 signature is valid and the timestamp is fresh."""
    if not signing_secret:
        return False
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except ValueError:
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(signing_secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── route_inbound stub ────────────────────────────────────────────────────────


async def route_inbound(event_data: dict[str, object]) -> None:
    """Route an inbound Slack event into a floating-artemis session.

    Session key: (team_id, channel_id, thread_ts or "_").
    The session is marked surface=slack so the web UI panel doesn't show it.
    After handle_turn completes, the response text is posted back in-thread.
    """
    team_id = str(event_data.get("team_id", ""))
    channel_id = str(event_data.get("channel", ""))
    thread_ts = event_data.get("thread_ts")
    ts = str(event_data.get("ts", ""))
    text = str(event_data.get("text", ""))

    if not team_id or not channel_id or not text:
        logger.warning("route_inbound: missing required fields in event_data")
        return

    # Stable session key — one FA session per Slack thread (or channel if not threaded)
    bucket = str(thread_ts) if thread_ts else "_"
    session_id = f"slack-{team_id}-{channel_id}-{bucket}"
    reply_thread_ts = str(thread_ts) if thread_ts else ts

    import artemis.db as _db
    from artemis.floating_artemis import repository as fa_repo
    from artemis.floating_artemis.chat import handle_turn

    async with _db.SessionLocal() as db_session:
        try:
            await fa_repo.get_session_by_id(db_session, session_id)
        except ValueError:
            await fa_repo.create_session(
                db_session,
                session_id=session_id,
                metadata={"surface": "slack", "team_id": team_id, "channel_id": channel_id},
            )
            await db_session.commit()

    try:
        result = await handle_turn(session_id=session_id, user_text=text)
    except Exception:
        logger.exception("route_inbound: handle_turn failed for session %s", session_id)
        return

    response_text = result.response_text
    if not response_text:
        return

    try:
        async with _db.SessionLocal() as db_session:
            from artemis.integrations import repository as int_repo
            from artemis.integrations.crypto import decrypt_credentials
            from artemis.integrations.slack.client import SlackClient

            rows = await int_repo.list_active(db_session, provider="slack")
            if not rows:
                logger.warning("route_inbound: no active Slack integration to reply with")
                return
            integration = rows[0]
            creds = decrypt_credentials(bytes(integration.encrypted_credentials))
            token = str(creds.get("access_token", ""))
            client = SlackClient(token=token)
            await client.post_message(
                channel=channel_id,
                text=response_text,
                thread_ts=reply_thread_ts,
            )
    except Exception:
        logger.exception("route_inbound: failed to post Slack reply for session %s", session_id)


# ── Event handler helper ──────────────────────────────────────────────────────


async def _handle_mentionable_event(
    payload: dict[str, object],
    event: dict[str, object],
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> None:
    """Dedupe and background-dispatch a mentionable event (app_mention / im message)."""
    event_id: str = str(payload.get("event_id", ""))
    team_id: str = str(payload.get("team_id", ""))
    channel_id: str = str(event.get("channel", ""))
    user_id: str = str(event.get("user", ""))
    ts: str = str(event.get("ts", ""))
    thread_ts: str | None = str(event["thread_ts"]) if "thread_ts" in event else None
    raw_text: str | None = str(event["text"]) if "text" in event else None

    # Strip bot-mention prefix from app_mention messages
    text: str | None = _BOT_MENTION_RE.sub("", raw_text).strip() if raw_text else raw_text

    is_new = await repo.upsert_slack_inbound(
        session,
        event_id=event_id,
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
    )
    await session.commit()

    if is_new:
        event_data: dict[str, object] = {
            "event_id": event_id,
            "team_id": team_id,
            "channel": channel_id,
            "user": user_id,
            "text": text,
            "ts": ts,
            "thread_ts": thread_ts,
        }
        background_tasks.add_task(route_inbound, event_data)
    else:
        logger.debug("Duplicate Slack event_id=%s — ignored", event_id)


# ── Main endpoint ─────────────────────────────────────────────────────────────


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> JSONResponse:
    """Handle all incoming Slack Events API callbacks."""
    raw_body: bytes = await request.body()

    # ── 1. Parse JSON early so we can check for url_verification ─────────────
    try:
        payload: dict[str, object] = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    event_type: str = str(payload.get("type", ""))

    # ── 2. url_verification — no HMAC required (Slack docs) ──────────────────
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        return JSONResponse(status_code=200, content={"challenge": challenge})

    # ── 3. Resolve signing secret (DB first, env fallback) ───────────────────
    signing_secret = ""
    try:
        from artemis.integrations.config_resolver import resolve_slack_config

        slack_cfg = await resolve_slack_config(session)
        signing_secret = slack_cfg.signing_secret
    except Exception:
        signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

    # ── 4. HMAC verification ──────────────────────────────────────────────────
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(raw_body, timestamp, signature, signing_secret):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    # ── 5. Dispatch by type ───────────────────────────────────────────────────
    if event_type == "event_callback":
        event: dict[str, object] = payload.get("event", {})  # type: ignore[assignment]
        if not isinstance(event, dict):
            event = {}
        inner_type: str = str(event.get("type", ""))
        channel_type: str = str(event.get("channel_type", ""))

        if inner_type == "app_mention" or inner_type == "message" and channel_type == "im":
            await _handle_mentionable_event(payload, event, background_tasks, session)
        else:
            logger.debug(
                "Unhandled Slack inner event type=%r channel_type=%r — ignoring",
                inner_type,
                channel_type,
            )
    else:
        logger.debug("Unhandled Slack callback type=%r — ignoring", event_type)

    return JSONResponse(status_code=200, content={"ok": True})
