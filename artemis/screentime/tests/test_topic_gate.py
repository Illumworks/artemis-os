"""Unit tests for the screen-time TOPIC-relevance gate (pure, no DB).

The core data-quality fix: the gate must DROP the exact off-topic noise the
first live run stored (reading-retention, literacy mandates, curriculum
approvals, test scores) and KEEP genuine instructional/student screen-time items
(a screen-time limit; an evidence-based-tool exemption to a screen-time rule).
It also fixes the "exempt" false-favorable at the source — an off-topic
reading-retention exemption is dropped before the classifier ever sees it.
"""

from __future__ import annotations

import copy

import pytest

from artemis.screentime.classifier import classify_by_rules
from artemis.screentime.filters import (
    TOPIC_AMBIGUOUS,
    TOPIC_DROP,
    TOPIC_KEEP,
    CandidateSignal,
    passes_topic_gate,
    passes_topic_gate_async,
    topic_prescreen,
)
from artemis.screentime.models import STANCE_FAVORABLE
from artemis.screentime.stance_config import DEFAULT_STANCE_RULES
from artemis.screentime.topic_config import DEFAULT_TOPIC_RULES

TOPIC = DEFAULT_TOPIC_RULES
STANCE = DEFAULT_STANCE_RULES


def _cand(title: str, summary: str = "") -> CandidateSignal:
    return CandidateSignal(
        state="TN",
        title=title,
        summary=summary,
        source_type="legislative",
        source_url="http://x",
        status="proposed",
    )


# --- DROPS the exact noise we saw -------------------------------------------

