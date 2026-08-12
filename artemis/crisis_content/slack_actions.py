"""Slack interactivity dispatch for crisis-content decisions (slice B2c, CCA5).

Called from the dispatch branch ``artemis/routes/integrations_slack_interactivity.py``
adds for the two crisis-content ``action_id``s. This module owns:

  - resolving the clicking Slack user to an email (via ``DirectoryPerson``)
    and checking it against the per-route allowlist
    (``artemis.crisis_content.authorization``)
  - the already-decided / double-click guard
    (``artemis.crisis_content.decisions.is_blocked_by_existing_decision``)
  - persisting the decision (``artemis.crisis_content.decisions.record_decision``)
  - updating the original card in place after a decision
  - scheduling the write-back + Jen notification (CCA7,
    ``artemis.crisis_content.writeback.schedule_decision_writeback``) once an
    ``Approve`` decision has actually been recorded -- fire-and-forget, off
    this request's path (see that module for why) -- **but deliberately
    NEVER for an ``Edit in doc`` decision; see ``_handle_edit_in_doc``'s
    docstring below.**

**CCA12: the "Request changes" modal is gone.** It used to live here --
``views.open``, its ``view_submission`` handler, the note it collected, the
``private_metadata`` that carried the target back to the submit handler, and
``CRISIS_CONTENT_VIEW_CALLBACK_ID``. All deleted, along with the tests that
covered them (see ``briefs/cca12-edit-in-doc-button.md``): the vendor's team
asked to edit directly in the document rather than describe a change in
Slack, and real use confirmed the modal was friction pointing the wrong way
(Angela couldn't tell where to edit, pasted a rewrite into the thread, then
edited the doc by hand anyway). The second button is now ``Edit in doc``
(``ACTION_EDIT_IN_DOC``) -- a Block Kit ``url`` button that ALSO carries an
``action_id``, so the same tap that opens the document in the approver's
browser also delivers a ``block_actions`` interaction here. See
``_handle_edit_in_doc``.

Identity is taken ONLY from the verified payload's top-level ``user.id`` --
this module never reads a button ``value`` for WHO clicked; that field
exists only to identify the TARGET (``card_id``, ``route``). The signature
verification that makes ``user.id`` trustworthy happens in the route BEFORE
any of this module runs -- see
``artemis/routes/integrations_slack_interactivity.py``.

Every public entry point here is written to never raise: unexpected errors
are caught and logged, and the route acks Slack with 200 either way. A retry
storm on a broken button is worse than a silent no-op -- the same policy the
``pipeline_approval_*`` dispatch already follows in that route.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.crisis_content.authorization import is_authorized_for_route
from artemis.crisis_content.decisions import (
    Decision,
    get_latest_decision,
    is_blocked_by_existing_decision,
    record_decision,
)
from artemis.crisis_content.notify import (
    ACTION_APPROVE,
    ACTION_EDIT_IN_DOC,
    render_decision_message,
    render_editing_in_doc_message,
)
from artemis.crisis_content.transitions import (
    Route,
    find_posted_location,
    find_reopening_decision,
)
from artemis.crisis_content.writeback import schedule_decision_writeback
from artemis.directory.models import DirectoryPerson
from artemis.integrations.slack.client import SlackClient

logger = logging.getLogger(__name__)

__all__ = [
    "CRISIS_CONTENT_ACTION_IDS",
    "handle_crisis_content_block_action",
]

CRISIS_CONTENT_ACTION_IDS = (ACTION_APPROVE, ACTION_EDIT_IN_DOC)

# CCA12: the one-time "what a suggestion cannot express" invite, threaded
# onto the card exactly once per genuine Edit-in-doc decision -- see
# _reply_edit_invitation.
_EDIT_INVITATION_TEXT = (
    "Opened for edits. If something isn't a specific wording change — a question, "
    "or the whole angle — drop it here."
)

_ACK: dict[str, Any] = {}


@dataclass(frozen=True)
class _Target:
    card_id: int
    route: Route


def _parse_target(value: str) -> _Target | None:
    """Parse a button's ``value`` -- ``f"{card_id}:{route}"`` -- into a ``_Target``.

    Returns ``None`` on any malformed shape rather than raising; callers ack
    Slack with 200 and log a warning instead of 500ing on a button whose
    ``value`` this endpoint doesn't recognize.
    """
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    raw_id, route = parts
    if route not in ("asset", "copy"):
        return None
    try:
        card_id = int(raw_id)
    except ValueError:
        return None
    return _Target(card_id=card_id, route=cast("Route", route))


async def _resolve_email(
    session: AsyncSession, slack_user_id: str, *, access_token: str = ""
) -> str | None:
    """Best-effort Slack user id -> email. ``None`` on any miss.

    Unlike the ``pipeline_approval_*`` flow's ``_resolve_decided_by`` (which
    always wants SOME display label, falling back to username/raw id), this
    needs a strict email-or-nothing: the whole point is deciding whether we
    KNOW who this is well enough to check them against an allowlist. A miss
    here always means "unauthorized" downstream, never "authorized under a
    fallback label" -- silently widening an allowlist by accident is exactly
    the mistake a fallback label would risk.

    Tries ``directory_people`` first, then falls back to Slack's own
    ``users.info``.

    **Why the fallback exists (production incident, 2026-08-12).** This
    originally read ``directory_people`` alone. In the live database all four
    crisis-content approvers were present by email with
    ``slack_user_id = NULL`` -- the directory sync had never populated ids for
    them -- so every lookup missed, authorization failed closed, and the first
    real click got "I don't recognize you as an approver". Not just for one
    person: NOBODY could approve anything, and the pipeline looked functional
    the whole time because no code path errored.

    Slack is the authority on its own user ids, so asking it directly is both
    more correct and immune to directory drift. The directory is kept as the
    first hop because it needs no network call.
    """
    if not slack_user_id:
        return None
    try:
        result = await session.execute(
            select(DirectoryPerson.email).where(DirectoryPerson.slack_user_id == slack_user_id)
        )
        email = result.scalar_one_or_none()
    except Exception:
        logger.exception(
            "crisis_content: directory lookup failed for slack_user_id=%s", slack_user_id
        )
        email = None
    if email:
        return str(email)

    if not access_token:
        logger.warning(
            "crisis_content: no directory email for slack_user_id=%s and no token to ask "
            "Slack -- treating as unknown",
            slack_user_id,
        )
        return None
    try:
        profile_email = await SlackClient(token=access_token).lookup_user_email(slack_user_id)
    except Exception:
        logger.exception(
            "crisis_content: users.info lookup failed for slack_user_id=%s", slack_user_id
        )
        return None
    if profile_email:
        logger.info(
            "crisis_content: resolved slack_user_id=%s via users.info (not in directory_people)",
            slack_user_id,
        )
    return profile_email


def _display_label(email: str | None, slack_user_id: str) -> str:
    """Best-effort human label for the outcome line ("Approved by X")."""
    if email:
        return email
    if slack_user_id:
        return f"<@{slack_user_id}>"
    return "unknown"


async def _post_ephemeral(response_url: str | None, text: str) -> None:
    """Best-effort ephemeral reply via ``response_url``. Never raises.

    ``response_url`` is per-interaction and needs no bot token -- Slack
    supplies it on every ``block_actions``/``view_submission`` payload. A
    failure to deliver this is logged, never escalated: it is feedback about
    a click that has already been (correctly) rejected or ignored, not a
    write that needs to be retried.
    """
    if not response_url:
        logger.warning("crisis_content: no response_url to deliver ephemeral reply %r", text)
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                response_url,
                json={
                    "response_type": "ephemeral",
                    # MUST be explicit. Observed in production 2026-08-12: without it,
                    # a rejected click replaced the whole card with this one line and
                    # the post copy was destroyed in the channel. Slack's docs do not
                    # pin the default for an interactive-message response_url, so never
                    # rely on it.
                    "replace_original": False,
                    "text": text,
                },
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("crisis_content: failed to POST ephemeral reply to response_url")


async def _reply_edit_invitation(
    session: AsyncSession, *, card_id: int, route: Route, access_token: str
) -> None:
    """Thread the one-time "what a suggestion cannot express" invite (CCA12).

    Per ``briefs/cca12-edit-in-doc-button.md`` "On click" step 3: reply once
    in the card's own thread, inviting whatever a Google Docs suggestion
    cannot express -- a question, or "this whole angle needs a rethink."
    This call is synchronous with the decision but never raises back into
    the caller -- a delivery failure here must not undo (or even look like
    it undid) a decision that is already committed by the time this runs.

    "Once" is enforced by the caller, not by anything in here: the only
    caller (``_handle_edit_in_doc``) only reaches this function after
    ``record_decision`` has successfully inserted a NEW row, and a repeat
    click on the same (card, route) is already turned away earlier by
    ``is_blocked_by_existing_decision`` (a second ``changes_requested``
    attempt in a row IS blocked -- see that function's docstring) before any
    of this runs. So the append-only decision guard IS the one-reply guard;
    there is no separate dedup ledger here.

    Deliberately NOT a Jen mention -- see the module docstring's "Do NOT
    notify Jen on this click": this text is an open invitation to whoever is
    already in the thread, never a targeted ping.

    Finds where to thread via ``find_posted_location`` -- the
    ``crisis_content_notifications`` row CCA9 populates with
    ``channel_id``/``message_ts`` at post time (see
    ``artemis.crisis_content.notify.post_transition_card``). No row (or an
    incomplete pair) means "nothing to thread onto" and this is skipped with
    a warning rather than guessing a destination -- notably true for every
    row seeded directly as a bare ``CrisisContentCard`` (no notification),
    which is how most of this module's existing tests build their fixtures.
    """
    if not access_token:
        logger.warning(
            "crisis_content: no Slack token -- cannot post the edit-in-doc thread reply "
            "for card_id=%s route=%s",
            card_id,
            route,
        )
        return

    location = await find_posted_location(session, card_id, route)
    if location is None:
        logger.warning(
            "crisis_content: no posted-location record for card_id=%s route=%s -- "
            "cannot thread the edit-in-doc invite",
            card_id,
            route,
        )
        return
    channel_id, message_ts = location
    if not channel_id or not message_ts:
        logger.warning(
            "crisis_content: posted-location incomplete for card_id=%s route=%s "
            "(channel_id=%r message_ts=%r) -- cannot thread the edit-in-doc invite",
            card_id,
            route,
            channel_id,
            message_ts,
        )
        return

    try:
        await SlackClient(token=access_token).post_message(
            channel=channel_id, text=_EDIT_INVITATION_TEXT, thread_ts=message_ts
        )
    except Exception:
        logger.exception(
            "crisis_content: failed to post edit-in-doc thread reply for card_id=%s route=%s",
            card_id,
            route,
        )


def _message_ts_from_payload(payload: dict[str, Any]) -> str | None:
    container = payload.get("container")
    if isinstance(container, dict):
        ts = container.get("message_ts")
        if ts:
            return str(ts)
    message = payload.get("message")
    if isinstance(message, dict):
        ts = message.get("ts")
        if ts:
            return str(ts)
    return None


async def handle_crisis_content_block_action(
    session: AsyncSession,
    *,
    action_id: str,
    value: str,
    payload: dict[str, Any],
    access_token: str,
) -> JSONResponse:
    """Dispatch one ``crisis_content_approve`` / ``crisis_content_edit_in_doc`` click.

    Never raises -- any unexpected error is logged and acked with 200,
    matching the interactivity route's overall "never 500 on a button" policy.
    """
    try:
        return await _handle_block_action(
            session, action_id=action_id, value=value, payload=payload, access_token=access_token
        )
    except Exception:
        logger.exception("crisis_content: unhandled error dispatching action_id=%r", action_id)
        return JSONResponse(status_code=200, content=_ACK)


async def _handle_block_action(
    session: AsyncSession,
    *,
    action_id: str,
    value: str,
    payload: dict[str, Any],
    access_token: str,
) -> JSONResponse:
    target = _parse_target(value)
    if target is None:
        logger.warning(
            "crisis_content: malformed button value %r for action_id=%r", value, action_id
        )
        return JSONResponse(status_code=200, content=_ACK)

    user_obj = payload.get("user")
    slack_user_id = str(user_obj.get("id") or "") if isinstance(user_obj, dict) else ""
    response_url_raw = payload.get("response_url")
    response_url = str(response_url_raw) if response_url_raw else None

    email = await _resolve_email(session, slack_user_id, access_token=access_token)
    if email is None:
        logger.warning(
            "crisis_content: click from unresolvable slack_user_id=%r (card=%s route=%s) "
            "-- denying",
            slack_user_id,
            target.card_id,
            target.route,
        )
        await _post_ephemeral(
            response_url,
            "I don't recognize you as an approver for this pipeline, so this click wasn't "
            "recorded.",
        )
        return JSONResponse(status_code=200, content=_ACK)

    if not is_authorized_for_route(email, target.route):
        logger.warning(
            "crisis_content: %s not authorized for route=%s (card=%s)",
            email,
            target.route,
            target.card_id,
        )
        await _post_ephemeral(
            response_url,
            f"You're not an approver for the {target.route} route, so this click wasn't recorded.",
        )
        return JSONResponse(status_code=200, content=_ACK)

    attempted: Decision = "approved" if action_id == ACTION_APPROVE else "changes_requested"
    latest = await get_latest_decision(session, target.card_id, target.route)
    # A prior decision only blocks while it is still ABOUT the current copy.
    # If the route has been reopened -- the copy was revised after that
    # decision (CCA11) -- the old decision refers to text that no longer
    # exists, so the re-fired card's buttons must work. Without this, a
    # reopened card posts with live buttons that always answer "Already
    # decided", which is worse than not re-posting at all: it tells the
    # approver something needs re-reviewing and then refuses to let them.
    reopened = await find_reopening_decision(session, target.card_id, target.route)
    if (
        latest is not None
        and reopened is None
        and is_blocked_by_existing_decision(latest, attempted)
    ):
        who = _display_label(latest.decided_by_email, latest.decided_by_slack_user_id)
        await _post_ephemeral(
            response_url,
            f"Already decided: {latest.decision} by {who} at {latest.decided_at.isoformat()}.",
        )
        return JSONResponse(status_code=200, content=_ACK)

    if action_id == ACTION_EDIT_IN_DOC:
        return await _handle_edit_in_doc(
            session,
            target=target,
            slack_user_id=slack_user_id,
            email=email,
            message_ts=_message_ts_from_payload(payload),
            access_token=access_token,
        )

    # ACTION_APPROVE: decide now, synchronously.
    message_ts = _message_ts_from_payload(payload)
    row = await record_decision(
        session,
        card_id=target.card_id,
        route=target.route,
        decision="approved",
        decided_by_slack_user_id=slack_user_id,
        decided_by_email=email,
        slack_message_ts=message_ts,
    )
    schedule_decision_writeback(row.id)
    text, blocks = render_decision_message(
        decision="approved",
        actor_label=_display_label(email, slack_user_id),
        decided_at=row.decided_at,
        note=None,
    )
    return JSONResponse(
        status_code=200,
        content={"replace_original": True, "text": text, "blocks": blocks},
    )


async def _handle_edit_in_doc(
    session: AsyncSession,
    *,
    target: _Target,
    slack_user_id: str,
    email: str | None,
    message_ts: str | None,
    access_token: str,
) -> JSONResponse:
    """Record the ``Edit in doc`` decision and repaint the card -- no doc write, no Jen ping.

    Per ``briefs/cca12-edit-in-doc-button.md`` "On click": record a
    ``changes_requested`` decision with ``note = NULL`` (the button's ``url``
    already sent the approver straight to the document -- there is nothing
    to describe), repaint the card to say who is editing, and reply once in
    the thread inviting whatever a Google Docs suggestion cannot express.

    **This is the ONLY caller of ``record_decision`` that deliberately never
    calls ``schedule_decision_writeback``.** That call is what schedules the
    doc line + Drive ``@mention`` + email (``artemis.crisis_content.writeback``,
    CCA7) -- scheduling it here would ping Jen the instant someone taps the
    button, before a single edit exists in the document; she would open the
    doc to find nothing changed. In the doc-editing workflow the document
    itself is the message: Jen sees the edits where she is already working,
    and the eventual ``Approve`` still notifies her normally through the
    unchanged path below. **This is a deliberate reduction, not an
    oversight -- do not "restore" the call here.** If Jen later proves to
    need an explicit nudge, that is a future suggestion-detection slice
    (batched, and suppressed when a human already pinged her in-thread), not
    a reason to schedule today's write-back on this click.

    Authorization and the already-decided/reopen checks already ran in the
    caller (``_handle_block_action``) before this function is reached --
    same rules as ``Approve``, per the brief's "Same authorization check,
    same route rules."
    """
    row = await record_decision(
        session,
        card_id=target.card_id,
        route=target.route,
        decision="changes_requested",
        decided_by_slack_user_id=slack_user_id,
        decided_by_email=email,
        note=None,
        slack_message_ts=message_ts,
    )
    # Deliberately NOT schedule_decision_writeback(row.id) -- see docstring.

    actor_mention = f"<@{slack_user_id}>" if slack_user_id else _display_label(email, slack_user_id)
    text, blocks = render_editing_in_doc_message(
        actor_mention=actor_mention, decided_at=row.decided_at
    )

    await _reply_edit_invitation(
        session, card_id=target.card_id, route=target.route, access_token=access_token
    )

    return JSONResponse(
        status_code=200,
        content={"replace_original": True, "text": text, "blocks": blocks},
    )
