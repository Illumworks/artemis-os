"""Slack triage — mention queue + resolution.

Provides coroutines consumed by the /api/slack/signals/mentions routes:
  classify_mention_type(text, authed_user_id)  → mention type string
  resolve_user(session, user_id, token)         → dict with id/name
  resolve_channel(session, channel_id, token)   → dict with id/name/is_im
  list_unresolved_mentions(session, ...)        → list + total count
  resolve_mention(session, event_id)            → (found, new_total)
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.models import SlackInboundMessage, SlackUser
from artemis.integrations.slack.client import SlackAPIError, SlackClient

logger = logging.getLogger(__name__)

# ── Mention-type patterns ──────────────────────────────────────────────────────

# Matches <!channel> or <!here>
_CHANNEL_RE = re.compile(r"<!(?:channel|here)>", re.IGNORECASE)

# Matches <!subteam^GROUPID> (user-group pings)
_SUBTEAM_RE = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>", re.IGNORECASE)

# Matches <@USERID> — any user mention
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", re.IGNORECASE)

# Cache TTL for name lookups: 7 days
_NAME_CACHE_STALE_DAYS = 7


def classify_mention_type(text: str, authed_user_id: str) -> str:
    """Classify the primary mention type in a Slack message.

    Priority order (first match wins):
      1. direct  — text contains <@AUTHED_USER_ID> (Jon was specifically called out)
      2. channel — text contains <!channel> or <!here>
      3. group   — text contains <!subteam^GROUPID>
      4. keyword — fallback (keyword trigger or other catch-all)

    When authed_user_id is empty, any <@USER> mention is treated as 'direct'
    (conservative fallback — keeps pre-configuration behaviour).
    """
    if authed_user_id:
        # Check for direct mention of authed user
        pattern = re.compile(r"<@" + re.escape(authed_user_id) + r"(?:\|[^>]*)?>", re.IGNORECASE)
        if pattern.search(text):
            return "direct"
    else:
        # No user ID configured — any user mention treated as direct
        if _USER_MENTION_RE.search(text):
            return "direct"

    if _CHANNEL_RE.search(text):
        return "channel"

    if _SUBTEAM_RE.search(text):
        return "group"

    return "keyword"


# ── Name resolution helpers ────────────────────────────────────────────────────


async def resolve_user(
    session: AsyncSession,
    user_id: str,
    token: str,
) -> dict[str, str]:
    """Return {id, name} for a Slack user, using cache-first strategy.

    Cache is considered stale after _NAME_CACHE_STALE_DAYS days.
    Falls back gracefully: if the API call fails, returns {id: user_id, name: user_id}
    so the UI degrades to showing the raw ID rather than breaking.
    """
    cached = await repo.get_slack_user(session, user_id)
    stale_threshold = datetime.now(UTC) - timedelta(days=_NAME_CACHE_STALE_DAYS)

    if cached is not None and cached.fetched_at > stale_threshold:
        return {"id": cached.id, "name": cached.name}

    # Not cached or stale — call Slack API
    try:
        client = SlackClient(token=token)
        data = await client._post("users.info", user=user_id)
        user_obj: dict[str, object] = data.get("user", {})  # type: ignore[assignment]
        profile: dict[str, object] = user_obj.get("profile", {})  # type: ignore[assignment]
        real_name = str(user_obj.get("real_name") or "")
        display_name = str(profile.get("display_name") or "")
        name = display_name or real_name or user_id
        is_bot = bool(user_obj.get("is_bot", False))

        await repo.upsert_slack_user(
            session,
            user_id=user_id,
            name=name,
            real_name=real_name or None,
            is_bot=is_bot,
        )
        await session.commit()
        return {"id": user_id, "name": name}
    except SlackAPIError as exc:
        if exc.error in ("user_not_found", "account_inactive", "no_permission"):
            # User was deleted / deactivated — store a stub so we stop querying
            stub_name = user_id
            await repo.upsert_slack_user(
                session, user_id=user_id, name=stub_name, real_name=None, is_bot=False
            )
            await session.commit()
        else:
            logger.warning("resolve_user(%s): Slack API error %s", user_id, exc.error)
        return {"id": user_id, "name": user_id}
    except Exception:
        logger.exception("resolve_user(%s): unexpected error", user_id)
        return {"id": user_id, "name": user_id}


async def resolve_channel(
    session: AsyncSession,
    channel_id: str,
    token: str,
) -> dict[str, object]:
    """Return {id, name, is_im} for a Slack channel, using cache-first strategy.

    Falls back gracefully on errors: returns {id: channel_id, name: channel_id, is_im: False}.
    """
    cached = await repo.get_slack_channel(session, channel_id)
    stale_threshold = datetime.now(UTC) - timedelta(days=_NAME_CACHE_STALE_DAYS)

    if cached is not None and cached.fetched_at > stale_threshold:
        return {"id": cached.id, "name": cached.name, "is_im": cached.is_im}

    # Not cached or stale — call Slack API
    try:
        client = SlackClient(token=token)
        data = await client._post("conversations.info", channel=channel_id)
        ch: dict[str, object] = data.get("channel", {})  # type: ignore[assignment]
        name = str(ch.get("name") or channel_id)
        is_im = bool(ch.get("is_im", False))
        is_private = bool(ch.get("is_private", False))

        await repo.upsert_slack_channel(
            session,
            channel_id=channel_id,
            name=name,
            is_im=is_im,
            is_private=is_private,
        )
        await session.commit()
        return {"id": channel_id, "name": name, "is_im": is_im}
    except SlackAPIError as exc:
        if exc.error in ("channel_not_found", "not_in_channel", "no_permission"):
            stub_name = channel_id
            await repo.upsert_slack_channel(
                session, channel_id=channel_id, name=stub_name, is_im=False, is_private=False
            )
            await session.commit()
        else:
            logger.warning("resolve_channel(%s): Slack API error %s", channel_id, exc.error)
        return {"id": channel_id, "name": channel_id, "is_im": False}
    except Exception:
        logger.exception("resolve_channel(%s): unexpected error", channel_id)
        return {"id": channel_id, "name": channel_id, "is_im": False}


# ── Permalink helper ───────────────────────────────────────────────────────────


def _make_permalink(channel_id: str, ts: str, subdomain: str | None = None) -> str:
    """Build a Slack deep link for a message.

    When ``subdomain`` is provided (or SLACK_WORKSPACE_SUBDOMAIN env is set),
    we link directly to the workspace: https://<sub>.slack.com/archives/<C>/p<ts>.
    The workspace-direct form avoids Slack's redirector adding ``?name=…&perma=…``
    tracking params that produce a "glitch" page on DMs.

    Falls back to the universal form (https://slack.com/archives/<C>/p<ts>) when
    no subdomain is known — Slack redirects to the correct workspace.

    Either form ends without any query string.
    """
    ts_nodot = ts.replace(".", "")
    sub = subdomain or os.environ.get("SLACK_WORKSPACE_SUBDOMAIN", "").strip()
    if sub:
        return f"https://{sub}.slack.com/archives/{channel_id}/p{ts_nodot}"
    return f"https://slack.com/archives/{channel_id}/p{ts_nodot}"


# ── List / resolve coroutines ──────────────────────────────────────────────────


async def list_unresolved_mentions(
    session: AsyncSession,
    limit: int = 20,
    include_types: list[str] | None = None,
    token: str | None = None,
) -> dict[str, object]:
    """Return the most recent unresolved direct Slack mentions.

    By default only returns rows where mention_type = 'direct' (or NULL for
    legacy rows), filtered to real personal @-you mentions.

    ``include_types`` can override the filter — e.g. ``['direct', 'channel']``
    to see both direct and @channel mentions.

    When a ``token`` is supplied, user and channel names are resolved via the
    Slack API (cache-first); without a token, raw IDs are returned.
    """
    # Default: direct mentions only (include NULL for pre-J9b rows)
    if include_types is None:
        include_types = ["direct"]

    type_filter = or_(
        SlackInboundMessage.mention_type.in_(include_types),
        SlackInboundMessage.mention_type.is_(None),
    )

    # Bot-author filter: exclude rows whose sender is a known bot in slack_users.
    # LEFT OUTER JOIN so unresolved senders (no slack_users row yet) still appear;
    # they'll be filtered next pass once resolve_user() caches their is_bot flag.
    bot_filter = or_(SlackUser.is_bot.is_(False), SlackUser.id.is_(None))

    rows_result = await session.execute(
        select(SlackInboundMessage)
        .outerjoin(SlackUser, SlackUser.id == SlackInboundMessage.user_id)
        .where(SlackInboundMessage.resolved_at.is_(None), type_filter, bot_filter)
        .order_by(SlackInboundMessage.ts.desc())
        .limit(limit)
    )
    rows = list(rows_result.scalars().all())

    count_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id))
        .outerjoin(SlackUser, SlackUser.id == SlackInboundMessage.user_id)
        .where(
            SlackInboundMessage.resolved_at.is_(None),
            type_filter,
            bot_filter,
        )
    )
    total: int = count_result.scalar_one() or 0

    # Resolve workspace subdomain once per request (env override, else None →
    # universal slack.com URL). Avoids per-row env reads.
    subdomain = os.environ.get("SLACK_WORKSPACE_SUBDOMAIN", "").strip() or None

    mentions = []
    for row in rows:
        if token:
            sender = await resolve_user(session, row.user_id, token)
            channel = await resolve_channel(session, row.channel_id, token)
        else:
            sender = {"id": row.user_id, "name": row.user_id}
            channel = {"id": row.channel_id, "name": row.channel_id, "is_im": False}

        mentions.append(
            {
                "id": row.event_id,
                "sender": sender,
                "channel": channel,
                "ts": row.ts,
                "text": row.text,
                "permalink": _make_permalink(row.channel_id, row.ts, subdomain),
                "mention_type": row.mention_type or "direct",
            }
        )

    return {"mentions": mentions, "total_unresolved": total}


async def resolve_mention(
    session: AsyncSession,
    event_id: str,
) -> tuple[bool, int]:
    """Set resolved_at = now() on a single row.

    Idempotent: if already resolved, no-op.
    Returns (found, new_total_unresolved).
    Raises ValueError when the event_id doesn't exist.
    """
    # Fetch first to validate existence
    row_result = await session.execute(
        select(SlackInboundMessage).where(SlackInboundMessage.event_id == event_id)
    )
    row = row_result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"No mention with event_id={event_id!r}")

    if row.resolved_at is None:
        await session.execute(
            update(SlackInboundMessage)
            .where(SlackInboundMessage.event_id == event_id)
            .values(resolved_at=datetime.now(UTC))
        )
        await session.commit()

    # Recount after potential update — only direct / null rows (same filter as list),
    # and exclude bot-authored rows so the count matches the visible queue.
    count_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id))
        .outerjoin(SlackUser, SlackUser.id == SlackInboundMessage.user_id)
        .where(
            SlackInboundMessage.resolved_at.is_(None),
            or_(
                SlackInboundMessage.mention_type == "direct",
                SlackInboundMessage.mention_type.is_(None),
            ),
            or_(SlackUser.is_bot.is_(False), SlackUser.id.is_(None)),
        )
    )
    new_total: int = count_result.scalar_one() or 0
    return True, new_total
