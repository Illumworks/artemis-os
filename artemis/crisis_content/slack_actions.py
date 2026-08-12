"""Slack interactivity dispatch for crisis-content decisions (slice B2c, CCA5).

Called from the dispatch branch ``artemis/routes/integrations_slack_interactivity.py``
adds for the two crisis-content ``action_id``s and the ``view_submission``
this package's "Request changes" modal produces. This module owns:

  - resolving the clicking Slack user to an email (via ``DirectoryPerson``)
    and checking it against the per-route allowlist
    (``artemis.crisis_content.authorization``)
  - opening the "why" modal (``views.open``) for Request changes, and
    handling its ``view_submission``
  - the already-decided / double-click guard
    (``artemis.crisis_content.decisions.is_blocked_by_existing_decision``)
  - persisting the decision (``artemis.crisis_content.decisions.record_decision``)
  - updating the original card in place after a decision
  - scheduling the write-back + Jen notification (CCA7,
    ``artemis.crisis_content.writeback.schedule_decision_writeback``) once a
    decision has actually been recorded -- fire-and-forget, off this
    request's path (see that module for why)

Identity is taken ONLY from the verified payload's top-level ``user.id`` --
this module never reads a button ``value`` or a modal's ``private_metadata``
for WHO clicked; those fields exist only to identify the TARGET
(``card_id``, ``route``). The signature verification that makes ``user.id``
trustworthy happens in the route BEFORE any of this module runs -- see
``artemis/routes/integrations_slack_interactivity.py``.

Every public entry point here is written to never raise: unexpected errors
are caught and logged, and the route acks Slack with 200 either way. A retry
storm on a broken button is worse than a silent no-op -- the same policy the
``pipeline_approval_*`` dispatch already follows in that route.
"""

from __future__ import annotations

import json
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
    ACTION_REQUEST_CHANGES,
    jen_mention,
    render_decision_message,
)
from artemis.crisis_content.transitions import Route, find_posted_location
from artemis.crisis_content.writeback import schedule_decision_writeback
from artemis.directory.models import DirectoryPerson
from artemis.integrations.slack.client import SlackClient

logger = logging.getLogger(__name__)

__all__ = [
    "CRISIS_CONTENT_ACTION_IDS",
    "CRISIS_CONTENT_VIEW_CALLBACK_ID",
    "handle_crisis_content_block_action",
    "handle_crisis_content_view_submission",
]

CRISIS_CONTENT_ACTION_IDS = (ACTION_APPROVE, ACTION_REQUEST_CHANGES)
CRISIS_CONTENT_VIEW_CALLBACK_ID = "crisis_content_request_changes_modal"

_NOTE_BLOCK_ID = "crisis_content_note_block"
_NOTE_ACTION_ID = "crisis_content_note_input"

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


async def _update_card_via_response_url(
    response_url: str | None, *, text: str, blocks: list[dict[str, Any]]
) -> None:
    """Best-effort card replacement via ``response_url``. Never raises.

    Used by the ``view_submission`` path: a modal submission's own HTTP
    response only controls the MODAL, not the card message that opened it --
    only ``response_url`` (captured from the original ``block_actions``
    payload and carried through the modal's ``private_metadata``) can
    replace that original message from here. The decision row is already
    committed by the time this runs, so a delivery failure here means a
    stale-looking card, never a lost decision.
    """
    if not response_url:
        logger.warning("crisis_content: no response_url to update the original card")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                response_url,
                json={"replace_original": True, "text": text, "blocks": blocks},
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("crisis_content: failed to update the original card via response_url")


