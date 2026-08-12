"""Poll loop for the crisis-comms content-approval doc (slice B2b, CCA4).

Every ``settings.crisis_content_poll_interval_minutes`` (default 2), on the
existing automation-style APScheduler pattern (mirrors
``artemis/automations/scheduler.py`` / ``artemis/integrations/token_refresh/
scheduler.py``):

    resolve Jon's personal Google access token
        -> fetch_crisis_content_export_html
        -> parse_review_cards
        -> resolve_card_tab_map (CCA13, one documents.get call -- see below)
        -> record_observation (per card, see "Commit granularity" below)
        -> for each transition: post_transition_card, then mark_notified
        -> _maybe_mine_rules (CCA15, interval-gated, failures swallowed)

Two contracts this module exists to honor, both called out in the brief and
both easy to get backwards:

1. ``record_observation`` / ``mark_notified`` (``artemis.crisis_content.
   transitions``) flush but never commit. This module is the caller, so it
   owns every commit -- see ``_run_poll_tick_locked`` below.
2. ``mark_notified`` is called only *after* ``post_transition_card`` returns
   without raising -- never before. A delivery failure must never be
   recorded as delivered, or the retry this module exists to provide never
   happens.

Commit granularity (a deliberate departure from a naive "commit once at the
end of the tick"): each CARD's ``record_observation`` call and its
transitions are committed -- or rolled back -- as one unit, per card, rather
than the whole tick's cards sharing one commit. Reasoning: ``_evaluate_route``
(transitions.py) only detects a transition when the freshly observed status
differs from the row's *stored* previous status. If a card's status
advancement were committed while ANOTHER card in the same tick then failed
to post, the failed card's row would already be durably advanced to the new
status by the shared commit -- so the next poll would see "no change" and
never re-detect the failed transition, permanently losing a notification the
Slack-post-failure retry path exists to guarantee. Per-card commit/rollback
means a failure on one card cannot swallow a sibling card's retry. The
trade-off (documented, not hidden): if a single card has two routes (asset
+ copy) both transitioning in the same tick and only one route's post fails,
rolling back that card also undoes the succeeding route's already-flushed
``mark_notified`` write, so the next poll may re-send the succeeding route's
notification once more. A rare double-send is preferred over a silent,
permanent drop -- the failure mode this whole repo keeps getting burned by
(see CLAUDE.md's six-store liveness trap).

Failure handling is debounced: alert Jon once on entering a failing state,
once again on recovery, and stay quiet in between (see ``_enter_failure`` /
``_maybe_recover``). Overlap is guarded by an in-process ``asyncio.Lock``
(``_poll_lock``): a slow pass causes the next tick to skip, logged at INFO,
never to queue behind it.

**Tab resolution (CCA13) -- the dangerous failure mode.** Right after
parsing this tick's cards (and only if there are any -- an empty list skips
the network call entirely), ``_run_poll_tick_locked`` calls
``artemis.crisis_content.tab_resolution.resolve_card_tab_map`` exactly ONCE
to learn which Google Docs tab each card lives on, which is also how a card
is recognized as living in Jon's ``TESTING`` tab
(``Transition.is_test`` -- see ``transitions.py`` and ``tab_resolution.py``).
If that single ``documents.get`` call fails (``TabResolutionError``), this
function does the same thing every other pre-notify failure branch above
does: log ERROR via ``_enter_failure`` (debounced), commit, and return --
**notifying NOTHING this tick**, never calling ``record_observation`` or
``post_transition_card`` at all. This is deliberate, not merely convenient:
without tab titles this tick cannot tell a test card from a real one, and
treating an unresolved card as real would risk posting Jon's duplicated
test card to the live channel and ``@mention``-ing the external vendor
about a fake post -- the exact outcome the test lane exists to prevent.
Nothing is lost by skipping: as above, ``mark_notified`` only ever records
after a successful post, so the next tick retries cleanly. A card that
fails to resolve individually (the fetch succeeded, but that one card
couldn't be positively matched -- see ``tab_resolution.py``'s own
docstring) does not raise here; ``record_observation`` skips just that
card, per its own ``tab_map`` contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.config import settings
from artemis.crisis_content.export_client import (
    TARGET_DOCUMENT_ID,
    fetch_crisis_content_export_html,
)
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.notify import post_transition_card
from artemis.crisis_content.orm import CrisisContentCard
from artemis.crisis_content.parser import (
    NoReviewCardsFoundError,
    SignInPageError,
    parse_review_cards,
)
from artemis.crisis_content.rule_mining import (
    extract_suggestion_pairs,
    fetch_document_with_suggestions,
    record_and_propose,
)
from artemis.crisis_content.tab_resolution import (
    CardIdentityKey,
    CardTabInfo,
    TabResolutionError,
    resolve_card_tab_map,
)
from artemis.crisis_content.transitions import mark_notified, record_observation
from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
from artemis.google_docs.models import GoogleCredential
from artemis.google_integration import resolve_google_oauth_client_config
from artemis.integrations.slack.client import SlackClient
from artemis.proactivity.commitments import (
    _get_slack_token_for_agent,
    _resolve_artemis_dm_recipient,
)

logger = logging.getLogger(__name__)

__all__ = [
    "start_crisis_content_scheduler",
    "stop_crisis_content_scheduler",
    "get_crisis_content_scheduler",
    "run_poll_tick",
]

_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "crisis_content_poll"

# In-process overlap guard: a slow pass must not run concurrently with the
# next scheduled tick. asyncio.Lock rather than a DB advisory lock -- simple,
# sufficient for a single-process deployment (this app runs one uvicorn
# worker; see settings.uvicorn_workers), and testable without a DB round trip.
_poll_lock = asyncio.Lock()

# Last rule-mining pass (CCA15), for the interval gate in ``_maybe_mine_rules``.
# Module state rather than a DB row on purpose: losing it across a restart
# costs one extra documents.get, and mining is idempotent per suggestion
# (``_occurrence_key``), so an early re-run double-counts nothing.
_last_mining_at: datetime | None = None


class _CredentialUnavailableError(Exception):
    """Jon's personal Google credential is missing or could not be refreshed."""


