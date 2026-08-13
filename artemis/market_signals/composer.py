"""Assemble and post Callie's one combined daily brief to #market-signals.

See this package's ``__init__`` for the decision and the section contract.

Why the composer owns delivery rather than each feed
---------------------------------------------------
Two sessions were independently building toward this channel, and the failure
mode we were heading for was two posts a day in the channel created to reduce
noise. One writer, three contributors.

Once-per-day is enforced by the database, not by the scheduler
--------------------------------------------------------------
Delivery reserves a row in ``morning_brief_deliveries`` under
``delivery_kind='market_signals_brief'``, which carries
``uq_morning_brief_delivery_once_per_day UNIQUE (delivery_kind, provider,
recipient_id, delivery_date)``. A second run the same day loses the insert and
returns without posting. That matters more than it looks: the section builders
MARK THEIR ITEMS REPORTED as they build, so a double-post would also silently
consume a day of screentime signals into a message nobody reads twice. Reserving
before building is what makes the whole thing safe to retry.

No migration for any of this — that table already had exactly the right shape.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.config import settings

logger = logging.getLogger(__name__)

_JOB_ID = "market_signals.daily_brief"
_DELIVERY_KIND = "market_signals_brief"
_PROVIDER = "slack"

# Section builders, in the order they appear in the post. Campaign signals lead
# because they are the ones with a buying clock attached; screentime is context.
# Each entry is (heading, dotted import path) — imported lazily and individually
# so a feed that does not exist yet (crisis, still being built in the other
# session) simply contributes nothing instead of breaking the import.
_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("Campaign signals", "artemis.market_signals.campaign_section", "build_campaign_section"),
    ("Crisis signals", "artemis.crisis_content.brief_section", "build_crisis_section"),
    ("Screen time", "artemis.screentime.reporting", "build_screentime_section"),
)


async def _resolve_section(
    module_path: str, func_name: str
) -> Callable[[AsyncSession], Awaitable[str | None]] | None:
    """Import one section builder, or ``None`` if it does not exist yet.

    Deliberately tolerant of ImportError/AttributeError: the three feeds are
    being built by different sessions on different days, and a brief that
    refuses to post because one contributor has not landed yet is worse than a
    brief with two sections.
    """
    try:
        module = __import__(module_path, fromlist=[func_name])
    except Exception:
        logger.info("market_signals: no %s yet — skipping that section", module_path)
        return None
    func = getattr(module, func_name, None)
    if func is None:
        logger.info("market_signals: %s has no %s — skipping", module_path, func_name)
        return None
    return func  # type: ignore[no-any-return]


async def _mention_text(session: AsyncSession) -> str:
    """``<@U…>`` mentions for the people who should read this, or a plain fallback.

    Jon asked for Josh and Angela specifically. Resolved through Slack's own
    records rather than ``directory_people``, whose ``slack_user_id`` was NULL
    for every real approver and silently broke crisis-content approvals.
    Unresolvable ids degrade to names — a brief that posts without a mention is
    fine; a brief that does not post is not.
    """
    from artemis.integrations.slack.client import SlackClient
    from artemis.proactivity.commitments import _get_slack_token_for_agent

    wanted = ("joshua.mukai@amiralearning.com", "angela.miata@amiralearning.com")
    try:
        token = await _get_slack_token_for_agent(session, agent_id="callie")
        if not token:
            return "Josh, Angela"
        client = SlackClient(token=token)
        ids = [uid for email in wanted if (uid := await client.lookup_user_by_email(email))]
        if ids:
            return " ".join(f"<@{uid}>" for uid in ids)
    except Exception:
        logger.warning("market_signals: could not resolve mentions", exc_info=True)
    return "Josh, Angela"


async def build_daily_brief(session: AsyncSession) -> str | None:
    """Assemble today's brief, or ``None`` when every feed is quiet.

    Returns ``None`` rather than posting an empty brief. A daily message that
    says "nothing today" trains people to stop opening it, and the individual
    cards in #campaign-signals already cover anyone who wants everything.
    """
    parts: list[str] = []
    for heading, module_path, func_name in _SECTIONS:
        builder = await _resolve_section(module_path, func_name)
        if builder is None:
            continue
        try:
            body = await builder(session)
        except Exception:
            # The contract says sections do not raise; this is belt-and-braces,
            # because one feed's bug must not cost the other two their day.
            logger.warning(
                "market_signals: section %s raised — omitting it", func_name, exc_info=True
            )
            continue
        if body and body.strip():
            parts.append(f"*{heading}*\n{body.strip()}")

    if not parts:
        logger.info("market_signals: every feed was quiet — no brief today")
        return None

    mention = await _mention_text(session)
    stamp = datetime.now(UTC).strftime("%A %d %B")
    header = f"*Market signals — {stamp}*\n{mention}"
    return "\n\n".join([header, *parts])


async def _reserve_today(session: AsyncSession, channel: str) -> bool:
    """Claim today's slot. ``False`` means someone already has it.

    Insert-and-catch rather than check-then-insert: the unique constraint is the
    arbiter, so two runs racing cannot both decide the slot is free.
    """
    try:
        await session.execute(
            text(
                "INSERT INTO morning_brief_deliveries "
                "(delivery_kind, provider, recipient_id, delivery_date, status) "
                "VALUES (:kind, :provider, :recipient, :day, 'reserved')"
            ),
            {
                "kind": _DELIVERY_KIND,
                "provider": _PROVIDER,
                "recipient": channel,
                "day": date.today(),
            },
        )
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        logger.info("market_signals: today's brief is already reserved — not posting again")
        return False


async def _mark(session: AsyncSession, channel: str, status: str, error: str | None = None) -> None:
    await session.execute(
        text(
            "UPDATE morning_brief_deliveries SET status = :status, last_error = :err, "
            "delivered_at = CASE WHEN :status = 'sent' THEN now() ELSE delivered_at END, "
            "updated_at = now() "
            "WHERE delivery_kind = :kind AND provider = :provider "
            "AND recipient_id = :recipient AND delivery_date = :day"
        ),
        {
            "status": status,
            "err": error,
            "kind": _DELIVERY_KIND,
            "provider": _PROVIDER,
            "recipient": channel,
            "day": date.today(),
        },
    )
    await session.commit()


async def _release_today(session: AsyncSession, channel: str) -> None:
    """Give today's slot back so a retry can have it.

    Called only when the post FAILED. Leaving the row as 'failed' would satisfy
    the unique constraint and silently forbid any retry until tomorrow, burning
    a day for a transient Slack error.
    """
    await session.execute(
        text(
            "DELETE FROM morning_brief_deliveries WHERE delivery_kind = :kind "
            "AND provider = :provider AND recipient_id = :recipient AND delivery_date = :day"
        ),
        {
            "kind": _DELIVERY_KIND,
            "provider": _PROVIDER,
            "recipient": channel,
            "day": date.today(),
        },
    )
    await session.commit()


async def post_daily_brief(session: AsyncSession) -> dict[str, Any]:
    """Reserve today's slot, build the brief, post it as Callie.

    Two orderings here are load-bearing, and the second one I got wrong first
    time round.

    **Reserve before building.** Building MARKS FEED ITEMS REPORTED, so a losing
    racer that built first would consume a day of screentime signals into a
    message it then declines to send.

    **Commit the marks only after the post succeeds.** The section builders write
    their "already reported" markers on the session we hand them and never commit
    (``artemis.memory.store.write_observation`` leaves the transaction to its
    caller — that is what makes this possible). So a failed post rolls back,
    the signals stay unreported, and they appear in the next brief instead of
    vanishing into a message nobody received. Without this, any Slack failure
    silently ate a day of every feed's signals — the marks were durable and the
    brief was not.

    On failure the reservation is released too, so a retry today is allowed
    rather than being blocked by its own bookkeeping.
    """
    channel = settings.market_signals_channel_id.strip()
    if not channel:
        logger.info("market_signals: no channel configured — brief disabled")
        return {"posted": False, "reason": "no_channel"}

    if not await _reserve_today(session, channel):
        return {"posted": False, "reason": "already_reserved"}

    try:
        # Anything the sections mark is now pending on this transaction.
        body = await build_daily_brief(session)
        if not body:
            # Nothing to report is a success: quiet days should not retry all day.
            # Committing here is correct and cheap — a quiet build marked nothing.
            await session.commit()
            await _mark(session, channel, "sent", error="nothing to report")
            return {"posted": False, "reason": "quiet_day"}

        from artemis.integrations.slack.client import SlackClient
        from artemis.proactivity.commitments import _get_slack_token_for_agent
        from artemis.writing_rules.agent_lint import lint_agent_text

        token = await _get_slack_token_for_agent(session, agent_id="callie")
        if not token:
            raise RuntimeError("no Callie Slack token — cannot deliver the brief")

        await SlackClient(token=token).post_message(channel=channel, text=lint_agent_text(body))
        # Delivered. NOW the "already reported" markers become durable.
        await session.commit()
        await _mark(session, channel, "sent")
        logger.info("market_signals: posted the daily brief to %s", channel)
        return {"posted": True, "channel": channel, "chars": len(body)}
    except Exception as exc:
        # Discard the reported-markers for a brief nobody received, and hand the
        # day back so a retry is possible.
        await session.rollback()
        await _release_today(session, channel)
        logger.exception("market_signals: failed to post the daily brief — released today's slot")
        return {"posted": False, "reason": "error", "error": str(exc)}


async def run_daily_brief() -> dict[str, Any]:
    """Cron entry point. Never raises."""
    try:
        async with _db.SessionLocal() as session:
            return await post_daily_brief(session)
    except Exception as exc:  # pragma: no cover - cron guard
        logger.exception("market_signals: run_daily_brief failed")
        return {"posted": False, "error": str(exc)}


def register_market_signals_schedule(scheduler: Any) -> None:
    """Register the daily brief. Idempotent; dormant with no channel set.

    Note there must be exactly ONE cron posting to this channel. The
    standalone screentime digest job is deliberately not registered any more —
    it contributes a section here instead.
    """
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    if not settings.market_signals_channel_id.strip():
        logger.info("market_signals: no channel configured — not scheduling the brief")
        return

    trigger = CronTrigger.from_crontab(
        settings.market_signals_brief_cron, timezone=settings.market_signals_brief_tz
    )
    scheduler.add_job(
        run_daily_brief,
        trigger=trigger,
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "market_signals: registered daily brief cron %s (%s) -> channel %s",
        settings.market_signals_brief_cron,
        settings.market_signals_brief_tz,
        settings.market_signals_channel_id,
    )


__all__ = [
    "build_daily_brief",
    "post_daily_brief",
    "register_market_signals_schedule",
    "run_daily_brief",
]
