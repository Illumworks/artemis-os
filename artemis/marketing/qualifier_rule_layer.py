"""Qualifier Rule Layer — M4 (Josh §4: hard skip / suppress / boost).

Pure functions only — no DB, no LLM.  Context is pre-fetched by the orchestrator.
Layer order: hard_skips → suppress → boost.
Hard-skipped signals never reach suppress/boost; suppressed are not boosted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_TIERS: list[str] = ["enrichment", "standard", "hot"]


def _idx(t: str) -> int:
    try:
        return _TIERS.index(t)
    except ValueError:
        return 1


def _down(t: str) -> str:
    return _TIERS[max(0, _idx(t) - 1)]


def _up(t: str) -> str:
    return _TIERS[min(2, _idx(t) + 1)]


@dataclass
class SkipDecision:
    applied: bool
    rule_id: str | None = None
    reason: str | None = None
    new_priority: str | None = None  # noqa: E702


@dataclass
class SuppressDecision:
    applied: bool
    rule_id: str | None = None
    reason: str | None = None
    new_priority: str | None = None  # noqa: E702


@dataclass
class BoostDecision:
    applied: bool
    rule_id: str | None = None
    reason: str | None = None
    new_priority: str | None = None  # noqa: E702


S = dict[str, Any]
C = dict[str, Any]
Pred = Callable[[S, C], bool]


@dataclass
class HardSkipRule:
    id: str
    description: str
    predicate: Pred
    skip_reason: str
    action: str = "skip"  # noqa: E702


@dataclass
class SuppressRule:
    id: str
    description: str
    predicate: Pred
    suppress_reason: str  # noqa: E702
    new_status: str | None = None
    force_priority: str | None = None
    action: str = "suppress"  # noqa: E702


@dataclass
class BoostRule:
    id: str
    description: str
    predicate: Pred
    boost_reason: str  # noqa: E702
    force_priority: str | None = None
    action: str = "boost"  # noqa: E702


# ── Predicate helpers ─────────────────────────────────────────────────────────


def _codes(sig: S) -> set[str]:
    return {
        r["code"] for r in (sig.get("reason_codes") or []) if isinstance(r, dict) and r.get("code")
    }


def _prior_recent(ctx: C, days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for ps in ctx.get("prior_signals") or []:
        raw = ps.get("created_at")
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except ValueError:
                continue
        elif isinstance(raw, datetime):
            dt = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
        else:
            continue
        if dt >= cutoff:
            out.append(ps)
    return out


def _prior_codes(ctx: C, days: int) -> set[str]:
    return {
        r["code"]
        for ps in _prior_recent(ctx, days)
        for r in (ps.get("reason_codes") or [])
        if isinstance(r, dict) and r.get("code")
    }


# ── §4.1 Hard-skip rules ──────────────────────────────────────────────────────

HARD_SKIP_RULES: list[HardSkipRule] = [
    HardSkipRule(
        "skip_hmh_partner",
        "HMH partner or HMH Into Reading core ELA",
        lambda s, c: bool(c.get("is_hmh_partner")) or bool(c.get("board_adoption_hmh")),
        "hmh_partner_channel_conflict",
    ),
    HardSkipRule(
        "skip_single_school",
        "Signal geography scope is school not district",
        lambda s, c: str((s.get("geography") or {}).get("scope", "")).lower() == "school",
        "single_school_below_motion",
    ),
    HardSkipRule(
        "skip_below_enrollment",
        "District enrollment < 5000",
        lambda s, c: (e := c.get("district_enrollment")) is not None and int(e) < 5000,
        "district_below_enrollment_threshold",
    ),
]

# ── §4.2 Suppress rules ───────────────────────────────────────────────────────


def _p_stale(sig: S, ctx: C) -> bool:
    if ctx.get("material_change_check_passed", True):
        return False
    did, sc = sig.get("district_id"), _codes(sig)
    for ps in _prior_recent(ctx, 30):
        if ps.get("district_id") != did:
            continue
        if sc & {
            r["code"]
            for r in (ps.get("reason_codes") or [])
            if isinstance(r, dict) and r.get("code")
        }:
            return True
    return False


def _p_speculation(sig: S, ctx: C) -> bool:
    """PROCUREMENT_ELA_ADOPTION without RFP (spec BOARD_OBC_DISCUSSION → substituted; not in Josh's 17)."""
    return "PROCUREMENT_ELA_ADOPTION" in _codes(sig) and "PROCUREMENT_LITERACY_RFP" not in (
        _codes(sig) | _prior_codes(ctx, 7)
    )


def _p_single_src_leader(sig: S, ctx: C) -> bool:
    if "LEADER_TRANSITION_FORMAL" not in _codes(sig):
        return False
    if (
        str((sig.get("source") or {}).get("type", sig.get("source_type", ""))).lower()
        != "linkedin_post"
    ):
        return False
    corr = {"board_minutes", "news_article", "state_doe"}
    for ps in _prior_recent(ctx, 7):
        pc = {
            r["code"]
            for r in (ps.get("reason_codes") or [])
            if isinstance(r, dict) and r.get("code")
        }
        if (
            "LEADER_TRANSITION_FORMAL" in pc
            and str((ps.get("source") or {}).get("type", ps.get("source_type", ""))).lower() in corr
        ):
            return False
    return True


SUPPRESS_RULES: list[SuppressRule] = [
    SuppressRule(
        "suppress_stale_signal",
        "Same district+code 30d no material change",
        _p_stale,
        "stale_no_material_change",
        new_status="suppressed_stale",
    ),
    SuppressRule(
        "downgrade_speculation_not_action",
        "PROCUREMENT_ELA_ADOPTION without RFP (BOARD_OBC_DISCUSSION substituted—not in Josh's 17)",
        _p_speculation,
        "procurement_discussion_without_rfp_code_substituted",
        force_priority="standard",
    ),
    SuppressRule(
        "hold_single_source_leader_transition",
        "LEADER_TRANSITION_FORMAL linkedin-only no corroboration 7d",
        _p_single_src_leader,
        "single_source_leader_transition_pending_corroboration",
        new_status="held_pending_corroboration",
        force_priority="enrichment",
    ),
    SuppressRule(
        "downgrade_paywalled_evidence",
        "evidence_quote_partial flag",
        lambda s, c: "evidence_quote_partial" in (s.get("flags") or []),
        "paywalled_source",
    ),
    SuppressRule(
        "suppress_tx_biliteracy_v1",
        "TX DISTRICT_DLL_EXPANSION deprioritized v0.1",
        lambda s, c: (
            str((s.get("geography") or {}).get("state", s.get("state", ""))).upper() == "TX"
            and "DISTRICT_DLL_EXPANSION" in _codes(s)
        ),
        "tx_biliteracy_v01_skip",
        new_status="suppressed_deprioritized",
    ),
]

# ── §4.3 Boost rules ──────────────────────────────────────────────────────────

_TX_CODES = {"TX_HB1416_WAIVER", "TX_HB3_DYSLEXIA_COMPLIANCE"}


BOOST_RULES: list[BoostRule] = [
    BoostRule(
        "boost_stacked_signals",
        "Two distinct reason_codes on same district in 30d",
        lambda s, c: bool(s.get("district_id")) and len(_codes(s) | _prior_codes(c, 30)) >= 2,
        "stacked_signals_two_distinct_codes",
    ),
    BoostRule(
        "boost_leader_plus_curriculum",
        "LEADER_TRANSITION_FORMAL + ELA adoption or strategic literacy",
        lambda s, c: (
            "LEADER_TRANSITION_FORMAL" in (_codes(s) | _prior_codes(c, 90))
            and bool(
                (_codes(s) | _prior_codes(c, 90))
                & {"PROCUREMENT_ELA_ADOPTION", "DISTRICT_STRATEGIC_LITERACY"}
            )
        ),
        "leader_transition_plus_curriculum_signal",
        force_priority="hot",
    ),
    BoostRule(
        "boost_texas_approval_signals",
        "TX_HB1416_WAIVER or TX_HB3_DYSLEXIA_COMPLIANCE",
        lambda s, c: bool(_codes(s) & _TX_CODES),
        "texas_tea_approved_substitution_signal",
        force_priority="hot",
    ),
    BoostRule(
        "urgency_bill_stage",
        "POLICY_LIT_MANDATE at PASSED_CHAMBER or ENACTED",
        lambda s, c: (
            "POLICY_LIT_MANDATE" in _codes(s)
            and str((s.get("metadata") or {}).get("bill_stage", "")).upper()
            in {"PASSED_CHAMBER", "ENACTED"}
        ),
        "policy_lit_mandate_enacted_or_passed",
        force_priority="hot",
    ),
]

# ── Apply functions ───────────────────────────────────────────────────────────


def apply_hard_skips(signal: S, ctx: C) -> SkipDecision:
    """Return first matching hard-skip rule.  Pure: no DB, no LLM."""
    for rule in HARD_SKIP_RULES:
        if rule.predicate(signal, ctx):
            return SkipDecision(applied=True, rule_id=rule.id, reason=rule.skip_reason)
    return SkipDecision(applied=False)


def apply_suppress(signal: S, ctx: C, current_priority: str) -> tuple[SuppressDecision, str]:
    """Apply all matching suppress rules (stacking); return (decision, final_priority)."""
    fired = [r for r in SUPPRESS_RULES if r.predicate(signal, ctx)]
    if not fired:
        return SuppressDecision(applied=False), current_priority
    priority = current_priority
    for rule in fired:
        candidate = rule.force_priority if rule.force_priority else _down(priority)
        if _idx(candidate) < _idx(priority):
            priority = candidate
    last = fired[-1]
    return SuppressDecision(
        applied=True,
        rule_id=last.id,
        reason="|".join(r.suppress_reason for r in fired),
        new_priority=priority,
    ), priority


def apply_boost(signal: S, ctx: C, current_priority: str) -> tuple[BoostDecision, str]:
    """Apply all matching boost rules (stacking); return (decision, final_priority).  Ceiling=hot."""
    fired = [r for r in BOOST_RULES if r.predicate(signal, ctx)]
    if not fired:
        return BoostDecision(applied=False), current_priority
    priority = current_priority
    for rule in fired:
        candidate = rule.force_priority if rule.force_priority else _up(priority)
        if _idx(candidate) > _idx(priority):
            priority = candidate
    last = fired[-1]
    return BoostDecision(
        applied=True,
        rule_id=last.id,
        reason="|".join(r.boost_reason for r in fired),
        new_priority=priority,
    ), priority
