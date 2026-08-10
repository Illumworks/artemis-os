"""Escalation sweep — Phase 1 of the Artemis hub.

Runs hourly.  For each overdue pending ask (created > ~1 day ago, unresolved,
not yet escalated) Artemis:

  1. Posts a TERMINAL connective comment in-channel:
       "@AgentName, I'll take this — escalating to Jon."
     This comment is TERMINAL: no agent should reply to it.  The original
     agent does not respond to Artemis.

  2. Notifies Jon via her DM (sole-interrupt path from ``artemis.hub.notify``).

Loop-proof guarantee:
  - Artemis's comment is posted with ``agent_id="artemis"`` so the bot-self
    filter in ``integrations_slack_events._is_bot_authored`` silently drops
    any event_callback Slack generates for it (no re-dispatch).
  - ``escalated_at`` is stamped before the Slack calls, so if either call
    fails the row won't be re-escalated until the next sweep.

Routing (non-urgent escalations vs brief):
  - After posting the in-channel comment, Artemis DMing Jon counts as the
    interrupt.  Non-urgent FYIs from other agents are included in the morning
    brief via ``artemis.hub.brief_injection.pending_asks_brief_section``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import artemis.db as _db
from artemis.hub import repository as hub_repo
from artemis.hub.models import AgentPendingAsk

logger = logging.getLogger(__name__)

# The connective comment template Artemis posts in-channel.
# %s = display name of the originating agent (e.g. "Kai", "Callie").
_TERMINAL_COMMENT_TEMPLATE = "@{agent_display}, I'll take this — escalating to Jon."


@dataclass
class EscalationSweepSummary:
    checked: int
    escalated: int
    failed: int


def _agent_display_name(agent_id: str) -> str:
    """Return a human-friendly display name for an agent id."""
    return agent_id.capitalize()


async def run_escalation_sweep() -> EscalationSweepSummary:
    """Find overdue pending asks and escalate each one.

    Returns a summary for logging.  Individual escalation failures are logged
    but do not abort the sweep (best-effort).
    """
    async with _db.SessionLocal() as session:
        overdue = await hub_repo.list_overdue_unescalated(session)

    checked = len(overdue)
    escalated = 0
    failed = 0

    for ask in overdue:
        try:
            ok = await _escalate_one(ask)
            if ok:
                escalated += 1
            else:
                failed += 1
        except Exception:
            logger.exception(
                "hub escalation: unexpected error for ask_id=%d agent=%s channel=%s",
                ask.id,
                ask.agent_id,
                ask.channel_id,
            )
            failed += 1

    return EscalationSweepSummary(checked=checked, escalated=escalated, failed=failed)


async def _escalate_one(ask: AgentPendingAsk) -> bool:
    """Perform the full escalation for a single pending ask.

    Returns True on full success, False if either Slack call failed.
    """

    async with _db.SessionLocal() as session:
        # Stamp escalated_at first — prevents double-fire even if Slack calls
        # fail partway through.
        await hub_repo.mark_escalated(session, ask_id=ask.id)
        await session.commit()

    # Step 1: post terminal connective comment in the agent's channel.
    agent_display = _agent_display_name(ask.agent_id)
    comment_text = _TERMINAL_COMMENT_TEMPLATE.format(agent_display=agent_display)
    channel_ok = await _post_in_channel(
        channel_id=ask.channel_id,
        text=comment_text,
    )

    # Step 2: DM Jon via Artemis's sole-interrupt path.
    dm_text = (
        f"*{agent_display}* asked a question in <#{ask.channel_id}> "
        f"that's been waiting {_age_label(ask)} without a reply:\n\n"
        f"> {ask.summary}\n\n"
        f"I've noted it in-channel — let me know if you want to respond."
    )
    dm_ok = await _dm_jon(text=dm_text, urgent=_is_urgent(ask))

    return channel_ok and dm_ok


def _age_label(ask: AgentPendingAsk) -> str:
    """Return a human-readable age string like '26 hours' or '2 days'."""
    from datetime import UTC, datetime

    delta = datetime.now(UTC) - ask.created_at
    hours = int(delta.total_seconds() / 3600)
    if hours < 48:
        return f"{hours} hours"
    days = hours // 24
    return f"{days} days"


def _is_urgent(ask: AgentPendingAsk) -> bool:
    """Determine whether this ask meets the interrupt bar.

    Conservative: only mark urgent when the summary explicitly mentions
    urgency signals.  The full urgency bar (external deadline / a real person
    waiting / production breaking / a commitment about to slip) is evaluated
    here with simple keyword matching; a future enhancement can use LLM
    classification.
    """
    _URGENCY_KEYWORDS = (
        "urgent",
        "asap",
        "blocking",
        "production",
        "outage",
        "deadline",
        "waiting on you",
        "commitment",
        "slip",
        "external",
    )
    lower = ask.summary.lower()
    return any(kw in lower for kw in _URGENCY_KEYWORDS)


async def _post_in_channel(*, channel_id: str, text: str) -> bool:
    """Post Artemis's terminal comment in the given channel."""
    try:
        async with _db.SessionLocal() as session:
            from artemis.integrations.slack.client import SlackClient
            from artemis.proactivity.scheduler import _get_slack_token_for_agent

            token = await _get_slack_token_for_agent(session, agent_id="artemis")
            if not token:
                logger.warning(
                    "hub escalation: no Artemis Slack token — in-channel comment skipped (channel=%s)",
                    channel_id,
                )
                return False
            await SlackClient(token=token).post_message(channel=channel_id, text=text)
            return True
    except Exception:
        logger.exception(
            "hub escalation: failed to post in-channel comment to channel=%s", channel_id
        )
        return False


async def _dm_jon(*, text: str, urgent: bool) -> bool:
    """DM Jon via the sole-interrupt path (Artemis only)."""
    try:
        async with _db.SessionLocal() as session:
            from artemis.hub.notify import notify_jon

            return await notify_jon(
                session,
                requested_by="artemis",
                text=text,
                urgent=urgent,
            )
    except Exception:
        logger.exception("hub escalation: failed to DM Jon")
        return False
