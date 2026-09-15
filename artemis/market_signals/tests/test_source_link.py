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
    # Quoted since 2026-09-14, so a dozen outlets running the same wire story
    # do not all match the search.
    assert "search?q=%22Illinois+districts+weigh" in out


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


# ── Naming the publisher (2026-09-14) ─────────────────────────────────────────
# Going direct to publisher feeds was considered and measured against: 134
# distinct publishers in 202 live items across five scout-style queries, 78% of
# them appearing exactly once. Local district news is a long tail, so a curated
# feed list cannot cover a national territory. What the Google News feed DOES
# give on every item is `<source url="...">Publisher</source>`, and that was
# being thrown away.


def test_the_search_is_narrowed_to_the_publisher_when_known() -> None:
    from artemis.market_signals.source_link import search_url

    out = search_url("Laneville ISD names interim superintendent", "https://www.marinij.com")
    assert "site%3Awww.marinij.com" in out
    # Quoted, so a dozen outlets running the same wire story do not all match.
    assert "%22Laneville" in out


def test_without_a_publisher_the_search_still_works() -> None:
    from artemis.market_signals.source_link import search_url

    out = search_url("Laneville ISD names interim superintendent")
    assert "site%3A" not in out
    assert "Laneville" in out


def test_the_link_label_is_the_publisher() -> None:
    out = slack_link(
        JULIE_LINK, "Marin Independent Journal", headline=HEADLINE, domain="www.marinij.com"
    )
    assert out.endswith("|Marin Independent Journal>")
    assert "site%3Awww.marinij.com" in out


def test_a_publisher_domain_is_normalised_however_it_arrives() -> None:
    from artemis.market_signals.source_link import publisher_host

    for raw in ("https://www.marinij.com", "www.marinij.com", "http://www.marinij.com/"):
        assert publisher_host(raw) == "www.marinij.com"
    for junk in ("", None, "   "):
        assert publisher_host(junk) == ""


def test_the_publisher_suffix_is_split_off_the_headline() -> None:
    """Google News appends " - Publisher" to every title. Left inside a quoted
    search it prevents the match it is meant to make."""
    from artemis.market_signals.source_link import split_google_title

    head, pub = split_google_title(
        "Illinois State Board of Education issues AI guidance, "
        "written with help from AI - Capitol News Illinois"
    )
    assert pub == "Capitol News Illinois"
    assert head.endswith("written with help from AI")


def test_a_headline_containing_a_dash_is_not_cut_in_half() -> None:
    """The split is on the LAST separator, and only when the tail looks like an
    outlet name rather than a clause."""
    from artemis.market_signals.source_link import split_google_title

    text = "Board approves K-5 curriculum - and then reversed course the following week entirely"
    head, pub = split_google_title(text)
    assert pub == ""
    assert head == text


def test_a_title_with_no_publisher_survives_intact() -> None:
    from artemis.market_signals.source_link import split_google_title

    assert split_google_title("A headline with no suffix") == ("A headline with no suffix", "")
    assert split_google_title("") == ("", "")
