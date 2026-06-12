"""Tests for the p2-okr-opener-digest brief.

Coverage:
1. Opener does NOT contain all active KRs and does NOT contain target-description prose.
2. Seeded recent OKR activity on KR A + B -> digest "in motion" names A/B.
3. A KR past its progress floor with no recent activity surfaces in "slipping";
   a KR with no grounding gets no claim made about it.
4. With NO activity history -> opener still produces a minimal digest via the
   deadline-pressure heuristic (no crash, no dump).
5. Header is date-aware: NOT "Friday" when date is not a Friday.
6. Reconcile path unchanged: a word-dump after the opener still leads to
   update_okr_kr being the right layer-3 tool (opener change didn't break reconcile).
7. build_checkin_digest: in-motion ordering, cap, slipping cap, no fabrication.
8. build_checkin_digest: activity older than 21 days NOT counted as in-motion.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from artemis.proactivity.okr_checkin import (
    build_checkin_digest,
    format_checkin_for_slack,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_kr(
    kr_id: int,
    title: str,
    prog: int = 50,
    archived_at: datetime | None = None,
    target_text: str | None = None,
) -> MagicMock:
    kr = MagicMock()
    kr.id = kr_id
    kr.title = title
    kr.prog = prog
    kr.archived_at = archived_at
    kr.target_text = target_text
    return kr


def _make_obj(obj_id: int, title: str, krs: list[MagicMock]) -> MagicMock:
    obj = MagicMock()
    obj.id = obj_id
    obj.title = title
    obj.key_results = krs
    return obj


def _make_activity(kr_id: int, text: str = "did some work", *, days_ago: int = 1) -> MagicMock:
    act = MagicMock()
    act.kr_id = kr_id
    act.text = text
    act.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    return act


# ── 1. Opener does NOT recite all KRs or target-description prose ─────────────


def test_opener_does_not_recite_all_krs() -> None:
    """With 20 KRs, the opener mentions at most ~5 (well under 20)."""
    krs = [_make_kr(i, f"KR {i}", prog=10 + i) for i in range(1, 21)]
    obj = _make_obj(1, "Big Objective", krs)

    # Provide no activity so all KRs are candidates for slipping heuristic.
    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources, today=date(2026, 6, 12))

    in_motion = digest["in_motion"]
    slipping = digest["slipping"]
    total_mentioned = len(in_motion) + len(slipping)

    assert total_mentioned <= 5, (
        f"Opener should mention at most 5 KRs total; got {total_mentioned} "
        f"(in_motion={len(in_motion)}, slipping={len(slipping)})"
    )


def test_opener_plain_text_no_target_description_prose() -> None:
    """format_checkin_for_slack must NOT include target-description strings in the opener."""
    target_prose = "By end of Q1, we want to achieve 100 champion users engaged monthly"
    kr = _make_kr(1, "Champion Engagement", prog=0, target_text=target_prose)
    obj = _make_obj(1, "Partnerships", [kr])

    text = format_checkin_for_slack(
        [],
        delivery_date=date(2026, 6, 12),
        objectives=[obj],
    )

    # Must NOT contain the long target description.
    assert target_prose not in text, "Opener must not recite target-description prose"
    assert "By end of Q1" not in text, "Target description prose must be absent from opener"


def test_opener_kr_count_capped_in_plain_text() -> None:
    """Plain-text opener mentions no more than 5 KRs total."""
    # 10 KRs all at prog <= 40 so they're candidates for slipping.
    krs = [_make_kr(i, f"KR {i:02}", prog=i * 3) for i in range(1, 11)]
    obj = _make_obj(1, "Objective", krs)

    text = format_checkin_for_slack(
        [],
        delivery_date=date(2026, 6, 12),
        objectives=[obj],
    )

    # Count how many KR-like entries appear. We check by counting "KR XX" patterns.
    kr_mentions = sum(1 for kr in krs if f"KR {kr.title.split()[-1]}" in text or kr.title in text)
    assert kr_mentions <= 5, f"Opener plain-text must cap KR mentions at <=5; found {kr_mentions}"


# ── 2. Recent activity → in-motion names those KRs ───────────────────────────


def test_digest_in_motion_names_active_krs() -> None:
    """KRs A and B with recent activity appear in the digest's in_motion."""
    kr_a = _make_kr(1, "Founding Members", prog=10)
    kr_b = _make_kr(2, "Self-Service Portal", prog=30)
    kr_c = _make_kr(3, "Unrelated KR", prog=80)
    obj = _make_obj(1, "Growth", [kr_a, kr_b, kr_c])

    act_a = _make_activity(1, "Sent outreach to 3 founding members", days_ago=2)
    act_b = _make_activity(2, "Launched self-service demo", days_ago=5)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [act_a, act_b],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources, today=date(2026, 6, 12))

    in_motion_ids = {e["kr_id"] for e in digest["in_motion"]}
    assert 1 in in_motion_ids, "KR A (id=1) with recent activity must be in_motion"
    assert 2 in in_motion_ids, "KR B (id=2) with recent activity must be in_motion"
    assert 3 not in in_motion_ids, "KR C (id=3) with no activity must NOT be in_motion"


