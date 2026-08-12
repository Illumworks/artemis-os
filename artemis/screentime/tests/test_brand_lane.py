"""Brand + entrant lanes in the topic gate (v4).

Why these exist: on 2026-08-12 the Screen-Time Watch held ZERO New Mexico
signals during an active New Mexico crisis in which the state is moving to
remove Amira from schools. The gate was not broken — it was watching policy
vocabulary ("screen time", "AI policy") while the crisis is a brand and
procurement story, and it actively excluded "literacy"/"reading" as ed-policy
noise. The single most important class of item we have was structurally
invisible.

The headlines below are REAL, taken from the live Google News feed for NM on
2026-08-12, not invented. Every one of them was dropped by v3.
"""

import pytest

from artemis.screentime.filters import TOPIC_DROP, TOPIC_KEEP, topic_prescreen
from artemis.screentime.topic_config import DEFAULT_TOPIC_RULES

# v3 = the rules as they stood before the brand/entrant lanes. Used to prove
# these cases were genuinely missed rather than already covered.
_V3_RULES = {
    k: v
    for k, v in DEFAULT_TOPIC_RULES.items()
    if k not in ("brand_any", "entrant_any", "entrant_context_any")
}

# Real headlines from the live NM brand feed, 2026-08-12.
_REAL_NM_HEADLINES = [
    "Meet Amira, an AI reading tutor alarming some parents and school leaders in New Mexico",
    "New Mexico Public Education Department Names Curriculum Associates to Its "
    "High-Quality Professional Learning Marketplace",
    "Renaissance Learning Sued Over Info Collection From K-12 Kids",
    "Students Using Lexia Core5 Reading Outperformed Their Peers On the Smarter "
    "Balanced English Language Arts/Literacy Assessment",
]


@pytest.mark.parametrize("headline", _REAL_NM_HEADLINES)
def test_real_crisis_headlines_now_pass(headline: str) -> None:
    assert topic_prescreen(headline, DEFAULT_TOPIC_RULES) != TOPIC_DROP


@pytest.mark.parametrize("headline", _REAL_NM_HEADLINES)
def test_real_crisis_headlines_were_missed_before(headline: str) -> None:
    """The regression guard: proves the brand lane is load-bearing, not decorative."""
    assert topic_prescreen(headline, _V3_RULES) == TOPIC_DROP


def test_brand_hit_beats_the_literacy_exclusion() -> None:
    """A vendor removal is reported in exactly the words the gate excludes."""
    text = "District ends its contract with Amira Learning literacy software"
    assert topic_prescreen(text, _V3_RULES) == TOPIC_DROP
    assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_KEEP


def test_brand_hit_needs_no_policy_anchor() -> None:
    """Brand short-circuits before require_any is consulted."""
    text = "Santa Fe board votes to pause i-Ready after parent complaints"
    assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_KEEP


@pytest.mark.parametrize(
    "text",
    [
        "Local band will amplify the message at the renaissance fair",
        "Runners kept a brisk pace through multitudes of cheering fans",
        "Imagine learning to play the violin at age fifty",
    ],
)
def test_ambiguous_english_words_do_not_trip_the_brand_lane(text: str) -> None:
    """Vendor names that are ordinary words must be qualified in brand_any.

    Same failure mode the config's own docstring warns about for a bare "ai"
    anchor: the gate does plain substring matching, so "Amplify"/"Renaissance"/
    "Brisk"/"Multitudes" alone would match concerts, fairs and foot races.

    KNOWN ACCEPTED COLLISION: "star reading" and "imagine learning" are real
    Tier-1/2 product names that are also ordinary English phrases. "Imagine
    learning to play..." is caught here because the substring is "imagine
    learning"; the case below documents the limit deliberately rather than
    pretending it does not exist. The feed is school-scoped, so prose collisions
    are rare in practice and the cost is one false keep, not a miss.
    """
    if "imagine learning" in text.lower():
        # Documents the boundary: this DOES match, and that is the accepted
        # trade for catching the real product. Asserting the truth beats
        # asserting the wish.
        assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_KEEP
        return
    assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_DROP


def test_entrant_requires_education_context() -> None:
    """Mark's left-field lane must not admit general AI news."""
    assert topic_prescreen("OpenAI releases a new reasoning model", DEFAULT_TOPIC_RULES) == (
        TOPIC_DROP
    )
    assert (
        topic_prescreen("OpenAI launches ChatGPT for K-12 classroom use", DEFAULT_TOPIC_RULES)
        == TOPIC_KEEP
    )


@pytest.mark.parametrize(
    "text",
    [
        "Texas schools adopt a new screen time policy",
        "State issues AI in schools guidance for districts",
    ],
)
def test_policy_lane_still_works(text: str) -> None:
    """The brand lane is additive — it must not disturb the tuned policy gate."""
    assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_KEEP
    assert topic_prescreen(text, _V3_RULES) == TOPIC_KEEP


@pytest.mark.parametrize(
    "text",
    [
        "Third grade reading retention bill advances in committee",
        "School board approves general appropriations budget bill",
    ],
)
def test_previously_excluded_noise_is_still_excluded(text: str) -> None:
    assert topic_prescreen(text, DEFAULT_TOPIC_RULES) == TOPIC_DROP


def test_brand_query_is_school_scoped_and_disambiguated() -> None:
    from artemis.screentime.national_news import build_state_brand_query

    q = build_state_brand_query("NM")
    assert q.startswith("New Mexico schools (")
    assert '"Amira Learning"' in q
    # Ordinary-word vendors must be qualified in the QUERY too, or the feed
    # returns concerts and fairs.
    assert '"Amplify reading"' in q and '"Amplify"' not in q.replace('"Amplify reading"', "")
    assert '"Renaissance Learning"' in q
