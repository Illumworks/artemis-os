"""Unit tests for the screen-time scout fan-out — the legislative query fix.

Regression: the first live run's `legislative: ok:0`. The shared LegislativeScout
joins its keyword list with spaces and LegiScan's ADAS getSearch AND-s
space-separated terms, so a flat multi-term keyword list became one giant
all-terms-required query that matched ~nothing. We now hand the scout a single
pre-composed ADAS boolean OR expression.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from artemis.screentime.scout_fanout import (
    _SCOUT_GATHERERS,
    SCREENTIME_KEYWORDS,
    SCREENTIME_TERMS,
    _legiscan_query,
    gather_national_findings,
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


# ---------------------------------------------------------------------------
# 2026-07-10 broadening: board_peer_validation wired in + regional_news
# outlet/keyword coverage passed through the fan-out.
# ---------------------------------------------------------------------------


def test_board_peer_validation_registered_in_gatherers():
    assert "board_peer_validation" in _SCOUT_GATHERERS


async def test_gather_national_findings_includes_board_peer_validation_status():
    """Injected gatherers dict is honored (existing DI pattern) — the label surfaces
    in per_source_status regardless of the real scout implementation."""

    async def _fake_legislative(_states):
        return [{"headline": "leg"}]

    async def _fake_board_peer(_states):
        return [{"headline": "peer finding"}]

    findings, status = await gather_national_findings(
        states=["FL"],
        gatherers={
            "legislative": _fake_legislative,
            "board_peer_validation": _fake_board_peer,
        },
    )
    assert status["board_peer_validation"] == "ok:1"
    assert any(f["headline"] == "peer finding" for f in findings)


async def test_gather_national_findings_board_peer_validation_failure_is_isolated():
    """A failing board_peer_validation source must not break the rest of the sweep."""

    async def _boom(_states):
        raise RuntimeError("boarddocs down")

    async def _ok(_states):
        return [{"headline": "ok"}]

    findings, status = await gather_national_findings(
        states=["FL"],
        gatherers={"board_peer_validation": _boom, "regional_news": _ok},
    )
    assert status["board_peer_validation"].startswith("error:")
    assert status["regional_news"] == "ok:1"
    assert findings == [{"headline": "ok"}]


async def test_gather_regional_news_passes_broadened_topics_and_domains():
    """_gather_regional_news wires TOPIC_KEYWORDS + NEWS_OUTLET_DOMAINS into the scout."""
    from artemis.scouts.regional_news.client import NEWS_OUTLET_DOMAINS, TOPIC_KEYWORDS

    fake_instance = AsyncMock()
    fake_instance._gather_findings.return_value = []

    with patch(
        "artemis.scouts.regional_news.scout.RegionalNewsScout", return_value=fake_instance
    ) as mock_cls:
        from artemis.screentime.scout_fanout import _gather_regional_news

        result = await _gather_regional_news()

    assert result == []
    _, kwargs = mock_cls.call_args
    assert kwargs["query_topics"] == TOPIC_KEYWORDS
    assert kwargs["news_domains"] == NEWS_OUTLET_DOMAINS


async def test_gather_board_peer_validation_calls_scout():
    """_gather_board_peer_validation constructs BoardPeerValidationScout and gathers."""
    fake_instance = AsyncMock()
    fake_instance._gather_findings.return_value = [{"headline": "peer"}]

    with patch(
        "artemis.scouts.board_minutes.peer_scout.BoardPeerValidationScout",
        return_value=fake_instance,
    ):
        from artemis.screentime.scout_fanout import _gather_board_peer_validation

        result = await _gather_board_peer_validation()

    assert result == [{"headline": "peer"}]
