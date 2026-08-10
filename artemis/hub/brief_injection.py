"""Morning-brief injection for unresolved agent asks.

Non-Artemis agents that have pending asks outstanding (but not yet escalated)
are folded into the morning brief as a "Waiting on you" section, so Jon sees
them in a batched, non-interrupting way.

ROUTING RULE (from artemis-hub-plan.md):
  - Artemis → DMs Jon directly (handled by escalation.py + notify.py).
  - Everyone else (Callie, Kai, …) → batched into the morning brief.

This module provides ``pending_asks_brief_section`` which is called by the
brief generator / scheduler to append agent-pending-ask items to the morning
brief text.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.hub import repository as hub_repo

logger = logging.getLogger(__name__)


async def pending_asks_brief_section(session: AsyncSession) -> str:
    """Return a Slack-formatted string for unresolved non-Artemis pending asks.

    Returns an empty string when there are no items to include.
    The section is appended to the morning brief text; the scheduler includes
    it only when non-empty.
    """
    try:
        asks = await hub_repo.list_unresolved(session)
        # Exclude asks that have already been escalated (Jon has been DM-notified)
        # and exclude Artemis's own asks (she routes directly).
        items = [a for a in asks if a.agent_id != "artemis" and a.escalated_at is None]
        if not items:
            return ""

        lines = ["*Agent asks waiting on you*"]
        for item in items[:8]:  # cap at 8 to avoid wall-of-text
            agent_name = item.agent_id.capitalize()
            lines.append(f"- *{agent_name}* in <#{item.channel_id}>: {item.summary}")

        return "\n".join(lines)
    except Exception:
        logger.warning("hub brief_injection: failed to gather pending asks", exc_info=True)
        return ""
