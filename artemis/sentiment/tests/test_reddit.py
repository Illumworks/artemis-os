"""Tests for the parent-sentiment watch's Reddit source (mocked HTTP -- never hits network).

No live Reddit OAuth app exists yet (Jon has not created one), so every test
here mocks httpx. Nothing in this file is evidence the client works against
real Reddit; it is evidence the code does what it claims against the shapes
Reddit's own documentation describes.

Coverage:
  - RedditClient is structurally read-only: its public method set is exactly
    {search, search_all, list_subreddit_posts, list_subreddit_posts_all} --
    no vote/comment/post/message method.
  - fetch_access_token: happy path (HTTP Basic Auth, not body creds), non-2xx,
    network error, 2xx-but-missing-access_token.
  - resolve_reddit_config: DB-first, env fallback, missing-fields raises.
  - search / list_subreddit_posts: happy path, pagination cursor, non-2xx
    raises RedditAPIError, malformed listing degrades to an empty page rather
    than raising.
  - list_subreddit_posts_all / search_all: follow `after` across pages, stop
    at max_posts.
  - Rate limiting: the floor pacing interval is honoured, and a low
    X-Ratelimit-Remaining schedules backoff before the next call.
  - post_to_finding: shape parity with national_news.item_to_finding's
    top-level keys, no author/username anywhere, removed/deleted body
    handling, no-title returns None.
  - gather_subreddit_findings / gather_search_findings: fail-safe (never
    raise; return [] + log on any client error).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.integrations.config_resolver import MissingProviderConfigError
from artemis.sentiment.reddit import (
    NATIONAL,
    RedditAPIError,
    RedditAuthError,
    RedditClient,
    RedditListingPage,
    _parse_listing,
    _RedditRateLimiter,
    fetch_access_token,
    gather_search_findings,
    gather_subreddit_findings,
    post_to_finding,
    resolve_reddit_config,
)


def _mock_response(status: int, body: Any, headers: dict[str, str] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = body
    resp.text = str(body)
    resp.headers = httpx.Headers(headers or {})
    return resp


def _mock_http_client(method_name: str, resp: MagicMock) -> AsyncMock:
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    setattr(mock_http, method_name, AsyncMock(return_value=resp))
    return mock_http


def _listing(children: list[dict[str, Any]], after: str | None = None) -> dict[str, Any]:
    return {"kind": "Listing", "data": {"children": children, "after": after, "before": None}}


def _post_child(
    *,
    title: str = "Some post",
    selftext: str = "",
    subreddit: str = "parenting",
    permalink: str = "/r/parenting/comments/abc123/some_post/",
    url: str = "",
    created_utc: float = 1_755_000_000.0,
    author: str = "some_redditor",
) -> dict[str, Any]:
    return {
        "kind": "t3",
        "data": {
            "title": title,
            "selftext": selftext,
            "subreddit": subreddit,
            "permalink": permalink,
            "url": url,
            "created_utc": created_utc,
            # Included in fixtures deliberately -- proves post_to_finding
            # drops it rather than merely never having seen it.
            "author": author,
        },
    }


# ── Structural read-only guarantee ────────────────────────────────────────────


def test_client_public_surface_is_exactly_search_and_listing() -> None:
    """This client must be structurally incapable of writing to Reddit --
    voting, commenting, posting, or messaging. Assert the public method set
    directly rather than checking a few guessed names."""
    public_methods = {
        name
        for name in dir(RedditClient)
        if not name.startswith("_") and callable(getattr(RedditClient, name))
    }
    assert public_methods == {
        "search",
        "search_all",
        "list_subreddit_posts",
        "list_subreddit_posts_all",
    }


def test_client_has_no_write_verb_methods() -> None:
    for verb in (
        "vote",
        "upvote",
        "downvote",
        "comment",
        "submit",
        "post",
        "reply",
        "message",
        "delete",
        "edit",
        "report",
    ):
        assert not hasattr(RedditClient, verb), f"RedditClient must not have .{verb}()"


# ── fetch_access_token ────────────────────────────────────────────────────────


async def test_fetch_access_token_happy_path_uses_basic_auth() -> None:
    resp = _mock_response(
        200, {"access_token": "tok-123", "token_type": "bearer", "expires_in": 3600}
    )
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_client("post", resp)
        mock_cls.return_value = mock_http
        token = await fetch_access_token(
            client_id="cid", client_secret="csecret", user_agent="test-agent/1.0 (by /u/tester)"
        )
    assert token.access_token == "tok-123"
    assert token.expires_in == 3600
    # Basic Auth, not client_id/secret smuggled into the body -- the
    # divergence from the brief's shorthand this module documents.
    _, kwargs = mock_http.post.call_args
    assert kwargs["auth"] == ("cid", "csecret")
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert "client_id" not in kwargs["data"]
    assert "client_secret" not in kwargs["data"]
    assert kwargs["headers"]["User-Agent"] == "test-agent/1.0 (by /u/tester)"


async def test_fetch_access_token_missing_expires_in_defaults_to_3600() -> None:
    resp = _mock_response(200, {"access_token": "tok-123"})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        token = await fetch_access_token(client_id="cid", client_secret="cs", user_agent="ua")
    assert token.expires_in == 3600


async def test_fetch_access_token_rejected_raises_auth_error() -> None:
    resp = _mock_response(401, {"message": "invalid_grant"})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        with pytest.raises(RedditAuthError):
            await fetch_access_token(client_id="bad", client_secret="bad", user_agent="ua")


async def test_fetch_access_token_network_error_raises_auth_error() -> None:
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = mock_http
        with pytest.raises(RedditAuthError):
            await fetch_access_token(client_id="cid", client_secret="cs", user_agent="ua")


async def test_fetch_access_token_missing_access_token_raises_auth_error() -> None:
    resp = _mock_response(200, {"token_type": "bearer", "expires_in": 3600})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        with pytest.raises(RedditAuthError):
            await fetch_access_token(client_id="cid", client_secret="cs", user_agent="ua")


async def test_fetch_access_token_non_json_response_raises_auth_error() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.side_effect = ValueError("not json")
    resp.text = "<html>not json</html>"
    resp.headers = httpx.Headers({})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        with pytest.raises(RedditAuthError):
            await fetch_access_token(client_id="cid", client_secret="cs", user_agent="ua")


# ── resolve_reddit_config ──────────────────────────────────────────────────────


async def test_resolve_reddit_config_from_db() -> None:
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "client_id": "db-client-id",
            "client_secret": "db-client-secret",
            "user_agent": "db-user-agent",
        }
        cfg = await resolve_reddit_config(mock_session)

    assert cfg.client_id == "db-client-id"
    assert cfg.client_secret == "db-client-secret"
    assert cfg.user_agent == "db-user-agent"


async def test_resolve_reddit_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "env-client-secret")
    monkeypatch.setenv("REDDIT_USER_AGENT", "env-user-agent")

    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        cfg = await resolve_reddit_config(mock_session)

    assert cfg.client_id == "env-client-id"
    assert cfg.client_secret == "env-client-secret"
    assert cfg.user_agent == "env-user-agent"


async def test_resolve_reddit_config_db_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "env-client-id")
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "client_id": "db-client-id",
            "client_secret": "db-secret",
            "user_agent": "db-ua",
        }
        cfg = await resolve_reddit_config(mock_session)
    assert cfg.client_id == "db-client-id"


async def test_resolve_reddit_config_raises_when_missing() -> None:
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_reddit_config(mock_session)
    assert "client_id" in exc_info.value.missing_fields
    assert "client_secret" in exc_info.value.missing_fields
    assert "user_agent" in exc_info.value.missing_fields


async def test_resolve_reddit_config_user_agent_required_even_with_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-Agent is not a nicety here -- Reddit throttles/rejects without one."""
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"client_id": "cid", "client_secret": "cs"}
        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_reddit_config(mock_session)
    assert exc_info.value.missing_fields == ["user_agent"]


