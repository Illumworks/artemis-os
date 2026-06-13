"""Propose→confirm agency-writes gate.

This is the ONLY path through which Artemis executes side-effecting actions
on Jon's behalf.  The gate enforces:

1. No action executes without an explicit approval from the target user.
2. A proposal executes at most once (idempotency guard on double-yes).
3. The approved payload is the executed payload (immutable after insert).
4. Every state transition is audit-logged with actor + timestamp.
5. Pending proposals are matched to the SPECIFIC user who proposed; a bare
   "yes"/"no" falls through to normal conversation unless a proposal is
   unambiguously pending for that user.

Flow
----
Artemis drafts an action → ``propose_action`` → DM preview to Jon →
Jon replies "yes" (or "yes A<id>") → ``try_apply_proposed_action_reply`` →
``approve_proposed_action`` → ``execute_proposed_action`` → DM result.

Executors
---------
- calendar.create   — GCalClient.create_event + cache refresh
- calendar.update   — GCalClient.update_event + cache refresh
- calendar.respond  — NOT implemented (no RSVP API); route is proposable but
                      executor raises NotImplementedError.  Wire it in when the
                      calendar scope includes RSVP.
- jira.create       — JiraClient.create_issue
- slack.send        — Slack user-token chat.postMessage (as Jon)
- gmail.send        — personal Google credential Gmail messages.send

Reply syntax
------------
  yes           → approve the single pending proposal for this user (if exactly 1)
  yes A<id>     → approve proposal id=<id> for this user
  no            → reject the single pending proposal (if exactly 1)
  no A<id>      → reject proposal id=<id>

A bare "yes"/"no" with zero or multiple pending proposals returns None
(falls through to normal conversation).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import ProposedAction, RadarSurfacedItem
from artemis.proactivity.proposed_actions_repository import (
    approve_proposed_action,
    create_proposed_action,
    expire_stale_proposals,
    list_pending_for_user,
    mark_executed,
    mark_failed,
    reject_proposed_action,
)

logger = logging.getLogger(__name__)

# ── Reply-matcher regexes ─────────────────────────────────────────────────────
# Matches: "yes", "yes A12", "yes a12"
_YES_RE = re.compile(r"^yes(?:\s+[Aa](\d+))?\s*$", re.IGNORECASE)
# Matches: "no", "no A12"
_NO_RE = re.compile(r"^no(?:\s+[Aa](\d+))?\s*$", re.IGNORECASE)


# ── Credential resolution helpers ─────────────────────────────────────────────


async def _resolve_gcal_client() -> Any:
    """Return a live GCalClient from the active integration, or None."""
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


async def _resolve_jira_client() -> Any:
    """Return a live JiraClient from the active Jira integration, or None.

    Uses resolve_jira_config which reads per-field from DB then falls back to
    env vars — consistent with how the rest of the codebase resolves Jira creds.
    Returns None if any required field is missing (MissingProviderConfigError).
    """
    import artemis.db as _db
    from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_jira_config
    from artemis.integrations.jira.client import JiraClient

    async with _db.SessionLocal() as session:
        try:
            cfg = await resolve_jira_config(session)
        except MissingProviderConfigError:
            return None
    return JiraClient(site_url=cfg.site_url, email=cfg.email, api_token=cfg.api_token)


async def _resolve_slack_user_token(session: AsyncSession) -> str | None:
    from artemis.proactivity.radar import _resolve_slack_user_token as _resolve_radar_token

    return await _resolve_radar_token(session)


async def _resolve_personal_gmail_client(session: AsyncSession) -> Any:
    from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
    from artemis.google_docs.repository import get_google_credential
    from artemis.google_integration import google_has_any_scope, resolve_google_oauth_client_config
    from artemis.integrations.gmail.client import GmailClient

    credential = await get_google_credential(session, user_id=1, purpose="personal")
    if credential is None:
        raise RuntimeError("No personal Google credential connected")
    if not google_has_any_scope(credential.scope, "https://www.googleapis.com/auth/gmail.send"):
        raise RuntimeError("Reconnect Google personal account to grant gmail.send access")

    config = await resolve_google_oauth_client_config(session)
    now = datetime.now(UTC)
    if credential.expiry <= now + timedelta(seconds=60):
        if not credential.refresh_token:
            raise RuntimeError("Reconnect Google personal account to refresh Gmail access")
        try:
            refreshed = await refresh_access_token(
                refresh_token=credential.refresh_token,
                client_id=config.client_id,
                client_secret=config.client_secret,
            )
        except GoogleReauthRequiredError as exc:
            raise RuntimeError("Reconnect Google personal account to refresh Gmail access") from exc
        credential.access_token = refreshed.access_token
        credential.refresh_token = refreshed.refresh_token
        credential.expiry = refreshed.expiry
        if refreshed.scope:
            credential.scope = refreshed.scope
        credential.updated_at = now

    return GmailClient(
        access_token=credential.access_token,
        refresh_token=credential.refresh_token or "",
        client_id=config.client_id,
        client_secret=config.client_secret,
    )


# ── Executors (one per action_type) ──────────────────────────────────────────


async def _execute_calendar_create(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    from artemis.integrations.gcal.sync import sync_recent_gcal_events_cache
    from artemis.integrations.gcal.types import EventDateTime

    p = action.payload
    client = await _resolve_gcal_client()
    if client is None:
        raise RuntimeError("No active GCal integration")

    calendar_id: str = str(p.get("calendar_id", "primary"))
    summary: str = str(p.get("summary", ""))
    start_raw: dict[str, Any] = p.get("start", {})
    end_raw: dict[str, Any] = p.get("end", {})
    attendees: list[str] = p.get("attendees") or []
    description: str | None = p.get("description")

    start = EventDateTime.model_validate(start_raw)
    end = EventDateTime.model_validate(end_raw)

    event = await client.create_event(
        calendar_id=calendar_id,
        summary=summary,
        start=start,
        end=end,
        attendees=attendees if attendees else None,
        description=description,
    )

    # Refresh cache so the new event is immediately visible.
    try:
        await sync_recent_gcal_events_cache(session, calendar_id=calendar_id)
    except Exception:
        logger.warning("Post-create gcal cache refresh failed — non-fatal", exc_info=True)

    link: str = event.html_link or ""
    return {"event_id": event.id, "link": link, "summary": event.summary or summary}


async def _execute_calendar_update(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    from artemis.integrations.gcal.sync import sync_recent_gcal_events_cache
    from artemis.integrations.gcal.types import EventDateTime

    p = action.payload
    client = await _resolve_gcal_client()
    if client is None:
        raise RuntimeError("No active GCal integration")

    calendar_id: str = str(p.get("calendar_id", "primary"))
    event_id: str = str(p.get("event_id", ""))
    if not event_id:
        raise ValueError("calendar.update payload must include event_id")

    summary: str | None = p.get("summary")
    start_raw: dict[str, Any] | None = p.get("start")
    end_raw: dict[str, Any] | None = p.get("end")
    attendees: list[str] | None = p.get("attendees")
    description: str | None = p.get("description")

    start = EventDateTime.model_validate(start_raw) if start_raw else None
    end = EventDateTime.model_validate(end_raw) if end_raw else None

    event = await client.update_event(
        calendar_id=calendar_id,
        event_id=event_id,
        summary=summary,
        start=start,
        end=end,
        attendees=attendees,
        description=description,
    )

    try:
        await sync_recent_gcal_events_cache(session, calendar_id=calendar_id)
    except Exception:
        logger.warning("Post-update gcal cache refresh failed — non-fatal", exc_info=True)

    link: str = event.html_link or ""
    return {"event_id": event.id, "link": link, "summary": event.summary or ""}


async def _execute_jira_create(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    p = action.payload
    client = await _resolve_jira_client()
    if client is None:
        raise RuntimeError("No active Jira integration")

    result = await client.create_issue(
        project_key=str(p.get("project_key", "")),
        summary=str(p.get("summary", "")),
        description=str(p.get("description", "")),
        assignee_account_id=p.get("assignee_account_id"),
        priority_name=str(p.get("priority_name", "Medium")),
        labels=p.get("labels") or [],
        issue_type_name=str(p.get("issue_type_name", "Task")),
    )
    key: str = str(result.get("key", ""))
    # Build a browse link — base_url available from creds is not in scope here;
    # callers can reconstruct it from the key if needed.
    return {"key": key, "id": str(result.get("id", ""))}


async def _execute_calendar_respond(
    session: AsyncSession, action: ProposedAction
) -> dict[str, Any]:
    # calendar.respond is not implemented — no RSVP endpoint in the current
    # GCalClient.  The action_type is accepted in the CHECK constraint for
    # forward-compatibility.  When the RSVP scope is added, implement here.
    raise NotImplementedError(
        "calendar.respond executor not yet implemented — "
        "needs calendar RSVP scope; wire in when GCalClient gains respond_to_event()"
    )


async def _execute_slack_send(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    from artemis.integrations.slack.client import SlackClient

    p = action.payload
    token = await _resolve_slack_user_token(session)
    if not token:
        raise RuntimeError("No active Slack user token")

    channel = str(p.get("channel") or "").strip()
    text = str(p.get("text") or "").strip()
    thread_ts_raw = p.get("thread_ts")
    thread_ts = str(thread_ts_raw).strip() if thread_ts_raw else None

    if not channel:
        raise ValueError("slack.send payload must include channel")
    if not text:
        raise ValueError("slack.send payload must include text")

    posted = await SlackClient(token=token).post_message(
        channel=channel,
        text=text,
        thread_ts=thread_ts,
    )
    return {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts or "",
        "message_ts": str(posted.get("ts") or ""),
    }


async def _execute_gmail_send(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    p = action.payload
    client = await _resolve_personal_gmail_client(session)

    to = str(p.get("to") or "").strip()
    subject = str(p.get("subject") or "")
    body = str(p.get("body") or "").strip()
    thread_id_raw = p.get("thread_id")
    in_reply_to_raw = p.get("in_reply_to")
    thread_id = str(thread_id_raw).strip() if thread_id_raw else None
    in_reply_to = str(in_reply_to_raw).strip() if in_reply_to_raw else None

    if not to:
        raise ValueError("gmail.send payload must include to")
    if not body:
        raise ValueError("gmail.send payload must include body")

    sent = await client.send_message(
        to=to,
        subject=subject,
        body=body,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )
    return {
        "message_id": str(sent.get("id") or ""),
        "thread_id": str(sent.get("threadId") or thread_id or ""),
        "to": to,
        "subject": subject,
    }


# ── Generic executor dispatch ─────────────────────────────────────────────────

_EXECUTOR_MAP = {
    "calendar.create": _execute_calendar_create,
    "calendar.update": _execute_calendar_update,
    "calendar.respond": _execute_calendar_respond,
    "jira.create": _execute_jira_create,
    "slack.send": _execute_slack_send,
    "gmail.send": _execute_gmail_send,
}


async def execute_proposed_action(session: AsyncSession, action: ProposedAction) -> dict[str, Any]:
    """Dispatch to the correct integration executor.

    PRECONDITION: action.status MUST be 'approved'.  This function raises
    ValueError if called with any other status — callers in this module always
    enforce approved status before calling; this guard makes it impossible to
    accidentally bypass the gate.
    """
    if action.status != "approved":
        raise ValueError(
            f"execute_proposed_action called with status={action.status!r}; "
            "only 'approved' proposals may execute"
        )

    executor = _EXECUTOR_MAP.get(action.action_type)
    if executor is None:
        raise ValueError(f"Unknown action_type={action.action_type!r}")

    return await executor(session, action)


# ── Proposal creation (public API) ───────────────────────────────────────────


async def propose_action(
    session: AsyncSession,
    *,
    action_type: str,
    payload: dict[str, Any],
    preview: str,
    requested_by: str,
    target_user_id: str,
    ttl_hours: int = 24,
) -> ProposedAction:
    """Insert a proposal and return the row.  Caller is responsible for DM-ing Jon."""
    return await create_proposed_action(
        session,
        action_type=action_type,
        payload=payload,
        preview=preview,
        requested_by=requested_by,
        target_user_id=target_user_id,
        ttl_hours=ttl_hours,
    )


async def propose_radar_slack_reply(
    session: AsyncSession,
    *,
    radar_item_id: int,
    reply_text: str,
    requested_by: str,
    target_user_id: str,
) -> tuple[ProposedAction, RadarSurfacedItem]:
    from sqlalchemy import select

    reply_text = reply_text.strip()
    if not reply_text:
        raise ValueError("reply_text is required")

    result = await session.execute(
        select(RadarSurfacedItem).where(RadarSurfacedItem.id == radar_item_id)
    )
    radar_item = result.scalar_one_or_none()
    if radar_item is None:
        raise LookupError(f"Radar item #{radar_item_id} not found")
    if radar_item.item_type != "slack_mention":
        raise ValueError(f"Radar item #{radar_item_id} is not a Slack mention")

    try:
        channel, thread_ts = radar_item.item_key.split(":", 1)
    except ValueError as exc:
        raise ValueError(f"Radar item #{radar_item_id} has an invalid Slack key") from exc

    preview = f'reply in {radar_item.label}: "{reply_text}"'
    action = await propose_action(
        session,
        action_type="slack.send",
        payload={
            "channel": channel,
            "thread_ts": thread_ts,
            "text": reply_text,
        },
        preview=preview,
        requested_by=requested_by,
        target_user_id=target_user_id,
    )
    await send_proposal_dm(session, action)
    return action, radar_item


async def send_proposal_dm(
    session: AsyncSession,
    action: ProposedAction,
) -> None:
    """DM Jon a preview of the proposed action.

    Resolves the Artemis Slack bot token + Jon's DM recipient the same way
    commitments.py does, so there is a single source of truth for identity.
    """
    from artemis.integrations.slack.client import SlackClient
    from artemis.proactivity.commitments import (
        _get_slack_token_for_agent,
        _resolve_artemis_dm_recipient,
    )

    token = await _get_slack_token_for_agent(session, agent_id="artemis")
    if not token:
        logger.warning("send_proposal_dm: no active Slack token for artemis agent")
        return

    recipient = await _resolve_artemis_dm_recipient(session)
    id_tag = f"A{action.id}"
    text = (
        f"I'd like to {action.action_type.replace('.', ' ')} — {action.preview}. "
        f"Reply *yes {id_tag}* to approve, *no {id_tag}* to skip."
    )
    await SlackClient(token=token).post_dm(user=recipient, text=text)


# ── Reply handler (plug into route_inbound) ───────────────────────────────────


async def try_apply_proposed_action_reply(
    session: AsyncSession,
    *,
    text: str,
    slack_user_id: str,
    now: datetime | None = None,
) -> str | None:
    """Handle a yes/no reply to a pending proposal.

    Returns an ack string if a proposal was matched and processed, or None to
    fall through to normal conversation.

    CRITICAL: A bare "yes" or "no" with zero or multiple pending proposals
    returns None — it is NEVER swallowed as an action approval.
    """
    current = now or datetime.now(UTC)
    normalized = text.strip()

    yes_match = _YES_RE.match(normalized)
    no_match = _NO_RE.match(normalized)
    if yes_match is None and no_match is None:
        return None

    is_yes = yes_match is not None
    match_obj = yes_match if is_yes else no_match
    assert match_obj is not None

    id_str = match_obj.group(1)  # None if bare "yes"/"no"

    # First expire any stale proposals so they don't pollute the pending list.
    await expire_stale_proposals(session, now=current)

    if id_str is not None:
        # Explicit id — target this specific proposal.
        action_id = int(id_str)
        if is_yes:
            row = await approve_proposed_action(
                session, action_id=action_id, actor=slack_user_id, now=current
            )
            if row is None:
                return f"No pending proposal A{action_id} found (it may have already been handled)."
            # Re-fetch to confirm approved status before executing.
            if row.status != "approved":
                return f"Proposal A{action_id} is already {row.status}."
            return await _run_approved_action(session, row, slack_user_id)
        else:
            row = await reject_proposed_action(
                session, action_id=action_id, actor=slack_user_id, now=current
            )
            if row is None:
                return f"No pending proposal A{action_id} found."
            await session.commit()
            return f"Skipped — proposal A{action_id} ({row.action_type}) cancelled."
    else:
        # Bare "yes"/"no" — only proceed if exactly one proposal is pending.
        pending = await list_pending_for_user(session, target_user_id=slack_user_id, now=current)
        if len(pending) == 0:
            # No pending proposals — fall through to normal conversation.
            return None
        if len(pending) > 1:
            # Ambiguous — ask for a specific id.
            tags = ", ".join(f"A{p.id}" for p in pending)
            return (
                f"You have {len(pending)} pending proposals ({tags}). "
                f"Reply *yes A<id>* or *no A<id>* to handle a specific one."
            )
        # Exactly one — process it.
        action = pending[0]
        if is_yes:
            row = await approve_proposed_action(
                session, action_id=action.id, actor=slack_user_id, now=current
            )
            if row is None:
                return f"Proposal A{action.id} is no longer pending."
            return await _run_approved_action(session, row, slack_user_id)
        else:
            row = await reject_proposed_action(
                session, action_id=action.id, actor=slack_user_id, now=current
            )
            if row is None:
                return f"Proposal A{action.id} is no longer pending."
            await session.commit()
            return f"Skipped — proposal A{action.id} ({action.action_type}) cancelled."


async def _run_approved_action(
    session: AsyncSession,
    action: ProposedAction,
    actor: str,
) -> str:
    """Execute an approved proposal and return a human-readable result string.

    Safety: action MUST be in 'approved' state.  execute_proposed_action
    will raise ValueError if this invariant is violated.
    """
    # Verify status before any execution attempt — belt-and-suspenders.
    if action.status != "approved":
        raise ValueError(
            f"_run_approved_action called with status={action.status!r} for id={action.id}"
        )

    try:
        result = await execute_proposed_action(session, action)
        await mark_executed(session, action_id=action.id, result=result, actor=actor)
        await session.commit()

        # Format a friendly result message.
        if action.action_type in ("calendar.create", "calendar.update"):
            link = result.get("link", "")
            summary = result.get("summary", "")
            link_str = f" — {link}" if link else ""
            return (
                f"Done — calendar event '{summary}' {action.action_type.split('.')[1]}d.{link_str}"
            )
        if action.action_type == "jira.create":
            key = result.get("key", "")
            return f"Done — Jira issue {key} created."
        if action.action_type == "slack.send":
            return "Done — Slack message posted."
        if action.action_type == "gmail.send":
            to = str(result.get("to", "")).strip()
            subject = str(result.get("subject", "")).strip()
            subject_part = f" re: {subject}" if subject else ""
            return f"Done — email sent to {to}{subject_part}."
        return f"Done — {action.action_type} executed."
    except NotImplementedError as exc:
        # Deferred executors (slack.send, gmail.send, calendar.respond).
        await mark_failed(session, action_id=action.id, error=str(exc), actor=actor)
        await session.commit()
        return f"Sorry, {action.action_type} is not yet available. Proposal cancelled."
    except Exception as exc:
        error_msg = str(exc)[:500]
        logger.exception(
            "agency_gate: executor failed for action_id=%s action_type=%s",
            action.id,
            action.action_type,
        )
        await mark_failed(session, action_id=action.id, error=error_msg, actor=actor)
        await session.commit()
        return f"Something went wrong executing {action.action_type}: {error_msg}"
