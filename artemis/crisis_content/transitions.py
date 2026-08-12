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

**The re-approval fix (CCA9).** Before this slice, a card that reached
``Ready``, got a ``changes_requested`` decision, and was then revised by
Jen would never notify again: the chip still reads ``Ready`` (finding 5 --
we cannot write chip values), so ``_evaluate_route``'s "did the status
change" check saw no change and emitted nothing, and even if it had, the
OLD ``(card_id, route, status_value)`` ledger row would have deduped it.
``_evaluate_route`` now ALSO re-fires when the status is unchanged at
``Ready`` but ``find_reopening_decision`` finds a genuine revision (a
``crisis_content_copy_versions`` row newer than the route's latest
qualifying decision) -- and the ledger's unique constraint now includes
``copy_hash`` (migration 0109), so the re-fire is not itself swallowed as a
duplicate of the original notification. See ``_evaluate_route`` and
``find_reopening_decision`` below.

**Reopening after approval too (CCA11).** Originally ``approved`` stayed
terminal here -- ``find_reopening_decision`` (then named
``_reopened_after_changes_requested``) only ever returned a row for a
route whose LATEST decision was ``changes_requested``. That was right
while all editing happened before approval. It stopped being right once
the vendor's team started putting edits directly in the Google Doc rather
than describing them in Slack (Steffie Cruz, DigiGeeks, 2026-08-12): an
approval that names specific wording, followed by someone changing that
wording, is now the expected shape of the workflow, not an edge case --
and an approval record that refers to text that no longer exists is an
integrity problem for crisis communications, where the exact wording is
the thing being signed off on. ``find_reopening_decision`` now returns the
latest decision for EITHER ``changes_requested`` OR ``approved`` (still
gated on a genuine ``crisis_content_copy_versions`` row after
``decided_at`` -- see "Do not reopen on noise" below); ``_evaluate_route``
tells the two apart by attaching ``Transition.reopened_after_approval``
only for the ``approved`` case, so ``artemis.crisis_content.notify`` can
render the "you are re-reviewing something already approved" warning for
that case only -- never for a ``changes_requested`` reopen, which is the
expected loop and stays silent about being a reopen at all.

**Do not reopen on noise.** The re-fire keys ONLY on a genuine new
``crisis_content_copy_versions`` row, never on a raw document re-read.
Google's exported hrefs carry ``ust``/``usg`` tracking params that change
on every fetch, which is why ``copy_hash`` is computed from normalized text
(``artemis.crisis_content.parser``) rather than from the raw HTML -- any
comparison that fell back to the raw export would reintroduce that
instability and re-post every approved card on every poll tick.

**Routes reopen independently.** ``find_reopening_decision`` reads the
LATEST decision filtered to the one route being evaluated -- a route with
no decision at all, or whose own latest decision doesn't qualify, never
reopens, regardless of what the OTHER route's decision is or whether the
shared copy-version log has a new row. That log is card-level, not
route-level (there is no asset-specific version log -- see
``find_reopening_decision``'s own docstring), so if BOTH routes
independently have a qualifying decision, one genuine revision can reopen
both; that is the existing CCA9 behavior (see
``test_asset_route_reapproval_mirrors_copy_route_rule``) and is preserved
on purpose, not a gap this slice introduces.

**Tab resolution + the test lane (CCA13).** ``record_observation`` takes an
OPTIONAL ``tab_map`` -- ``ReviewCard.identity_key -> CardTabInfo`` -- built
once per poll tick by ``artemis.crisis_content.tab_resolution.resolve_card_tab_map``
(one ``documents.get`` call, never per card; see that module). Two states:

- ``tab_map is None`` (every caller before CCA13, and every existing test in
  ``tests/test_crisis_content_transitions.py``): unchanged behavior. No tab
  is known, ``Transition.tab_id`` is ``None`` and ``Transition.is_test`` is
  ``False``, exactly like every transition before this slice.
- ``tab_map`` is a (possibly empty) mapping: tab resolution was attempted
  this tick. A card whose ``identity_key`` is NOT in the map -- because
  ``resolve_card_tab_map`` could not positively locate its live table, e.g.
  a race between the HTML-export fetch and the ``documents.get`` fetch,
  both against a live, externally-edited document -- is skipped ENTIRELY
  for this tick: no row upsert, no copy-version append, no transition. This
  is the fail-closed rule the CCA13 brief calls "the dangerous part" pushed
  down to card granularity: without a tab we cannot tell a test card from a
  real one, so treating it as either would risk exactly the wrong-audience
  post the test lane exists to prevent. Nothing is lost -- the row's stored
  status is left untouched, so the next tick's comparison against the
  freshly observed status is unaffected and will retry cleanly once
  resolution succeeds.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
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
    CrisisContentDecision,
    CrisisContentNotification,
)
from artemis.crisis_content.parser import classify_status
from artemis.crisis_content.tab_resolution import CardIdentityKey, CardTabInfo

