"""Unit tests for the 50-state news gatherer (mocked RSS — never hits network).

Covers: query construction (multi-word, school-scoped), RSS parse → Finding
normalization (state set correctly), the rotation cursor (advances + wraps),
that findings pass the broadened (screen-time OR AI-in-schools) topic gate,
and that the dedup key is populated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from artemis.screentime.filters import normalize_finding, passes_topic_gate
from artemis.screentime.national_news import (
    STATE_NAMES,
    _rotation_window,
    build_state_news_query,
    build_state_news_rss_url,
    gather_national_policy_news,
    gather_state_news,
    item_to_finding,
    parse_news_rss,
)
from artemis.screentime.scout_fanout import US_STATES_AND_DC
from artemis.screentime.topic_config import DEFAULT_TOPIC_RULES

_SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>Florida schools weigh new screen time limits for classrooms</title>
  <link>https://news.example.com/fl-screen-time</link>
  <pubDate>Fri, 10 Jul 2026 12:00:00 GMT</pubDate>
  <description>The state board is considering device-time limits in K-12 classrooms.</description>
</item>
<item>
  <title>Florida district adopts generative AI policy for students</title>
  <link>https://news.example.com/fl-ai-policy</link>
  <pubDate>Fri, 10 Jul 2026 13:00:00 GMT</pubDate>
  <description>Guidance covers responsible AI use in the classroom.</description>
</item>
</channel></rss>
"""


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def test_all_states_and_dc_have_names():
    """Every state scout_fanout sweeps must have a query-buildable full name."""
    for abbr in US_STATES_AND_DC:
        assert abbr in STATE_NAMES, f"missing full name for {abbr}"
        assert build_state_news_query(abbr)  # does not raise


def test_query_is_multiword_and_school_scoped():
    """2026-07-11 broadening: state + 'schools' bare-ANDed with a short OR'd
    group of quoted core phrases (replaces the old 4x fully-quoted 5-6-word
    sentences that returned 0 live hits — see module docstring)."""
    query = build_state_news_query("FL")
    assert query.startswith("Florida schools (")
    assert " OR " in query
    # The OR group's phrases are quoted and each is multi-word (no bare "ai").
    inner = query[query.index("(") + 1 : query.rindex(")")]
    phrases = [p.strip() for p in inner.split(" OR ")]
    assert len(phrases) == 4
    for phrase in phrases:
        assert phrase.startswith('"') and phrase.endswith('"')
        assert len(phrase.strip('"').split()) > 1
    # Never a bare "ai" anchor.
    assert '"ai"' not in query.lower()


def test_query_covers_screentime_and_ai():
    query = build_state_news_query("TX").lower()
    assert query.startswith("texas schools (")
    assert "screen time" in query
    assert "device policy" in query
    assert "ai policy" in query
    assert "artificial intelligence" in query


def test_unknown_state_raises():
    with pytest.raises(KeyError):
        build_state_news_query("ZZ")


def test_rss_url_shape_matches_state_doe_convention():
    url = build_state_news_rss_url("CA")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert url.endswith("&hl=en-US&gl=US&ceid=US%3Aen")


# ---------------------------------------------------------------------------
# RSS parse → Finding
# ---------------------------------------------------------------------------


def test_parse_news_rss_extracts_items():
    items = parse_news_rss(_SAMPLE_RSS)
    assert len(items) == 2
    assert items[0]["title"] == "Florida schools weigh new screen time limits for classrooms"
    assert items[0]["link"] == "https://news.example.com/fl-screen-time"
    assert items[0]["summary"]


def test_parse_news_rss_malformed_returns_empty():
    assert parse_news_rss("<not-xml") == []
    assert parse_news_rss("") == []


def test_item_to_finding_sets_state_and_fields():
    items = parse_news_rss(_SAMPLE_RSS)
    finding = item_to_finding(items[0], "FL")
    assert finding is not None
    assert finding["state"] == "FL"
    assert finding["metadata"]["state"] == "FL"
    assert finding["metadata"]["source_url"] == "https://news.example.com/fl-screen-time"
    assert finding["sourceType"] == "national_news"
    assert finding["title"]
    assert finding["evidence"]
    assert "POLICY_EDTECH_TIME_LIMIT" in finding["reasonCodes"]


def test_item_to_finding_ai_item_gets_ai_reason_code():
    items = parse_news_rss(_SAMPLE_RSS)
    finding = item_to_finding(items[1], "FL")
    assert finding is not None
    assert "POLICY_AI_IN_SCHOOLS" in finding["reasonCodes"]


def test_item_to_finding_no_title_returns_none():
    assert item_to_finding({"title": "", "summary": "x"}, "FL") is None