def test_digest_in_motion_includes_real_prog() -> None:
    """In-motion entries carry the real current % — nothing fabricated."""
    kr = _make_kr(42, "Champion Engagement", prog=17)
    obj = _make_obj(1, "Partnerships", [kr])
    act = _make_activity(42, "Met with 2 champions", days_ago=3)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [act],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)
    assert digest["in_motion"][0]["prog"] == 17, (
        f"in_motion entry must carry the real KR prog; got {digest['in_motion'][0]['prog']!r}"
    )


# ── 3. Slipping heuristic + no-grounding guard ────────────────────────────────


def test_digest_slipping_surfaces_low_prog_stalled_kr() -> None:
    """A KR with low progress and NO recent activity surfaces in slipping."""
    kr_slip = _make_kr(5, "Annual Report", prog=0)
    kr_ok = _make_kr(6, "Dashboard", prog=75)  # too high for slipping
    obj = _make_obj(1, "Ops", [kr_slip, kr_ok])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],  # no activity — heuristic-only mode
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)

    slipping_ids = {e["kr_id"] for e in digest["slipping"]}
    assert 5 in slipping_ids, "Low-progress stalled KR must be in slipping"
    assert 6 not in slipping_ids, "High-progress KR (75%) must not be in slipping"


def test_digest_no_claim_for_ungrounded_kr() -> None:
    """A KR with prog=80 and no activity gets no mention in the digest at all."""
    kr = _make_kr(7, "Invisible KR", prog=80)
    obj = _make_obj(1, "Silent Objective", [kr])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)

    all_kr_ids = {e["kr_id"] for e in digest["in_motion"]} | {
        e["kr_id"] for e in digest["slipping"]
    }
    assert 7 not in all_kr_ids, (
        "A KR with prog=80 and no activity must not appear in the digest at all"
    )


def test_digest_in_motion_kr_excluded_from_slipping() -> None:
    """A KR that is 'in motion' (recent activity) must NOT also appear in slipping,
    even if its progress is low."""
    kr = _make_kr(10, "Active Low KR", prog=5)
    obj = _make_obj(1, "Obj", [kr])
    act = _make_activity(10, "Worked on it", days_ago=1)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [act],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)

    in_motion_ids = {e["kr_id"] for e in digest["in_motion"]}
    slipping_ids = {e["kr_id"] for e in digest["slipping"]}

    assert 10 in in_motion_ids, "Recently-active KR must be in_motion"
    assert 10 not in slipping_ids, (
        "A KR in in_motion must NOT also appear in slipping — it's not stalled"
    )


# ── 4. No activity history → slipping heuristic still works ──────────────────


def test_opener_no_history_fallback_no_crash() -> None:
    """With zero activity history, opener uses slipping heuristic only — no crash."""
    kr = _make_kr(1, "Founding Members", prog=10)
    obj = _make_obj(1, "Growth", [kr])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],  # no history at all
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources, today=date(2026, 6, 12))

    assert digest["in_motion"] == [], "No activity → in_motion must be empty"
    assert len(digest["slipping"]) >= 1, "Low-progress KR must still appear in slipping"

    # Plain-text fallback must not crash and must produce a coherent message.
    text = format_checkin_for_slack(
        [],
        delivery_date=date(2026, 6, 12),
        digest=digest,
    )
    assert text
    assert "Founding Members" in text
    assert "go" in text.lower()


def test_opener_no_history_no_objectives_fallback() -> None:
    """With zero activity and zero objectives, opener still produces a sensible message."""
    sources: dict[str, Any] = {
        "objectives": [],
        "activity": [],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources, today=date(2026, 6, 12))
    text = format_checkin_for_slack([], delivery_date=date(2026, 6, 12), digest=digest)

    assert text
    assert "go" in text.lower()
    # No KR names, no crash.


# ── 5. Date-aware header ──────────────────────────────────────────────────────


def test_header_is_friday_checkin_on_friday() -> None:
    """On a Friday, the header should say 'Friday check-in'."""
    friday = date(2026, 6, 12)
    assert friday.weekday() == 4, "Fixture assumption: June 12 2026 is a Friday"

    text = format_checkin_for_slack([], delivery_date=friday)
    assert "Friday" in text
    assert "Friday check-in" in text or "Friday check" in text.lower()


def test_header_is_not_friday_on_non_friday() -> None:
    """On a non-Friday date, the header must NOT say 'Friday check-in'."""
    thursday = date(2026, 6, 11)
    assert thursday.weekday() == 3, "Fixture assumption: June 11 2026 is a Thursday"

    text = format_checkin_for_slack([], delivery_date=thursday)
    # Must NOT start with a "Friday" header.
    first_line = text.split("\n")[0]
    assert "Friday" not in first_line, (
        f"Header on a Thursday must not say 'Friday'; got: {first_line!r}"
    )
    # Must still be a recognisable check-in header.
    assert "check-in" in first_line.lower() or "OKR" in first_line