logger = logging.getLogger(__name__)

__all__ = [
    "Route",
    "find_reopening_decision",
    "ReopenedAfterApproval",
    "Transition",
    "record_observation",
    "has_notified",
    "mark_notified",
    "find_card_id",
    "find_posted_location",
]

Route = Literal["asset", "copy"]

_READY = "Ready"

_NOTIFICATIONS_CONSTRAINT = "uq_crisis_content_notifications_card_route_status"

# A latest decision value that qualifies a route for reopening (CCA9 added
# "changes_requested"; CCA11 added "approved" -- see the module docstring's
# "Reopening after approval too (CCA11)"). Any other value (there is
# currently no third one, but this is defensive) never reopens.
_REOPENABLE_DECISIONS = ("changes_requested", "approved")


class ReopenedAfterApproval(BaseModel):
    """Present on a ``Transition`` iff this re-fire follows an ``approved``
    decision (CCA11) -- ``None`` on every other transition, including a
    ``changes_requested`` reopen (CCA9) and an ordinary first-time ``Ready``
    transition.

    Carries exactly what ``artemis.crisis_content.notify`` needs to render
    the "you are re-reviewing something already approved" banner: who
    approved it (``approved_by``) and when (``approved_at``). See
    ``briefs/cca11-reopen-on-post-approval-edit.md``, "The card must say why
    it is back" -- a re-fired card that looks identical to a first-time card
    is worse than no card, because the approver has no way to tell they are
    re-reviewing something they already signed off on. This field is the
    ONLY thing that distinguishes the two reopen reasons for a renderer that
    must warn for one and stay silent for the other.
    """

    model_config = ConfigDict(frozen=True)

    approved_by: str
    approved_at: datetime