@dataclass
class _FailureState:
    is_failing: bool = False
    reason: str | None = None


_failure_state = _FailureState()


def reset_poller_state_for_tests() -> None:
    """Test-only: reset module-level failure-debounce + mining-interval state."""
    global _last_mining_at

    _failure_state.is_failing = False
    _failure_state.reason = None
    _last_mining_at = None


# ── Scheduler wiring ─────────────────────────────────────────────────────────


def get_crisis_content_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_crisis_content_scheduler() -> None:
    """Register the poll job and start the scheduler. Called from FastAPI lifespan."""
    scheduler = get_crisis_content_scheduler()
    scheduler.add_job(
        run_poll_tick,
        trigger=IntervalTrigger(minutes=settings.crisis_content_poll_interval_minutes),
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,  # defense in depth alongside _poll_lock
        misfire_grace_time=60,
    )
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "crisis_content: poll scheduler started (interval=%d min)",
            settings.crisis_content_poll_interval_minutes,
        )


def stop_crisis_content_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("crisis_content: poll scheduler stopped")
    _scheduler = None


# ── Google credential resolution (mirrors routes/google_docs.py::_valid_access_token) ──


async def _resolve_access_token(session: AsyncSession) -> str:
    """Return a valid access token for Jon's personal Google credential.

    Token-refresh mechanics deliberately mirror
    ``artemis.routes.google_docs._valid_access_token`` rather than
    reinventing them (per the brief) -- same 60-second expiry leeway, same
    refresh call, same field updates. Raises ``_CredentialUnavailableError``
    (never an HTTPException -- there is no request here) on anything that
    would need Jon to reconnect or that Google itself rejected.

    Credential lookup is BY PURPOSE ("personal"), not by a hardcoded
    ``user_id`` -- there is exactly one personal Google account in this
    system today. ``artemis.integrations.gmail.tools._resolve_gmail_client``
    hardcodes ``user_id=1``, but that id is the dev-shim user
    ("dev@local"); Jon's real row is a different id. The already-fixed
    precedent for this exact mistake is
    ``artemis.proactivity.agency_gate._resolve_personal_gmail_client``,
    whose docstring notes it "was hardcoded user_id=1, which never matches
    the real personal account" -- this function follows that fix, not the
    still-broken gmail/tools.py one.
    """
    result = await session.execute(
        select(GoogleCredential)
        .where(GoogleCredential.purpose == "personal")
        .order_by(GoogleCredential.updated_at.desc())
        .limit(1)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise _CredentialUnavailableError(
            "No personal Google credential connected for Jon -- connect via "
            "/api/google/oauth/start?purpose=personal"
        )

    now = datetime.now(UTC)
    if credential.expiry > now + timedelta(seconds=60):
        return credential.access_token

    if not credential.refresh_token:
        raise _CredentialUnavailableError(
            "Personal Google credential has no refresh_token -- Jon must reconnect"
        )

    client_config = await resolve_google_oauth_client_config(session)
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=client_config.client_id,
            client_secret=client_config.client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise _CredentialUnavailableError(f"Google reconnect required: {exc}") from exc
    except httpx.HTTPError as exc:
        raise _CredentialUnavailableError(f"Google token refresh failed: {exc}") from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    await session.flush()
    return credential.access_token


# ── Failure debounce + owner alert ──────────────────────────────────────────


async def _alert_jon(session: AsyncSession, text: str) -> None:
    """Best-effort Slack DM to Jon via the Artemis bot. Never raises.

    Reuses the exact owner-alert path the GCal/Gmail token-death handlers use
    (``artemis.proactivity.commitments._get_slack_token_for_agent`` +
    ``_resolve_artemis_dm_recipient``) rather than inventing a new one --
    see ``artemis/integrations/gcal/auth_dead.py`` for the precedent.
    """
    try:
        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning(
                "crisis_content: no active Slack token for agent_id='artemis' -- cannot alert Jon"
            )
            return
        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=text)
        logger.info("crisis_content: owner alert DM sent (recipient=%s)", recipient_id)
    except Exception:
        logger.exception("crisis_content: failed to send owner alert DM")


