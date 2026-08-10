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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from artemis.directory.models import DirectoryPerson

logger = logging.getLogger(__name__)

# Attendee emails that are calendars/resources, not people.
_NON_PERSON_EMAIL_MARKERS = ("resource.calendar.google.com", "group.calendar.google.com")


def _name_from_email(email: str) -> str:
    """Derive a display name from an email local-part (e.g. angela.miata → Angela Miata)."""
    local = email.split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) if parts else email


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


async def _resolve_gcal_client() -> Any | None:
    """Build a live GCalClient from the active gcal integration, or None.

    Mirrors the credential path used elsewhere (post_meeting_scheduling /
    agency_gate) so there is one consistent way to authenticate.
    """
    import artemis.db as _db
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.gcal.client import GCalClient

    async with _db.SessionLocal() as session:
        rows = await repo.list_active(session, provider="gcal")
    if not rows:
        return None
    creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
    )


async def sync_directory_from_calendar(session: Any, *, months_back: int = 6) -> int:
    """Harvest people from the owner's recent calendar attendees into directory_people.

    The Slack roster misses people the owner schedules with who aren't in the
    workspace (his own team). Calendar attendees are exactly the scheduling-
    relevant roster, and their emails are guaranteed to match real calendars.

    Names from the calendar NEVER downgrade richer Slack data: on conflict we
    COALESCE (keep an existing non-empty name) and never touch slack_user_id.
    Source is set to "calendar" only for brand-new rows.

    FAILURE-SAFE: returns 0 on any error instead of raising (runs in cron).
    """
    try:
        client = await _resolve_gcal_client()
        if client is None:
            logger.warning("directory sync: no active gcal integration — skipping calendar source")
            return 0

        now = datetime.now(UTC)
        # Walk the past `months_back` in ~monthly windows (list_events caps at 50
        # results per call and exposes no page token, so we page by time window).
        seen: dict[str, dict[str, Any]] = {}
        for i in range(months_back):
            win_end = now - timedelta(days=30 * i)
            win_start = now - timedelta(days=30 * (i + 1))
            try:
                events = await client.list_events(
                    "primary", win_start.isoformat(), win_end.isoformat(), max_results=50
                )
            except Exception:
                logger.warning("directory sync: list_events failed for a window", exc_info=True)
                continue
            for ev in events:
                for att in getattr(ev, "attendees", None) or []:
                    email = (getattr(att, "email", "") or "").strip().lower()
                    if not email or any(mk in email for mk in _NON_PERSON_EMAIL_MARKERS):
                        continue
                    display = (getattr(att, "display_name", "") or "").strip()
                    # Prefer the richest display name seen across all events.
                    prev = seen.get(email)
                    if prev is None or (display and not prev.get("display")):
                        seen[email] = {"display": display}

        upserted = 0
        ts = datetime.now(UTC)
        for email, info in seen.items():
            full_name = info.get("display") or _name_from_email(email)
            first_name, last_name = _split_name(full_name)
            insert_vals: dict[str, Any] = {
                "email": email,
                "full_name": full_name,
                "display_name": info.get("display") or None,
                "first_name": first_name,
                "last_name": last_name,
                "slack_user_id": None,
                "source": "calendar",
                "is_active": True,
                "updated_at": ts,
            }
            stmt = pg_insert(DirectoryPerson).values(**insert_vals)
            # On conflict: keep existing non-empty names (Slack wins), fill gaps,
            # refresh is_active/updated_at, and leave slack_user_id + source intact.
            stmt = stmt.on_conflict_do_update(
                index_elements=["email"],
                set_={
                    "full_name": func.coalesce(
                        func.nullif(DirectoryPerson.full_name, ""), stmt.excluded.full_name
                    ),
                    "first_name": func.coalesce(
                        DirectoryPerson.first_name, stmt.excluded.first_name
                    ),
                    "last_name": func.coalesce(DirectoryPerson.last_name, stmt.excluded.last_name),
                    "is_active": True,
                    "updated_at": ts,
                },
            )
            await session.execute(stmt)
            upserted += 1

        await session.commit()
        logger.info("directory sync: upserted %d people from calendar attendees", upserted)
        return upserted
    except Exception:
        logger.warning("directory calendar sync: failed — returning 0 (cron-safe)", exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
        return 0


async def sync_directory(session: Any) -> int:
    """Run all directory sources (Slack roster + calendar attendees). Cron entry point."""
    total = 0
    total += await sync_directory_from_slack(session)
    total += await sync_directory_from_calendar(session)
    return total
