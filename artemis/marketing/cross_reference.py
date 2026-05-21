"""Cross-Reference Agent — M4 integration entry points.

Call order (per brief §5):
  Pre-Phase-1:  run_hard_skips        (cheap kill before any LLM cost)
  Post-Phase-3: run_suppress_and_boost (suppress, then boost if not suppressed)

TODO(M3): Replace direct status writes with transition() once M3 is merged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import QualifierRuleApplication, SignalQueue, SkippedSignal
from artemis.marketing.qualifier_rule_layer import (
    BOOST_RULES,
    SUPPRESS_RULES,
    apply_boost,
    apply_hard_skips,
    apply_suppress,
)

log = logging.getLogger(__name__)
_T = ["enrichment", "standard", "hot"]


def _ti(t: str) -> int:
    try:
        return _T.index(t)
    except ValueError:
        return 1


async def build_rule_context(signal: SignalQueue, session: AsyncSession) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(SignalQueue)
                .where(SignalQueue.district_id == signal.district_id, SignalQueue.id != signal.id)
                .order_by(SignalQueue.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    prov = signal.provenance or {}
    sf = prov.get("salesforce_account") or {}
    return {
        "is_hmh_partner": bool(sf.get("is_hmh_partner")),
        "board_adoption_hmh": bool(prov.get("board_adoption_hmh")),
        "district_enrollment": prov.get("district_enrollment"),
        "material_change_check_passed": bool(prov.get("material_change_check_passed", True)),
        "prior_signals": [
            {
                "district_id": p.district_id,
                "reason_codes": p.reason_codes or [],
                "source_type": p.source_type,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in rows
        ],
    }


def _as_dict(s: SignalQueue) -> dict[str, Any]:
    prov = s.provenance or {}
    return {
        "district_id": s.district_id,
        "reason_codes": s.reason_codes or [],
        "geography": {"state": s.state, "scope": prov.get("geography_scope", "district")},
        "source": {"type": s.source_type},
        "source_type": s.source_type,
        "state": s.state,
        "flags": prov.get("flags") or [],
        "metadata": prov.get("signal_metadata") or {},
    }


async def run_hard_skips(signal: SignalQueue, ctx: dict[str, Any], session: AsyncSession) -> bool:
    """Pre-Phase-1 hard-skip gate.  Returns True if signal was killed."""
    dec = apply_hard_skips(_as_dict(signal), ctx)
    if not dec.applied or not dec.rule_id:
        return False
    fp = signal.urgency_tier or "standard"
    signal.signal_status = "rejected_hard_skip"  # TODO(M3): transition()
    signal.rejected_reason = dec.reason
    signal.updated_at = datetime.now(UTC)
    session.add(signal)
    session.add(
        SkippedSignal(
            signal_id=signal.id,
            district_id=signal.district_id,
            rule_id=dec.rule_id,
            reason=dec.reason,
        )
    )
    session.add(
        QualifierRuleApplication(
            signal_id=signal.id,
            rule_id=dec.rule_id,
            layer="skip",
            from_priority=fp,
            reason=dec.reason,
        )
    )
    await session.flush()
    log.info("signal %s hard-skipped rule=%s", signal.id, dec.rule_id)
    return True


async def run_suppress_and_boost(
    signal: SignalQueue, ctx: dict[str, Any], session: AsyncSession
) -> str:
    """Post-Phase-3 suppress then boost.  Returns final urgency_tier."""
    sd = _as_dict(signal)
    fp = signal.urgency_tier or "standard"
    sup_dec, after_sup = apply_suppress(sd, ctx, fp)
    if sup_dec.applied:
        cur = fp
        for r in [x for x in SUPPRESS_RULES if x.predicate(sd, ctx)]:
            nxt = r.force_priority or _T[max(0, _ti(cur) - 1)]
            if _ti(nxt) < _ti(cur) or r.new_status:
                session.add(
                    QualifierRuleApplication(
                        signal_id=signal.id,
                        rule_id=r.id,
                        layer="suppress",
                        from_priority=cur,
                        to_priority=nxt,
                        reason=r.suppress_reason,
                    )
                )
                cur = nxt
        ns = next(
            (
                r.new_status
                for r in reversed([x for x in SUPPRESS_RULES if x.predicate(sd, ctx)])
                if r.new_status
            ),
            None,
        )
        if ns:
            signal.signal_status = ns  # TODO(M3): transition()
        signal.urgency_tier = after_sup
        signal.updated_at = datetime.now(UTC)
        session.add(signal)
        await session.flush()
        return after_sup
    bst_dec, after_bst = apply_boost(sd, ctx, fp)
    if bst_dec.applied:
        cur = fp
        for br in [x for x in BOOST_RULES if x.predicate(sd, ctx)]:
            nxt = br.force_priority or _T[min(2, _ti(cur) + 1)]
            if _ti(nxt) > _ti(cur):
                session.add(
                    QualifierRuleApplication(
                        signal_id=signal.id,
                        rule_id=br.id,
                        layer="boost",
                        from_priority=cur,
                        to_priority=nxt,
                        reason=br.boost_reason,
                    )
                )
                cur = nxt
        signal.urgency_tier = after_bst
        signal.updated_at = datetime.now(UTC)
        session.add(signal)
        await session.flush()
        return after_bst
    return fp