async def _enter_failure(session: AsyncSession, reason: str) -> None:
    """Log the failure at ERROR always; DM Jon only on entry into failing state.

    Debounce: a 2-minute poll that DMs on every pass for the same ongoing
    condition sends ~720 messages a day and gets muted -- see the brief.
    """
    logger.error("crisis_content poll failing: %s", reason)
    if _failure_state.is_failing:
        logger.info("crisis_content: already failing -- suppressing repeat alert")
        return
    _failure_state.is_failing = True
    _failure_state.reason = reason
    await _alert_jon(
        session,
        f"🚨 Crisis-content poller is failing:\n{reason}\n\nI'll let you know when it recovers.",
    )


async def _maybe_recover(session: AsyncSession) -> None:
    """DM Jon once on the first healthy tick after a failing streak."""
    if not _failure_state.is_failing:
        return
    previous_reason = _failure_state.reason
    _failure_state.is_failing = False
    _failure_state.reason = None
    await _alert_jon(
        session,
        f"✅ Crisis-content poller recovered. Previous failure: {previous_reason}",
    )


# ── Card-id lookup (read-only; mirrors the identity lookup in transitions.py) ──


async def _resolve_card_id(session: AsyncSession, card: ReviewCard) -> int:
    """Look up the persisted ``CrisisContentCard.id`` for ``card``'s identity.

    Read-only NULL-safe lookup, mirroring the identity comparison in
    ``artemis.crisis_content.transitions._resolve_card_row`` (that helper is
    private to transitions.py and not imported here -- this module only
    consumes the exported ``CrisisContentCard`` ORM class, per the "no
    modifications outside adding exports" constraint on the merged slices).
    Safe to call right after ``record_observation`` has processed this exact
    card: the row is guaranteed to exist (flushed, even if not yet committed).
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
    return result.scalar_one()


# ── The tick ─────────────────────────────────────────────────────────────────


async def run_poll_tick() -> None:
    """Scheduler entry point. Skips (logs INFO) if the previous tick is still running."""
    if _poll_lock.locked():
        logger.info("crisis_content: previous poll tick still running -- skipping this tick")
        return
    async with _poll_lock:
        await _run_poll_tick_locked()


async def _run_poll_tick_locked() -> None:
    async with _db.SessionLocal() as session:
        try:
            access_token = await _resolve_access_token(session)
            await session.commit()
        except _CredentialUnavailableError as exc:
            await _enter_failure(session, str(exc))
            await session.commit()
            return
        except Exception as exc:  # pragma: no cover - defensive, matches "loud, not silent"
            logger.exception("crisis_content: unexpected error resolving Google credential")
            await _enter_failure(session, f"Unexpected credential error: {exc}")
            await session.commit()
            return

        try:
            html = await fetch_crisis_content_export_html(
                document_id=TARGET_DOCUMENT_ID, access_token=access_token
            )
            skipped_cards: list[str] = []
            cards = parse_review_cards(html, skipped=skipped_cards)
        except SignInPageError as exc:
            await _enter_failure(session, f"Sign-in page returned instead of doc export: {exc}")
            await session.commit()
            return
        except NoReviewCardsFoundError as exc:
            await _enter_failure(
                session,
                "Zero review cards parsed -- a label may have been renamed or the export "
                f"shape changed (this is NOT 'no work to do'): {exc}",
            )
            await session.commit()
            return
        except httpx.HTTPStatusError as exc:
            await _enter_failure(
                session,
                f"Export fetch returned HTTP {exc.response.status_code}: {exc}",
            )
            await session.commit()
            return
        except httpx.HTTPError as exc:
            await _enter_failure(session, f"Export fetch failed: {exc}")
            await session.commit()
            return

        # A malformed card is skipped rather than killing the tick (see
        # parser.parse_review_cards). Skipping silently would be the same
        # class of mistake: the vendor would think a post was submitted and
        # nobody would ever be told otherwise. So report it, once, through the
        # same debounce as every other failure -- and keep processing the
        # cards that ARE well-formed, which is the whole point of the change.
        if skipped_cards:
            await _enter_failure(
                session,
                f"{len(skipped_cards)} card(s) in the doc couldn't be read and were "
                "skipped -- the other cards were processed normally. Usually a card "
                "missing its 'August XX, 2026 - Title' header row. Details: "
                + "; ".join(skipped_cards),
            )
            await session.commit()

        # CCA13: one documents.get call, only if there is at least one card
        # to resolve a tab for -- see the module docstring's "Tab resolution"
        # section. A failure here means we cannot tell a test card from a
        # real one, so this tick notifies NOTHING and returns, same as every
        # failure branch above.
        tab_map: dict[CardIdentityKey, CardTabInfo] = {}
        if cards:
            try:
                tab_map = dict(await resolve_card_tab_map(access_token, TARGET_DOCUMENT_ID, cards))
            except TabResolutionError as exc:
                await _enter_failure(
                    session,
                    "Tab resolution failed -- cannot tell test cards from real ones, "
                    f"so nothing will be notified this tick: {exc}",
                )
                await session.commit()
                return

        notify_failures: list[str] = await _observe_and_notify(session, cards, tab_map)

        if notify_failures:
            await _enter_failure(session, "Slack post failed for: " + "; ".join(notify_failures))
            await session.commit()
        else:
            await _maybe_recover(session)
            await session.commit()

    # Outside the tick's session and after every notification decision, so a
    # mining fault cannot roll back or delay an approval post (see
    # ``_maybe_mine_rules``).
    await _maybe_mine_rules(access_token)


async def _maybe_mine_rules(access_token: str) -> None:
    """Mine Angela and Hannah's suggestions into candidate rules (CCA15).

    Deliberately the LAST thing a tick does, in its own session, and it
    swallows every exception. Mining is an opportunistic read that turns
    repeated editorial edits into proposals for Angela's review queue; the
    notification path is the product. A mining failure must never call
    ``_enter_failure`` -- that debounce-alerts Jon and marks the whole
    pipeline failing, which would mean a broken nice-to-have stops the
    approvals that the vendor and three approvers depend on.

    Gated on its own interval (``settings.crisis_content_rule_mining_
    interval_minutes``, 0 to disable) because it needs a SECOND
    ``documents.get``: mining reads suggestions, so it cannot reuse tab
    resolution's ``PREVIEW_WITHOUT_SUGGESTIONS`` fetch, whose whole point is
    that suggestions are absent.
    """
    global _last_mining_at

    interval = settings.crisis_content_rule_mining_interval_minutes
    if interval <= 0:
        return

    now = datetime.now(UTC)
    if _last_mining_at is not None and now - _last_mining_at < timedelta(minutes=interval):
        return
    # Stamp BEFORE the work, not after: a mining pass that raises should still
    # wait out the interval rather than retrying on every 2-minute tick.
    _last_mining_at = now

    try:
        document = await fetch_document_with_suggestions(access_token, TARGET_DOCUMENT_ID)
        pairs = extract_suggestion_pairs(document)
        if not pairs:
            # INFO, not DEBUG: this pass runs once an hour, so one line is not
            # noise, and "found nothing" and "never ran" must be
            # distinguishable in the log. Reading a silent path as a dead one
            # (and vice versa) is this repo's recurring diagnostic failure --
            # see CLAUDE.md's six-store liveness trap.
            logger.info("crisis_content: rule mining ran, no suggestion pairs in the doc")
            return
        async with _db.SessionLocal() as session:
            result = await record_and_propose(session, pairs)
        logger.info(
            "crisis_content: rule mining saw %d pair(s), recorded %d new occurrence(s) "
            "(skipped %d test-tab, %d noise), proposed %d candidate rule(s), "
            "held %d at threshold by the length guard",
            len(pairs),
            result.new_observations,
            result.skipped_test_tab,
            result.skipped_noise,
            len(result.proposed_candidates),
            result.held_by_length_guard,
        )
    except Exception:  # noqa: BLE001 -- see docstring: must not break notification
        logger.exception(
            "crisis_content: rule mining failed -- notifications are unaffected; "
            "the next pass retries in %d min",
            interval,
        )


async def _observe_and_notify(
    session: AsyncSession,
    cards: list[ReviewCard],
    tab_map: dict[CardIdentityKey, CardTabInfo] | None = None,
) -> list[str]:
    """Persist + notify one card at a time; return failure descriptions, if any.

    See the module docstring ("Commit granularity") for why this iterates
    per card with its own commit/rollback rather than sharing one commit
    across the whole tick. ``tab_map`` (CCA13) is threaded straight into
    ``record_observation`` -- see that function's own docstring for what a
    card missing from it means.
    """
    notify_failures: list[str] = []
    for card in cards:
        card_transitions = await record_observation(session, [card], tab_map)
        if not card_transitions:
            await session.commit()
            continue

        card_failed = False
        for transition in card_transitions:
            try:
                posted = await post_transition_card(session, transition)
            except Exception as exc:
                card_failed = True
                notify_failures.append(f"{transition.card.identity_key} {transition.route}: {exc}")
                logger.exception(
                    "crisis_content: Slack post failed for card=%r route=%s -- mark_notified "
                    "NOT called, next poll will retry",
                    transition.card.identity_key,
                    transition.route,
                )
                continue

            # `getattr` with a `None` default rather than assuming `posted` is a
            # `PostedCardMessage` -- a monkeypatched fake in the poller's own
            # test suite (pre-CCA9) returns `None`, and a missing channel_id/
            # message_ts must degrade gracefully (nullable columns), not crash
            # the tick that would otherwise have marked a successful post.
            card_id = await _resolve_card_id(session, transition.card)
            await mark_notified(
                session,
                card_id,
                transition.route,
                transition.new_status,
                copy_hash=transition.card.copy_hash,
                channel_id=getattr(posted, "channel_id", None),
                message_ts=getattr(posted, "message_ts", None),
            )

        if card_failed:
            await session.rollback()
        else:
            await session.commit()

    return notify_failures
