"""Stance + Amira-angle classification — config-driven, tool-less, cheap provider.

This is bulk text work, so it runs on **codex → claude-code fallback** via
``complete_with_fallback`` — NEVER Opus, never a national Opus sweep.

Two layers:
  1. ``classify_by_rules`` — a PURE, deterministic keyword classifier driven by
     the tunable stance rules. It is the ground truth for "tunable" (changing
     the rules flips the stance) and the failure-safe fallback when no model is
     reachable.
  2. ``classify_signal`` — calls the cheap LLM for a one-line Amira angle (and a
     stance suggestion) and reconciles it with the rule result. The rule result
     wins on stance so classification stays config-driven and reproducible; the
     LLM contributes the human-readable Amira angle.

Provider footgun (per brief): ``model=None`` goes INSIDE the CompletionRequest;
never pass ``model=`` as a kwarg to complete_with_fallback. All provider/LLM
imports are lazy (inside functions) to keep app boot circular-import-safe.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from artemis.screentime.filters import CandidateSignal, is_screentime_relevant
from artemis.screentime.models import (
    STANCE_FAVORABLE,
    STANCE_NEUTRAL,
    STANCE_UNFAVORABLE,
)

_logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a policy analyst for Amira Learning, an evidence-based, purpose-built "
    "K-12 literacy tool. You classify screen-time legislation/policy by its impact "
    "on tools like Amira. Reply with ONLY compact JSON: "
    '{"stance":"favorable|unfavorable|neutral","amira_angle":"<one sentence: does '
    'it restrict or carve out evidence-based tools like Amira?>"}. '
    "favorable = a screen-time restriction WITH a carve-out/exemption for "
    "evidence-based or purpose-built instructional tools. "
    "unfavorable = a blanket screen-time restriction with NO carve-out. "
    "neutral = nothing relevant, unknown, or out of scope. No prose, JSON only."
)


@dataclass(slots=True)
class Classification:
    stance: str
    amira_angle: str
    served_by: str | None = None  # provider that produced the angle, or None (rules-only)


# Words that, immediately before a carve-out keyword, negate it — so
# "no exceptions" / "without exemption" is NOT a favorable carve-out.
_NEGATORS: tuple[str, ...] = ("no", "not", "without", "zero", "any", "n't")


def _keyword_present_unnegated(lower_text: str, keyword: str) -> bool:
    """True if *keyword* appears in *lower_text* not immediately preceded by a negator."""
    idx = lower_text.find(keyword)
    while idx != -1:
        prefix = lower_text[:idx].rstrip()
        last_word = prefix.rsplit(" ", 1)[-1] if prefix else ""
        # Strip trailing punctuation from the preceding word.
        last_word = last_word.strip(".,;:!?()[]\"'")
        if last_word not in _NEGATORS:
            return True
        idx = lower_text.find(keyword, idx + 1)
    return False


def classify_by_rules(
    text: str,
    rules: dict[str, Any],
    *,
    topic_rules: dict[str, Any] | None = None,
) -> str:
    """Deterministic stance from the tunable rules. PURE, no I/O.

    favorable = a restriction (blanket | restrictive-action | anchor) present AND
                a favorable carve-out keyword present.
    unfavorable = a blanket keyword OR an explicit restrictive-ACTION keyword
                present (and no carve-out). A broad anchor alone (e.g. a "Standards
                Act" that merely mentions screen time, a study) is NOT unfavorable.
    neutral = anchor-only / standards-framework / study, otherwise, or out-of-lane
                (cellphone ban / not screen-time).

    Hardening (Brief 4): a carve-out / "exempt(ion)" signal counts toward
    FAVORABLE only when the item is screen-time-TOPIC-relevant. Post-gate every
    item reaching the classifier is already topical, so this is belt-and-
    suspenders — it stops a stray reading-retention "exemption" from ever reading
    🟢 favorable even if the classifier is called directly (off the runner path).
    When *topic_rules* is omitted the check is skipped (callers that pre-gate).
    The existing negation-awareness ("no exceptions") is preserved.
    """
    lower = text.lower()
    if not is_screentime_relevant(text, rules):
        return STANCE_NEUTRAL

    favorable = [k.lower() for k in rules.get("favorable_keywords", [])]
    unfavorable = [k.lower() for k in rules.get("unfavorable_keywords", [])]
    action = [k.lower() for k in rules.get("restriction_action_keywords", [])]
    restriction = [k.lower() for k in rules.get("restriction_keywords", [])]

    has_carveout = any(_keyword_present_unnegated(lower, k) for k in favorable)
    has_blanket = any(k in lower for k in unfavorable)
    has_action = any(k in lower for k in action)
    # "Is this unfavorable on its own?" — a blanket policy OR an explicit
    # restrictive action on screen/device/instructional time.
    has_unfavorable_intent = has_blanket or has_action
    # "Is this a restriction at all?" — the above, OR just a broad anchor present.
    has_restriction = has_unfavorable_intent or any(k in lower for k in restriction)

    # A carve-out only earns 🟢 favorable on a genuinely screen-time-topical item.
    # If topic_rules are supplied and the item is NOT topic-relevant, neutralize
    # the carve-out so an off-topic "exemption" can't flip the stance favorable.
    if has_carveout and topic_rules is not None:
        from artemis.screentime.filters import passes_topic_gate

        if not passes_topic_gate(text, topic_rules):
            has_carveout = False

    if has_restriction and has_carveout:
        return STANCE_FAVORABLE
    if has_unfavorable_intent and not has_carveout:
        return STANCE_UNFAVORABLE
    # Anchor-only (a "Standards Act", a study, a framework with no restrictive
    # action and no carve-out) → neutral. Genuinely no clear direction.
    return STANCE_NEUTRAL


def _rule_angle(stance: str, candidate: CandidateSignal) -> str:
    """Deterministic Amira-angle text used when no model is reachable."""
    if stance == STANCE_FAVORABLE:
        return (
            f"{candidate.state}: restriction appears to carve out evidence-based / "
            "purpose-built tools — likely protects Amira's footprint."
        )
    if stance == STANCE_UNFAVORABLE:
        return (
            f"{candidate.state}: blanket screen-time restriction with no visible "
            "carve-out — could limit instructional tools like Amira."
        )
    return f"{candidate.state}: no clear screen-time impact on evidence-based tools yet."


async def classify_signal(
    candidate: CandidateSignal,
    rules: dict[str, Any],
    *,
    session: Any | None = None,
    topic_rules: dict[str, Any] | None = None,
) -> Classification:
    """Classify one candidate: rule-driven stance + LLM Amira angle (cheap provider).

    Never raises — on any provider failure it returns the deterministic
    rule-based classification so a sweep always completes (failure-safe).

    *topic_rules*, when supplied, hardens the carve-out → favorable path so it
    only fires on a screen-time-topical item (belt-and-suspenders post-gate).
    """
    rule_stance = classify_by_rules(candidate.text, rules, topic_rules=topic_rules)

    # Lazy provider imports — keep them out of module top (circular-import / boot safe).
    try:
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback
    except Exception:  # pragma: no cover - import guard
        _logger.warning("screentime.classify: provider import failed; using rules only", exc_info=True)
        return Classification(rule_stance, _rule_angle(rule_stance, candidate), served_by=None)

    prompt = (
        f"State: {candidate.state}\n"
        f"Level: {candidate.level}\n"
        f"Status: {candidate.status}\n"
        f"Title: {candidate.title}\n"
        f"Summary: {candidate.summary}\n"
        f"Rule-based stance hint: {rule_stance}\n"
        "Return the JSON classification."
    )
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        system=_SYSTEM,
        model=None,  # MUST be inside the request; codex/claude-code pick their own default
        max_tokens=200,
        cache_system=False,
        cache_tools=False,
    )

    served: list[str] = []
    try:
        resp = await complete_with_fallback(
            req,
            primary="codex",
            fallback="claude-code",
            session=session,
            feature_tag="screentime_classify",
            serving_provider_out=served,
        )
    except Exception:
        _logger.warning("screentime.classify: provider call failed; using rules only", exc_info=True)
        return Classification(rule_stance, _rule_angle(rule_stance, candidate), served_by=None)

    answer = ""
    for block in resp.message.content:
        if hasattr(block, "text"):
            answer = block.text.strip()
            break

    angle = _rule_angle(rule_stance, candidate)
    parsed = _parse_json(answer)
    if parsed and isinstance(parsed.get("amira_angle"), str) and parsed["amira_angle"].strip():
        angle = parsed["amira_angle"].strip()

    # Stance stays config-driven (the rule result wins) so it's reproducible and
    # tunable. The LLM only enriches the human-readable angle.
    return Classification(
        stance=rule_stance,
        amira_angle=angle,
        served_by=served[0] if served else None,
    )


def _parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort extract a JSON object from a model reply."""
    text = text.strip()
    if not text:
        return None
    # Strip code fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
