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
from artemis.writing_rules import lint_agent_text

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
    text = str(event_data.get("text", ""))
    slack_user_id = str(event_data.get("user", ""))

    if not team_id or not channel_id or not text:
        logger.warning("route_inbound: missing required fields in event_data")
        return

    # Stable session key — one FA session per Slack thread (or channel if not threaded)
    bucket = str(thread_ts) if thread_ts else "_"
    session_id = f"slack-{team_id}-{channel_id}-{bucket}"
    # Reply in-thread ONLY when the source was already a thread reply.
    # Top-level mentions get top-level replies (no thread spam).
    reply_thread_ts = str(thread_ts) if thread_ts else None

    import artemis.db as _db
    from artemis.floating_artemis import repository as fa_repo
    from artemis.floating_artemis.chat import handle_turn

    # Resolve the speaker's display name from the J9b user cache so Artemis knows
    # who she's addressing.  Falls back to the raw id when the user isn't cached.
    speaker_name: str | None = None
    async with _db.SessionLocal() as db_session:
        if slack_user_id:
            cached = await repo.get_slack_user(db_session, slack_user_id)
            if cached is not None:
                speaker_name = cached.real_name or cached.name
        try:
            await fa_repo.get_session_by_id(db_session, session_id)
        except ValueError:
            await fa_repo.create_session(
                db_session,
                session_id=session_id,
                metadata={
                    "surface": "slack",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "slack_user_id": slack_user_id,
                },
            )
            await db_session.commit()

    try:
        result = await handle_turn(session_id=session_id, user_text=text, speaker_name=speaker_name)
    except Exception:
        logger.exception("route_inbound: handle_turn failed for session %s", session_id)
        return

    response_text = result.response_text
    if not response_text:
        return
    outbound_text = lint_agent_text(response_text)
    if not outbound_text.strip():
        logger.warning("route_inbound: linted Slack reply became empty for session %s", session_id)
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
                text=outbound_text,
                thread_ts=reply_thread_ts,
            )
    except Exception:
        logger.exception("route_inbound: failed to post Slack reply for session %s", session_id)


# ── Bot-self / non-human filter ────────────────────────────────────────────────


def _is_bot_authored(event: dict[str, object], bot_user_id: str) -> bool:
    """Return True if this event was authored by a bot (incl. Artemis herself).

    Without this guard, Artemis's own replies — delivered back to the app as
    `message.im` / `app_mention` events — would be re-dispatched into the agent
    loop, producing an infinite echo loop.  Slack marks bot-authored messages
    with a ``bot_id`` and/or ``subtype == "bot_message"``; we also match our own
    ``bot_user_id`` as a belt-and-suspenders check.
    """
    if event.get("bot_id"):
        return True
    if str(event.get("subtype", "")) == "bot_message":
        return True
    return bool(bot_user_id) and str(event.get("user", "")) == bot_user_id


# ── Event handler helper ──────────────────────────────────────────────────────


async def _handle_mentionable_event(
    payload: dict[str, object],
    event: dict[str, object],
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> None:
    """Dedupe and background-dispatch a mentionable event (app_mention / im message).

    Two guards stand between an inbound event and Artemis's agent loop:
      1. Bot-self filter — drop bot-authored events (kills the reply echo loop).
      2. Allowlist gate — only dispatch when the sender is permitted to converse
         with Artemis.  Fail-closed: when no allowlist is configured, nothing is
         routed.  Non-allowed human messages are still recorded for audit/triage.
    """
    from artemis.integrations.slack.triage import classify_mention_type

    event_id: str = str(payload.get("event_id", ""))
    team_id: str = str(payload.get("team_id", ""))
    channel_id: str = str(event.get("channel", ""))
    user_id: str = str(event.get("user", ""))
    ts: str = str(event.get("ts", ""))
    thread_ts: str | None = str(event["thread_ts"]) if "thread_ts" in event else None
    raw_text: str | None = str(event["text"]) if "text" in event else None

    # Resolve the active Slack bot identity so we can recognise our own messages.
    bot_user_id = ""
    try:
        rows = await repo.list_active(session, provider="slack")
        if rows:
            bot_user_id = str(rows[0].bot_user_id or "")
    except Exception:
        bot_user_id = ""

    # ── Guard 1: bot-self filter — drop bot-authored events (no record, no loop) ─
    if _is_bot_authored(event, bot_user_id):
        logger.debug("Slack event_id=%s is bot-authored — dropped (echo guard)", event_id)
        return

    # Strip bot-mention prefix from app_mention messages
    text: str | None = _BOT_MENTION_RE.sub("", raw_text).strip() if raw_text else raw_text

    # Resolve Slack config once: authed_user_id (J9b mention classifier) + the
    # inbound-conversation allowlist (fail-closed when unconfigured).
    authed_user_id = ""
    allowed_user_ids: tuple[str, ...] = ()
    try:
        from artemis.integrations.config_resolver import resolve_slack_config

        slack_cfg = await resolve_slack_config(session)
        authed_user_id = slack_cfg.authed_user_id
        allowed_user_ids = slack_cfg.allowed_user_ids
    except Exception:
        # Credentials missing — fall back to env for both fields.
        from artemis.integrations.config_resolver import _parse_allowed_user_ids

        authed_user_id = os.environ.get("SLACK_AUTHED_USER_ID", "")
        extras = _parse_allowed_user_ids(os.environ.get("SLACK_ALLOWED_USER_IDS", ""))
        allowed_user_ids = tuple(
            dict.fromkeys(([authed_user_id] if authed_user_id else []) + extras)
        )

    mention_type = classify_mention_type(text or "", authed_user_id)

    # Record every real (human) inbound for audit/triage, allowed or not.
    is_new = await repo.upsert_slack_inbound(
        session,
        event_id=event_id,
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        mention_type=mention_type,
    )
    await session.commit()

    if not is_new:
        logger.debug("Duplicate Slack event_id=%s — ignored", event_id)
        return

    # ── Guard 2: allowlist gate — only allowed senders reach the agent loop ─────
    if user_id not in allowed_user_ids:
        logger.info(
            "Slack inbound from non-allowlisted user=%s (event_id=%s) recorded but not routed",
            user_id,
            event_id,
        )
        return

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
