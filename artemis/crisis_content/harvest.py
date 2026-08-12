"""Harvest approved crisis-content copy into Writing Studio (CCA14, slice D).

See ``docs/crisis-content-approval-pipeline.md`` "Slice D -- harvest approved
copy into Writing Studio" and ``briefs/cca14-harvest-approved-copy.md`` for
the full design; several decisions there are implemented here without being
relitigated (see each function's docstring for which).

**What this module does.** After an ``approved`` decision on the ``copy``
route (never ``asset`` -- see ``harvest_decision``'s docstring), fan the
approved text out into one ``writing_examples`` row per canonical channel
the vendor's ``Platform`` value maps to, under a dedicated ``Amira Social``
writing profile (created on first use). Silent: no second button, nothing
on the card mentions storage -- every copy-route approval is harvested. A
test card (``CrisisContentCard.is_test``, CCA13) is never harvested.

**Retrieval answer (the brief's open question).** Read
``artemis.writing_rules.repository.list_examples`` and
``artemis.marketing.writing_studio.compose_engine.build_ruleset_grounding_block``:
retrieval over ``writing_examples`` is a plain profile-scoped ``SELECT``
(``list_examples`` takes an optional ``profile_id``/``example_type`` filter,
orders by ``created_at, id`` -- no vector column, no ``ORDER BY embedding
<=>``), followed by a deterministic in-Python relevance RANKING
(``_example_relevance_score``: +3 for an exact ``asset_type`` match, +3 for
an exact ``channel`` match, capped at ``PROMPT_EXAMPLE_LIMIT``). There is no
``embedding`` column on ``WritingExample`` anywhere in the schema. **This
module therefore writes no embedding** -- there is nothing in the retrieval
path that would ever read one, and adding one would be dead weight the
brief explicitly said not to add speculatively.

**Hook pattern.** Mirrors
``artemis.crisis_content.writeback.schedule_decision_writeback`` exactly:
fire-and-forget off the Slack interactivity request path (a DB round trip
plus a possible Slack alert can outlast Slack's 3-second budget), on its own
DB session (the request's session may already be closed by the time this
task runs). Called from ``artemis.crisis_content.slack_actions`` immediately
after ``schedule_decision_writeback``, for the same ``approved`` click only
-- never for ``Edit in doc`` (``changes_requested``), matching writeback's
own rule that only a genuine approval schedules any side effect here.

**Idempotency.** Dedup key is ``(copy_hash, channel)``, not ``copy_hash``
alone -- a combo post ("FB, LI, & X") fans out to three rows sharing one
``copy_hash``, and each must independently survive a re-run. Enforced both
at the application level (``get_example_by_copy_hash_channel`` before
insert) and at the DB level (``uq_writing_examples_copy_hash_channel``,
migration 0113) as a race-safety net -- see ``_harvest_one_channel``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.config import settings
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
    CrisisContentDecision,
)
from artemis.integrations.slack.client import SlackClient
from artemis.proactivity.commitments import (
    _get_slack_token_for_agent,
    _resolve_artemis_dm_recipient,
)
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.models import WritingProfile

logger = logging.getLogger(__name__)

__all__ = [
    "CANONICAL_CHANNELS",
    "SOCIAL_PROFILE_NAME",
    "ChannelResolution",
    "HarvestOutcome",
    "harvest_decision",
    "resolve_channels",
    "schedule_decision_harvest",
]

SOCIAL_PROFILE_NAME = "Amira Social"

# writing_examples.example_type / asset_type values for a harvested row.
# "reference"/"template"/"learned" are the vocabulary already in use
# (seed_corpus.py, repository.promote_training_candidate); "approved_post"
# is a new, distinct value -- these rows are neither reference material nor
# a training-candidate promotion, they are finished, shipped copy.
_EXAMPLE_TYPE = "approved_post"
_ASSET_TYPE = "social_post"

# The design doc's "All" row: "every canonical channel -- define the list
# explicitly, do not infer." This IS the explicit definition; nothing
# elsewhere derives it from the alias table below.
CANONICAL_CHANNELS: tuple[str, ...] = ("facebook", "instagram", "linkedin", "x")

_CHANNEL_LABELS: dict[str, str] = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "x": "X",
}

# Single-platform tokens only. A combo value ("FB/IG", "FB, LI, & X", ...) is
# split on [,/&] and each piece is looked up here -- see resolve_channels.
_SINGLE_PLATFORM_ALIASES: dict[str, str] = {
    "facebook": "facebook",
    "fb": "facebook",
    "instagram": "instagram",
    "ig": "instagram",
    "linkedin": "linkedin",
    "li": "linkedin",
    "x": "x",
    "twitter": "x",
    "twitter(x)": "x",
}

_TOKEN_SPLIT_RE = re.compile(r"[,/&]")

PlatformStatus = Literal["resolved", "no_platform", "unrecognized"]


@dataclass(frozen=True)
class ChannelResolution:
    """Result of mapping one vendor ``Platform`` value to canonical channels.

    ``status``:
      - ``"resolved"``   -- ``channels`` is non-empty; fan out and harvest.
      - ``"no_platform"``-- ``TBD``, blank, or unset (``None``). Nothing to
        harvest, and this is NOT an alert-worthy state: an unset platform
        chip is Jen's normal in-progress state, not a vendor adding a new
        dropdown option. (The brief's alias table does not list ``None``
        explicitly; treating it the same as ``TBD`` is a judgment call made
        here, not a decision recorded in the design doc -- see the CCA14
        report.)
      - ``"unrecognized"`` -- a non-empty value that does not parse into
        known single-platform tokens. ``channels`` is empty; the caller
        MUST alert (never store the literal value, never silently drop it).
    """

    status: PlatformStatus
    channels: tuple[str, ...] = ()


def resolve_channels(platform: str | None) -> ChannelResolution:
    """Map a vendor ``Platform`` chip value to canonical Writing Studio channels.

    Implements ``docs/crisis-content-approval-pipeline.md`` "Channel
    normalization" / ``briefs/cca14-harvest-approved-copy.md`` "Channel
    fan-out" -- do not relitigate the alias table itself, only extend it if a
    genuinely new single-platform token appears.

    ``All`` is special-cased to the explicit ``CANONICAL_CHANNELS`` tuple
    rather than derived from the alias table, per the brief ("define the
    list explicitly, do not infer"). Every other non-blank, non-``TBD``
    value is tokenized on ``,``/``/``/``&`` (handles ``"FB/IG"``,
    ``"Facebook, IG, X"``, ``"FB, LI, & X"``, ``"Facebook/IG, LinkedIn"`` --
    the fuller value set the design doc records, a superset of the brief's
    own worked table) and each token is looked up in
    ``_SINGLE_PLATFORM_ALIASES``. Channels are deduplicated, preserving
    first-seen order. If ANY token fails to resolve, the WHOLE value is
    unrecognized -- a partially-understood combo is not a safe partial
    harvest, it is a signal the alias table is missing something.
    """
    if platform is None:
        return ChannelResolution(status="no_platform")
    normalized = platform.strip()
    if not normalized:
        return ChannelResolution(status="no_platform")
    lowered = normalized.lower()
    if lowered == "tbd":
        return ChannelResolution(status="no_platform")
    if lowered == "all":
        return ChannelResolution(status="resolved", channels=CANONICAL_CHANNELS)

    tokens = [tok.strip().lower() for tok in _TOKEN_SPLIT_RE.split(normalized) if tok.strip()]
    if not tokens:
        return ChannelResolution(status="unrecognized")

    resolved: list[str] = []
    for token in tokens:
        canonical = _SINGLE_PLATFORM_ALIASES.get(token)
        if canonical is None:
            return ChannelResolution(status="unrecognized")
        if canonical not in resolved:
            resolved.append(canonical)
    return ChannelResolution(status="resolved", channels=tuple(resolved))


HarvestStatus = Literal[
    "not_approved",
    "wrong_route",
    "test_card",
    "no_platform",
    "unrecognized_platform",
    "missing_copy_version",
    "card_not_found",
    "disabled",
    "harvested",
]


@dataclass(frozen=True)
class HarvestOutcome:
    status: HarvestStatus
    rows_written: int = 0
    channels: tuple[str, ...] = ()


async def harvest_decision(
    session: AsyncSession, decision: CrisisContentDecision
) -> HarvestOutcome:
    """Harvest one decision's approved copy, fanned out across channels.

    Only ``decision.decision == "approved"`` harvests -- ``changes_requested``
    (including the ``Edit in doc`` decision, which never even schedules this
    call -- see the module docstring) never does. Re-reads the card's CURRENT
    ``copy_hash``/``identity_platform`` from the DB rather than trusting
    anything captured earlier: the copy can change between the "Ready"
    notification and the approval click (Jen edits, or CCA11's
    reopen-after-approval), and the corpus must hold what was actually
    approved, not a stale snapshot (brief: "Capture the final text").

    **Only the ``copy`` route harvests.** ``CrisisContentDecision.route`` is
    also ``"asset"`` -- Jon approving the graphic. That decision is about the
    image, not the wording, and can land before OR after the copy route's own
    decision; harvesting off it would risk storing text that copy approval
    hasn't actually signed off on yet (or duplicating the same text a second
    time, harmlessly caught by the ``(copy_hash, channel)`` dedup, but for
    the wrong reason). The brief's own title and every "when copy is
    approved" phrasing in the design doc are specific to this route; an
    ``asset``-route approval returns ``"wrong_route"`` and writes nothing.
    ``slack_actions.py`` calls ``schedule_decision_harvest`` unconditionally
    for every ``Approve`` click (both routes) -- this function, not the
    caller, is where that distinction is enforced, mirroring how
    ``writeback.py`` centralizes its own per-decision rules.

    Safe to call more than once for the SAME decision -- see the module
    docstring's "Idempotency". Never raises to the caller in the intended
    (background-task) use; ``schedule_decision_harvest`` /
    ``_run_harvest_background`` provide that guarantee, mirroring
    ``writeback.schedule_decision_writeback``. Called directly (not through
    the scheduler) is fine too and is how the test suite exercises it.
    """
    if not settings.crisis_content_harvest_enabled:
        logger.warning(
            "crisis_content harvest: disabled via settings "
            "(crisis_content_harvest_enabled=False) -- decision_id=%s will NOT "
            "be harvested until re-enabled",
            decision.id,
        )
        return HarvestOutcome(status="disabled")

    if decision.decision != "approved":
        logger.info(
            "crisis_content harvest: decision_id=%s is %r, not 'approved' -- nothing to harvest",
            decision.id,
            decision.decision,
        )
        return HarvestOutcome(status="not_approved")

    if decision.route != "copy":
        logger.info(
            "crisis_content harvest: decision_id=%s route=%r is not 'copy' -- only a "
            "copy-route approval harvests text into Writing Studio",
            decision.id,
            decision.route,
        )
        return HarvestOutcome(status="wrong_route")

    card = await session.get(CrisisContentCard, decision.card_id)
    if card is None:
        logger.error(
            "crisis_content harvest: no CrisisContentCard row for card_id=%s (decision_id=%s)",
            decision.card_id,
            decision.id,
        )
        return HarvestOutcome(status="card_not_found")

    if card.is_test:
        # A test post is training data from a fake post -- never enters the
        # corpus, regardless of route or platform. See migration 0111 /
        # CrisisContentCard.is_test.
        logger.info(
            "crisis_content harvest: skipping TEST card_id=%s decision_id=%s -- "
            "is_test posts never enter the Writing Studio corpus",
            card.id,
            decision.id,
        )
        return HarvestOutcome(status="test_card")

    resolution = resolve_channels(card.identity_platform)
    if resolution.status == "no_platform":
        logger.info(
            "crisis_content harvest: card_id=%s has no usable platform value (%r) -- "
            "nothing to harvest",
            card.id,
            card.identity_platform,
        )
        return HarvestOutcome(status="no_platform")
    if resolution.status == "unrecognized":
        logger.error(
            "crisis_content harvest: unrecognized platform value %r on card_id=%s "
            "decision_id=%s -- alerting, NOT harvesting, NOT storing the literal "
            "value as a channel",
            card.identity_platform,
            card.id,
            decision.id,
        )
        await _alert_jon(
            session,
            "🚨 Crisis-content harvest: unrecognized platform value "
            f"{card.identity_platform!r} on card_id={card.id} (decision #{decision.id}, "
            f"header {card.identity_header!r}). Nothing was harvested into Writing "
            "Studio -- add this value to the channel alias map in "
            "artemis/crisis_content/harvest.py before it happens again.",
        )
        return HarvestOutcome(status="unrecognized_platform")

    copy_body = await _current_copy_body(session, card)
    if copy_body is None:
        logger.error(
            "crisis_content harvest: no crisis_content_copy_versions row for "
            "card_id=%s copy_hash=%s (decision_id=%s) -- cannot harvest without "
            "the approved text",
            card.id,
            card.copy_hash,
            decision.id,
        )
        await _alert_jon(
            session,
            "🚨 Crisis-content harvest: could not find the approved copy text for "
            f"decision #{decision.id} (card_id={card.id}). Nothing was harvested.",
        )
        return HarvestOutcome(status="missing_copy_version")

    profile = await _get_or_create_social_profile(session)

    written = 0
    for channel in resolution.channels:
        written += await _harvest_one_channel(
            session,
            profile=profile,
            card=card,
            copy_hash=card.copy_hash,
            copy_body=copy_body,
            channel=channel,
        )

    logger.info(
        "crisis_content harvest: decision_id=%s card_id=%s wrote %s/%s example "
        "row(s) (channels=%s)",
        decision.id,
        card.id,
        written,
        len(resolution.channels),
        resolution.channels,
    )
    return HarvestOutcome(status="harvested", rows_written=written, channels=resolution.channels)


async def _current_copy_body(session: AsyncSession, card: CrisisContentCard) -> str | None:
    """The approved text, read fresh at harvest time -- see ``harvest_decision``.

    ``card.copy_hash`` is the LATEST hash the poller has observed for this
    card (``transitions._resolve_card_row`` updates it on every poll tick,
    whether or not a new version is appended). Joining that against
    ``crisis_content_copy_versions`` -- the append-only log
    ``transitions._maybe_append_copy_version`` writes -- is "the card's
    current state" the brief asks for, as opposed to whatever text a
    Slack-rendered notification happened to carry when it first posted
    (which this function never reads).
    """
    stmt = (
        select(CrisisContentCopyVersion.copy_body)
        .where(
            CrisisContentCopyVersion.card_id == card.id,
            CrisisContentCopyVersion.copy_hash == card.copy_hash,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _get_or_create_social_profile(session: AsyncSession) -> WritingProfile:
    """Get-or-create the ``Amira Social`` profile, exactly once.

    Decided 2026-08-11 (design doc): separate from ``Amira Marketing Voice``
    because ``writing_rules`` are profile-scoped, and sharing one profile
    would leak social conventions into document drafting. Leaves that
    profile, its rules, and Angela's pending training candidates completely
    untouched -- this function only ever reads/writes a profile named
    ``Amira Social``.

    **Known race, accepted, not silently pretended away**: there is no DB
    unique constraint on ``writing_profiles.name`` (none existed before this
    slice, and adding one is a broader schema decision than this brief asks
    for -- other profiles may legitimately share a name today). Two
    background harvest tasks racing this get-or-create for the very first
    time could each insert an ``Amira Social`` row. This is the same level
    of protection ``artemis.writing_rules.seed_corpus.import_writing_seed_corpus``
    already accepts for the default profile (check-then-create, no DB
    backstop) -- not a new gap this slice introduces, but worth flagging: see
    the CCA14 report.
    """
    existing = await wr_repo.get_profile_by_name(session, SOCIAL_PROFILE_NAME)
    if existing is not None:
        return existing
    profile = await wr_repo.create_profile(
        session,
        name=SOCIAL_PROFILE_NAME,
        description=(
            "Approved social copy harvested from the crisis-comms content "
            "pipeline (CCA14). Kept separate from Amira Marketing Voice: "
            "writing_rules are profile-scoped, and social conventions "
            "(hashtags, character limits, 'Link in bio') would otherwise leak "
            "into document/whitepaper drafting."
        ),
        status="active",
    )
    await session.commit()
    logger.info(
        "crisis_content harvest: created %r writing profile id=%s", SOCIAL_PROFILE_NAME, profile.id
    )
    return profile


async def _harvest_one_channel(
    session: AsyncSession,
    *,
    profile: WritingProfile,
    card: CrisisContentCard,
    copy_hash: str,
    copy_body: str,
    channel: str,
) -> int:
    """Insert one ``writing_examples`` row for ``channel``, idempotently.

    Returns 1 if a row was written, 0 if it already existed (a genuine
    no-op re-run) or an insert race lost to a concurrent writer (see the
    ``IntegrityError`` handling below).

    ``title`` embeds the card's full identity header (which already carries
    Jen's date/topic disambiguator), the channel label, and an 8-char
    ``copy_hash`` prefix. The hash suffix is deliberate, not decorative:
    ``writing_examples`` has an existing UNIQUE constraint on
    ``(profile_id, title, example_type)`` that predates this slice
    (``idx_writing_examples_profile_title_type``) and is relied on elsewhere
    (``repository.promote_training_candidate``). Without a disambiguator, a
    card that is approved, edited, and re-approved (CCA11's
    reopened-after-approval path) would produce a SECOND harvest with a
    genuinely different ``copy_hash`` and body but an IDENTICAL header +
    channel + example_type -- which would collide on that title constraint
    even though it is exactly the kind of before/after pair the design doc
    calls out as valuable training signal. The hash suffix keeps both
    revisions in the corpus instead of losing the second one to a silent
    constraint violation.
    """
    existing = await wr_repo.get_example_by_copy_hash_channel(session, copy_hash, channel)
    if existing is not None:
        logger.info(
            "crisis_content harvest: (copy_hash=%s, channel=%s) already harvested as "
            "writing_examples.id=%s -- skipping (idempotent re-run)",
            copy_hash,
            channel,
            existing.id,
        )
        return 0

    label = _CHANNEL_LABELS[channel]
    title = f"{card.identity_header} — {label} [{copy_hash[:8]}]"
    try:
        await wr_repo.create_example(
            session,
            profile_id=profile.id,
            title=title,
            body=copy_body,
            example_type=_EXAMPLE_TYPE,
            asset_type=_ASSET_TYPE,
            channel=channel,
            copy_hash=copy_hash,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning(
            "crisis_content harvest: insert raced/collided for (copy_hash=%s, "
            "channel=%s) -- treating as already-harvested",
            copy_hash,
            channel,
        )
        return 0
    return 1


async def _alert_jon(session: AsyncSession, text: str) -> None:
    """Best-effort owner alert DM. Never raises.

    Duplicated rather than imported from ``artemis.crisis_content.writeback``
    on purpose -- that module's own ``_alert_jon`` docstring records the
    house convention here: each caller of this exact resolution path
    (``_get_slack_token_for_agent`` + ``_resolve_artemis_dm_recipient``)
    holds its own copy rather than sharing one, and ``writeback.py`` is out
    of file scope for this slice regardless.
    """
    try:
        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning(
                "crisis_content harvest: no active Slack token for agent_id='artemis' "
                "-- cannot alert Jon"
            )
            return
        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=text)
    except Exception:
        logger.exception("crisis_content harvest: failed to send owner alert DM")


# ---------------------------------------------------------------------------
# Fire-and-forget scheduling -- called from slack_actions.py
# ---------------------------------------------------------------------------

# Mirrors writeback._BACKGROUND_TASKS / integrations_slack_events.py's own
# pattern: asyncio.create_task() returns a weakly-referenced Task, so hold a
# strong ref here or it can be GC'd before it runs.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def schedule_decision_harvest(decision_id: int) -> None:
    """Fire-and-forget: harvest one decision into Writing Studio, off the request path.

    Called from ``artemis.crisis_content.slack_actions`` immediately after
    ``schedule_decision_writeback``, for an ``Approve`` click only. Opens its
    OWN session rather than reusing the request's -- see
    ``writeback.schedule_decision_writeback``'s docstring for the identical
    reasoning (the request's session may already be committed/closed by the
    time this task actually runs, and this must never block Slack's
    3-second interactivity budget).
    """
    task = asyncio.create_task(_run_harvest_background(decision_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _run_harvest_background(decision_id: int) -> None:
    try:
        async with _db.SessionLocal() as session:
            decision = await session.get(CrisisContentDecision, decision_id)
            if decision is None:
                logger.error(
                    "crisis_content harvest: decision_id=%s vanished before the "
                    "background harvest could run",
                    decision_id,
                )
                return
            outcome = await harvest_decision(session, decision)
            logger.info("crisis_content harvest: decision_id=%s outcome=%r", decision_id, outcome)
    except Exception:
        logger.exception(
            "crisis_content harvest: unhandled error in background task for decision_id=%s",
            decision_id,
        )
