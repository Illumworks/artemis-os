"""Sync the company roster from Slack into directory_people.

``sync_directory_from_slack`` pulls the full workspace member list via Slack's
``users.list`` (the verified roster source: ~58 people, all with name+email),
derives first/last names from each member's real_name, and UPSERTs by lowercased
email.

FAILURE-SAFE: this runs in a weekly cron. Any Slack or DB error is logged and
the function returns 0 — it never raises.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from artemis.directory.models import DirectoryPerson

logger = logging.getLogger(__name__)


def _split_name(real_name: str) -> tuple[str | None, str | None]:
    """Derive (first_name, last_name) from a real_name.

    Single token → (token, None). Multiple tokens → (first, last) where last is
    the final whitespace-separated token.
    """
    tokens = (real_name or "").split()
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return tokens[0], None
    return tokens[0], tokens[-1]


async def _load_working_slack_client() -> Any | None:
    """Return a SlackClient whose token yields a roster with profile emails.

    Iterates every active Slack integration row, decrypts its credentials, and
    returns the first client whose ``list_users`` returns members carrying
    profile emails. Returns None if none work.
    """
    import artemis.db as _db
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.slack.client import SlackClient

    async with _db.SessionLocal() as session:
        rows = await repo.list_active(session, provider="slack")

    for row in rows:
        try:
            creds = decrypt_credentials(bytes(row.encrypted_credentials))
        except Exception:
            logger.warning("directory sync: could not decrypt slack creds for a row", exc_info=True)
            continue
        token = creds.get("bot_token") or creds.get("access_token") or creds.get("token")
        if not token:
            continue
        client = SlackClient(token=str(token))
        try:
            members = await client.list_users(limit=500)
        except Exception:
            logger.warning("directory sync: list_users failed for a slack token", exc_info=True)
            continue
        # Accept this token if at least one member has a profile email.
        for m in members:
            profile = m.get("profile") or {}
            if isinstance(profile, dict) and profile.get("email"):
                return client
    return None


async def sync_directory_from_slack(session: Any) -> int:
    """Sync the Slack roster into directory_people; return count upserted.

    FAILURE-SAFE: returns 0 on any error instead of raising (runs in cron).
    """
    try:
        client = await _load_working_slack_client()
        if client is None:
            logger.warning("directory sync: no working Slack token with roster emails — skipping")
            return 0

        members = await client.list_users(limit=500)

        upserted = 0
        now = datetime.now(UTC)
        for m in members:
            if m.get("is_bot") or m.get("deleted"):
                continue
            profile = m.get("profile") or {}
            if not isinstance(profile, dict):
                continue
            email = str(profile.get("email") or "").strip().lower()
            if not email:
                continue

            real_name = str(m.get("real_name") or profile.get("real_name") or "").strip()
            display_name = str(profile.get("display_name") or "").strip() or None
            first_name, last_name = _split_name(real_name)
            slack_user_id = str(m.get("id") or "") or None

            values: dict[str, Any] = {
                "email": email,
                "full_name": real_name or email,
                "display_name": display_name,
                "first_name": first_name,
                "last_name": last_name,
                "slack_user_id": slack_user_id,
                "source": "slack",
                "is_active": True,
                "updated_at": now,
            }
            stmt = (
                pg_insert(DirectoryPerson)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["email"],
                    set_={
                        "full_name": values["full_name"],
                        "display_name": values["display_name"],
                        "first_name": values["first_name"],
                        "last_name": values["last_name"],
                        "slack_user_id": values["slack_user_id"],
                        "source": "slack",
                        "is_active": True,
                        "updated_at": now,
                    },
                )
            )
            await session.execute(stmt)
            upserted += 1

        await session.commit()
        logger.info("directory sync: upserted %d people from Slack", upserted)
        return upserted
    except Exception:
        logger.warning("directory sync: failed — returning 0 (cron-safe)", exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
        return 0