# ── search / list_subreddit_posts ──────────────────────────────────────────────


async def test_list_subreddit_posts_happy_path() -> None:
    body = _listing([_post_child(title="Parents worried about voice recordings")], after="t3_next")
    resp = _mock_response(200, body)
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        page = await client.list_subreddit_posts("parenting")
    assert len(page.posts) == 1
    assert page.posts[0]["title"] == "Parents worried about voice recordings"
    assert page.after == "t3_next"


async def test_list_subreddit_posts_sends_bearer_and_user_agent() -> None:
    resp = _mock_response(200, _listing([]))
    client = RedditClient("my-token", "my-agent/1.0")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_client("get", resp)
        mock_cls.return_value = mock_http
        await client.list_subreddit_posts("parenting")
    _, kwargs = mock_http.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer my-token"
    assert kwargs["headers"]["User-Agent"] == "my-agent/1.0"
    assert kwargs["params"]["raw_json"] == 1


async def test_list_subreddit_posts_error_raises_api_error() -> None:
    resp = _mock_response(404, {"message": "subreddit not found"})
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with pytest.raises(RedditAPIError):
            await client.list_subreddit_posts("doesnotexist12345")


async def test_search_global_hits_search_path() -> None:
    resp = _mock_response(200, _listing([_post_child()]))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_client("get", resp)
        mock_cls.return_value = mock_http
        await client.search("voice recording children")
    args, kwargs = mock_http.get.call_args
    assert args[0] == "https://oauth.reddit.com/search"
    assert kwargs["params"]["q"] == "voice recording children"
    assert "restrict_sr" not in kwargs["params"]


