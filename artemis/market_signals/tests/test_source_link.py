"""A reader in #market-signals clicked a link and it did not reach the article.

29 of the 33 links in the brief she was reading were Google News redirects, so
these tests pin the behaviour for the whole class, not for her one row.
"""

from __future__ import annotations

import pytest

from artemis.market_signals.source_link import is_unresolvable, slack_link

# The actual link Julie reported, truncated: the `CBMi…` blob is opaque.
JULIE_LINK = "https://news.google.com/rss/articles/CBMirwFBVV95cUxPT2NjaHVDU3NT"
HEADLINE = "Illinois districts weigh new literacy screener rules"


@pytest.mark.parametrize(
    "url",
    [
        JULIE_LINK,
        "https://news.google.com/search?q=x",
        "https://www.news.google.com/rss/articles/CBMi",
    ],
)
def test_google_news_is_unresolvable(url: str) -> None:
    assert is_unresolvable(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://go.boarddocs.com/il/cps/Board.nsf/goto",
        "https://www.publicpurchase.com/gems/bid/x",
        "https://chalkbeat.org/chicago/story",
        "",
    ],
)
def test_real_sources_are_left_alone(url: str) -> None:
    assert not is_unresolvable(url)


def test_unresolvable_link_becomes_a_headline_search() -> None:
    out = slack_link(JULIE_LINK, "Chalkbeat Chicago", headline=HEADLINE)
    # The reader still sees the publisher, and the href is now static.
    assert out.endswith("|Chalkbeat Chicago>")
    assert JULIE_LINK not in out
    assert "search?q=Illinois+districts+weigh" in out


def test_resolvable_link_is_passed_through_untouched() -> None:
    url = "https://go.boarddocs.com/il/cps/Board.nsf/goto"
    assert slack_link(url, "BoardDocs", headline=HEADLINE) == f"<{url}|BoardDocs>"


def test_no_headline_means_no_link_rather_than_a_broken_one() -> None:
    """With nothing to search for, plain text beats a link that does not work."""
    assert slack_link(JULIE_LINK, "Chalkbeat Chicago", headline="") == "Chalkbeat Chicago"
    assert slack_link(JULIE_LINK, "Chalkbeat Chicago") == "Chalkbeat Chicago"


def test_missing_url_renders_the_label() -> None:
    assert slack_link("", "Chalkbeat Chicago", headline=HEADLINE) == "Chalkbeat Chicago"
    assert slack_link(None, "Chalkbeat Chicago", headline=HEADLINE) == "Chalkbeat Chicago"


def test_rendered_link_survives_the_house_style_linter() -> None:
    """``lint_agent_text`` silently stripped an emoji marker from this same
    brief once, so anything we rely on rendering is checked against it."""
    from artemis.writing_rules.agent_lint import lint_agent_text

    out = slack_link(JULIE_LINK, "Chalkbeat Chicago", headline=HEADLINE)
    assert lint_agent_text(out) == out