class Transition(BaseModel):
    """One status transition slice B2 should consider notifying on.

    Carries the full ``ReviewCard`` (not just IDs) so slice B2 can render
    the copy/asset inline without a second lookup.

    ``reopened_after_approval`` is ``None`` for every transition except a
    CCA11 reopen following an ``approved`` decision -- see
    ``ReopenedAfterApproval`` above.

    ``tab_id`` (CCA12, typed then; populated starting CCA13) is the Google
    Docs tab this card's live table currently lives in, if known --
    consumed by ``artemis.crisis_content.notify.render_transition_blocks``
    to deep-link the "Edit in doc" button's ``url`` with ``?tab=<tab_id>``
    instead of a bare doc link (Docs has no per-row anchor; the tab is the
    best available precision -- see ``briefs/cca12-edit-in-doc-button.md``).
    Defaults to ``None``, which renders the bare doc link, exactly like
    every card before CCA12.

    **CCA12 shipped this field deliberately unpopulated.** The HTML-export
    read path (``artemis.crisis_content.parser``) is tab-agnostic by design
    (see that module's ``_is_review_card_table`` docstring) -- it walks
    every tab's tables flattened together and never resolves which tab any
    of them came from. The only place this repo could resolve a tab id at
    the time was ``artemis.crisis_content.writeback.locate_card_table``,
    which needs a second, live Docs JSON fetch (``documents.get`` with
    ``includeTabsContent=true``) distinct from the HTML export, and which
    only ran at write-back time (after a decision), never at card-render
    time. Wiring that fetch into the render path was judged out of scope
    for CCA12 (new network dependency + failure mode in the hot notify
    path, for every future ``Ready`` transition) and flagged, not silently
    guessed at, in the CCA12 report -- this field existed so that follow-up
    work had a typed, tested seam to populate.

    **Populated as of CCA13** when ``record_observation`` is given a
    ``tab_map`` (see that function and
    ``artemis.crisis_content.tab_resolution``) and this card's identity is
    present in it. Still ``None`` when no ``tab_map`` is supplied, or when
    this specific card could not be positively located this tick -- in the
    latter case ``record_observation`` skips the card entirely rather than
    emitting a ``Transition`` with a guessed tab, so ``tab_id`` is never
    ``None`` on an emitted ``Transition`` for a card that WAS resolved as a
    test card (see ``is_test`` below); ``None`` here means either "no tab
    resolution was attempted" or "this card was skipped," never "resolved,
    and it's not a test."

    ``is_test`` (CCA13) is ``True`` iff this card's live table lives on a
    tab whose title contains ``settings.crisis_content_test_tab_marker``
    (see ``artemis.crisis_content.tab_resolution._is_test_title``) --
    Jon's test lane, a duplicated card he can approve/edit/reopen without
    the channel or the external vendor seeing any of it. Consumed by
    ``artemis.crisis_content.notify.post_transition_card`` to route a test
    card to Jon's DM (never the channel) with the ``⚠️ Testing`` footer
    restored, regardless of ``settings.crisis_content_notify_destination``.
    Defaults ``False`` -- exactly the pre-CCA13 behavior -- for every
    transition where tab resolution was not attempted or did not apply.
    """

    model_config = ConfigDict(frozen=True)

    card: ReviewCard
    route: Route
    previous_status: str | None
    new_status: str
    is_new_card: bool
    reopened_after_approval: ReopenedAfterApproval | None = None
    tab_id: str | None = None
    is_test: bool = False


async def record_observation(
    session: AsyncSession,
    cards: Sequence[ReviewCard],
    tab_map: Mapping[CardIdentityKey, CardTabInfo] | None = None,
) -> list[Transition]:
    """Persist ``cards`` and return the transitions worth notifying on.

    Per card: resolve the card row by identity (insert if new, else update
    the mutable fields and ``last_seen_at``), append a copy-version row iff
    this exact ``(card, copy_hash)`` pair hasn't been seen before, then
    compute transitions. See the module docstring for the transaction
    contract -- this function flushes but never commits.

    ``tab_map`` (CCA13, optional) is ``ReviewCard.identity_key ->
    CardTabInfo``, built once per poll tick by
    ``artemis.crisis_content.tab_resolution.resolve_card_tab_map`` -- see
    the module docstring's "Tab resolution + the test lane" section for the
    full contract. ``None`` (the default, and every call from a test written
    before CCA13) disables the feature entirely: behavior is byte-for-byte
    what it was before this slice. When a mapping IS supplied, any card
    whose identity is missing from it is skipped in full -- no row upsert,
    no copy-version append, no transition -- because without a resolved tab
    this module cannot tell a test card from a real one, and guessing wrong
    is the exact failure the test lane exists to prevent. That card's stored
    status is left untouched, so the next tick's before/after comparison is
    unaffected and will retry cleanly once resolution succeeds.
    """
    transitions: list[Transition] = []
    for card in cards:
        tab_info: CardTabInfo | None = None
        if tab_map is not None:
            tab_info = tab_map.get(card.identity_key)
            if tab_info is None:
                logger.error(
                    "crisis_content: card=%r (header=%r) has no resolved tab this "
                    "tick -- skipping ALL observation for it (no row upsert, no "
                    "transition) so its stored status is not silently advanced "
                    "without a matching notification. The next tick will retry.",
                    card.identity_key,
                    card.header,
                )
                continue
        transitions.extend(await _observe_card(session, card, tab_info))
    return transitions


