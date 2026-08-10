"""Shared helper for handling a revoked GCal refresh token.

Called from both the meeting summarizer (J6d) and the token-refresh scheduler
(J10e) whenever a GCalAuthDeadError is raised.  Responsibilities:

  1. Mark the integration row as needs_reauth so the Connectors UI surface the
     amber reconnect CTA.
  2. Send a rate-limited Slack DM to the owner via the Artemis bot so the user
     knows meeting summaries are paused.

Rate-limiting: the DM is only sent when the row's status is NOT already
'needs_reauth' (i.e. this is the first time we're detecting the failure).
Subsequent ticks that happen to pass through will find status='needs_reauth'
and skip the DM.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo

logger = logging.getLogger(__name__)

_RECONNECT_URL = "https://app.artemisos.me/connectors"
_ALERT_TEXT = (
    f"⚠️ Google Calendar is disconnected — meeting summaries are paused. Reconnect: {_RECONNECT_URL}"
)


async def handle_gcal_auth_dead(session: AsyncSession, integration_id: int) -> None:
    """Mark the GCal integration needs_reauth and DM the owner once.

    Safe to call from any async context.  Does NOT commit — caller owns the
    transaction.
    """
    # Fetch current status before marking so we can decide whether to DM.
    try:
        integration = await repo.get_by_id(session, integration_id)
        already_alerted = integration.status == "needs_reauth"
    except Exception:
        already_alerted = False

    await repo.mark_needs_reauth(session, integration_id)
    logger.error("gcal_auth_dead: integration_id=%d marked needs_reauth", integration_id)

    if already_alerted:
        # Already sent the DM on a previous tick — don't spam.
        logger.debug(
            "gcal_auth_dead: DM suppressed (already needs_reauth) for integration_id=%d",
            integration_id,
        )
        return

    await _send_owner_dm(session)


async def _send_owner_dm(session: AsyncSession) -> None:
    """Send a one-time Slack DM to the Artemis owner about the GCal disconnect."""
    try:
        from artemis.integrations.slack.client import SlackClient
        from artemis.proactivity.commitments import (
            _get_slack_token_for_agent,
            _resolve_artemis_dm_recipient,
        )

        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning("gcal_auth_dead: no active Slack token for agent_id='artemis'; skip DM")
            return

        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=_ALERT_TEXT)
        logger.info("gcal_auth_dead: Slack DM sent to owner (recipient=%s)", recipient_id)
    except Exception:
        logger.exception("gcal_auth_dead: failed to send owner Slack DM")