async def test_search_scoped_to_subreddit_sets_restrict_sr() -> None:
    resp = _mock_response(200, _listing([]))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_client("get", resp)
        mock_cls.return_value = mock_http
        await client.search("AI chatbot", subreddit="florida")
    args, kwargs = mock_http.get.call_args
    assert args[0] == "https://oauth.reddit.com/r/florida/search"
    assert kwargs["params"]["restrict_sr"] == 1


async def test_search_error_raises_api_error() -> None:
    resp = _mock_response(500, {"message": "internal error"})
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with pytest.raises(RedditAPIError):
            await client.search("anything")


async def test_limit_is_clamped_to_100() -> None:
    resp = _mock_response(200, _listing([]))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_client("get", resp)
        mock_cls.return_value = mock_http
        await client.list_subreddit_posts("parenting", limit=500)
    _, kwargs = mock_http.get.call_args
    assert kwargs["params"]["limit"] == 100


# ── _parse_listing ─────────────────────────────────────────────────────────────


def test_parse_listing_extracts_t3_children_only() -> None:
    data = _listing(
        [
            _post_child(title="A real post"),
            {"kind": "more", "data": {"children": ["x", "y"]}},
        ],
        after="t3_abc",
    )
    page = _parse_listing(data)
    assert len(page.posts) == 1
    assert page.posts[0]["title"] == "A real post"
    assert page.after == "t3_abc"


def test_parse_listing_no_after_is_none() -> None:
    page = _parse_listing(_listing([]))
    assert page.posts == []
    assert page.after is None


def test_parse_listing_malformed_returns_empty_page() -> None:
    assert _parse_listing({}) == RedditListingPage(posts=[], after=None)
    assert _parse_listing({"data": "not-a-dict"}) == RedditListingPage(posts=[], after=None)
    assert _parse_listing({"data": {"children": "not-a-list"}}) == RedditListingPage(
        posts=[], after=None
    )


# ── pagination (list_subreddit_posts_all / search_all) ─────────────────────────


async def test_list_subreddit_posts_all_follows_after_cursor() -> None:
    page1 = _mock_response(200, _listing([_post_child(title="post 1")], after="cursor-2"))
    page2 = _mock_response(200, _listing([_post_child(title="post 2")], after=None))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=[page1, page2])
        mock_cls.return_value = mock_http
        posts = await client.list_subreddit_posts_all("parenting", page_size=1)
    assert [p["title"] for p in posts] == ["post 1", "post 2"]
    assert mock_http.get.await_count == 2
    # Second call carried the cursor from the first page's "after".
    _, kwargs = mock_http.get.await_args_list[1]
    assert kwargs["params"]["after"] == "cursor-2"


async def test_list_subreddit_posts_all_stops_at_max_posts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Every page reports a further cursor -- without the cap this loops forever.
    page = _mock_response(200, _listing([_post_child(title="p")] * 10, after="always-more"))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", page)
        with caplog.at_level(logging.WARNING):
            posts = await client.list_subreddit_posts_all("parenting", max_posts=15, page_size=10)
    assert len(posts) == 20  # two pages of 10, capped/logged after exceeding 15
    assert any("stopped at max_posts" in r.message for r in caplog.records)


async def test_search_all_stops_when_after_repeats() -> None:
    """A page whose 'after' equals the cursor we just sent must not loop forever
    -- simulates Reddit returning a cursor that never advances."""
    page1 = _mock_response(200, _listing([_post_child(title="p1")], after="stuck-cursor"))
    page2 = _mock_response(200, _listing([_post_child(title="p2")], after="stuck-cursor"))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        # A third response would mean the loop failed to stop -- StopIteration
        # on a third call fails the test rather than hanging it.
        mock_http.get = AsyncMock(side_effect=[page1, page2])
        mock_cls.return_value = mock_http
        posts = await client.search_all("query", max_posts=500)
    assert [p["title"] for p in posts] == ["p1", "p2"]
    assert mock_http.get.await_count == 2


