"""Shared helper for handling a revoked Gmail refresh token.

Called from any call site that catches GmailAuthDeadError.  Responsibilities:

  1. Mark the personal gcal/Google integration row as needs_reauth so the
     Connectors UI surfaces the amber reconnect CTA.  Gmail and GCal share the
     same personal Google credential (same refresh token); a dead Gmail token
     means the whole personal Google connection needs to be reconnected.
  2. Send a rate-limited Slack DM to the owner via the Artemis bot so the user
     knows Gmail access is paused.

Rate-limiting: the DM is only sent when the row's status is NOT already
'needs_reauth' (i.e. this is the first time we're detecting the failure).
Subsequent calls that find status='needs_reauth' skip the DM.

Reuses the same _send_owner_dm and repository helpers as gcal/auth_dead.py.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo

logger = logging.getLogger(__name__)

_RECONNECT_URL = "https://app.artemisos.me/connectors"
_ALERT_TEXT = (
    f"⚠️ Google account is disconnected — Gmail access is paused. Reconnect: {_RECONNECT_URL}"
)


async def handle_gmail_auth_dead(session: AsyncSession) -> None:
    """Mark the personal Google (gcal) integration needs_reauth and DM the owner once.

    Gmail and GCal share the same personal Google refresh token (stored in both
    the google_credentials table and mirrored into the gcal integration row).
    A dead Gmail token means the entire personal Google connection is dead, so
    we mark the gcal integration row and notify the owner — the same action
    taken by handle_gcal_auth_dead.

    Safe to call from any async context.  Does NOT commit — caller owns the
    transaction.
    """
    # Find the active personal gcal integration row.
    integrations = await repo.list_active(session, provider="gcal")
    if not integrations:
        logger.warning("gmail_auth_dead: no active gcal integration row found; skipping mark")
        return

    integration = integrations[0]
    already_alerted = integration.status == "needs_reauth"

    await repo.mark_needs_reauth(session, integration.id)
    logger.error(
        "gmail_auth_dead: personal Google integration_id=%d marked needs_reauth",
        integration.id,
    )

    if already_alerted:
        logger.debug(
            "gmail_auth_dead: DM suppressed (already needs_reauth) for integration_id=%d",
            integration.id,
        )
        return

    await _send_owner_dm(session)


async def _send_owner_dm(session: AsyncSession) -> None:
    """Send a one-time Slack DM to the Artemis owner about the Gmail disconnect."""
    try:
        from artemis.integrations.slack.client import SlackClient
        from artemis.proactivity.commitments import (
            _get_slack_token_for_agent,
            _resolve_artemis_dm_recipient,
        )

        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning("gmail_auth_dead: no active Slack token for agent_id='artemis'; skip DM")
            return

        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=_ALERT_TEXT)
        logger.info("gmail_auth_dead: Slack DM sent to owner (recipient=%s)", recipient_id)
    except Exception:
        logger.exception("gmail_auth_dead: failed to send owner Slack DM")
