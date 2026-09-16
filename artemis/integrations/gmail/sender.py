"""Resolve a Gmail client for a given Google credential *purpose*.

Every other Gmail path in this codebase hardcodes ``purpose == "personal"``
(see ``artemis.proactivity.agency_gate._resolve_personal_gmail_client`` and the
near-identical copy in ``artemis.crisis_content.writeback``). The Champions
digest sends from the *marketing* credential -- ``amiracentral@`` -- so it needs
a resolver that takes the purpose as an argument rather than assuming one.

This module deliberately does not refactor those two existing personal paths.
They are load-bearing and heavily monkeypatched in tests; widening them is a
separate change with its own risk.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
from artemis.google_docs.models import GoogleCredential
from artemis.google_integration import (
    GooglePurpose,
    google_has_any_scope,
    resolve_google_oauth_client_config,
)
from artemis.integrations.gmail.client import GmailClient

logger = logging.getLogger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailSenderNotReadyError(RuntimeError):
    """The requested purpose cannot send mail, with a reason worth repeating.

    Raised rather than returning None so a caller cannot mistake "no credential"
    for "sent". The message distinguishes *not connected* from *connected but
    missing the scope* -- those need different fixes, and a single fail-closed
    error that conflates them sends people hunting the wrong problem.
    """


async def resolve_gmail_client(
    session: AsyncSession,
    *,
    purpose: GooglePurpose = "personal",
    connected_email: str | None = None,
) -> GmailClient:
    """Return a send-capable GmailClient for ``purpose``.

    ``connected_email`` names WHICH account, and callers that send on a
    schedule must pass it. "The marketing credential" is not a single thing:
    on 2026-09-16 there were three (amiracentral@, julie.kalinowski@,
    kristen.spiker@), only one of which could send. Picking the most recently
    updated row -- which is what this did first -- is a coin flip that lands
    differently every time the background refresh loop runs, so the weekly
    digest would have sent from whoever happened to refresh last.

    With several candidates and no name given, this raises rather than
    choosing. A wrong sender is worse than a failed run, and silently picking
    one is how you find out in front of the recipients.

    Refreshes the access token if it is close to expiry, and persists any
    in-request refresh the client performs on a 401.
    """
    stmt = select(GoogleCredential).where(GoogleCredential.purpose == purpose)
    if connected_email:
        stmt = stmt.where(GoogleCredential.connected_email == connected_email)
    candidates = list((await session.execute(stmt.order_by(GoogleCredential.id))).scalars())

    if not candidates:
        if connected_email:
            raise GmailSenderNotReadyError(
                f"No {purpose} Google credential for {connected_email}. Connect it at "
                f"/api/google/oauth/start?purpose={purpose} while signed in as that account."
            )
        raise GmailSenderNotReadyError(
            f"No {purpose} Google credential is connected. Connect one at "
            f"/api/google/oauth/start?purpose={purpose}."
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(str(c.connected_email) for c in candidates))
        raise GmailSenderNotReadyError(
            f"{len(candidates)} {purpose} Google credentials are connected ({names}); "
            f"pass connected_email to say which one should send. Refusing to guess -- "
            f"the most recently refreshed row changes on its own."
        )
    credential = candidates[0]
    if not google_has_any_scope(credential.scope, GMAIL_SEND_SCOPE):
        raise GmailSenderNotReadyError(
            f"The {purpose} Google credential ({credential.connected_email}) is connected "
            f"but does not carry {GMAIL_SEND_SCOPE}. A refresh will not add it -- "
            f"re-authorize at /api/google/oauth/start?purpose={purpose} while signed in "
            f"as that account."
        )

    config = await resolve_google_oauth_client_config(session)
    now = datetime.now(UTC)
    if credential.expiry <= now + timedelta(seconds=60):
        if not credential.refresh_token:
            raise GmailSenderNotReadyError(
                f"The {purpose} Google credential has no refresh token; re-authorize at "
                f"/api/google/oauth/start?purpose={purpose}."
            )
        try:
            refreshed = await refresh_access_token(
                refresh_token=credential.refresh_token,
                client_id=config.client_id,
                client_secret=config.client_secret,
            )
        except GoogleReauthRequiredError as exc:
            raise GmailSenderNotReadyError(
                f"The {purpose} Google refresh token has been revoked; re-authorize at "
                f"/api/google/oauth/start?purpose={purpose}."
            ) from exc
        credential.access_token = refreshed.access_token
        credential.refresh_token = refreshed.refresh_token
        credential.expiry = refreshed.expiry
        if refreshed.scope:
            credential.scope = refreshed.scope
        credential.updated_at = now

    async def _on_tokens_refreshed(
        new_access_token: str, new_refresh_token: str, new_expires_at: float
    ) -> None:
        credential.access_token = new_access_token
        if new_refresh_token:
            credential.refresh_token = new_refresh_token
        credential.expiry = datetime.fromtimestamp(new_expires_at, tz=UTC)
        credential.updated_at = datetime.now(UTC)

    return GmailClient(
        access_token=credential.access_token,
        refresh_token=credential.refresh_token or "",
        client_id=config.client_id,
        client_secret=config.client_secret,
        expires_at=credential.expiry.timestamp(),
        on_tokens_refreshed=_on_tokens_refreshed,
    )
