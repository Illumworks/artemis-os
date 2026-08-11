"""Decision persistence for the crisis-content approval pipeline (slice B2c, CCA5).

``crisis_content_decisions`` is append-only -- CLAUDE.md rule 3, lossless
memory. No function in this module ever UPDATEs or DELETEs a row; a changed
mind is a new row. See ``alembic/versions/0107_crisis_content_decisions.py``
and ``docs/crisis-content-approval-pipeline.md``.

This module owns DECISION semantics only:
  - what "already decided" means for the double-click / double-submit guard
    (``is_blocked_by_existing_decision``)
  - inserting the append-only row (``record_decision``)

It has no Slack awareness and no authorization opinion -- who may decide a
route is ``artemis.crisis_content.authorization``; talking to Slack (opening
the modal, rendering the post-decision card, dispatching on action_id) is
``artemis.crisis_content.slack_actions`` / ``notify.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.crisis_content.orm import CrisisContentDecision
from artemis.crisis_content.transitions import Route

logger = logging.getLogger(__name__)

__all__ = [
    "Decision",
    "get_latest_decision",
    "is_blocked_by_existing_decision",
    "record_decision",
]

Decision = Literal["approved", "changes_requested"]


async def get_latest_decision(
    session: AsyncSession, card_id: int, route: Route
) -> CrisisContentDecision | None:
    """Most recent decision row for ``(card_id, route)``, or ``None`` if undecided.

    Read-only. Ordered by ``id DESC`` rather than ``decided_at DESC`` --
    ``id`` is strictly monotonic on insert order, while two rows from the
    same request burst could share a ``decided_at`` at second resolution.
    """
    stmt = (
        select(CrisisContentDecision)
        .where(
            CrisisContentDecision.card_id == card_id,
            CrisisContentDecision.route == route,
        )
        .order_by(CrisisContentDecision.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def is_blocked_by_existing_decision(
    latest: CrisisContentDecision | None, attempted: Decision
) -> bool:
    """True iff ``attempted`` should be rejected as an already-decided duplicate.

    ``approved`` is terminal for a route: once one approver settles it (per
    the "any one is sufficient" copy-route quorum), any further click --
    approve again, or a belated request-changes -- is a stale/duplicate tap
    on a card whose buttons should already be gone, and it is blocked.

    ``changes_requested`` is NOT terminal: docs explicitly allow
    ``changes_requested`` -> later ``approved`` (both rows must survive --
    see the migration's "no unique constraint" note), so an ``approved``
    attempt after a ``changes_requested`` is allowed through. A second
    ``changes_requested`` attempt in a row IS blocked here -- that shape only
    arises from a double-submitted modal (a double-tap on the modal's
    Submit button), which this slice does not need to distinguish from a
    deliberate second round of feedback; see
    ``briefs/cca5-approval-loop.md``'s "second click on a decided card" test.
    """
    if latest is None:
        return False
    if latest.decision == "approved":
        return True
    return attempted == "changes_requested"


async def record_decision(
    session: AsyncSession,
    *,
    card_id: int,
    route: Route,
    decision: Decision,
    decided_by_slack_user_id: str,
    decided_by_email: str | None,
    note: str | None = None,
    slack_message_ts: str | None = None,
    decided_at: datetime | None = None,
) -> CrisisContentDecision:
    """INSERT one decision row. Never UPDATEs, never DELETEs (CLAUDE.md rule 3).

    ``decided_by_slack_user_id`` must be sourced from the verified Slack
    interactivity payload's top-level ``user.id`` by the caller -- this
    function trusts whatever it is handed and does not re-verify identity,
    so it must never be called with anything read from a button ``value`` or
    a modal's ``private_metadata``. See ``artemis.crisis_content.slack_actions``.

    Commits before returning -- this is the terminal write of the decision
    flow (mirrors ``apply_pipeline_approval_slack_action``'s own commit
    inside ``apply_approval_decision`` rather than leaving it to the caller).
    """
    row = CrisisContentDecision(
        card_id=card_id,
        route=route,
        decision=decision,
        decided_by_slack_user_id=decided_by_slack_user_id,
        decided_by_email=decided_by_email,
        note=note,
        slack_message_ts=slack_message_ts,
        decided_at=decided_at if decided_at is not None else datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    logger.info(
        "crisis_content: decision recorded card_id=%s route=%s decision=%s by=%s",
        card_id,
        route,
        decision,
        decided_by_email or decided_by_slack_user_id,
    )
    return row