def test_header_non_friday_contains_actual_day() -> None:
    """On a Wednesday the header includes 'Wednesday' (or is an 'OKR check-in')."""
    wednesday = date(2026, 6, 10)
    assert wednesday.weekday() == 2

    text = format_checkin_for_slack([], delivery_date=wednesday)
    first_line = text.split("\n")[0]
    # Must not say Friday.
    assert "Friday" not in first_line
    # Must mention the correct day or at least be an 'OKR check-in'.
    assert "Wednesday" in first_line or "OKR check-in" in first_line


# ── 6. Reconcile path unchanged ───────────────────────────────────────────────


def test_update_okr_kr_still_layer3_after_opener_change() -> None:
    """The opener change must not affect the layer-3 gate on update_okr_kr."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.floating_artemis.tools.okr import register_okr_tools

    registry = AuthorizedToolRegistry()
    register_okr_tools(registry)

    assert "update_okr_kr" in registry
    entry = registry.get("update_okr_kr")
    assert entry is not None
    assert entry.layer == 3, f"update_okr_kr must still be layer 3; got {entry.layer}"
    assert not registry.is_auto_invoke("update_okr_kr")
    assert registry.requires_confirmation("update_okr_kr")


# ── 7. build_checkin_digest: in-motion ordering and cap ──────────────────────


def test_digest_in_motion_ordered_by_most_recent() -> None:
    """In-motion entries are ordered most-recent first."""
    kr_a = _make_kr(1, "KR Alpha", prog=50)
    kr_b = _make_kr(2, "KR Beta", prog=60)
    kr_c = _make_kr(3, "KR Gamma", prog=70)
    obj = _make_obj(1, "Obj", [kr_a, kr_b, kr_c])

    # Gamma most recent, then Alpha, then Beta.
    act_a = _make_activity(1, "worked on alpha", days_ago=10)
    act_b = _make_activity(2, "worked on beta", days_ago=15)
    act_c = _make_activity(3, "worked on gamma", days_ago=2)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [act_a, act_b, act_c],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)
    in_motion = digest["in_motion"]

    # All 3 should appear (under the cap of 3).
    assert len(in_motion) == 3
    ids_in_order = [e["kr_id"] for e in in_motion]
    assert ids_in_order[0] == 3, "Most-recent KR (Gamma, 2 days) should be first"
    assert ids_in_order[1] == 1, "Second-most-recent (Alpha, 10 days) should be second"
    assert ids_in_order[2] == 2, "Oldest (Beta, 15 days) should be last"


def test_digest_in_motion_capped_at_three() -> None:
    """In-motion is capped at 3 even when 4+ KRs have recent activity."""
    krs = [_make_kr(i, f"KR{i}", prog=50) for i in range(1, 6)]
    obj = _make_obj(1, "Obj", krs)
    activities = [_make_activity(i, "active", days_ago=i) for i in range(1, 6)]

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": activities,
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)
    assert len(digest["in_motion"]) == 3, "in_motion must be capped at 3"


def test_digest_slipping_capped_at_two() -> None:
    """Slipping bucket is capped at 2 entries."""
    krs = [_make_kr(i, f"SlipKR{i}", prog=5 * i) for i in range(1, 6)]
    obj = _make_obj(1, "Obj", krs)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)
    assert len(digest["slipping"]) <= 2, "slipping must be capped at 2"


# ── 8. Stale activity (>21 days) NOT counted as in-motion ────────────────────


def test_digest_old_activity_not_in_motion() -> None:
    """Activity older than 21 days must NOT put a KR into in_motion."""
    kr = _make_kr(1, "Old Worker", prog=30)
    obj = _make_obj(1, "Obj", [kr])

    # Activity 25 days ago — outside the 21-day window.
    old_act = _make_activity(1, "ancient work", days_ago=25)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [old_act],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)

    in_motion_ids = {e["kr_id"] for e in digest["in_motion"]}
    assert 1 not in in_motion_ids, (
        "Activity older than 21 days must NOT count as in_motion; "
        "the KR should fall to the slipping heuristic instead"
    )
    # With prog=30 (<= 40 threshold), it should now appear in slipping.
    slipping_ids = {e["kr_id"] for e in digest["slipping"]}
    assert 1 in slipping_ids, (
        "After old activity is excluded, low-prog KR should appear in slipping"
    )


def test_digest_recent_and_old_activity_uses_recent() -> None:
    """If a KR has both old and recent activity, the recent one puts it in in_motion."""
    kr = _make_kr(1, "Active KR", prog=40)
    obj = _make_obj(1, "Obj", [kr])

    old_act = _make_activity(1, "ancient work", days_ago=30)
    recent_act = _make_activity(1, "fresh work", days_ago=5)

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [old_act, recent_act],
        "jira_done": [],
        "action_items": [],
    }
    digest = build_checkin_digest(sources)

    in_motion_ids = {e["kr_id"] for e in digest["in_motion"]}
    assert 1 in in_motion_ids, (
        "KR with recent activity (5 days) must be in_motion despite also having old activity"
    )
