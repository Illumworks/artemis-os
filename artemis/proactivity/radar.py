"""Awaiting-reply radar — Lane R.

Surfaces Slack @mentions and Gmail threads where Jon owes a reply.
Read-only (no sends). Tokens encrypted, bodies never logged.

Two fetchers:
  - fetch_slack_awaiting_reply(session)  — user token, search:read
  - fetch_gmail_awaiting_reply(session)  — existing gmail.readonly creds

Both return a list of RadarItem dataclasses.  The caller (scheduler /
morning-brief path) renders them into a nudge section.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Window: only look at messages/threads from the last N days.
_WINDOW_DAYS: int = 14
# Cap: max items to surface per type per run.
_CAP: int = 5
# Noise-filter: skip senders matching these patterns for Gmail.
_NOREPLY_RE = re.compile(
    r"(no.?reply|noreply|do.not.reply|donotreply|notifications?@|"
    r"newsletter|unsubscribe|mailer-daemon|postmaster)",
    re.IGNORECASE,
)
# Jon's primary email — used to detect his own replies in Gmail threads.
JON_EMAIL: str = "jon.fila@amiralearning.com"


@dataclass(frozen=True)
class RadarItem:
    item_type: str  # "slack_mention" | "gmail_thread"
    item_key: str  # dedup key
    sender: str  # human-readable name/email
    where: str  # channel name or Gmail subject
    permalink: str | None  # deep-link
    # Short snippet — never the full message body; safe to surface.
    snippet: str
    received_at: datetime  # when the unanswered message arrived


# ── Slack fetcher ──────────────────────────────────────────────────────────────


async def fetch_slack_awaiting_reply(session: AsyncSession) -> list[RadarItem]:
    """Return Slack mentions/DMs that Jon has not yet replied to.

    Requires a Slack user token (provider="slack_user") with search:read.
    Falls back gracefully to [] if no user token is stored.
    """
    token = await _resolve_slack_user_token(session)
    if not token:
        logger.debug("radar: no Slack user token found — skipping Slack half")
        return []

    jon_slack_id = await _resolve_jon_slack_id(token)
    if not jon_slack_id:
        logger.warning("radar: could not resolve Jon's Slack user ID — skipping Slack half")
        return []

    from artemis.integrations.slack.client import SlackAPIError, SlackClient

    client = SlackClient(token=token)
    cutoff_days = _WINDOW_DAYS
    query = f"<@{jon_slack_id}>"

    try:
        hits = await client.search_messages(query=query, count=50)
    except SlackAPIError as exc:
        logger.warning("radar: search.messages failed: %s", exc)
        return []

    cutoff_ts = datetime.now(UTC) - timedelta(days=cutoff_days)
    items: list[RadarItem] = []

    for hit in hits:
        if len(items) >= _CAP:
            break

        hit_ts_raw = str(hit.get("ts") or "")
        if not hit_ts_raw:
            continue

        try:
            hit_dt = datetime.fromtimestamp(float(hit_ts_raw), tz=UTC)
        except (ValueError, OSError):
            continue

        if hit_dt < cutoff_ts:
            # search.messages returns newest-first; once we pass the cutoff we're done.
            break

        channel_info: dict[str, Any] = hit.get("channel") or {}  # type: ignore[assignment]
        channel_id = str(channel_info.get("id") or "")
        thread_ts = str(hit.get("thread_ts") or hit_ts_raw)
        item_key = f"{channel_id}:{thread_ts}"

        # Check if Jon has replied after this mention.
        if channel_id and not await _jon_replied_in_slack_thread(
            client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            jon_id=jon_slack_id,
            after_ts=hit_ts_raw,
        ):
            channel_name = str(channel_info.get("name") or channel_id)
            sender_name = str(hit.get("username") or hit.get("user") or "unknown")
            # snippet: use text but truncate — never log full body.
            text_raw = str(hit.get("text") or "")
            snippet = text_raw[:120] + ("…" if len(text_raw) > 120 else "")
            permalink = str(hit.get("permalink") or "")

            items.append(
                RadarItem(
                    item_type="slack_mention",
                    item_key=item_key,
                    sender=sender_name,
                    where=f"#{channel_name}",
                    permalink=permalink or None,
                    snippet=snippet,
                    received_at=hit_dt,
                )
            )

    return items


async def _jon_replied_in_slack_thread(
    client: Any,
    *,
    channel_id: str,
    thread_ts: str,
    jon_id: str,
    after_ts: str,
) -> bool:
    """Return True if Jon posted any message in thread AFTER after_ts."""
    from artemis.integrations.slack.client import SlackAPIError

    try:
        replies = await client.get_conversation_replies(
            channel=channel_id, thread_ts=thread_ts, limit=50
        )
    except SlackAPIError as exc:
        # channel_not_found / thread_not_found / missing_scope — treat as "not replied"
        logger.debug(
            "radar: conversations.replies failed for %s:%s — %s", channel_id, thread_ts, exc
        )
        return False

    for msg in replies:
        msg_user = str(msg.get("user") or "")
        msg_ts = str(msg.get("ts") or "")
        if msg_user == jon_id and msg_ts > after_ts:
            return True
    return False


async def _resolve_slack_user_token(session: AsyncSession) -> str | None:
    """Resolve Jon's Slack user token from the 'slack_user' integration row."""
    from sqlalchemy import select

    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.models import Integration

    result = await session.execute(
        select(Integration)
        .where(
            Integration.provider == "slack_user",
            Integration.status == "active",
        )
        .order_by(Integration.connected_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    creds = decrypt_credentials(bytes(row.encrypted_credentials))
    token = creds.get("access_token") or creds.get("user_token") or creds.get("token")
    return str(token) if token else None


async def _resolve_jon_slack_id(token: str) -> str | None:
    """Look up Jon's Slack user ID using his email via users.lookupByEmail."""
    from artemis.integrations.slack.client import SlackClient

    client = SlackClient(token=token)
    return await client.lookup_user_by_email(JON_EMAIL)


# ── Gmail fetcher ──────────────────────────────────────────────────────────────


async def fetch_gmail_awaiting_reply(session: AsyncSession) -> list[RadarItem]:
    """Return Gmail threads where Jon is a recipient but has NOT replied last.

    Uses the existing gmail.readonly integration credentials (gcal-style
    per-user row with provider="gmail").  Falls back to [] on any error.
    """
    creds = await _resolve_gmail_creds(session)
    if creds is None:
        logger.debug("radar: no Gmail credentials found — skipping Gmail half")
        return []

    from artemis.integrations.gmail.client import GmailAPIError, GmailClient

    client = GmailClient(
        access_token=creds["access_token"],
        refresh_token=creds["refresh_token"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )

    cutoff_epoch = int((datetime.now(UTC) - timedelta(days=_WINDOW_DAYS)).timestamp())
    # Gmail search: newer_than:14d, in:inbox, NOT from Jon.
    query = f"newer_than:{_WINDOW_DAYS}d in:inbox -from:{JON_EMAIL}"

    try:
        messages = await client.list_recent_messages(max_results=30, query=query)
    except GmailAPIError as exc:
        logger.warning("radar: Gmail list_recent_messages failed: %s", exc)
        return []

    # Collect unique thread IDs from the message list.
    seen_threads: set[str] = set()
    thread_ids: list[str] = []
    for msg in messages:
        tid = str(msg.get("threadId") or "")
        if tid and tid not in seen_threads:
            seen_threads.add(tid)
            thread_ids.append(tid)

    items: list[RadarItem] = []

    for thread_id in thread_ids[:20]:  # cap API calls
        if len(items) >= _CAP:
            break

        try:
            thread = await client.get_thread(thread_id)
        except GmailAPIError as exc:
            logger.debug("radar: get_thread(%s) failed: %s", thread_id, exc)
            continue

        thread_messages: list[dict[str, Any]] = thread.get("messages") or []
        if not thread_messages:
            continue

        # Filter out threads where Jon's last reply is the most recent message.
        last_msg = thread_messages[-1]
        last_from = str(last_msg.get("from") or "")
        if JON_EMAIL.lower() in last_from.lower():
            # Jon sent the last message — not awaiting his reply.
            continue

        # Noise filter: skip newsletters, no-reply senders.
        if _NOREPLY_RE.search(last_from):
            continue

        # Check internal date is within window.
        internal_date_ms = str(last_msg.get("internalDate") or "")
        if internal_date_ms:
            try:
                msg_epoch = int(internal_date_ms) // 1000
                if msg_epoch < cutoff_epoch:
                    continue
                received_at = datetime.fromtimestamp(msg_epoch, tz=UTC)
            except (ValueError, OSError):
                received_at = datetime.now(UTC)
        else:
            received_at = datetime.now(UTC)

        subject = str(thread_messages[0].get("subject") or "(no subject)")
        sender = _extract_sender_name(last_from)
        snippet = str(last_msg.get("snippet") or "")[:120]
        permalink = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

        items.append(
            RadarItem(
                item_type="gmail_thread",
                item_key=thread_id,
                sender=sender,
                where=subject,
                permalink=permalink,
                snippet=snippet,
                received_at=received_at,
            )
        )

    return items


def _extract_sender_name(from_header: str) -> str:
    """Return the display name from a From header, or the email address."""
    from_header = from_header.strip()
    # "Display Name <email@example.com>"
    match = re.match(r'^"?([^"<]+?)"?\s*<[^>]+>$', from_header)
    if match:
        return match.group(1).strip()
    # Just an email address.
    return from_header


async def _resolve_gmail_creds(session: AsyncSession) -> dict[str, str] | None:
    """Resolve Gmail OAuth credentials from the Integration table."""
    from sqlalchemy import select

    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.models import Integration

    result = await session.execute(
        select(Integration)
        .where(
            Integration.provider == "gmail",
            Integration.status == "active",
        )
        .order_by(Integration.connected_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    creds = decrypt_credentials(bytes(row.encrypted_credentials))

    # Resolve client_id / client_secret — may be in the integration row or
    # provider config (same pattern as gcal).
    client_id = str(creds.get("client_id") or "")
    client_secret = str(creds.get("client_secret") or "")

    if not client_id or not client_secret:
        # Try provider config table.
        from artemis.integrations import repository as integration_repo

        cfg = await integration_repo.get_provider_config(session, "gmail") or {}
        client_id = client_id or str(cfg.get("client_id") or "")
        client_secret = client_secret or str(cfg.get("client_secret") or "")

    if not client_id or not client_secret:
        logger.warning("radar: Gmail client_id/client_secret not resolvable — skipping")
        return None

    access_token = str(creds.get("access_token") or "")
    refresh_token = str(creds.get("refresh_token") or "")
    if not access_token:
        logger.warning("radar: Gmail access_token missing — skipping")
        return None

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }


# ── Render + surface ──────────────────────────────────────────────────────────


def format_radar_nudge(items: list[RadarItem]) -> str:
    """Render a compact 'awaiting your reply' section for the morning brief DM.

    Returns an empty string if there are no items.
    """
    if not items:
        return ""

    lines: list[str] = [
        f"*{len(items)} {'person is' if len(items) == 1 else 'people are'} waiting on you:*"
    ]
    for item in items[:_CAP]:
        age = _age_label(item.received_at)
        where = item.where
        link = f" (<{item.permalink}|open>)" if item.permalink else ""
        lines.append(f"- {item.sender} in {where}{link} — {age}")

    return "\n".join(lines)


def _age_label(received_at: datetime) -> str:
    delta = datetime.now(UTC) - received_at
    hours = int(delta.total_seconds() // 3600)
    if hours < 2:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    days = delta.days
    return f"{days}d ago"


async def gather_radar_items(
    session: AsyncSession,
) -> list[RadarItem]:
    """Gather Slack + Gmail radar items concurrently, capped + deduped at fetch time."""
    import asyncio

    slack_task = fetch_slack_awaiting_reply(session)
    gmail_task = fetch_gmail_awaiting_reply(session)

    results = await asyncio.gather(slack_task, gmail_task, return_exceptions=True)

    items: list[RadarItem] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
        elif isinstance(r, Exception):
            logger.warning("radar: gather_radar_items partial failure: %s", r)

    # Sort by received_at desc (most urgent first).
    items.sort(key=lambda x: x.received_at, reverse=True)
    return items[: _CAP * 2]  # global cap across both sources
