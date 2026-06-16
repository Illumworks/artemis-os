"""Sole-interrupt notify path — Artemis's exclusive right to reach Jon directly.

HARD RULE: ONLY Artemis (agent_id == "artemis") may call these functions.
Every other agent must queue its FYI into the morning brief (see
``artemis/hub/escalation.py``).  The guard in ``notify_jon`` raises
``PermissionError`` if a non-Artemis caller sneaks through.

Urgency tagging (interrupt bar):
  Tag ``urgent=True`` when any of:
    - External deadline / a real person waiting on Jon
    - Production breaking
    - A commitment Jon made is about to slip

  ``urgent=False`` (default) → DM Jon, but no bypass-silence mechanism is
  triggered.  The actual DND-bypass mechanism (future: iMessage) is a
  follow-up; for now ``urgent`` is just logged + annotated in the DM text.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Only Artemis may use the interrupt path.
_ARTEMIS_AGENT_ID = "artemis"


class InterruptNotAllowed(PermissionError):
    """Raised when a non-Artemis agent tries to use the sole-interrupt path."""


async def notify_jon(
    session: AsyncSession,
    *,
    requested_by: str,
    text: str,
    urgent: bool = False,
) -> bool:
    """Send a direct message to Jon from Artemis.

    Gate: ``requested_by`` MUST be ``"artemis"``.  Any other value raises
    ``InterruptNotAllowed`` — this is the sole-interrupt invariant.

    Args:
        session: DB session (for resolving Jon's Slack ID + Artemis token).
        requested_by: Agent making the request. Must be ``"artemis"``.
        text: The message text to send.
        urgent: When True the message is prefixed with an URGENT marker and
            logged at WARNING level.  The actual DND-bypass mechanism is a
            planned follow-up; for now routing to DM is the interrupt.

    Returns:
        True on success, False if no token could be resolved (logged, not raised).
    """
    if requested_by != _ARTEMIS_AGENT_ID:
        raise InterruptNotAllowed(
            f"notify_jon: sole-interrupt violation — only 'artemis' may call "
            f"notify_jon; got requested_by={requested_by!r}"
        )

    outbound = text
    if urgent:
        outbound = f":rotating_light: *URGENT* :rotating_light:\n{text}"
        logger.warning("notify_jon: URGENT interrupt — %s", text[:200])
    else:
        logger.info("notify_jon: DM to Jon — %s", text[:200])

    try:
        from artemis.proactivity.scheduler import _get_slack_token_for_agent
        from artemis.proactivity.scheduler import _resolve_morning_brief_recipient
        from artemis.integrations.slack.client import SlackClient

        token = await _get_slack_token_for_agent(session, agent_id=_ARTEMIS_AGENT_ID)
        if not token:
            logger.warning("notify_jon: no Slack token for artemis — DM not sent")
            return False

        recipient_id = await _resolve_morning_brief_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=outbound)
        return True
    except Exception:
        logger.exception("notify_jon: failed to send DM to Jon")
        return False
