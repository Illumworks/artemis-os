"""Tests for the Regional News Scout (artemis/scouts/regional_news/).

All tests mock external I/O — no live network calls.

Coverage:
- mapping.py  (≥12 tests)
- client.py   (≥5 tests)
- scout.py    (≥6 tests)
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import ScoutConfig
from artemis.scouts.regional_news.mapping import (
    article_to_finding,
    board_item_to_finding,
    doe_item_to_finding,
)
from artemis.scouts.regional_news.scout import RegionalNewsScout

# ---------------------------------------------------------------------------
# Test helpers / fixtures
# ---------------------------------------------------------------------------

_DISTRICT: dict[str, Any] = {
    "district_id": "FL_pinellas",
    "state": "FL",
    "district_name": "Pinellas County Schools",
    "boarddocs_url": "https://go.boarddocs.com/fl/pinellas/Board.nsf/Public",
}


def _make_article(
    title: str = "Pinellas Schools RFP approved for literacy curriculum",
    description: str = "The board approved the RFP for a new reading program.",
    url: str = "https://example.com/article1",
    published_at: str = "2024-03-01T12:00:00Z",
    source_name: str = "Tampa Bay Times",
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "url": url,
        "published_at": published_at,
        "source_name": source_name,
        "content": "",
    }


def _make_board_item(
    title: str = "Board approves literacy curriculum review",
    text: str = "The board voted to approve the literacy curriculum review.",
    source_url: str = "https://boarddocs.com/minutes/12345.pdf",
    date: str = "2024-03-01",
) -> dict[str, Any]:
    return {
        "title": title,
        "text": text,
        "source_url": source_url,
        "date": date,
    }


def _make_doe_item(
    title: str = "Florida DoE issues guidance on reading instruction",
    summary: str = "New guidance for K-3 reading assessment frameworks.",
    link: str = "https://fldoe.org/news/guidance-reading",
    published: str = "2024-03-01",
) -> dict[str, Any]:
    return {
        "title": title,
        "summary": summary,
        "link": link,
        "published": published,
    }


def _make_http_mock(
    response_json: dict[str, Any],
    status_code: int = 200,
) -> ScoutHttpClient:
    """Return a ScoutHttpClient whose inner httpx.AsyncClient is mocked."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status.return_value = None

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


# ===========================================================================
# mapping.py tests
# ===========================================================================


