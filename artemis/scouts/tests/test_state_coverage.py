"""Every state-iterating layer must agree, by construction.

Regression guard for a silent-coverage bug found 2026-08-21. Three components
each kept their own list of states and had drifted apart:

    screentime.scout_fanout.US_STATES_AND_DC      51 jurisdictions
    screentime.national_news.STATE_NAMES          51 jurisdictions
    scouts.state_doe.sources.STATE_DOE_SOURCES    22 jurisdictions

The State-DoE scout derives its default state list FROM its own map, so the 29
absent states were not under-covered — they were not polled at all, and the run
report showed no error because nothing had asked for them. Oklahoma produced
more screen-time signals than any other state while missing from that map.

These tests are deliberately DB-free: they check the config layers, which are
code, so they hold in CI and on a fresh clone. Coverage of *live* data — which
states actually have signals, and whether territory_config knows about a state
where we support districts — depends on production rows and is reported by
``python -m artemis.ops`` instead.
"""

from __future__ import annotations

from artemis.scouts._states import (
    NON_STATE_JURISDICTIONS,
    STATE_NAMES,
    agency_name,
)
from artemis.scouts.state_doe.sources import STATE_DOE_SOURCES
from artemis.screentime.national_news import STATE_NAMES as NEWS_STATE_NAMES
from artemis.screentime.scout_fanout import US_STATES_AND_DC


def test_canonical_table_is_fifty_states_plus_dc() -> None:
    assert len(STATE_NAMES) == 51, "expected 50 states + DC"
    assert "DC" in STATE_NAMES
    assert not (set(STATE_NAMES) & NON_STATE_JURISDICTIONS), (
        "territories have no state DoE, governor feed or state board in the shape "
        "these scouts fetch — they must stay out of the state table"
    )


def test_every_state_layer_agrees() -> None:
    """The assertion whose absence cost us 29 states of coverage."""
    canonical = set(STATE_NAMES)
    layers = {
        "screentime.scout_fanout.US_STATES_AND_DC": set(US_STATES_AND_DC),
        "screentime.national_news.STATE_NAMES": set(NEWS_STATE_NAMES),
        "scouts.state_doe.sources.STATE_DOE_SOURCES": set(STATE_DOE_SOURCES),
    }
    for name, states in layers.items():
        missing = sorted(canonical - states)
        extra = sorted(states - canonical)
        assert not missing, f"{name} is missing {len(missing)} states: {missing}"
        assert not extra, f"{name} has states not in the canonical table: {extra}"


def test_every_state_has_both_feeds() -> None:
    """A state present but feedless is the same silent gap wearing a disguise."""
    for state in STATE_NAMES:
        cfg = STATE_DOE_SOURCES[state]
        assert cfg.get("doe_rss"), f"{state} has no doe_rss"
        assert cfg.get("governor_rss"), f"{state} has no governor_rss"


def test_topic_vocabulary_is_uniform_across_states() -> None:
    """No state may be hand-tuned to see more than the others.

    The original queries asked only about "literacy reading"; a later pass asked
    about "screen time OR AI policy". The vocabulary that would have caught the
    stories that mattered — reading screener, voice recording, opt out, student
    data — existed for Georgia and New Mexico alone, because someone had gone and
    edited those two entries by hand.
    """
    required = ["reading+screener", "voice+recording", "student+data", "opt+out"]
    for state in STATE_NAMES:
        url = STATE_DOE_SOURCES[state]["doe_rss"] or ""
        normalized = url.replace("%20", "+").replace("%22", "").lower()
        for term in required:
            assert term in normalized, f"{state} doe_rss is missing {term!r}"


def test_agency_name_resolves_for_every_state() -> None:
    for state in STATE_NAMES:
        name = agency_name(state)
        assert name and name.strip() == name
        assert "None" not in name