def test_reading_retention_dropped():
    c = _cand(
        "HB 1: third grade reading retention",
        "Requires retention of students not proficient in reading; exempts certain pupils.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_literacy_mandate_dropped():
    c = _cand(
        "Science of reading literacy mandate",
        "Districts must adopt evidence-based literacy and phonics curriculum.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_curriculum_approval_dropped():
    c = _cand(
        "State board curriculum approval",
        "Approved curriculum list updated; instructional materials adoption cycle.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_test_scores_item_dropped():
    c = _cand(
        "Standardized test scores release",
        "Assessment scores show literacy gains; graduation requirement unchanged.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


# --- DROPS the screenings / budget-study noise (v2 tightening) --------------

def test_behavioral_health_screening_dropped():
    c = _cand(
        "Pediatric Behavioral Health Screenings",
        "Establishes mental health screening program for students.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_screening_with_incidental_screen_anchor_not_clean_keep():
    """A health 'screening' item that incidentally name-drops a screen-time anchor
    is no longer a clean keep — the v2 exclude makes it ambiguous (flaggable)."""
    c = _cand(
        "Pediatric behavioral health screening",
        "Screening program that also limits student screen time during screenings.",
    )
    assert topic_prescreen(c.text, TOPIC) == TOPIC_AMBIGUOUS


def test_budget_study_dropped():
    c = _cand(
        "General Appropriations Act",
        "Budget bill commissions a funding study of school facilities.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_real_bills_still_pass_the_gate():
    """The named real stored bills must all survive the tightened gate."""
    titles = [
        "Screen time prohibited for children in preschool and kindergarten",
        "A bill limiting screen time for prekindergarten through fifth grade",
        "Screen-based instruction limited in kindergarten",
        "Student Screen-Time Standards Act",
        "An act limiting screen time for students, exempting evidence-based "
        "purpose-built instructional tools",
    ]
    for t in titles:
        assert passes_topic_gate(t, TOPIC) is True, t


# --- KEEPS real screen-time items -------------------------------------------

def test_instructional_screen_time_limit_kept():
    c = _cand(
        "Bill to limit instructional screen time",
        "Caps daily screen time for students in K-3 classrooms.",
    )
    assert passes_topic_gate(c.text, TOPIC) is True


def test_evidence_based_tool_exemption_to_screentime_rule_kept():
    c = _cand(
        "Device time rule with carve-out",
        "Limits device time but exempts evidence-based instructional software.",
    )
    assert passes_topic_gate(c.text, TOPIC) is True


# --- prescreen tri-state ----------------------------------------------------

def test_prescreen_keep_drop_ambiguous():
    assert topic_prescreen("limits screen time in classrooms", TOPIC) == TOPIC_KEEP
    assert topic_prescreen("a literacy and phonics mandate", TOPIC) == TOPIC_DROP
    # anchor + excluded theme = mixed signal.
    mixed = "reading retention bill that also caps screen time for retained students"
    assert topic_prescreen(mixed, TOPIC) == TOPIC_AMBIGUOUS


# --- "exempt" no longer favorable: dropped at the gate ----------------------

def test_reading_retention_exempt_dropped_by_gate_not_classified():
    """The first-run false-favorable: a reading-retention exemption.

    Pre-gate it would reach classify_by_rules and (because it carries
    'evidence-based' + 'exemption' + 'limit') read favorable. The gate drops it
    first, and the hardened classifier neutralizes it even if called directly.
    """
    text = (
        "Third grade reading retention bill exempts students using evidence-based "
        "reading programs from the retention limit."
    )
    # 1. Gate drops it (off-topic) — it never reaches store/classify.
    assert passes_topic_gate(text, TOPIC) is False
    # 2. Belt-and-suspenders: classifier with topic_rules will NOT call it favorable.
    assert classify_by_rules(text, STANCE, topic_rules=TOPIC) != STANCE_FAVORABLE


def test_screentime_exempt_still_favorable():
    """A genuine screen-time exemption stays favorable (we did not over-correct)."""
    text = "Bill limits screen time but exempts evidence-based instructional software."
    assert passes_topic_gate(text, TOPIC) is True
    assert classify_by_rules(text, STANCE, topic_rules=TOPIC) == STANCE_FAVORABLE


# --- Tunability: changing the config flips a borderline item ----------------

def test_tunable_require_term_flips_borderline_item():
    text = "Policy on classroom tablet minutes for young students."
    # Default require-set has no "tablet minutes" anchor → dropped.
    assert passes_topic_gate(text, TOPIC) is False
    # Tune: add the anchor → same item now passes the gate (no deploy).
    tuned = copy.deepcopy(DEFAULT_TOPIC_RULES)
    tuned["require_any"] = tuned["require_any"] + ["tablet minutes"]
    assert passes_topic_gate(text, tuned) is True


def test_tunable_exclude_term_flips_item_to_dropped():
    text = "Bill caps screen time in vocational training programs."
    # Default → kept (has the screen-time anchor, no excluded theme).
    assert passes_topic_gate(text, TOPIC) is True
    # Tune: classify "vocational training" as out-of-scope noise. Now mixed →
    # deterministic default keeps it (anchor wins), but prescreen flags ambiguous.
    tuned = copy.deepcopy(DEFAULT_TOPIC_RULES)
    tuned["exclude_any"] = tuned["exclude_any"] + ["vocational training"]
    assert topic_prescreen(text, tuned) == TOPIC_AMBIGUOUS


# --- async gate: LLM tie-break only for ambiguous, off by default -----------

pytestmark_async = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_async_gate_clear_items_never_call_llm(monkeypatch):
    called = {"n": 0}

    async def _boom(*a, **k):  # would be called only on ambiguous + tiebreak
        called["n"] += 1
        raise AssertionError("LLM must not be called for clear keep/drop")

    import artemis.screentime.filters as f

    monkeypatch.setattr(f, "_llm_topic_relevant", _boom, raising=True)

    keep = _cand("limits student screen time", "caps daily screen time")
    drop = _cand("literacy phonics mandate", "science of reading curriculum")
    assert await passes_topic_gate_async(keep, TOPIC) is True
    assert await passes_topic_gate_async(drop, TOPIC) is False
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_async_gate_ambiguous_default_keeps_without_llm(monkeypatch):
    called = {"n": 0}

    async def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("tie-break disabled → no LLM call")

    import artemis.screentime.filters as f

    monkeypatch.setattr(f, "_llm_topic_relevant", _boom, raising=True)

    mixed = _cand(
        "reading retention with screen time cap",
        "retention of students; also caps screen time for them",
    )
    # llm_tiebreak defaults off → anchor wins, kept, no LLM.
    assert await passes_topic_gate_async(mixed, TOPIC, llm_tiebreak=False) is True
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_async_gate_ambiguous_uses_llm_when_enabled(monkeypatch):
    seen = {"n": 0}

    async def _fake_llm(candidate, *, session=None):
        seen["n"] += 1
        return False  # LLM says: not really about screen-time

    import artemis.screentime.filters as f

    monkeypatch.setattr(f, "_llm_topic_relevant", _fake_llm, raising=True)

    mixed = _cand(
        "reading retention with screen time cap",
        "retention of students; also caps screen time for them",
    )
    assert await passes_topic_gate_async(mixed, TOPIC, llm_tiebreak=True) is False
    assert seen["n"] == 1


# --- 2026-07-10 broadening: AI-in-schools policy anchors (v3) ---------------
# The owner decided screen-time and AI-in-schools policy are one "rein in the
# technology" story and should be tracked together (exec report "Board Meetings
# on Screen Time & the Use of AI"). require_any now carries AI-policy anchors
# alongside the screen/device-time anchors; either family alone passes the gate.


def test_ai_in_schools_policy_item_now_passes_gate():
    """An AI-only item (no screen-time anchor at all) now PASSES on the new anchors."""
    c = _cand(
        "State board adopts AI guidance for classrooms",
        "New policy on artificial intelligence use in schools; districts must "
        "publish an AI use policy for student and teacher use of chatgpt tools.",
    )
    assert passes_topic_gate(c.text, TOPIC) is True
    assert topic_prescreen(c.text, TOPIC) == TOPIC_KEEP


def test_ai_moratorium_and_chatgpt_bill_kept():
    c = _cand(
        "Bill proposes AI moratorium in K-12",
        "Prohibits generative ai tools including chatgpt from classroom use pending study.",
    )
    assert passes_topic_gate(c.text, TOPIC) is True


def test_screentime_items_still_pass_after_ai_broadening():
    """Existing screen-time-only findings are unaffected by the v3 widening."""
    c = _cand(
        "Bill to limit instructional screen time",
        "Caps daily screen time for students in K-3 classrooms.",
    )
    assert passes_topic_gate(c.text, TOPIC) is True


def test_generic_noise_still_dropped_after_ai_broadening():
    c = _cand(
        "Science of reading literacy mandate",
        "Districts must adopt evidence-based literacy and phonics curriculum.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False


def test_bare_ai_substring_false_positive_still_excluded():
    """A bare 'ai' anchor would substring-match ordinary words ('email',
    'available') — we deliberately never added a bare 'ai' require-term, so
    text that only contains 'ai' embedded in unrelated words must still DROP."""
    c = _cand(
        "District staff directory update",
        "Send email available for the front office; captain of the maintenance "
        "team is available Monday through Friday.",
    )
    assert passes_topic_gate(c.text, TOPIC) is False
    # Confirm no baked-in anchor is a bare "ai" that would false-positive here.
    assert "ai" not in TOPIC["require_any"]


@pytest.mark.asyncio
async def test_async_gate_llm_unreachable_failsafe_keeps(monkeypatch):
    async def _none(candidate, *, session=None):
        return None  # provider unreachable

    import artemis.screentime.filters as f

    monkeypatch.setattr(f, "_llm_topic_relevant", _none, raising=True)

    mixed = _cand(
        "reading retention with screen time cap",
        "retention of students; also caps screen time for them",
    )
    # Failure-safe: unreachable provider → keep the anchored item.
    assert await passes_topic_gate_async(mixed, TOPIC, llm_tiebreak=True) is True
