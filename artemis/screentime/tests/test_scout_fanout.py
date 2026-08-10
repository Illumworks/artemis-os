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


_AI_TOPIC_MARKERS = (
    "artificial intelligence",
    "generative ai",
    " ai ",
    "ai in",
    "ai use",
    "ai literacy",
    "ai policy",
    "ai guidance",
)


def test_screentime_terms_include_school_scoped_ai_policy_terms():
    """2026-07-10 broadening: AI-in-schools policy terms are part of the LegiScan
    query too, each scoped to schools/classrooms/students (not bare "AI")."""
    ai_terms = [
        t
        for t in SCREENTIME_TERMS
        if any(marker in f" {t.lower()} " for marker in _AI_TOPIC_MARKERS)
    ]
    assert ai_terms, "expected at least one AI-in-schools term in SCREENTIME_TERMS"
    school_scope_words = ("school", "classroom", "student", "education")
    for term in ai_terms:
        assert any(w in term.lower() for w in school_scope_words), (
            f"AI term {term!r} must be school-scoped, not general AI"
        )
    # Every AI term is multi-word (never a bare "ai" token).
    for term in ai_terms:
        assert term.strip().lower() != "ai"


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


def test_daily_fan_out_is_the_fast_set_only():
    """2026-07-11 source tuning: the daily _SCOUT_GATHERERS is EXACTLY
    {legislative, national_news, regional_news}. state_doe (shared marketing
    scout, floods ~2,185 off-topic items) and board_minutes /
    board_peer_validation (too slow — BoardDocs + an LLM call per district,
    blows the 10-minute daily window) are excluded; the board scout moved to
    its own weekly sweep (``runner.run_board_sweep``)."""
    assert set(_SCOUT_GATHERERS.keys()) == {"legislative", "national_news", "regional_news"}
    assert "state_doe" not in _SCOUT_GATHERERS
    assert "board_minutes" not in _SCOUT_GATHERERS
    assert "board_peer_validation" not in _SCOUT_GATHERERS


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


# ---------------------------------------------------------------------------
# 2026-07-11: board sweep decoupled from the daily fan-out + bounded
# concurrency (the PoC-sweep timeout fix — 27 districts x BoardDocs + LLM
# call each blew the 10-minute daily window run serially).
# ---------------------------------------------------------------------------


async def test_gather_board_peer_validation_concurrent_respects_semaphore_cap():
    """No more than `concurrency` districts run BoardDocs+classify at once."""
    import asyncio

    from artemis.screentime.scout_fanout import _gather_board_peer_validation_concurrent

    concurrency = 2
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    class _FakeScout:
        def __init__(self, *_args, **_kwargs):
            pass

        async def _gather_findings(self):
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return [{"headline": "ok"}]

    watch_list = [{"district_id": f"D{i}", "boarddocs_url": "x"} for i in range(6)]

    with patch(
        "artemis.scouts.board_minutes.peer_scout.BoardPeerValidationScout",
        _FakeScout,
    ):
        results = await _gather_board_peer_validation_concurrent(
            concurrency=concurrency, watch_list=watch_list
        )

    assert len(results) == 6
    assert max_in_flight <= concurrency


async def test_gather_board_peer_validation_concurrent_isolates_district_failures():
    """One district raising doesn't stop the others from being gathered."""
    from artemis.screentime.scout_fanout import _gather_board_peer_validation_concurrent

    class _FakeScout:
        def __init__(self, *_args, watch_list=None, **_kwargs):
            self._district = (watch_list or [{}])[0]

        async def _gather_findings(self):
            if self._district.get("district_id") == "boom":
                raise RuntimeError("boarddocs down")
            return [{"headline": self._district.get("district_id")}]

    watch_list = [
        {"district_id": "ok1", "boarddocs_url": "x"},
        {"district_id": "boom", "boarddocs_url": "x"},
        {"district_id": "ok2", "boarddocs_url": "x"},
    ]

    with patch(
        "artemis.scouts.board_minutes.peer_scout.BoardPeerValidationScout",
        _FakeScout,
    ):
        results = await _gather_board_peer_validation_concurrent(watch_list=watch_list)

    headlines = {r["headline"] for r in results}
    assert headlines == {"ok1", "ok2"}


async def test_gather_board_peer_validation_concurrent_defaults_to_full_watch_list():
    """With no watch_list override, every district in the default starter
    seed list is scanned (one scout instance per district)."""
    from artemis.scouts.board_minutes.peer_scout import _DEFAULT_PEER_WATCH_LIST
    from artemis.screentime.scout_fanout import _gather_board_peer_validation_concurrent

    seen_watch_lists: list[list[dict]] = []

    class _FakeScout:
        def __init__(self, *_args, watch_list=None, max_districts_per_run=None, **_kwargs):
            seen_watch_lists.append(watch_list)
            assert max_districts_per_run == 1

        async def _gather_findings(self):
            return []

    with patch(
        "artemis.scouts.board_minutes.peer_scout.BoardPeerValidationScout",
        _FakeScout,
    ):
        await _gather_board_peer_validation_concurrent()

    assert len(seen_watch_lists) == len(_DEFAULT_PEER_WATCH_LIST)