def test_finding_normalizes_with_correct_state():
    items = parse_news_rss(_SAMPLE_RSS)
    finding = item_to_finding(items[0], "FL")
    assert finding is not None
    candidate = normalize_finding(finding)
    assert candidate is not None
    assert candidate.state == "FL"
    assert candidate.source_url == "https://news.example.com/fl-screen-time"
    assert candidate.source_type == "national_news"
    # Dedup key populated.
    assert candidate.content_hash
    assert len(candidate.content_hash) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Topic gate — screen-time and AI items both pass the broadened v3 gate.
# ---------------------------------------------------------------------------


def test_screentime_item_passes_topic_gate():
    items = parse_news_rss(_SAMPLE_RSS)
    finding = item_to_finding(items[0], "FL")
    assert finding is not None
    candidate = normalize_finding(finding)
    assert candidate is not None
    assert passes_topic_gate(candidate.text, DEFAULT_TOPIC_RULES) is True


def test_ai_item_passes_topic_gate():
    items = parse_news_rss(_SAMPLE_RSS)
    finding = item_to_finding(items[1], "FL")
    assert finding is not None
    candidate = normalize_finding(finding)
    assert candidate is not None
    assert passes_topic_gate(candidate.text, DEFAULT_TOPIC_RULES) is True


def test_generic_off_topic_item_dropped_by_gate():
    finding = item_to_finding(
        {
            "title": "Florida schools adopt new reading retention curriculum",
            "summary": "Literacy mandate.",
        },
        "FL",
    )
    assert finding is not None
    candidate = normalize_finding(finding)
    assert candidate is not None
    assert passes_topic_gate(candidate.text, DEFAULT_TOPIC_RULES) is False


# ---------------------------------------------------------------------------
# Rotation cursor — advances and wraps.
# ---------------------------------------------------------------------------


def test_rotation_window_advances():
    states = ["AL", "AK", "AZ", "AR", "CA"]
    window, next_cursor = _rotation_window(states, 0, 2)
    assert window == ["AL", "AK"]
    assert next_cursor == 2

    window2, next_cursor2 = _rotation_window(states, next_cursor, 2)
    assert window2 == ["AZ", "AR"]
    assert next_cursor2 == 4


def test_rotation_window_wraps_around():
    states = ["AL", "AK", "AZ", "AR", "CA"]
    # Starting at 4 with count 2 should wrap: CA, then back to AL.
    window, next_cursor = _rotation_window(states, 4, 2)
    assert window == ["CA", "AL"]
    assert next_cursor == 1


def test_rotation_window_full_cycle_covers_every_state_exactly_once():
    states = list(US_STATES_AND_DC)
    seen: list[str] = []
    cursor = 0
    per_run = 10
    for _ in range(6):  # 51 states / 10 per run -> 6 runs covers + wraps
        window, cursor = _rotation_window(states, cursor, per_run)
        seen.extend(window)
    # First len(states) items visited (before the wrap starts repeating) cover
    # every state exactly once, in order.
    assert seen[: len(states)] == states


def test_rotation_window_empty_states():
    assert _rotation_window([], 0, 5) == ([], 0)


async def test_gather_national_policy_news_default_sweeps_all_provided_states():
    fake_http = AsyncMock()
    fake_http.get.return_value.status_code = 200
    fake_http.get.return_value.text = _SAMPLE_RSS

    states = ["FL", "TX"]
    findings, next_cursor = await gather_national_policy_news(states=states, http=fake_http)
    assert next_cursor == 0
    # 2 items per state x 2 states. Both lanes return the same _SAMPLE_RSS here,
    # so this also asserts gather_state_news dedups the overlap by link rather
    # than double-counting it.
    assert len(findings) == 4
    # Two feeds per state since the brand lane landed (policy + brand).
    assert fake_http.get.await_count == 2 * len(states)


async def test_gather_national_policy_news_rotation_mode_returns_advanced_cursor():
    fake_http = AsyncMock()
    fake_http.get.return_value.status_code = 200
    fake_http.get.return_value.text = _SAMPLE_RSS

    findings, next_cursor = await gather_national_policy_news(
        states=["FL", "TX", "CA"], states_per_run=2, cursor=0, http=fake_http
    )
    assert next_cursor == 2
    # Only 2 of 3 states hit this run, x2 feeds each (policy + brand lanes).
    assert fake_http.get.await_count == 2 * 2


async def test_gather_state_news_failure_is_isolated():
    """A single state's HTTP error yields [] rather than raising."""
    fake_http = AsyncMock()
    fake_http.get.side_effect = RuntimeError("network down")

    result = await gather_state_news("FL", fake_http)
    assert result == []