async def test_list_subreddit_posts_all_stops_on_empty_page_with_no_after() -> None:
    """An empty page (no posts, no cursor) must stop the loop, not just a
    non-empty final page -- covers the "page.posts is empty" branch."""
    page1 = _mock_response(200, _listing([_post_child(title="only post")], after="cursor-2"))
    page2 = _mock_response(200, _listing([], after=None))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=[page1, page2])
        mock_cls.return_value = mock_http
        posts = await client.list_subreddit_posts_all("parenting", page_size=1)
    assert [p["title"] for p in posts] == ["only post"]
    assert mock_http.get.await_count == 2


async def test_search_all_no_after_stops_after_one_page() -> None:
    page = _mock_response(200, _listing([_post_child()], after=None))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", page)
        posts = await client.search_all("query", max_posts=500)
    assert len(posts) == 1


# ── Rate limiting ──────────────────────────────────────────────────────────────


async def test_rate_limiter_enforces_minimum_interval() -> None:
    limiter = _RedditRateLimiter(min_interval=0.05)
    sleep_calls: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    # monotonic() call order: wait#1 "now" (unused -- no prior request yet),
    # wait#1 "set last_request" -> 100.0, wait#2 "now" -> 100.01 (10ms later,
    # well inside the 50ms floor), wait#2 "set last_request" -> 100.01.
    with (
        patch("asyncio.sleep", new=AsyncMock(side_effect=_record_sleep)),
        patch("time.monotonic", side_effect=[0.0, 100.0, 100.01, 100.01]),
    ):
        await limiter.wait()
        await limiter.wait()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.04, abs=1e-6)


async def test_rate_limiter_observe_schedules_backoff_on_low_remaining() -> None:
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe(httpx.Headers({"x-ratelimit-remaining": "1", "x-ratelimit-reset": "7"}))
    assert limiter._extra_wait == 7.0


async def test_rate_limiter_observe_ignores_healthy_remaining() -> None:
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe(httpx.Headers({"x-ratelimit-remaining": "80", "x-ratelimit-reset": "40"}))
    assert limiter._extra_wait == 0.0


async def test_rate_limiter_observe_missing_headers_is_a_noop() -> None:
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe(httpx.Headers({}))
    assert limiter._extra_wait == 0.0


async def test_rate_limiter_retry_after_wins_over_default_backoff() -> None:
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe_retry_after("30")
    assert limiter._extra_wait == 30.0


async def test_rate_limiter_retry_after_missing_header_is_a_noop() -> None:
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe_retry_after(None)
    assert limiter._extra_wait == 0.0


async def test_rate_limiter_non_numeric_headers_are_ignored() -> None:
    """A header present but not parseable as a float must not crash the sweep."""
    limiter = _RedditRateLimiter(min_interval=0.0)
    limiter.observe(httpx.Headers({"x-ratelimit-remaining": "not-a-number"}))
    assert limiter._extra_wait == 0.0
    limiter.observe_retry_after("not-a-number-either")
    assert limiter._extra_wait == 0.0


async def test_429_response_feeds_retry_after_into_rate_limiter() -> None:
    resp = _mock_response(429, {"message": "too many requests"}, headers={"retry-after": "12"})
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with pytest.raises(RedditAPIError):
            await client.list_subreddit_posts("parenting")
    assert client._rate_limiter._extra_wait == 12.0


# ── post_to_finding ──────────────────────────────────────────────────────────


def test_post_to_finding_shape_matches_national_news_top_level_keys() -> None:
    post = _post_child(
        title="School district pauses AI chatbot over voice recording concerns",
        selftext="Parents are upset that the district is recording student voices.",
        subreddit="florida",
    )["data"]
    finding = post_to_finding(post, state="FL")
    assert finding is not None
    # Same top-level keys as national_news.item_to_finding (minus "lane" --
    # see module docstring for why).
    assert set(finding.keys()) == {
        "sourceType",
        "discoveredBy",
        "state",
        "title",
        "reasonCodes",
        "urgency",
        "evidence",
        "metadata",
    }
    assert finding["sourceType"] == "reddit"
    assert finding["discoveredBy"] == "reddit_scout"
    assert finding["state"] == "FL"
    assert finding["title"] == post["title"]
    assert finding["reasonCodes"] == []
    assert finding["urgency"] == "standard"
    assert "voice recording concerns" in finding["evidence"]
    assert finding["metadata"]["state"] == "FL"
    assert finding["metadata"]["subreddit"] == "florida"
    assert finding["metadata"]["source_type"] == "reddit"
    assert finding["metadata"]["source_name"] == "r/florida"


