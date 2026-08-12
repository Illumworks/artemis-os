"""Slack interactivity receiver — /api/integrations/slack/interactivity/{agent_id}.

Events API (`integrations_slack_events.py`) carries messages IN. This is the
counterpart that carries clicks BACK: Slack POSTs here when someone clicks an
interactive button on a message this app rendered (the Approve/Reject/View
buttons built by `artemis/integrations/slack/messages.py`, all sharing the
`pipeline_approval_*` action_id prefix).

Before this route existed, nothing in the repo consumed that payload — Slack
approval cards rendered buttons whose clicks went nowhere. This makes them
work, per `docs/crisis-content-approval-pipeline.md` ("Blocker for slice B2").

Request shape — deliberately different from the Events API:
  - Content-Type is application/x-www-form-urlencoded, not JSON.
  - The payload is one form field, `payload`, holding a JSON-encoded string.
    Parse the form first, then JSON-decode that field.

Security model:
  - Reuses `_verify_slack_signature` from `integrations_slack_events.py`
    verbatim (HMAC-SHA256 over `v0:{timestamp}:{raw_body}`, constant-time
    compare, 300s replay window) rather than a second implementation.
  - Verification runs against the raw request body bytes, read exactly once
    (Starlette cannot re-read a consumed body) before any form/JSON parsing.
  - The approving identity is taken ONLY from the verified payload's
    `user.id`. A valid signature proves the request came from Slack; it says
    nothing about who clicked, which only the payload carries. The button's
    `value` field is operator-authored Block Kit content, not something the
    signature vouches for — never trusted for identity.
  - Every malformed/unknown shape acks with 200 rather than 500 or raising,
    because Slack retries non-2xx responses and a retry storm on a dead
    button is worse than a silent no-op.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.crisis_content.slack_actions import (
    CRISIS_CONTENT_ACTION_IDS as _CRISIS_CONTENT_ACTION_IDS,
)
from artemis.crisis_content.slack_actions import (
    CRISIS_CONTENT_VIEW_CALLBACK_ID as _CRISIS_CONTENT_VIEW_CALLBACK_ID,
)
from artemis.crisis_content.slack_actions import (
    handle_crisis_content_block_action,
    handle_crisis_content_view_submission,
)
from artemis.directory.models import DirectoryPerson
from artemis.routes.integrations_slack_events import (
    _normalize_agent_id,
    _resolve_agent_slack_config,
    _SlackAgentConfig,
    _verify_slack_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/slack", tags=["slack-interactivity"])

# action_ids this endpoint knows how to apply. Everything else (the "View in
# Artemis" / "Edit in Writing Studio" url buttons, and any future action_id
# this route hasn't been taught yet) is acknowledged but not acted on.
_APPROVAL_ACTION_IDS = ("pipeline_approval_approve", "pipeline_approval_reject")


async def _resolve_decided_by(
    session: AsyncSession, *, slack_user_id: str, fallback_label: str
) -> str:
    """Best-effort resolve the clicking Slack user to a directory email.

    `directory_people` is the existing email<->slack_user_id cache (synced
    from Slack; see `artemis/directory/models.py`). A miss — unsynced person,
    a bot, a workspace guest — is expected and non-fatal: fall back to the
    verified payload's own display name/username, then the raw Slack id,
    rather than blocking the decision on a lookup that has nothing to do with
    whether the click itself was genuine. Every value returned here — email,
    username, or raw id — is sourced from the verified payload; none of it
    ever comes from the button's `value`.
    """
    if slack_user_id:
        try:
            result = await session.execute(
                select(DirectoryPerson.email).where(DirectoryPerson.slack_user_id == slack_user_id)
            )
            email = result.scalar_one_or_none()
        except Exception:
            logger.exception(
                "slack interactivity: directory lookup failed for slack_user_id=%s", slack_user_id
            )
            email = None
        if email:
            return str(email)
    return fallback_label or (f"slack:{slack_user_id}" if slack_user_id else "unknown_slack_user")


async def _resolve_agent_config_for_verification(
    session: AsyncSession, *, normalized_agent: str
) -> _SlackAgentConfig:
    """Resolve signing secret exactly as the events route does.

    Mirrors `_slack_events`'s own fallback: an agent with no configured
    integration (unknown `agent_id`, or one that simply hasn't been wired up)
    must not raise — it must fall through to a config whose signing_secret is
    empty (or the env fallback), so signature verification below fails closed
    with 401 rather than the route 500ing on an unhandled exception.
    """
    try:
        return await _resolve_agent_slack_config(
            session,
            agent_id=normalized_agent,
            load_integration=normalized_agent != "artemis",
        )
    except Exception:
        import os

        return _SlackAgentConfig(
            agent_id=normalized_agent,
            signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
            access_token="",
            bot_user_id="",
            authed_user_id="",
            allowed_user_ids=(),
            allowed_channel_ids=(),
            listen_channel_messages=False,
            always_respond_in_channels=False,
        )


@router.post("/interactivity/{agent_id}")
async def slack_interactivity(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> JSONResponse:
    """Verify, parse, dispatch, and acknowledge one Slack `block_actions` click."""
    # ── 1. Read the raw body ONCE. Verification needs the exact bytes Slack
    # signed; a second `request.body()`/`request.form()` call on the same
    # Starlette request would try to re-read an already-consumed stream.
    raw_body: bytes = await request.body()
    normalized_agent = _normalize_agent_id(agent_id)

    agent_cfg = await _resolve_agent_config_for_verification(
        session, normalized_agent=normalized_agent
    )

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    # ── 2. Verify BEFORE parsing anything. This is the one call this route
    # exists to make — reused verbatim from the Events API receiver.
    if not _verify_slack_signature(raw_body, timestamp, signature, agent_cfg.signing_secret):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    # ── 3. Now that the request is verified, parse it. Interactivity payloads
    # are form-encoded (unlike the Events API's raw JSON body): one field,
    # `payload`, holding a JSON-encoded string.
    try:
        form = await request.form()
    except Exception:
        logger.warning(
            "slack interactivity: could not parse form body for agent_id=%s", normalized_agent
        )
        return JSONResponse(status_code=400, content={"error": "invalid form body"})

    payload_raw = form.get("payload")
    if not payload_raw:
        logger.warning(
            "slack interactivity: missing payload field for agent_id=%s", normalized_agent
        )
        return JSONResponse(status_code=400, content={"error": "missing payload"})

    try:
        payload: Any = json.loads(str(payload_raw))
    except json.JSONDecodeError:
        logger.warning(
            "slack interactivity: malformed JSON payload for agent_id=%s", normalized_agent
        )
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    if not isinstance(payload, dict):
        logger.warning(
            "slack interactivity: payload is not an object for agent_id=%s", normalized_agent
        )
        return JSONResponse(status_code=400, content={"error": "invalid payload shape"})

    # ── Dispatch branch (CCA5): crisis-content decisions. `view_submission`
    # payloads (the "Request changes" modal's Submit) have no top-level
    # `actions` key at all — the generic `actions`-list handling a few lines
    # down would otherwise just warn-and-ack it as "no actions in payload".
    # Caught here, before that happens, and handed to the crisis_content
    # package, which owns everything about this decision — this route still
    # only verifies + dispatches; see artemis/crisis_content/slack_actions.py.
    payload_type = str(payload.get("type") or "")
    if payload_type == "view_submission":
        view_obj = payload.get("view")
        if isinstance(view_obj, dict) and view_obj.get("callback_id") == _CRISIS_CONTENT_VIEW_CALLBACK_ID:
            return await handle_crisis_content_view_submission(
                session, payload=payload, access_token=agent_cfg.access_token
            )
        return JSONResponse(status_code=200, content={})

    # ── 4. Identity comes ONLY from the verified payload's user object —
    # never from the button's `value`, which is just Block Kit content the
    # signature does not vouch for as "who clicked."
    user_obj = payload.get("user")
    slack_user_id = str(user_obj.get("id") or "") if isinstance(user_obj, dict) else ""
    slack_user_label = (
        str(user_obj.get("username") or user_obj.get("name") or "") if isinstance(user_obj, dict) else ""
    )

    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        logger.warning(
            "slack interactivity: no actions in payload for agent_id=%s", normalized_agent
        )
        return JSONResponse(status_code=200, content={})

    action = actions[0]
    if not isinstance(action, dict):
        return JSONResponse(status_code=200, content={})

    action_id = str(action.get("action_id") or "")
    value = str(action.get("value") or "")

    # ── Dispatch branch (CCA5): crisis-content decision buttons. Own action
    # ids, own module — see artemis/crisis_content/slack_actions.py for the
    # authorization, already-decided, and persistence logic. Checked before
    # `_APPROVAL_ACTION_IDS` so the two dispatch tables stay independent and
    # neither has to know the other's action_ids exist.
    if action_id in _CRISIS_CONTENT_ACTION_IDS:
        return await handle_crisis_content_block_action(
            session,
            action_id=action_id,
            value=value,
            payload=payload,
            access_token=agent_cfg.access_token,
        )

    if action_id not in _APPROVAL_ACTION_IDS:
        # Unknown/unhandled action_id (includes the url-buttons "_view" and
        # "_edit_draft", which Slack opens client-side and never needs a
        # server decision for). Ack — never look like a delivery failure.
        logger.warning(
            "slack interactivity: unhandled action_id=%r for agent_id=%s",
            action_id,
            normalized_agent,
        )
        return JSONResponse(status_code=200, content={})

    decided_by = await _resolve_decided_by(
        session, slack_user_id=slack_user_id, fallback_label=slack_user_label or "slack_user"
    )

    # ── 5. Dispatch through the SAME decision-persisting path the HTTP resume
    # route and the legacy Slack callback use — not a parallel one. See
    # `apply_pipeline_approval_slack_action` in artemis/pipelines/routes.py.
    from artemis.pipelines.routes import apply_pipeline_approval_slack_action

    result = await apply_pipeline_approval_slack_action(
        session,
        action_id=action_id,
        value=value,
        decided_by=decided_by,
        source="slack_interactivity",
    )

    if not result.get("handled"):
        logger.warning(
            "slack interactivity: action_id=%r not applied (%s) for agent_id=%s",
            action_id,
            result.get("reason"),
            normalized_agent,
        )
        # Includes the double-click case (reason == "approval_not_pending" or
        # "gate_not_suspended"): the first click already persisted the
        # decision, so a second one is a no-op ack, not an error.
        return JSONResponse(status_code=200, content={})

    # ── 6. Update the original message so the buttons disappear. This is the
    # Slack-documented way to update a block_actions message without a
    # separate outbound call: returning a message payload in the HTTP
    # response body (rather than posting to response_url) replaces the
    # original in place, well inside the 3-second window, with no Slack
    # client/token needed here. Preferred over an empty ack because it stops
    # double-approval taps and gives the clicker visible confirmation.
    decision_label = "Approved" if result["decision"] == "approved" else "Rejected"
    who = f"<@{slack_user_id}>" if slack_user_id else decided_by
    return JSONResponse(
        status_code=200,
        content={
            "replace_original": True,
            "text": f"{decision_label} by {who}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f":white_check_mark: *{decision_label}* by {who}"},
                }
            ],
        },
    )
