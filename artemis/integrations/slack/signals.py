"""Compute Slack signal counts for the Focus Rail / Daily Brief.

Single public coroutine: ``get_slack_signals(session, force_refresh=False)``.

Signal sources:
  - ``missedMentions``   : inbound rows received in the last 48 h that have not yet
                           been routed to a Floating-Artemis session (routed_to_session_id IS NULL).
                           All rows in ``slack_inbound_messages`` are app_mention or DM events —
                           the events receiver only stores those two types — so every unrouted
                           row is a legitimate missed mention / unread DM equivalent.
  - ``unreadDMs``        : requires ``im:read`` + ``im:history`` scope on a *user* token or the
                           bot-token ``conversations.list`` with type=im.  Our OAuth flow
                           requests ``im:history`` and ``im:read`` scopes (bot token), so we
                           attempt the API call and return None on scope error.
  - ``replyNeededThreads``: thread messages (thread_ts IS NOT NULL) received more than 4 h ago
                            and not yet routed (routed_to_session_id IS NULL).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.models import SlackInboundMessage
from artemis.integrations.slack.client import SlackAPIError, SlackClient

# TTL mirrors Node's SLACK_SIGNAL_CACHE_TTL_MS = 60_000
_CACHE_TTL_S = 60

_cache: dict[str, object] | None = None
_cache_ts: float = 0.0

_WINDOW_H = 48
_REPLY_LAG_H = 4


def _not_connected_shape() -> dict[str, object]:
    return {
        "connected": False,
        "status": "not_connected",
        "missedMentions": None,
        "unreadDMs": None,
        "replyNeededThreads": None,
        "checkedWindow": "",
        "checkedAt": datetime.now(UTC).isoformat(),
        "source": "artemis",
    }


async def _compute(session: AsyncSession) -> dict[str, object]:
    rows = await repo.list_active(session, provider="slack")
    if not rows:
        return _not_connected_shape()

    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        token = str(creds.get("access_token", ""))
    except Exception:
        return {
            **_not_connected_shape(),
            "status": "unavailable",
        }

    if not token:
        return {
            **_not_connected_shape(),
            "status": "unavailable",
        }

    window_start = datetime.now(UTC) - timedelta(hours=_WINDOW_H)
    reply_cutoff = datetime.now(UTC) - timedelta(hours=_REPLY_LAG_H)

    # missedMentions: unrouted inbound rows within 48 h window
    missed_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id)).where(
            SlackInboundMessage.received_at >= window_start,
            SlackInboundMessage.routed_to_session_id.is_(None),
        )
    )
    missed_mentions: int = missed_result.scalar_one() or 0

    # replyNeededThreads: thread messages older than 4 h and not yet routed
    reply_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id)).where(
            SlackInboundMessage.thread_ts.is_not(None),
            SlackInboundMessage.received_at >= window_start,
            SlackInboundMessage.received_at < reply_cutoff,
            SlackInboundMessage.routed_to_session_id.is_(None),
        )
    )
    reply_needed: int = reply_result.scalar_one() or 0

    # unreadDMs: attempt bot-token conversations.list(types="im")
    # Requires im:read scope; our OAuth flow requests it.  Fall back to None on
    # any scope or API error rather than fabricating a count.
    unread_dms: int | None = None
    dm_status = "connected"
    try:
        client = SlackClient(token=token)
        # conversations.list doesn't exist on SlackClient directly; call _post
        data = await client._post("conversations.list", types="im", limit=200)
        channels: list[dict[str, object]] = data.get("channels", [])  # type: ignore[assignment]
        # Count channels that have unread_count > 0 (Slack returns this field
        # in the channel object when the token has im:read scope).
        unread_count = sum(1 for ch in channels if int(str(ch.get("unread_count", 0))) > 0)
        unread_dms = unread_count
    except SlackAPIError as exc:
        if exc.error in ("missing_scope", "not_authed", "invalid_auth"):
            # Document the gap: bot token needs im:read scope.
            dm_status = "connected_no_dm_scope"
        else:
            dm_status = "unavailable"
        unread_dms = None
    except Exception:
        dm_status = "unavailable"
        unread_dms = None

    checked_window = f"slack_inbound_messages over last {_WINDOW_H}h" + (
        " + API conversations.list(im)" if unread_dms is not None else ""
    )

    return {
        "connected": True,
        "status": dm_status,
        "missedMentions": missed_mentions,
        "unreadDMs": unread_dms,
        "replyNeededThreads": reply_needed,
        "checkedWindow": checked_window,
        "checkedAt": datetime.now(UTC).isoformat(),
        "source": "artemis",
    }


async def get_slack_signals(
    session: AsyncSession,
    force_refresh: bool = False,
) -> dict[str, object]:
    """Return Slack signal counts, using a 60-second in-process cache.

    ``force_refresh=True`` bypasses the cache and re-queries.  The cache is
    module-level so it survives across requests within the same worker process
    (matching Node's behaviour).
    """
    global _cache, _cache_ts

    now = time.monotonic()
    if not force_refresh and _cache is not None and (now - _cache_ts) < _CACHE_TTL_S:
        return dict(_cache)

    try:
        result = await _compute(session)
    except Exception:
        result = {
            **_not_connected_shape(),
            "status": "unavailable",
        }

    _cache = result
    _cache_ts = now
    return dict(result)