async def _notify_jen_of_change_request(
    session: AsyncSession,
    *,
    card_id: int,
    route: Route,
    actor_label: str,
    note: str,
    access_token: str,
) -> None:
    """Post a real ``<@…>`` mention for Jen in the card's OWN thread (CCA9).

    Section 4 of ``briefs/cca9-card-lifecycle.md``: on a ``changes_requested``
    decision, thread a message onto the card mentioning Jen so she sees the
    ask in the same place the conversation is already happening -- separate
    from (and in addition to) the existing doc-line + Drive-comment + email
    notification (``artemis.crisis_content.writeback``, CCA7), which is
    fire-and-forget and off this request's path. This call is synchronous
    with the decision but never raises back into the caller -- a delivery
    failure here must not undo (or even look like it undid) a decision that
    is already committed by the time this runs.

    ``jen_mention()`` already falls back to the plain word "Jen" when
    ``settings.crisis_content_jen_slack_user_id`` is empty, so this never
    posts a broken ``<@>``.

    Finds where to thread via ``find_posted_location`` -- the
    ``crisis_content_notifications`` row CCA9 now populates with
    ``channel_id``/``message_ts`` at post time (see
    ``artemis.crisis_content.notify.post_transition_card``). No row (or an
    incomplete pair) means "nothing to thread onto" and this is skipped with
    a warning rather than guessing a destination -- notably true for every
    row seeded directly as a bare ``CrisisContentCard`` (no notification),
    which is how most of this module's existing tests build their fixtures.
    """
    if not access_token:
        logger.warning(
            "crisis_content: no Slack token -- cannot notify Jen of change request "
            "for card_id=%s route=%s",
            card_id,
            route,
        )
        return

    location = await find_posted_location(session, card_id, route)
    if location is None:
        logger.warning(
            "crisis_content: no posted-location record for card_id=%s route=%s -- "
            "cannot thread a Jen change-request mention",
            card_id,
            route,
        )
        return
    channel_id, message_ts = location
    if not channel_id or not message_ts:
        logger.warning(
            "crisis_content: posted-location incomplete for card_id=%s route=%s "
            "(channel_id=%r message_ts=%r) -- cannot thread a Jen change-request mention",
            card_id,
            route,
            channel_id,
            message_ts,
        )
        return

    text = f'{jen_mention()} — {actor_label} asked for a change on this one:\n"{note}"'
    try:
        await SlackClient(token=access_token).post_message(
            channel=channel_id, text=text, thread_ts=message_ts
        )
    except Exception:
        logger.exception(
            "crisis_content: failed to post Jen change-request mention for "
            "card_id=%s route=%s",
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
    """Dispatch one ``crisis_content_approve`` / ``crisis_content_request_changes`` click.

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
            f"You're not an approver for the {target.route} route, so this click wasn't "
            "recorded.",
        )
        return JSONResponse(status_code=200, content=_ACK)

    attempted: Decision = "approved" if action_id == ACTION_APPROVE else "changes_requested"
    latest = await get_latest_decision(session, target.card_id, target.route)
    if latest is not None and is_blocked_by_existing_decision(latest, attempted):
        who = _display_label(latest.decided_by_email, latest.decided_by_slack_user_id)
        await _post_ephemeral(
            response_url,
            f"Already decided: {latest.decision} by {who} at {latest.decided_at.isoformat()}.",
        )
        return JSONResponse(status_code=200, content=_ACK)

    if action_id == ACTION_REQUEST_CHANGES:
        return await _open_request_changes_modal(
            payload=payload, target=target, response_url=response_url, access_token=access_token
        )

    # ACTION_APPROVE: decide now, synchronously -- no modal needed.
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


async def _open_request_changes_modal(
    *,
    payload: dict[str, Any],
    target: _Target,
    response_url: str | None,
    access_token: str,
) -> JSONResponse:
    trigger_id = payload.get("trigger_id")
    if not trigger_id or not access_token:
        logger.error(
            "crisis_content: cannot open request-changes modal (trigger_id=%r, has_token=%s)",
            trigger_id,
            bool(access_token),
        )
        await _post_ephemeral(
            response_url, "Couldn't open the change-request form -- please try again."
        )
        return JSONResponse(status_code=200, content=_ACK)

    private_metadata = json.dumps(
        {
            "card_id": target.card_id,
            "route": target.route,
            "response_url": response_url,
            "message_ts": _message_ts_from_payload(payload),
        }
    )
    view: dict[str, Any] = {
        "type": "modal",
        "callback_id": CRISIS_CONTENT_VIEW_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Request changes"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": _NOTE_BLOCK_ID,
                "label": {"type": "plain_text", "text": "What needs to change?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": _NOTE_ACTION_ID,
                    "multiline": True,
                },
            }
        ],
    }
    try:
        await SlackClient(token=access_token).views_open(str(trigger_id), view)
    except Exception:
        logger.exception(
            "crisis_content: views.open failed for card=%s route=%s",
            target.card_id,
            target.route,
        )
        await _post_ephemeral(
            response_url, "Couldn't open the change-request form -- please try again."
        )
    return JSONResponse(status_code=200, content=_ACK)


async def handle_crisis_content_view_submission(
    session: AsyncSession, *, payload: dict[str, Any], access_token: str = ""
) -> JSONResponse:
    """Dispatch a ``view_submission`` from the "Request changes" modal.

    Never raises -- see ``handle_crisis_content_block_action``'s docstring
    for the same policy.

    ``access_token`` (CCA9) is Callie's bot token, needed to thread the Jen
    change-request mention (``_notify_jen_of_change_request``) -- defaults
    to ``""`` so existing callers that only ever exercised the pre-CCA9
    behavior keep working; ``_notify_jen_of_change_request`` itself no-ops
    (with a warning) on an empty token rather than raising.
    """
    try:
        return await _handle_view_submission(session, payload=payload, access_token=access_token)
    except Exception:
        logger.exception("crisis_content: unhandled error handling view_submission")
        return JSONResponse(status_code=200, content=_ACK)


async def _handle_view_submission(
    session: AsyncSession, *, payload: dict[str, Any], access_token: str = ""
) -> JSONResponse:
    view = payload.get("view")
    if not isinstance(view, dict):
        return JSONResponse(status_code=200, content=_ACK)

    try:
        metadata = json.loads(str(view.get("private_metadata") or "{}"))
    except json.JSONDecodeError:
        logger.warning("crisis_content: malformed private_metadata on view_submission")
        return JSONResponse(status_code=200, content=_ACK)

    if not isinstance(metadata, dict):
        return JSONResponse(status_code=200, content=_ACK)

    raw_card_id = metadata.get("card_id")
    route = metadata.get("route")
    response_url_raw = metadata.get("response_url")
    response_url = str(response_url_raw) if response_url_raw else None
    if not isinstance(raw_card_id, int) or route not in ("asset", "copy"):
        logger.warning("crisis_content: invalid target in private_metadata=%r", metadata)
        return JSONResponse(status_code=200, content=_ACK)
    card_id: int = raw_card_id
    route_typed = cast("Route", route)
    raw_message_ts = metadata.get("message_ts")
    message_ts = str(raw_message_ts) if raw_message_ts else None

    note = _extract_note(view)
    if not note:
        return JSONResponse(
            status_code=200,
            content={
                "response_action": "errors",
                "errors": {_NOTE_BLOCK_ID: "Say what needs to change before submitting."},
            },
        )

    user_obj = payload.get("user")
    slack_user_id = str(user_obj.get("id") or "") if isinstance(user_obj, dict) else ""
    email = await _resolve_email(session, slack_user_id, access_token=access_token)

    if email is None:
        return JSONResponse(
            status_code=200,
            content={
                "response_action": "errors",
                "errors": {
                    _NOTE_BLOCK_ID: "I don't recognize you as an approver for this pipeline."
                },
            },
        )

    if not is_authorized_for_route(email, route_typed):
        return JSONResponse(
            status_code=200,
            content={
                "response_action": "errors",
                "errors": {_NOTE_BLOCK_ID: f"You're not an approver for the {route_typed} route."},
            },
        )

    latest = await get_latest_decision(session, card_id, route_typed)
    if latest is not None and is_blocked_by_existing_decision(latest, "changes_requested"):
        who = _display_label(latest.decided_by_email, latest.decided_by_slack_user_id)
        return JSONResponse(
            status_code=200,
            content={
                "response_action": "errors",
                "errors": {_NOTE_BLOCK_ID: f"Already decided: {latest.decision} by {who}."},
            },
        )

    row = await record_decision(
        session,
        card_id=card_id,
        route=route_typed,
        decision="changes_requested",
        decided_by_slack_user_id=slack_user_id,
        decided_by_email=email,
        note=note,
        slack_message_ts=message_ts,
    )
    schedule_decision_writeback(row.id)

    actor_label = _display_label(email, slack_user_id)
    text, blocks = render_decision_message(
        decision="changes_requested",
        actor_label=actor_label,
        decided_at=row.decided_at,
        note=note,
    )
    await _update_card_via_response_url(response_url, text=text, blocks=blocks)

    # CCA9: mention Jen in-thread on a change request ONLY -- see
    # briefs/cca9-card-lifecycle.md section 4. Never raises (see the
    # function's own docstring); a delivery failure here must not affect the
    # ack Slack already has via the two calls above.
    await _notify_jen_of_change_request(
        session,
        card_id=card_id,
        route=route_typed,
        actor_label=actor_label,
        note=note,
        access_token=access_token,
    )

    return JSONResponse(status_code=200, content=_ACK)


def _extract_note(view: dict[str, Any]) -> str | None:
    state = view.get("state")
    if not isinstance(state, dict):
        return None
    values = state.get("values")
    if not isinstance(values, dict):
        return None
    block = values.get(_NOTE_BLOCK_ID)
    if not isinstance(block, dict):
        return None
    field = block.get(_NOTE_ACTION_ID)
    if not isinstance(field, dict):
        return None
    raw = field.get("value")
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None
