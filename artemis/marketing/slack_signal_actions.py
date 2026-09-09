"""Approve / reject a signal from the Slack card Callie already posts.

**Why this exists.** 3,479 signals sit at `qualified`, averaging 40 days old,
against 58 ever approved. The bottleneck was never that nobody wanted to decide
— it was that the notification arrives in Slack and the decision lived only in
the web app. Josh reads the card on his phone, agrees with it, and has nowhere
to say so.

**What it deliberately does NOT do.** It does not give an agent the ability to
approve anything. The card is a proposal; a named human clicks; the click is
signature-verified by Slack and the clicker's identity is resolved server-side.
Callie cannot press her own button.

**No parallel implementation.** Both handlers call the same route functions the
web app calls, which in turn call `promote_signal_to_candidate` — the module
docstring there is explicit that the manual path and the pipeline Gate-1 path
share it "so side effects cannot drift". A third path that reimplemented
promotion would drift by the end of the week.

**Scope, honestly.** Approving a signal creates a campaign candidate. As of
2026-09-09 that chain terminates in a stub: `campaign_sends` has zero rows ever
and `marketing/sends.py` says "NO REAL EMAIL — transport is stubbed". So these
buttons buy triage hygiene — a queue that reflects what has actually been looked
at — and not campaign execution. Worth knowing before anyone reads a cleared
queue as work shipped.
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import get_settings

logger = logging.getLogger(__name__)

#: Block Kit action ids. Namespaced so the interactivity route's dispatch tables
#: stay independent and none has to know another's ids exist.
SIGNAL_APPROVE = "signal_approve"
SIGNAL_REJECT = "signal_reject"

SIGNAL_ACTION_IDS = frozenset({SIGNAL_APPROVE, SIGNAL_REJECT})


def approver_emails() -> frozenset[str]:
    """Who may decide a signal. A config change, not a code edit."""
    raw = get_settings().signal_approver_emails or ""
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def is_authorized(email: str | None) -> bool:
    """Fail closed. An unresolvable clicker is not an authorized one."""
    if not email:
        return False
    return email.strip().lower() in approver_emails()


def _parse_signal_id(value: str) -> int | None:
    """Button values are `signal:<id>`; anything else is not ours to act on."""
    if not value.startswith("signal:"):
        return None
    try:
        return int(value.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def handle_signal_block_action(
    session: AsyncSession,
    *,
    action_id: str,
    value: str,
    decided_by: str | None,
    resolved_email: str | None,
) -> JSONResponse:
    """Apply one Approve/Reject click. Always acks; never 500s at Slack.

    ``resolved_email`` is the authorization subject and comes from the verified
    payload's user id, resolved server-side. ``decided_by`` is the label written
    to the record. They are separate parameters on purpose: a lookup that fails
    must read as "I could not identify you", never as "you are not permitted" —
    the crisis-content approval bug sent four people hunting an allowlist for
    what was a NULL `slack_user_id`.
    """
    signal_id = _parse_signal_id(value)
    if signal_id is None:
        logger.warning("signal action: unparseable value %r for %s", value, action_id)
        return JSONResponse(status_code=200, content={"text": "That button is malformed."})

    if not resolved_email:
        return JSONResponse(
            status_code=200,
            content={
                "text": (
                    "I could not work out who you are from Slack, so I have not "
                    "changed anything. That is an identity lookup failing, NOT a "
                    "permissions refusal — your Slack account may not be synced to a "
                    "directory record."
                )
            },
        )

    if not is_authorized(resolved_email):
        logger.info("signal action: %s not permitted to decide signals", resolved_email)
        return JSONResponse(
            status_code=200,
            content={
                "text": (
                    f"{resolved_email} is not on the signal approver list, so nothing "
                    "was changed. Jon can widen it in settings."
                )
            },
        )

    # The same functions the web app calls. Their HTTP exceptions are the
    # already-decided and wrong-state cases, which on a card mean "someone got
    # here first" rather than an error.
    from fastapi import HTTPException

    from artemis.marketing.routes.signal_queue import approve_signal_impl, reject_signal_impl

    who = decided_by or resolved_email
    try:
        if action_id == SIGNAL_APPROVE:
            await approve_signal_impl(signal_id, session=session, decided_by=who)
            text = f"Signal {signal_id} approved by {who} — promoted to a campaign candidate."
        else:
            await reject_signal_impl(signal_id, body=None, session=session, decided_by=who)
            text = f"Signal {signal_id} rejected by {who}."
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        # A double-click is the common case here and is not a failure.
        logger.info("signal action: signal %s not applied (%s)", signal_id, detail)
        return JSONResponse(
            status_code=200,
            content={
                "text": (
                    f"Signal {signal_id} was not changed — it is no longer waiting for "
                    f"a decision ({detail}). Someone may have got here first."
                )
            },
        )
    except Exception:
        logger.exception("signal action: signal %s failed unexpectedly", signal_id)
        return JSONResponse(
            status_code=200,
            content={
                "text": (
                    f"Signal {signal_id} could NOT be updated — something failed on our "
                    "side. Nothing was changed; please try the web queue."
                )
            },
        )

    logger.info("signal action: %s %s by %s", action_id, signal_id, who)
    return JSONResponse(status_code=200, content={"text": text})
