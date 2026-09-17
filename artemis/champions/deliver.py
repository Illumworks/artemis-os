"""Send the digest. The only module here that can reach a human.

Delivery is deliberately the last thing wired and the easiest to keep switched
off: ``send_digest`` takes its recipients as an argument and has no default
list, so nothing reaches the success team because a flag was forgotten.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.champions.digest import (
    Digest,
    render_html,
    render_slack,
    render_slack_blocks,
    render_text,
)
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gmail.sender import resolve_gmail_client

logger = logging.getLogger(__name__)

MARKETING_ACCOUNT = "amiracentral@amiralearning.com"
SENDER = "Hannah Slater <hannah.slater@amiralearning.com>"
REPLY_TO = "hannah.slater@amiralearning.com"

#: #hub. Kai must also carry this in its allowed_channel_ids, which is enforced
#: in our own code and is a separate gate from the Slack scope.
HUB_CHANNEL = "C0B26KNKGCT"


@dataclass
class DeliveryResult:
    email_message_id: str | None = None
    slack_targets: list[str] | None = None
    errors: list[str] | None = None


async def send_email(
    session: AsyncSession,
    digest: Digest,
    *,
    to: Sequence[str],
    subject_prefix: str = "",
) -> str:
    """Send the digest as Hannah, through the marketing credential.

    Gmail does not error on an unverified send-as -- it silently rewrites the
    From header and sends anyway -- so a message id returned here proves the
    call succeeded, not that it carries the sender we asked for.
    """
    if not to:
        raise ValueError("send_email requires explicit recipients")
    client = await resolve_gmail_client(
        session, purpose="marketing", connected_email=MARKETING_ACCOUNT
    )
    result = await client.send_message(
        to=list(to),
        subject=f"{subject_prefix}Champions community digest — {digest.window_label}",
        body=render_text(digest),
        html_body=render_html(digest),
        from_addr=SENDER,
        reply_to=REPLY_TO,
    )
    return str(result.get("id") or "")


async def _kai_token(session: AsyncSession) -> tuple[str, list[str]]:
    row = (
        await session.execute(
            text(
                "select encrypted_credentials from integrations "
                "where provider='slack' and agent_id='kai'"
            )
        )
    ).fetchone()
    if row is None:
        raise RuntimeError("No Slack credential for agent_id='kai'")
    creds = decrypt_credentials(row[0])
    token = str(creds.get("bot_token") or creds.get("access_token") or "")
    if not token:
        raise RuntimeError("Kai's Slack credential carries no bot token")
    allowed = creds.get("allowed_channel_ids") or []
    return token, [str(c) for c in (allowed if isinstance(allowed, list) else [])]


async def post_slack(
    session: AsyncSession,
    digest: Digest,
    *,
    channels: Sequence[str],
    preamble: str = "",
) -> list[str]:
    """Post as Kai to each target. A DM id (``U...``) is allowed; a channel must
    be on Kai's allowlist, which is our own gate and not Slack's."""
    token, allowed = await _kai_token(session)
    blocks = render_slack_blocks(digest)
    if preamble:
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": preamble}},
            *blocks,
        ]
    # `text` is the notification and accessibility fallback, not a duplicate of
    # the blocks -- Slack shows it in the sidebar and to screen readers, and a
    # message with blocks and no text is unreadable in both.
    fallback = render_slack(digest)

    sent: list[str] = []
    async with httpx.AsyncClient(timeout=30) as http:
        for target in channels:
            if target.startswith("C") and allowed and target not in allowed:
                raise RuntimeError(
                    f"Kai is not allowed to post to {target}; add it to "
                    f"allowed_channel_ids first (currently {allowed})"
                )
            resp = await http.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "channel": target,
                    "text": fallback,
                    "blocks": blocks,
                    "unfurl_links": False,
                },
            )
            payload = resp.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Slack post to {target} failed: {payload.get('error')}")
            sent.append(str(payload.get("channel") or target))
    return sent


async def resolve_slack_user(session: AsyncSession, email: str) -> str:
    """Slack user id for an email address.

    users.lookupByEmail, never users.list -- that paginates and its first page
    silently omits people, which has cost us an approver before now.
    """
    token, _ = await _kai_token(session)
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            "https://slack.com/api/users.lookupByEmail",
            headers={"Authorization": f"Bearer {token}"},
            params={"email": email},
        )
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Slack lookup for {email} failed: {payload.get('error')}")
    return str((payload.get("user") or {}).get("id") or "")
