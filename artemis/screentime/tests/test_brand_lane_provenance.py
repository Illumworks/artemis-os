"""Stream A — the brand lane must survive on PROVENANCE, not on its own text.

Regression cover for the 2026-08-20 finding: the brand lane was fetching the
right articles and storing none of them. Two independent causes, one test file.

Back-test against the live NM feed on 2026-08-20, before the fix:
    27 items fetched · 16 dropped by the topic gate · 11 dropped by the
    real-move bar · 0 stored — during an active statewide New Mexico crisis.

After: 5 reportable, 22 stored as corpus, 0 dropped.

These tests are deliberately offline — they use the real headlines observed in
that feed as fixtures, so the contract is pinned without a network call.
"""

from __future__ import annotations

import asyncio

from artemis.screentime import filters
from artemis.screentime.national_news import item_to_finding
from artemis.screentime.stance_config import DEFAULT_STANCE_RULES
from artemis.screentime.topic_config import DEFAULT_TOPIC_RULES

# Real headlines from the live NM brand feed, 2026-08-20. NONE of these names a
# vendor, and every one of them was dropped by the pre-fix topic gate.
HEADLINES_WITHOUT_VENDOR_NAME = [
    "Santa Fe Public Schools rejects state-required AI program - KOB 4",
    "Santa Fe, Los Alamos Schools Pause State-Required Reading Software - GovTech",
    "New Mexico Allows Schools to Opt Out of Controversial AI Tool - GovTech",
    "New Mexico officials call for statewide plan to govern AI use in schools",
]


def _candidate(title: str, *, lane: str, summary: str = "") -> filters.CandidateSignal:
    """Build a CandidateSignal the way the real pipeline does, via item_to_finding."""
    item = {"title": title, "summary": summary, "link": f"https://example.test/{hash(title)}"}
    if lane == filters.LANE_BRAND:
        item["lane"] = filters.LANE_BRAND
    finding = item_to_finding(item, "NM")
    assert finding is not None
    candidate = filters.normalize_finding(finding)
    assert candidate is not None
    return candidate


class TestLaneProvenanceSurvives:
    """The lane stamp must reach the CandidateSignal, or nothing downstream works."""

    def test_brand_stamp_survives_item_to_finding_and_normalize(self) -> None:
        c = _candidate("Santa Fe Public Schools rejects AI program", lane=filters.LANE_BRAND)
        assert c.lane == filters.LANE_BRAND

    def test_unstamped_items_default_to_policy(self) -> None:
        c = _candidate("Some state screen time bill advances", lane=filters.LANE_POLICY)
        assert c.lane == filters.LANE_POLICY


class TestTopicGateTrustsProvenance:
    """A brand-lane item is never re-tested against its own text."""

    def test_brand_lane_items_pass_even_with_no_vendor_name(self) -> None:
        for title in HEADLINES_WITHOUT_VENDOR_NAME:
            c = _candidate(title, lane=filters.LANE_BRAND)
            # Proof these genuinely fail the text-based gate — the test is
            # meaningless if the pure prescreen would have kept them anyway.
            assert not filters.passes_topic_gate(c.text, DEFAULT_TOPIC_RULES), (
                f"fixture no longer exercises the bug: {title!r} now passes on text alone"
            )
            assert asyncio.run(filters.passes_topic_gate_async(c, DEFAULT_TOPIC_RULES)) is True

    def test_policy_lane_is_unchanged_by_the_fix(self) -> None:
        """The same headlines, unstamped, must still be dropped. No gate loosening."""
        for title in HEADLINES_WITHOUT_VENDOR_NAME:
            c = _candidate(title, lane=filters.LANE_POLICY)
            assert asyncio.run(filters.passes_topic_gate_async(c, DEFAULT_TOPIC_RULES)) is False


class TestBrandRealMoveBar:
    """The brand lane needs its own reportable bar; the policy bar rejects everything."""

    def test_district_actions_are_reportable(self) -> None:
        for title in [
            "Santa Fe Public Schools rejects state-required AI program",
            "Santa Fe, Los Alamos Schools Pause State-Required Reading Software",
            "New Mexico Allows Schools to Opt Out of Controversial AI Tool",
            "LCPS disables retention of student voice recordings",
        ]:
            c = _candidate(title, lane=filters.LANE_BRAND)
            assert filters.is_real_move(c, DEFAULT_STANCE_RULES) is True, title

    def test_live_controversy_is_reportable_without_any_action(self) -> None:
        c = _candidate(
            "Meet Amira, an AI reading tutor alarming some parents and school leaders",
            lane=filters.LANE_BRAND,
        )
        assert filters.is_real_move(c, DEFAULT_STANCE_RULES) is True

    def test_opinion_pieces_are_reportable_in_the_brand_lane(self) -> None:
        """The op-ed that started the NM story WAS the event. The policy lane's
        opinion veto must not apply here."""
        c = _candidate(
            "OPINION: Get unvetted AI out of New Mexico schools — privacy concerns",
            lane=filters.LANE_BRAND,
        )
        assert filters.is_real_move(c, DEFAULT_STANCE_RULES) is True

    def test_vendor_pr_is_captured_but_not_reportable(self) -> None:
        """Corpus, not crisis. These must be stored and must NOT enter the read."""
        for title in [
            "Texas Education Agency Renews Amira Learning as Trusted Reading Assessment",
            "Curriculum Associates Introduces i-Ready Inform: Continuing Excellence",
            "Brisk Teaching Raises $6.9M in Funding for AI Education Tool",
        ]:
            c = _candidate(title, lane=filters.LANE_BRAND)
            assert filters.is_real_move(c, DEFAULT_STANCE_RULES) is False, title

    def test_policy_lane_real_move_semantics_unchanged(self) -> None:
        """A bare policy news item is still not a real move; a passed bill still is."""
        news = _candidate("Schools debate screen time in the classroom", lane=filters.LANE_POLICY)
        assert filters.is_real_move(news, DEFAULT_STANCE_RULES) is False

        passed = _candidate(
            "Legislature passed HB 123 limiting student screen time in schools",
            lane=filters.LANE_POLICY,
        )
        assert filters.is_real_move(passed, DEFAULT_STANCE_RULES) is True


class TestBrandLaneWinsDedup:
    """A URL in both lanes must keep the brand stamp — brand is more permissive."""

    def test_brand_is_fetched_first_so_it_wins_the_link_dedup(self) -> None:
        import inspect

        from artemis.screentime import national_news

        src = inspect.getsource(national_news.gather_state_news)
        brand_at = src.index("fetch_state_brand_items")
        policy_at = src.index("fetch_state_news_items")
        assert brand_at < policy_at, (
            "brand lane must be extended BEFORE the policy lane: the link dedup in "
            "gather_state_news keeps the first occurrence, so fetching policy first "
            "would silently demote a crisis item that also matched the policy query"
        )