def test_article_to_finding_rfp_approved_hot() -> None:
    """'rfp approved' in title+description → BOARD_RFP_AUTHORIZATION + hot."""
    article = _make_article(
        title="Pinellas RFP approved for literacy",
        description="The board approved the RFP for curriculum.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "BOARD_RFP_AUTHORIZATION" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_article_to_finding_board_vote_hot() -> None:
    """'board approved' in text → BOARD_LITERACY_CURRICULUM_REVIEW + hot."""
    article = _make_article(
        title="Board approved new reading program",
        description="The board approved the reading program for all K-3 students.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "BOARD_LITERACY_CURRICULUM_REVIEW" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_article_to_finding_superintendent_transition_hot() -> None:
    """'superintendent hired' → SUPERINTENDENT_TRANSITION + hot."""
    article = _make_article(
        title="New superintendent hired for Pinellas County Schools",
        description="The board named a new superintendent to lead literacy initiatives.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "SUPERINTENDENT_TRANSITION" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_article_to_finding_gubernatorial_hot() -> None:
    """'governor' + 'literacy mandate' → GUBERNATORIAL_EO_LITERACY + hot."""
    article = _make_article(
        title="Governor signs literacy mandate for Florida schools",
        description="Executive order requires all K-3 students pass reading assessment.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "GUBERNATORIAL_EO_LITERACY" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_article_to_finding_guidance_standard() -> None:
    """'guidance' in text (no hot keywords) → STATE_GUIDANCE_ISSUED + standard."""
    article = _make_article(
        title="New guidance issued on reading instruction",
        description="Florida DoE provides new reading guidance for elementary teachers.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "STATE_GUIDANCE_ISSUED" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_article_to_finding_obc_discussion_standard() -> None:
    """'outcomes-based' with no hot trigger → BOARD_OBC_DISCUSSION + standard."""
    article = _make_article(
        title="District exploring outcomes-based contracts for tutoring",
        description="Officials reviewed outcomes-based contract proposals.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "BOARD_OBC_DISCUSSION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_article_to_finding_esser_reference() -> None:
    """'esser' in text → ESSER_CLIFF_REFERENCE."""
    article = _make_article(
        title="Schools prepare for ESSER cliff",
        description="Districts grapple with end of ESSER funding for literacy programs.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "ESSER_CLIFF_REFERENCE" in finding["reasonCodes"]


def test_article_to_finding_irrelevant_returns_none() -> None:
    """Article with no literacy keywords should return None."""
    article = _make_article(
        title="Local sports team wins championship",
        description="High school football team advances to state playoffs.",
        url="https://example.com/sports",
    )
    result = article_to_finding(article, _DISTRICT)
    assert result is None


def test_article_to_finding_district_id() -> None:
    """districtId in finding must match district['district_id']."""
    article = _make_article()
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert finding["districtId"] == "FL_pinellas"


def test_article_to_finding_discovered_by() -> None:
    """discoveredBy must always be 'regional_news_scout'."""
    article = _make_article()
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert finding["discoveredBy"] == "regional_news_scout"


def test_article_to_finding_source_type_news_article() -> None:
    """sourceType for news articles must be 'news_article'."""
    article = _make_article()
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert finding["sourceType"] == "news_article"
    assert finding["metadata"]["source_type"] == "news_article"


def test_board_item_to_finding_maps_correctly() -> None:
    """Board item with literacy content maps to a finding with sourceType 'board_minutes'."""
    item = _make_board_item(
        title="RFP for literacy curriculum",
        text="The board voted to approve the RFP for new reading materials.",
    )
    finding = board_item_to_finding(item, _DISTRICT)
    assert finding is not None
    assert finding["sourceType"] == "board_minutes"
    assert finding["discoveredBy"] == "regional_news_scout"
    assert finding["districtId"] == "FL_pinellas"
    assert isinstance(finding["reasonCodes"], list)
    assert len(finding["reasonCodes"]) >= 1
    assert finding["metadata"]["source_type"] == "board_minutes"


def test_doe_item_to_finding_maps_correctly() -> None:
    """DoE item with literacy content maps to a finding with sourceType 'state_doe'."""
    item = _make_doe_item(
        title="Florida DoE issues dyslexia screening guidance",
        summary="New dyslexia screening requirements for all K-3 students.",
    )
    finding = doe_item_to_finding(item, _DISTRICT)
    assert finding is not None
    assert finding["sourceType"] == "state_doe"
    assert finding["discoveredBy"] == "regional_news_scout"
    assert finding["districtId"] == "FL_pinellas"
    assert "STATE_DYSLEXIA_MANDATE" in finding["reasonCodes"]
    assert finding["metadata"]["source_type"] == "state_doe"


# ===========================================================================
# client.py tests
# ===========================================================================


async def test_fetch_news_articles_no_api_key_returns_empty() -> None:
    """fetch_news_articles returns [] when api_key is empty."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    http = _make_http_mock({})
    result = await fetch_news_articles("Pinellas County Schools", http, api_key="")
    assert result == []


async def test_fetch_news_articles_calls_newsapi() -> None:
    """fetch_news_articles parses the newsapi.org articles array into a list of dicts."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    response_json: dict[str, Any] = {
        "status": "ok",
        "articles": [
            {
                "title": "Pinellas literacy curriculum approved",
                "description": "The board approved the curriculum.",
                "url": "https://example.com/news1",
                "publishedAt": "2024-03-01T12:00:00Z",
                "source": {"name": "Tampa Bay Times"},
                "content": "Full article text.",
            }
        ],
    }
    http = _make_http_mock(response_json)
    result = await fetch_news_articles("Pinellas County Schools", http, api_key="test-key")
    assert len(result) == 1
    assert result[0]["title"] == "Pinellas literacy curriculum approved"
    assert result[0]["source_name"] == "Tampa Bay Times"
    assert result[0]["published_at"] == "2024-03-01T12:00:00Z"


async def test_fetch_news_articles_filters_non_literacy() -> None:
    """fetch_news_articles excludes articles with no literacy keywords."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    response_json: dict[str, Any] = {
        "status": "ok",
        "articles": [
            {
                "title": "Local sports team wins championship",
                "description": "Football team advances to playoffs.",
                "url": "https://example.com/sports",
                "publishedAt": "2024-03-01",
                "source": {"name": "Sports Daily"},
                "content": "",
            },
            {
                "title": "Pinellas reading scores improve",
                "description": "Students show literacy gains.",
                "url": "https://example.com/literacy",
                "publishedAt": "2024-03-02",
                "source": {"name": "Tampa Bay Times"},
                "content": "",
            },
        ],
    }
    http = _make_http_mock(response_json)
    result = await fetch_news_articles("Pinellas County Schools", http, api_key="test-key")
    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/literacy"


async def test_fetch_news_articles_returns_empty_on_error() -> None:
    """fetch_news_articles returns [] when the HTTP client raises an exception."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.side_effect = httpx.ConnectError("Connection refused")
    http = ScoutHttpClient(
        rate_limit=100.0,
        backoff=(),
        _inner=typing.cast(httpx.AsyncClient, inner),
    )
    result = await fetch_news_articles("Pinellas County Schools", http, api_key="test-key")
    assert result == []


async def test_fetch_district_board_items_calls_boarddocs() -> None:
    """fetch_district_board_items returns literacy-filtered items from boarddocs."""
    from artemis.scouts.regional_news.client import fetch_district_board_items

    # We'll mock the underlying fetch_boarddocs by passing through a fake http
    # that returns a literacy-relevant HTML page so fetch_boarddocs emits one item.
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    # No PDF links in this HTML, so boarddocs falls back to emitting an HTML item
    mock_resp.text = "<html><body>literacy reading curriculum board minutes</body></html>"
    mock_resp.content = b""

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    result = await fetch_district_board_items(_DISTRICT, http)
    # Should have at least one item (the HTML fallback from boarddocs)
    assert isinstance(result, list)
    # Each item must contain the expected keys
    for item in result:
        assert "title" in item
        assert "source_url" in item


async def test_fetch_doe_press_items_calls_doe_rss() -> None:
    """fetch_doe_press_items returns literacy-filtered items from the DoE RSS feed."""
    from artemis.scouts.regional_news.client import fetch_doe_press_items

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>FL DoE News</title>
    <item>
      <title>Florida DoE releases new literacy guidance</title>
      <link>https://fldoe.org/news/guidance</link>
      <pubDate>Fri, 01 Mar 2024 12:00:00 GMT</pubDate>
      <description>New guidance for reading instruction in K-3 classrooms.</description>
    </item>
    <item>
      <title>Budget update for school districts</title>
      <link>https://fldoe.org/news/budget</link>
      <pubDate>Mon, 04 Mar 2024 09:00:00 GMT</pubDate>
      <description>Annual school budget update.</description>
    </item>
  </channel>
</rss>"""

    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = rss_xml

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    result = await fetch_doe_press_items("FL", http)
    # The first item has "literacy" and "guidance" — should be included
    assert isinstance(result, list)
    titles = [item["title"] for item in result]
    assert any("literacy" in t.lower() or "guidance" in t.lower() for t in titles)


# ===========================================================================
# scout.py tests
# ===========================================================================


async def test_gather_findings_returns_list() -> None:
    """_gather_findings() returns a list (may be empty) with mocked fetchers."""
    news_mock: AsyncMock = AsyncMock(return_value=[])
    board_mock: AsyncMock = AsyncMock(return_value=[])
    doe_mock: AsyncMock = AsyncMock(return_value=[])

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[_DISTRICT],
        _news_fetcher=news_mock,
        _board_fetcher=board_mock,
        _doe_fetcher=doe_mock,
    )
    result = await scout._gather_findings()
    assert isinstance(result, list)


async def test_gather_findings_deduplicates_by_url() -> None:
    """Findings with identical (district_id, source_url) appear only once."""
    duplicate_article: dict[str, Any] = _make_article(
        url="https://example.com/dupe",
        title="Pinellas literacy curriculum approved",
        description="board approved the curriculum",
    )
    # Both fetchers return the same source_url to test deduplication logic.
    # We supply two districts with the same id but simulate the same article twice.
    district_a = dict(_DISTRICT)

    async def _news_fetcher_dupe(
        district_name: str,
        http: ScoutHttpClient,
        *,
        api_key: str = "",
    ) -> list[dict[str, Any]]:
        return [duplicate_article, duplicate_article]

    async def _board_fetcher_empty(
        district: dict[str, Any],
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    async def _doe_fetcher_empty(
        state: str,
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[district_a],
        _news_fetcher=_news_fetcher_dupe,
        _board_fetcher=_board_fetcher_empty,
        _doe_fetcher=_doe_fetcher_empty,
    )
    results = await scout._gather_findings()
    urls = [f["metadata"]["source_url"] for f in results]
    assert urls.count("https://example.com/dupe") == 1


async def test_gather_findings_continues_on_district_error() -> None:
    """A per-district exception is caught and processing continues for other districts."""
    district_ok: dict[str, Any] = {
        "district_id": "TX_dallas",
        "state": "TX",
        "district_name": "Dallas ISD",
        "boarddocs_url": None,
    }
    district_bad: dict[str, Any] = {
        "district_id": "FAIL_district",
        "state": "FL",
        "district_name": "Bad District",
        "boarddocs_url": None,
    }

    call_count = 0

    async def _news_fetcher_raises(
        district_name: str,
        http: ScoutHttpClient,
        *,
        api_key: str = "",
    ) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        if district_name == "Bad District":
            raise RuntimeError("Simulated failure")
        return []

    async def _board_ok(
        district: dict[str, Any],
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    async def _doe_ok(
        state: str,
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[district_bad, district_ok],
        _news_fetcher=_news_fetcher_raises,
        _board_fetcher=_board_ok,
        _doe_fetcher=_doe_ok,
    )
    result = await scout._gather_findings()
    # Must not raise; should have been called for both districts
    assert call_count == 2
    assert isinstance(result, list)


async def test_gather_findings_filters_none_results() -> None:
    """Findings mapped to None (irrelevant items) are filtered out."""
    irrelevant_article: dict[str, Any] = _make_article(
        title="Local sports team wins",
        description="Football team advances to playoffs.",
        url="https://example.com/sports",
    )

    async def _news_fetcher(
        district_name: str,
        http: ScoutHttpClient,
        *,
        api_key: str = "",
    ) -> list[dict[str, Any]]:
        return [irrelevant_article]

    async def _board_empty(
        district: dict[str, Any],
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    async def _doe_empty(
        state: str,
        http: ScoutHttpClient,
    ) -> list[dict[str, Any]]:
        return []

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[_DISTRICT],
        _news_fetcher=_news_fetcher,
        _board_fetcher=_board_empty,
        _doe_fetcher=_doe_empty,
    )
    result = await scout._gather_findings()
    # Irrelevant article must be filtered out
    assert result == []


# ===========================================================================
# 2026-07-10 broadening: screen-time / AI-in-schools news coverage
# ===========================================================================


def test_article_to_finding_screen_time_only_is_relevant() -> None:
    """An article about screen-time with NO literacy word must still be kept."""
    article = _make_article(
        title="District adopts bell-to-bell cell phone ban",
        description="The board approved new screen time limits for all students.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "POLICY_EDTECH_TIME_LIMIT" in finding["reasonCodes"]


def test_article_to_finding_ai_in_schools_only_is_relevant() -> None:
    """An article about AI policy with NO literacy word must still be kept."""
    article = _make_article(
        title="School board issues generative AI guidance",
        description="New AI policy guides teacher use of ChatGPT in classrooms.",
    )
    finding = article_to_finding(article, _DISTRICT)
    assert finding is not None
    assert "POLICY_AI_IN_SCHOOLS" in finding["reasonCodes"]


def test_article_to_finding_phone_free_still_irrelevant_without_anchor() -> None:
    """Sanity: an article with none of the anchors is still dropped."""
    article = _make_article(
        title="Local bakery wins county fair ribbon",
        description="The bakery took first place for its sourdough.",
    )
    assert article_to_finding(article, _DISTRICT) is None


async def test_fetch_news_articles_query_includes_screen_time_and_ai_terms() -> None:
    """Default query broadens beyond literacy to screen-time + AI-in-schools terms."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    http = _make_http_mock({"articles": []})
    await fetch_news_articles("Pinellas County Schools", http, api_key="test-key")

    sent_params = typing.cast(AsyncMock, http._client).request.call_args.kwargs["params"]
    query = sent_params["q"]
    assert "screen time" in query
    assert "artificial intelligence" in query
    assert "literacy" in query  # original beat preserved


async def test_fetch_news_articles_query_topics_override() -> None:
    """query_topics overrides the default OR-group entirely."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    http = _make_http_mock({"articles": []})
    await fetch_news_articles(
        "Pinellas County Schools", http, api_key="test-key", query_topics=["ai moratorium"]
    )

    sent_params = typing.cast(AsyncMock, http._client).request.call_args.kwargs["params"]
    assert sent_params["q"] == '"Pinellas County Schools" AND (ai moratorium)'


async def test_fetch_news_articles_domains_param_sent_when_provided() -> None:
    """domains, when given, is joined into newsapi's `domains` filter."""
    from artemis.scouts.regional_news.client import NEWS_OUTLET_DOMAINS, fetch_news_articles

    http = _make_http_mock({"articles": []})
    await fetch_news_articles(
        "Pinellas County Schools", http, api_key="test-key", domains=NEWS_OUTLET_DOMAINS
    )

    sent_params = typing.cast(AsyncMock, http._client).request.call_args.kwargs["params"]
    assert sent_params["domains"] == ",".join(NEWS_OUTLET_DOMAINS)


async def test_fetch_news_articles_no_domains_param_by_default() -> None:
    """Without an explicit domains list, no `domains` filter is sent (unrestricted search)."""
    from artemis.scouts.regional_news.client import fetch_news_articles

    http = _make_http_mock({"articles": []})
    await fetch_news_articles("Pinellas County Schools", http, api_key="test-key")

    sent_params = typing.cast(AsyncMock, http._client).request.call_args.kwargs["params"]
    assert "domains" not in sent_params


def test_news_outlet_domains_covers_major_ed_policy_outlets() -> None:
    from artemis.scouts.regional_news.client import NEWS_OUTLET_DOMAINS

    expected = {
        "chalkbeat.org",
        "edsource.org",
        "k12dive.com",
        "edweek.org",
        "govtech.com",
        "the74million.org",
        "hechingerreport.org",
        "axios.com",
    }
    assert expected.issubset(set(NEWS_OUTLET_DOMAINS))


async def test_scout_forwards_query_topics_and_domains_to_fetcher() -> None:
    """RegionalNewsScout threads query_topics/news_domains through to the injected fetcher."""
    news_mock: AsyncMock = AsyncMock(return_value=[])
    board_mock: AsyncMock = AsyncMock(return_value=[])
    doe_mock: AsyncMock = AsyncMock(return_value=[])

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[_DISTRICT],
        query_topics=["screen time"],
        news_domains=["chalkbeat.org"],
        _news_fetcher=news_mock,
        _board_fetcher=board_mock,
        _doe_fetcher=doe_mock,
    )
    await scout._gather_findings()

    _, kwargs = news_mock.call_args
    assert kwargs["query_topics"] == ["screen time"]
    assert kwargs["domains"] == ["chalkbeat.org"]


async def test_scout_omits_query_topics_and_domains_when_unset() -> None:
    """Backward compatibility: a fetcher stub with the OLD signature still works."""

    async def _old_signature_fetcher(
        district_name: str, http: ScoutHttpClient, *, api_key: str = ""
    ) -> list[dict[str, Any]]:
        return []

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=[_DISTRICT],
        _news_fetcher=_old_signature_fetcher,
        _board_fetcher=AsyncMock(return_value=[]),
        _doe_fetcher=AsyncMock(return_value=[]),
    )
    result = await scout._gather_findings()
    assert result == []


def test_regional_news_scout_type_class_var() -> None:
    """RegionalNewsScout.scout_type class var must equal 'regional_news_scout'."""
    assert RegionalNewsScout.scout_type == "regional_news_scout"


async def test_gather_findings_calls_all_three_fetchers_per_district() -> None:
    """_gather_findings calls news, board, and DoE fetchers for each district."""
    news_mock: AsyncMock = AsyncMock(return_value=[])
    board_mock: AsyncMock = AsyncMock(return_value=[])
    doe_mock: AsyncMock = AsyncMock(return_value=[])

    two_districts = [_DISTRICT, {**_DISTRICT, "district_id": "TX_dallas", "state": "TX"}]

    scout = RegionalNewsScout(
        ScoutConfig(),
        watch_districts=two_districts,
        _news_fetcher=news_mock,
        _board_fetcher=board_mock,
        _doe_fetcher=doe_mock,
    )
    await scout._gather_findings()

    assert news_mock.call_count == 2
    assert board_mock.call_count == 2
    assert doe_mock.call_count == 2