async def _observe_card(
    session: AsyncSession, card: ReviewCard, tab_info: CardTabInfo | None = None
) -> list[Transition]:
    now = datetime.now(UTC)
    is_test = tab_info.is_test if tab_info is not None else False
    row, is_new, previous_asset_status, previous_copy_status = await _resolve_card_row(
        session, card, now, is_test=is_test
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
            tab_info=tab_info,
        )
        if transition is not None:
            card_transitions.append(transition)
    return card_transitions


async def _resolve_card_row(
    session: AsyncSession, card: ReviewCard, now: datetime, *, is_test: bool = False
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
            is_test=is_test,
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
    row.is_test = is_test
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
    tab_info: CardTabInfo | None = None,
) -> Transition | None:
    """Apply the transition + suppression rules for one route of one card.

    Emits iff: the status is set, is recognized, is exactly ``Ready``, and
    (for the asset route) an asset is actually attached -- AND EITHER the
    status differs from the previous observation (the normal case) OR
    ``find_reopening_decision`` finds a genuine reopen (a
    ``changes_requested`` OR ``approved`` decision followed by a revised
    copy version -- see the module docstring's "The re-approval fix (CCA9)"
    and "Reopening after approval too (CCA11)"). An unrecognized non-null
    status logs a WARNING and never emits -- silence here means Jen added a
    dropdown option and the pipeline quietly stopped working.

    ``tab_info`` (CCA13) is copied straight onto the emitted ``Transition``'s
    ``tab_id``/``is_test`` -- ``None`` (no tab resolution attempted, or this
    card was not in ``record_observation``'s ``tab_map``) reproduces the
    pre-CCA13 defaults exactly.
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

    if new_status != _READY:
        return None

    if route == "asset" and card.asset_url is None:
        return None

    reopening_decision: CrisisContentDecision | None = None
    if new_status == previous_status:
        # The chip never moved -- normally nothing to do. The one exception
        # is a genuine reopen: a changes_requested OR approved decision
        # followed by a genuine revision. See the module docstring's "The
        # re-approval fix (CCA9)" and "Reopening after approval too
        # (CCA11)".
        reopening_decision = await find_reopening_decision(session, card_id, route)
        if reopening_decision is None:
            return None
        logger.info(
            "crisis_content: re-firing %s route for card_id=%s -- a %s decision "
            "was followed by a revised copy version",
            route,
            card_id,
            reopening_decision.decision,
        )

    if await has_notified(session, card_id, route, new_status, card.copy_hash):
        return None

    reopened_after_approval: ReopenedAfterApproval | None = None
    if reopening_decision is not None and reopening_decision.decision == "approved":
        reopened_after_approval = ReopenedAfterApproval(
            approved_by=_decision_actor_label(reopening_decision),
            approved_at=reopening_decision.decided_at,
        )

    return Transition(
        card=card,
        route=route,
        previous_status=previous_status,
        new_status=new_status,
        is_new_card=is_new_card,
        reopened_after_approval=reopened_after_approval,
        tab_id=tab_info.tab_id if tab_info is not None else None,
        is_test=tab_info.is_test if tab_info is not None else False,
    )


def _decision_actor_label(decision: CrisisContentDecision) -> str:
    """Best-effort human label for the reopened-after-approval banner.

    Deliberately the INVERSE preference of ``writeback._actor_label`` /
    ``image_link._poster_label``, which prefer the email. Those two write into
    a Google Doc, where ``<@U123>`` is meaningless literal text. This banner
    renders in Slack, where a mention resolves to the person's display name --
    "Previously approved by @Angela M" instead of
    "by angela.miata@amiralearning.com". Same underlying data, different
    destination, so the right choice differs; keep them distinct rather than
    unifying them.

    Still invents no display-name resolution (no extra ``users.info`` call):
    Slack does the rendering for us from the id already on the decision row.
    """
    if decision.decided_by_slack_user_id:
        return f"<@{decision.decided_by_slack_user_id}>"
    if decision.decided_by_email:
        return decision.decided_by_email
    return "unknown"


async def find_reopening_decision(
    session: AsyncSession, card_id: int, route: Route
) -> CrisisContentDecision | None:
    """The decision that reopens ``route``, or ``None`` if it should not reopen.

    Renamed from ``_reopened_after_changes_requested`` (CCA11) -- that name
    stopped being accurate the moment ``approved`` also became reopenable;
    keeping it would have been a trap for the next reader, who would have
    had no reason to suspect a function with that name also handles the far
    more consequential case of an approved route being revised out from
    under the person who signed off on it.

    Returns the LATEST decision row for ``(card_id, route)`` iff its
    ``decision`` is ``changes_requested`` OR ``approved``
    (``_REOPENABLE_DECISIONS``) AND a ``crisis_content_copy_versions`` row
    exists for this CARD with ``first_seen_at`` after that decision's
    ``decided_at`` -- i.e. someone has genuinely revised the copy since.
    Returns ``None`` for no decision at all, a decision value outside
    ``_REOPENABLE_DECISIONS`` (there is currently no third value, but this
    is defensive), or no qualifying revision.

    Reads ``CrisisContentDecision`` directly with the same
    ``ORDER BY id DESC`` "latest decision" query
    ``artemis.crisis_content.decisions.get_latest_decision`` uses (same
    same-timestamp-burst reasoning: ``id`` is strictly monotonic on insert
    order, ``decided_at`` is not), rather than importing that function --
    ``decisions.py`` already imports ``Route`` from THIS module, so
    importing back from ``decisions.py`` here would be a circular import.
    ``artemis.crisis_content.orm`` is a leaf module (imports nothing from
    either), so reading its ``CrisisContentDecision`` class directly here is
    safe.

    Routes reopen independently BY CONSTRUCTION: the query below filters on
    ``route == route``, so a route with no decision, or whose own latest
    decision doesn't qualify, never reopens -- regardless of the OTHER
    route's decision. The version check IS still card-level, not
    route-level: there is no asset-specific version log (no new column was
    added for this -- brief section 5), so the SAME
    ``crisis_content_copy_versions`` log (keyed only by ``card_id``) is the
    "has this post been touched since the decision" signal for BOTH routes.
    This mirrors the asset route rather than special-casing it, per the
    CCA9 brief -- so if both routes independently have a qualifying
    decision, one genuine revision CAN reopen both; see
    ``test_asset_route_reapproval_mirrors_copy_route_rule``.
    """
    stmt = (
        select(CrisisContentDecision)
        .where(CrisisContentDecision.card_id == card_id, CrisisContentDecision.route == route)
        .order_by(CrisisContentDecision.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    latest = result.scalar_one_or_none()
    if latest is None or latest.decision not in _REOPENABLE_DECISIONS:
        return None

    version_stmt = (
        select(CrisisContentCopyVersion.id)
        .where(
            CrisisContentCopyVersion.card_id == card_id,
            CrisisContentCopyVersion.first_seen_at > latest.decided_at,
        )
        .limit(1)
    )
    version_result = await session.execute(version_stmt)
    if version_result.scalar_one_or_none() is None:
        return None
    return latest


async def find_card_id(session: AsyncSession, card: ReviewCard) -> int | None:
    """Read-only identity lookup for ``card``'s persisted ``CrisisContentCard.id``.

    NULL-safe on the platform column for the same reason ``_resolve_card_row``
    is above. Returns ``None`` when the card has never been observed, rather
    than raising -- used by ``artemis.crisis_content.notify.post_transition_card``
    (slice B2c, CCA5), which needs the row id to build a decision button's
    ``value`` and treats a miss as a real bug (it always runs after
    ``record_observation`` has upserted the row) rather than a case to
    silently swallow.

    Deliberately NOT the same helper as ``poller._resolve_card_id`` (private
    to that module, and it raises via ``scalar_one()`` because its caller
    already knows the row must exist) -- see that function's own docstring
    for why it duplicates this identity comparison rather than importing a
    shared one; this is the second, public copy for a caller with a
    different failure contract.
    """
    _, platform, ordinal = card.identity_key
    stmt = select(CrisisContentCard.id).where(
        CrisisContentCard.identity_header == card.header,
        CrisisContentCard.identity_ordinal == ordinal,
    )
    stmt = stmt.where(
        CrisisContentCard.identity_platform.is_(None)
        if platform is None
        else CrisisContentCard.identity_platform == platform
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def has_notified(
    session: AsyncSession, card_id: int, route: Route, status_value: str, copy_hash: str
) -> bool:
    """True if ``mark_notified`` already recorded this ``(card, route, status, copy_hash)``.

    ``copy_hash`` joined the dedup key in CCA9 (migration 0109) -- see the
    module docstring's "The re-approval fix (CCA9)". Without it, a genuine
    revision after a ``changes_requested`` decision would be swallowed by
    the OLD ledger row for the same ``(card, route, 'Ready')``, because the
    copy chip itself never leaves ``Ready`` (finding 5 -- chip values cannot
    be written back).
    """
    stmt = (
        select(CrisisContentNotification.id)
        .where(
            CrisisContentNotification.card_id == card_id,
            CrisisContentNotification.route == route,
            CrisisContentNotification.status_value == status_value,
            CrisisContentNotification.copy_hash == copy_hash,
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
    copy_hash: str,
    channel_id: str | None = None,
    message_ts: str | None = None,
    notified_at: datetime | None = None,
) -> None:
    """Record that ``(card_id, route, status_value, copy_hash)`` has been notified.

    Slice B2 calls this only after a successful Slack post -- never before,
    so a delivery failure is never mistaken for a delivered one.
    ``ON CONFLICT DO NOTHING`` on the same unique constraint the migration
    creates makes a retried call safe. Does not commit -- see the module
    docstring's transaction contract.

    ``copy_hash`` is required (CCA9) -- every real caller has it trivially
    available (``transition.card.copy_hash``), and making it required
    rather than defaulted avoids a caller silently writing a row the new
    re-approval dedup can never actually match against. ``channel_id`` /
    ``message_ts`` are optional and default to ``None`` -- CCA9 records
    them so ``find_card_thread_target``
    (``artemis.crisis_content.thread_notes``) can map a Slack thread reply
    back to this card, but a caller that cannot determine where the message
    landed (e.g. a test double that doesn't echo Slack's response) should
    not be forced to fabricate a value.
    """
    stamp = notified_at if notified_at is not None else datetime.now(UTC)
    stmt = (
        pg_insert(CrisisContentNotification)
        .values(
            card_id=card_id,
            route=route,
            status_value=status_value,
            copy_hash=copy_hash,
            channel_id=channel_id,
            message_ts=message_ts,
            notified_at=stamp,
        )
        .on_conflict_do_nothing(constraint=_NOTIFICATIONS_CONSTRAINT)
    )
    await session.execute(stmt)


async def find_posted_location(
    session: AsyncSession, card_id: int, route: Route
) -> tuple[str | None, str | None] | None:
    """Where the most recent notification for ``(card_id, route)`` was posted.

    Read-only. Returns ``(channel_id, message_ts)`` from the newest matching
    ``crisis_content_notifications`` row (highest ``id``), or ``None`` if no
    notification has ever been recorded for this route. Used by CCA9's Jen
    change-request mention (``artemis.crisis_content.slack_actions``) to
    find which thread to post into -- a miss, or an incomplete pair (either
    element ``None``), means "nothing to thread onto," and that caller skips
    rather than guessing a destination.
    """
    stmt = (
        select(CrisisContentNotification.channel_id, CrisisContentNotification.message_ts)
        .where(CrisisContentNotification.card_id == card_id, CrisisContentNotification.route == route)
        .order_by(CrisisContentNotification.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return (row[0], row[1])