def test_post_to_finding_defaults_to_national_state() -> None:
    post = _post_child()["data"]
    finding = post_to_finding(post)
    assert finding is not None
    assert finding["state"] == NATIONAL == "US"


def test_post_to_finding_builds_url_from_permalink() -> None:
    post = _post_child(permalink="/r/parenting/comments/xyz/title/")["data"]
    finding = post_to_finding(post, state="US")
    assert finding is not None
    assert (
        finding["metadata"]["source_url"]
        == "https://www.reddit.com/r/parenting/comments/xyz/title/"
    )


def test_post_to_finding_falls_back_to_url_field_when_no_permalink() -> None:
    post = _post_child(permalink="", url="https://example.com/article")["data"]
    finding = post_to_finding(post, state="US")
    assert finding is not None
    assert finding["metadata"]["source_url"] == "https://example.com/article"


def test_post_to_finding_no_title_returns_none() -> None:
    assert post_to_finding({"title": "", "selftext": "x"}) is None
    assert post_to_finding({}) is None


def test_post_to_finding_strips_removed_and_deleted_body() -> None:
    for marker in ("[removed]", "[deleted]"):
        post = _post_child(selftext=marker)["data"]
        finding = post_to_finding(post)
        assert finding is not None
        assert marker not in finding["evidence"]


def test_post_to_finding_formats_created_utc_as_iso() -> None:
    post = _post_child(created_utc=1_700_000_000.0)["data"]
    finding = post_to_finding(post)
    assert finding is not None
    assert finding["metadata"]["published_at"].startswith("2023-11-14")


def test_post_to_finding_bad_created_utc_yields_empty_string() -> None:
    post = _post_child()["data"]
    post["created_utc"] = "not-a-number"
    finding = post_to_finding(post)
    assert finding is not None
    assert finding["metadata"]["published_at"] == ""


def test_post_to_finding_never_carries_author_or_username() -> None:
    """No individual profiling, per the brief -- author must not leak into
    the finding OR its metadata, even though the raw post fixture has one."""
    post = _post_child(author="some_specific_redditor")["data"]
    finding = post_to_finding(post, state="NY")
    assert finding is not None
    serialized = repr(finding)
    assert "some_specific_redditor" not in serialized
    assert "author" not in finding
    assert "author" not in finding["metadata"]


def test_post_to_finding_evidence_truncates_long_body() -> None:
    post = _post_child(selftext="x" * 1000)["data"]
    finding = post_to_finding(post)
    assert finding is not None
    # title + ". " + up to 300 chars of body.
    assert len(finding["evidence"]) <= len(post["title"]) + 2 + 300


# ── gather_subreddit_findings / gather_search_findings (fail-safe) ─────────────


async def test_gather_subreddit_findings_happy_path() -> None:
    resp = _mock_response(
        200,
        _listing(
            [
                _post_child(title="AI is training on our kids voices"),
                _post_child(title="", selftext="no title, dropped"),
            ]
        ),
    )
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        findings = await gather_subreddit_findings(client, "parenting", state="GA")
    assert len(findings) == 1
    assert findings[0]["state"] == "GA"


async def test_gather_subreddit_findings_api_error_is_fail_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resp = _mock_response(503, {"message": "down"})
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with caplog.at_level(logging.WARNING):
            findings = await gather_subreddit_findings(client, "parenting")
    assert findings == []
    assert any("gather_subreddit_findings" in r.message for r in caplog.records)


async def test_gather_subreddit_findings_network_error_is_fail_safe() -> None:
    """The client does not wrap raw httpx exceptions (matches SalesforceClient) --
    the fail-safe wrapper must still catch them and never raise into a sweep."""
    client = RedditClient("tok", "ua")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = mock_http
        findings = await gather_subreddit_findings(client, "parenting")
    assert findings == []


async def test_gather_search_findings_happy_path() -> None:
    resp = _mock_response(200, _listing([_post_child(title="chatbot backlash grows")]))
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        findings = await gather_search_findings(client, "chatbot backlash", state="NM")
    assert len(findings) == 1
    assert findings[0]["state"] == "NM"


async def test_gather_search_findings_error_is_fail_safe() -> None:
    resp = _mock_response(500, {"message": "boom"})
    client = RedditClient("tok", "ua")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        findings = await gather_search_findings(client, "anything")
    assert findings == []
