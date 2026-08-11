"""Persistence + decision layer between the parser and Slack (slice B1).

``record_observation`` takes a freshly parsed ``list[ReviewCard]``, records
what was observed, appends to the copy version log, and returns the
``Transition`` objects worth notifying about. See
``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca2-transition-detection.md`` for the design.

No Slack, no HTTP, no scheduler. Slice B2 owns delivery: it consumes the
returned ``Transition`` list and calls ``mark_notified`` after a successful
post. This module only ever reads ``crisis_content_notifications``.

Transaction management is the caller's responsibility, mirroring
``artemis.memory.store`` (this repo's other lossless-write module)::

    async with session.begin():
        transitions = await record_observation(session, cards)

Neither ``record_observation`` nor ``mark_notified`` commits -- both flush
so later reads in the same call see earlier writes. A caller that closes
the session without committing loses the writes, same as
``artemis.memory.store.write_drawer``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
    CrisisContentNotification,
)
from artemis.crisis_content.parser import classify_status

logger = logging.getLogger(__name__)

__all__ = [
    "Route",
    "Transition",
    "record_observation",
    "has_notified",
    "mark_notified",
]

Route = Literal["asset", "copy"]

_READY = "Ready"

_NOTIFICATIONS_CONSTRAINT = "uq_crisis_content_notifications_card_route_status"


class Transition(BaseModel):
    """One status transition slice B2 should consider notifying on.

    Carries the full ``ReviewCard`` (not just IDs) so slice B2 can render
    the copy/asset inline without a second lookup.
    """

    model_config = ConfigDict(frozen=True)

    card: ReviewCard
    route: Route
    previous_status: str | None
    new_status: str
    is_new_card: bool


async def record_observation(
    session: AsyncSession,
    cards: Sequence[ReviewCard],
) -> list[Transition]:
    """Persist ``cards`` and return the transitions worth notifying on.

    Per card: resolve the card row by identity (insert if new, else update
    the mutable fields and ``last_seen_at``), append a copy-version row iff
    this exact ``(card, copy_hash)`` pair hasn't been seen before, then
    compute transitions. See the module docstring for the transaction
    contract -- this function flushes but never commits.
    """
    transitions: list[Transition] = []
    for card in cards:
        transitions.extend(await _observe_card(session, card))
    return transitions


async def _observe_card(session: AsyncSession, card: ReviewCard) -> list[Transition]:
    now = datetime.now(UTC)
    row, is_new, previous_asset_status, previous_copy_status = await _resolve_card_row(
        session, card, now
    )
    await _maybe_append_copy_version(session, row, card, now)

    # Same copy, new identity -- Jen filled in an "August XX" placeholder.
    # Suppress ONLY the routes the prior identity was already notified on. A
    # blanket suppression would lose a never-notified route permanently: it is
    # swallowed while the card is new, and afterwards the stored status equals
    # the observed one, so no transition remains to detect. The card + version
    # rows above are recorded either way.
    renamed_suppressed_routes: frozenset[Route] = frozenset()
    if is_new:
        renamed_suppressed_routes = await _routes_notified_for_same_copy(session, row)

    route_observations: list[tuple[Route, str | None, str | None]] = [
        ("asset", previous_asset_status, card.asset_status),
        ("copy", previous_copy_status, card.copy_status),
    ]
    card_transitions: list[Transition] = []
    for route, previous_status, new_status in route_observations:
        if route in renamed_suppressed_routes:
            continue
        transition = await _evaluate_route(
            session,
            card=card,
            card_id=row.id,
            route=route,
            previous_status=previous_status,
            new_status=new_status,
            is_new_card=is_new,
        )
        if transition is not None:
            card_transitions.append(transition)
    return card_transitions


async def _resolve_card_row(
    session: AsyncSession, card: ReviewCard, now: datetime
) -> tuple[CrisisContentCard, bool, str | None, str | None]:
    """Find-or-create the card row for ``card``'s identity.

    Returns ``(row, is_new, previous_asset_status, previous_copy_status)``.
    The two "previous" values are ``None`` for a brand-new row -- there is
    no prior observation to compare against, which is exactly what makes a
    new card already at ``Ready`` register as a transition (see the
    "First-run behaviour" section of the brief).

    NULL-safe on purpose: ``identity_platform`` is nullable, and
    ``Column == None`` compiles to ``= NULL`` in SQL, which is never true.
    Naively comparing with ``==`` when ``card.platform`` is ``None`` would
    never find the existing row and would insert a duplicate on every poll.
    """
    _, platform, ordinal = card.identity_key
    stmt = select(CrisisContentCard).where(
        CrisisContentCard.identity_header == card.header,
        CrisisContentCard.identity_ordinal == ordinal,
    )
    stmt = stmt.where(
        CrisisContentCard.identity_platform.is_(None)
        if platform is None
        else CrisisContentCard.identity_platform == platform
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = CrisisContentCard(
            identity_header=card.header,
            identity_platform=platform,
            identity_ordinal=ordinal,
            title=card.title,
            asset_status=card.asset_status,
            copy_status=card.copy_status,
            asset_url=card.asset_url,
            copy_hash=card.copy_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        await session.flush()
        return row, True, None, None

    previous_asset_status = row.asset_status
    previous_copy_status = row.copy_status
    row.title = card.title
    row.asset_status = card.asset_status
    row.copy_status = card.copy_status
    row.asset_url = card.asset_url
    row.copy_hash = card.copy_hash
    row.last_seen_at = now
    await session.flush()
    return row, False, previous_asset_status, previous_copy_status


async def _maybe_append_copy_version(
    session: AsyncSession, row: CrisisContentCard, card: ReviewCard, now: datetime
) -> None:
    """Append a version row iff this exact ``(card, copy_hash)`` pair is new.

    This is the write that makes "Jen wrote X, we changed it to Y"
    recoverable later -- the poller is the only place her original wording
    is ever visible, so this is load-bearing, not bookkeeping. Never
    UPDATE, never DELETE (``CrisisContentCopyVersion`` docstring,
    ``CLAUDE.md`` rule 3).
    """
    stmt = select(CrisisContentCopyVersion.id).where(
        CrisisContentCopyVersion.card_id == row.id,
        CrisisContentCopyVersion.copy_hash == card.copy_hash,
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return
    session.add(
        CrisisContentCopyVersion(
            card_id=row.id,
            copy_hash=card.copy_hash,
            copy_body=card.copy_body,
            first_seen_at=now,
        )
    )
    await session.flush()


async def _routes_notified_for_same_copy(
    session: AsyncSession, row: CrisisContentCard
) -> frozenset[Route]:
    """Routes already notified on some OTHER card with the same ``copy_hash``.

    Guards the "August XX" placeholder-becomes-a-real-date rename: see
    ``docs/crisis-content-approval-pipeline.md``, "Card identity". Only
    meaningful for a newly-created row -- an existing card's identity never
    changes underneath it, so this is only called when ``is_new`` is True.

    Returns routes rather than a bool deliberately. Suppressing the whole card
    would silently drop a route nobody has ever been asked about -- e.g. copy
    was approved under the old header and the asset went Ready in the same
    window as the rename. That request would never resurface, because the next
    poll sees no status change.
    """
    stmt = (
        select(CrisisContentNotification.route)
        .join(CrisisContentCard, CrisisContentNotification.card_id == CrisisContentCard.id)
        .where(
            CrisisContentCard.copy_hash == row.copy_hash,
            CrisisContentCard.id != row.id,
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return frozenset(cast("Route", value) for value in result.scalars().all())


async def _evaluate_route(
    session: AsyncSession,
    *,
    card: ReviewCard,
    card_id: int,
    route: Route,
    previous_status: str | None,
    new_status: str | None,
    is_new_card: bool,
) -> Transition | None:
    """Apply the transition + suppression rules for one route of one card.

    Emits iff: the status is set, is recognized, is exactly ``Ready``,
    differs from the previous observation, and (for the asset route) an
    asset is actually attached. An unrecognized non-null status logs a
    WARNING and never emits -- silence here means Jen added a dropdown
    option and the pipeline quietly stopped working.
    """
    if new_status is None:
        return None

    if classify_status(new_status) == "unknown":
        logger.warning(
            "crisis_content: unrecognized %s status %r on card %r "
            "(header=%r platform=%r) -- Jen may have added a new dropdown "
            "option; this status will not trigger a notification until it "
            "is added to the known vocabulary in artemis/crisis_content/parser.py.",
            route,
            new_status,
            card.identity_key,
            card.header,
            card.platform,
        )
        return None

    if new_status != _READY or new_status == previous_status:
        return None

    if route == "asset" and card.asset_url is None:
        return None

    if await has_notified(session, card_id, route, new_status):
        return None

    return Transition(
        card=card,
        route=route,
        previous_status=previous_status,
        new_status=new_status,
        is_new_card=is_new_card,
    )


async def has_notified(session: AsyncSession, card_id: int, route: Route, status_value: str) -> bool:
    """True if ``mark_notified`` has already recorded this ``(card, route, status)``."""
    stmt = (
        select(CrisisContentNotification.id)
        .where(
            CrisisContentNotification.card_id == card_id,
            CrisisContentNotification.route == route,
            CrisisContentNotification.status_value == status_value,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def mark_notified(
    session: AsyncSession,
    card_id: int,
    route: Route,
    status_value: str,
    *,
    notified_at: datetime | None = None,
) -> None:
    """Record that ``(card_id, route, status_value)`` has been notified.

    Slice B2 calls this only after a successful Slack post -- never before,
    so a delivery failure is never mistaken for a delivered one.
    ``ON CONFLICT DO NOTHING`` on the same unique constraint the migration
    creates makes a retried call safe. Does not commit -- see the module
    docstring's transaction contract.
    """
    stamp = notified_at if notified_at is not None else datetime.now(UTC)
    stmt = (
        pg_insert(CrisisContentNotification)
        .values(card_id=card_id, route=route, status_value=status_value, notified_at=stamp)
        .on_conflict_do_nothing(constraint=_NOTIFICATIONS_CONSTRAINT)
    )
    await session.execute(stmt)
