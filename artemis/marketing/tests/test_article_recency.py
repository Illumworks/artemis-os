"""A news article from 2024 is not a current market signal.

Julie and Jamie found these by reading the channel: a Michigan article from
February, a Kansas one from March, and a Los Angeles one from 2024, all posted as
current signals.

Nothing filtered them and nothing could have. `source_published_at` is captured
by the scout, carried the whole way through normalisation, and then not written
at the final INSERT, so the queue had no idea how old anything was.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from artemis.marketing.article_recency import (
    MAX_ARTICLE_AGE_DAYS,
    assess,
    parse_published,
)

TODAY = date(2026, 9, 8)


def _news(published: str | None, **kw: object) -> object:
    return assess(published_at=published, source_type="news_article", today=TODAY, **kw)


# ── the three articles that prompted this ────────────────────────────────────


def test_the_los_angeles_article_from_2024_is_rejected() -> None:
    verdict = _news("2024-11-12")
    assert verdict.should_reject
    assert "2024-11-12" in verdict.reason()


def test_the_michigan_article_from_february_is_rejected() -> None:
    assert _news("2026-02-14").should_reject


def test_the_kansas_article_from_march_is_rejected() -> None:
    assert _news("2026-03-03").should_reject


def test_a_genuinely_recent_article_passes() -> None:
    assert not _news((TODAY - timedelta(days=3)).isoformat()).should_reject


# ── the window ───────────────────────────────────────────────────────────────


def test_the_window_is_generous_enough_for_a_real_hiring_signal() -> None:
    """A superintendent appointed five weeks ago is still a live opening.

    Local education reporting also surfaces late, so a tight window would throw
    away real signals to fix a cosmetic one.
    """
    assert MAX_ARTICLE_AGE_DAYS >= 30
    assert not _news((TODAY - timedelta(days=30)).isoformat()).should_reject


def test_just_past_the_window_is_rejected() -> None:
    assert _news((TODAY - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1)).isoformat()).should_reject


# ── what must NOT be filtered ────────────────────────────────────────────────


def test_a_non_news_source_is_left_alone() -> None:
    """A statute from February is still a live fact.

    An RFP posted sixty days ago can close next week. Only news is judged on
    recency, because recency is the whole of its claim.
    """
    verdict = assess(published_at="2026-02-01", source_type="rfp", today=TODAY)
    assert verdict.verdict == "not_news"
    assert not verdict.should_reject


def test_board_minutes_are_not_judged_on_article_age() -> None:
    verdict = assess(published_at="2026-01-15", source_type="board_minutes", today=TODAY)
    assert not verdict.should_reject


# ── the failure modes that would make this useless ───────────────────────────


def test_an_unparseable_date_is_unknown_and_never_fresh() -> None:
    """Defaulting a bad date to "now" would mark every stale article fresh.

    That is the exact bug this module exists to prevent, so it must not be
    reintroduced by a forgiving parser.
    """
    verdict = _news("not a date at all")
    assert verdict.verdict == "unknown"
    assert not verdict.should_reject


def test_a_missing_date_is_recorded_as_unknown_not_assumed_fresh() -> None:
    verdict = _news(None)
    assert verdict.verdict == "unknown"


def test_a_future_date_is_unknown_rather_than_fresh() -> None:
    """A typo or a bad feed must not buy an article a free pass."""
    assert _news((TODAY + timedelta(days=30)).isoformat()).verdict == "unknown"


# ── date parsing, across the shapes feeds actually emit ──────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-08", date(2026, 9, 8)),
        ("2026-09-08T14:30:00Z", date(2026, 9, 8)),
        ("2026-09-08T14:30:00+00:00", date(2026, 9, 8)),
        # RFC 2822, which is what RSS pubDate uses.
        ("Mon, 08 Sep 2026 14:30:00 GMT", date(2026, 9, 8)),
        ("Sep 08, 2026", date(2026, 9, 8)),
        ("09/08/2026", date(2026, 9, 8)),
    ],
)
def test_the_shapes_real_feeds_emit_all_parse(raw: str, expected: date) -> None:
    assert parse_published(raw) == expected


def test_garbage_parses_to_none_rather_than_today() -> None:
    for raw in ("", None, "unknown", "recently", "n/a"):
        assert parse_published(raw) is None, raw
