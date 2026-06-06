"""Shared qualification helper — extracted from routes/signal_queue.py.

``run_and_store_qualification`` is the single authoritative path for:
  1. loading active rulesets + territory configs
  2. running the deterministic ``qualify_signal`` scorer
  3. annotating with DIST4 district-tier context
  4. persisting the result via ``save_signal_qualification``
  5. transitioning signal_status from pending_qualification → qualified

It is intentionally session/commit-agnostic: callers own their transaction.

Usage (fire-and-forget, non-fatal):
    try:
        await run_and_store_qualification(session, signal)
        await session.commit()
        await session.refresh(signal)
    except Exception:
        log.warning("qualification failed for signal %s (non-fatal)", signal.id)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import Ruleset, SignalQueue, TerritoryConfig
from artemis.marketing.qualifier import (
    RulesetInput,
    SignalInput,
    TerritoryEntry,
    annotate_district_tier,
    qualify_signal,
)
from artemis.marketing.repository import get_district, save_signal_qualification
from artemis.marketing.state_machine import SignalState, transition

log = logging.getLogger(__name__)


async def run_and_store_qualification(
    session: AsyncSession,
    signal: SignalQueue,
) -> dict[str, Any] | None:
    """Load active rulesets + territory configs, run qualify_signal(), store result.

    Returns the serialized qualification dict (after district annotation), or
    ``None`` if no active rulesets exist (non-fatal — signal stays
    ``pending_qualification`` and no status transition occurs).

    On success, also transitions ``signal.signal_status`` from
    ``pending_qualification`` to ``qualified`` (mirrors the
    ``POST /{id}/qualify`` route behaviour).

    Callers own commit/rollback.  Raises on unexpected DB errors.
    """
    # Load all active rulesets
    result = await session.execute(select(Ruleset).where(Ruleset.state == "active"))
    active_rulesets_rows = list(result.scalars().all())

    if not active_rulesets_rows:
        log.debug(
            "run_and_store_qualification: no active rulesets — signal id=%s stays pending",
            signal.id,
        )
        return None

    # Build RulesetInput list
    ruleset_inputs = [
        RulesetInput(
            campaign_family=row.family,
            version_number=row.version_tag,
            min_fit_score=0.5,  # default; rulesets don't have a min_fit_score column yet
            hard_filters=row.hard_filters or [],
            weighted_signals=row.weighted_signals or [],
        )
        for row in active_rulesets_rows
    ]

    # Load territory configs for the families we're scoring
    families = [r.family for r in active_rulesets_rows]
    tc_result = await session.execute(
        select(TerritoryConfig).where(TerritoryConfig.family.in_(families))
    )
    territory_rows = list(tc_result.scalars().all())

    # Build territories_by_family using JSONB hot_states / standard_states arrays
    territories_by_family: dict[str, list[TerritoryEntry]] = {}
    for tc in territory_rows:
        entries: list[TerritoryEntry] = []
        for state in tc.hot_states or []:
            entries.append(TerritoryEntry(state_code=str(state).upper(), priority_tier="hot"))
        for state in tc.standard_states or []:
            entries.append(TerritoryEntry(state_code=str(state).upper(), priority_tier="standard"))
        territories_by_family[tc.family] = entries

    # Build SignalInput from ORM row
    signal_input = SignalInput(
        state_code=signal.state,
        reason_codes=signal.reason_codes or [],
        campaign_family=signal.campaign_family,
        urgency_tier=signal.urgency_tier,
    )

    qual = qualify_signal(signal_input, ruleset_inputs, territories_by_family)
    qual_dict = qual.to_dict()

    # DIST4: annotate district tier soft-flag (no migration — stored in qualification_json)
    district = None
    if signal.resolved_district_id is not None:
        district = await get_district(session, signal.resolved_district_id)
    qual_dict = annotate_district_tier(
        qual_dict,
        district_id=district.id if district else None,
        district_name=district.name if district else None,
        district_state=district.state if district else None,
        district_tier=district.tier if district else None,
        district_enrollment=district.enrollment if district else None,
        district_supported=district.supported if district else None,
        district_on_skip_list=district.on_skip_list if district else None,
    )

    # Store on signal
    await save_signal_qualification(session, signal.id, qual_dict)

    # Determine whether the signal passes the fit bar in at least one family.
    # A signal "passes" if any FamilyScore has passes_min_fit_score=True.
    passes_fit = any(s.passes_min_fit_score for s in qual.scores)

    current_status = signal.signal_status

    if passes_fit:
        # Advance pending → qualified, or leave qualified alone (already there).
        if current_status == SignalState.pending_qualification:
            await transition(session, "signal", signal.id, SignalState.qualified)
            log.info(
                "run_and_store_qualification: signal id=%s → qualified (families=%s)",
                signal.id,
                [r.family for r in active_rulesets_rows],
            )
        # else: already qualified or in a Gate-1 state — no status change needed.
    else:
        # Fails the fit bar.  Demote qualified → pending_qualification so the signal
        # is withheld from Gate-1, inbox, and campaign promotion.  If it's already
        # pending_qualification (or in a terminal state), leave it alone.
        if current_status == SignalState.qualified:
            await transition(session, "signal", signal.id, SignalState.pending_qualification)
            log.info(
                "run_and_store_qualification: signal id=%s demoted qualified → "
                "pending_qualification (failed fit bar, families=%s)",
                signal.id,
                [r.family for r in active_rulesets_rows],
            )
        elif current_status == SignalState.pending_qualification:
            log.info(
                "run_and_store_qualification: signal id=%s stays pending_qualification "
                "(failed fit bar, families=%s)",
                signal.id,
                [r.family for r in active_rulesets_rows],
            )
        # Signals in terminal Gate-1 states (APPROVED, REJECTED_AT_GATE_1, SNOOZED,
        # ARCHIVED) are left untouched — their qualification_json is updated in place
        # for reference, but their workflow state is not disturbed.

    return qual_dict
