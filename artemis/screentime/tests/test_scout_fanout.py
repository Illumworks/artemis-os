"""Unit tests for the screen-time scout fan-out — the legislative query fix.

Regression: the first live run's `legislative: ok:0`. The shared LegislativeScout
joins its keyword list with spaces and LegiScan's ADAS getSearch AND-s
space-separated terms, so a flat multi-term keyword list became one giant
all-terms-required query that matched ~nothing. We now hand the scout a single
pre-composed ADAS boolean OR expression.
"""

from __future__ import annotations

from artemis.screentime.scout_fanout import (
    SCREENTIME_KEYWORDS,
    SCREENTIME_TERMS,
    _legiscan_query,
)


def test_keywords_is_single_or_expression():
    # Exactly one element so the scout's " ".join(keywords) yields the OR string,
    # not an implicit AND of many terms.
    assert len(SCREENTIME_KEYWORDS) == 1
    query = SCREENTIME_KEYWORDS[0]
    assert " OR " in query
    # Every term is present and phrase-quoted.
    for term in SCREENTIME_TERMS:
        assert f'"{term}"' in query
    # No implicit-AND: terms are joined by OR, not bare spaces between phrases.
    assert query.count(" OR ") == len(SCREENTIME_TERMS) - 1


def test_legiscan_query_quotes_and_ors():
    q = _legiscan_query(["screen time", "device time"])
    assert q == '"screen time" OR "device time"'


def test_join_with_spaces_preserves_or_expression():
    """The shared scout does ' '.join(keywords); confirm that is a no-op here."""
    joined = " ".join(SCREENTIME_KEYWORDS)
    assert joined == SCREENTIME_KEYWORDS[0]
    assert " OR " in joined
