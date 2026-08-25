"""Reddit OAuth client + normalization for the parent-sentiment / narrative watch.

See ``briefs/parent-sentiment-watch.md``. Reddit returns HTTP 403 to every
unauthenticated request, including ``old.reddit.com`` (confirmed 2026-08-20;
do not re-probe this) -- a free OAuth app is required. Jon has not created the
app yet, so NOTHING in this module has been exercised against live Reddit.
Every test below mocks HTTP. This module makes no claim about behavior Reddit
has not been observed to actually produce.

Auth flow: OAuth 2.0 Client Credentials (server-to-server, no user redirect;
same grant type as ``artemis.integrations.salesforce.client``, but the
credential-transport detail differs -- see ``fetch_access_token`` for why this
uses HTTP Basic Auth rather than body params):

    POST https://www.reddit.com/api/v1/access_token
      grant_type=client_credentials   (body)
      Authorization: Basic base64(client_id:client_secret)   (header)
      User-Agent: <required, descriptive>
    -> {"access_token": ..., "token_type": "bearer", "expires_in": 3600, ...}

Read calls go through ``https://oauth.reddit.com/...`` with
``Authorization: Bearer <token>`` and the SAME descriptive User-Agent (Reddit
throttles/blocks generic or missing User-Agents on every endpoint, not just
the token exchange). Source:
https://github.com/reddit-archive/reddit/wiki/OAuth2#application-only-oauth

Structural shape follows ``artemis.integrations.salesforce.client`` on
purpose (per brief): ``RedditClient`` takes an already-fetched access token
and does no config lookup, no token refresh scheduling, and no caching of its
own -- composition (resolve config -> fetch token -> reuse across a sweep)
is the future caller's job, exactly as Salesforce's is delegated to
``artemis.marketing.salesforce_suppression`` rather than living in
``salesforce/client.py``. Unlike Salesforce (where a fresh token is cheap
because the client is only ever called from a low-volume send-suppression
check), a Reddit sweep may call this client dozens of times per run, so a
future caller MUST cache the token for its ``expires_in`` window (~1 hour)
and refresh proactively -- fetching a new token per API call would double
outbound requests against the same free-tier budget this module is trying to
protect, and would also stress the token endpoint's own (tighter,
separately-documented) rate limit.

Scope guardrails baked into this module (see brief "Scope guardrails"):
  - Read-only, public content only: exactly two GET operations exist,
    ``search`` and ``list_subreddit_posts`` (+ their pagination variants).
    There is no vote/comment/post/message method, and none should ever be
    added here.
  - No individual profiling: ``post_to_finding`` captures title, self-text,
    subreddit, timestamp and URL only. It deliberately does NOT read or carry
    the post author anywhere, even into ``metadata`` -- no per-user history,
    no username aggregation. This is a company under scrutiny for how it
    handles children's data; the monitoring must not become the next story.
  - Fail-safe: the low-level client methods raise typed errors (matching
    Salesforce's contract, for a caller that wants to distinguish failure
    modes), but ``gather_search_findings`` / ``gather_subreddit_findings``
    catch everything and return ``[]`` + a log line -- a sweep must never
    abort because one subreddit or query failed.

Normalization note: ``post_to_finding`` produces the same top-level shape as
``artemis.screentime.national_news.item_to_finding`` (sourceType,
discoveredBy, state, title, reasonCodes, urgency, evidence, metadata) so a
shared downstream (topic gate / dedup / storage) can treat a Reddit finding
identically to a news finding, per the brief. Two DELIBERATE differences from
that reference, both explained at the call site below: no ``lane`` key
(screentime's brand/policy retrieval-provenance split has no Reddit analogue
here), and ``reasonCodes`` is always ``[]`` (theme matching is the job of the
not-yet-built theme layer, which reads ``evidence``/``title`` text -- this
module only discovers and normalizes, exactly as ``national_news.py`` does
for its own reason codes vs. stance classification split).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import MissingProviderConfigError

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"

# Reddit's free ("script"/"web app") OAuth tier is documented as roughly 100
# queries/minute per client id (OAuth2 wiki + api-rules). Paced to a comfortable
# fraction of that by default; ``observe()`` tightens further whenever a
# response's own X-Ratelimit-Remaining says we're close to the real ceiling,
# so this default is a floor, not a promise Reddit itself makes.
_DEFAULT_MIN_INTERVAL_SECONDS = 60.0 / 90.0
# Start backing off before the response headers say we've actually hit zero.
_LOW_REMAINING_THRESHOLD = 5.0
# Used when Reddit signals "back off" but gives no numeric reset/Retry-After.
_DEFAULT_BACKOFF_SECONDS = 2.0

NATIONAL = "US"  # No US state named in the text/subreddit -- mirrors
# artemis.screentime.national_news.NATIONAL's value and meaning, defined
# locally rather than imported: artemis/screentime is a different, actively
# edited module this task must not couple to.

_SOURCE_TYPE = "reddit"
_DISCOVERED_BY = "reddit_scout"

# selftext bodies Reddit itself substitutes for removed/deleted content --
# never usable evidence text.
_EMPTY_BODY_MARKERS = frozenset({"[removed]", "[deleted]"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RedditAuthError(Exception):
    """The Client Credentials token exchange itself failed.

    Callers must treat this like any other "Reddit unavailable" failure and
    fail safe (empty result), never as "no matches".
    """


class RedditAPIError(Exception):
    """A Reddit REST call (search / listing) returned a non-2xx response."""

    def __init__(self, operation: str, status: int, detail: str = "") -> None:
        super().__init__(f"Reddit {operation} failed ({status}): {detail[:200]}")
        self.operation = operation
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------
# DB-first / env-fallback, matching artemis.integrations.config_resolver's
# resolve_salesforce_config pattern exactly (same repo.get_provider_config
# call, same per-field fallback, same MissingProviderConfigError contract).
# Deliberately implemented HERE rather than added to config_resolver.py: this
# task's brief scopes file creation/edits to artemis/sentiment/** only. A
# follow-up slice can move this function verbatim into config_resolver.py
# alongside the other resolve_*_config functions for full consistency --
# nothing here depends on that move happening first.


@dataclass(frozen=True)
class RedditConfig:
    client_id: str
    client_secret: str
    # Reddit requires a descriptive User-Agent on every call (token exchange
    # included) -- see fetch_access_token. Required here, not merely a nicety.
    user_agent: str


async def resolve_reddit_config(session: AsyncSession) -> RedditConfig:
    """Resolve Reddit OAuth credentials: DB per-field, then env per-field fallback.

    Reads the "reddit" provider row via ``artemis.integrations.repository``
    (the same ``integration_configs`` table every other provider uses), then
    ``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET`` / ``REDDIT_USER_AGENT``.

    Raises MissingProviderConfigError if any required field is absent from
    both sources. Credentials are never read at import time -- only inside
    this function, which requires a session to call.
    """
    stored = await repo.get_provider_config(session, "reddit") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "REDDIT_CLIENT_SECRET", ""
    )
    user_agent = str(stored.get("user_agent") or "") or os.environ.get("REDDIT_USER_AGENT", "")

    missing = [
        name
        for name, val in [
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("user_agent", user_agent),
        ]
        if not val
    ]
    if missing:
        raise MissingProviderConfigError("reddit", missing)

    return RedditConfig(client_id=client_id, client_secret=client_secret, user_agent=user_agent)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedditToken:
    access_token: str
    # Seconds until expiry, per Reddit's token response (typically 3600).
    # Callers own caching/refresh scheduling -- this dataclass just carries
    # what Reddit told us so a caller CAN cache correctly; it caches nothing
    # itself, matching SalesforceToken's shape.
    expires_in: int


async def fetch_access_token(*, client_id: str, client_secret: str, user_agent: str) -> RedditToken:
    """Exchange client_id/client_secret for a bearer token (Client Credentials grant).

    IMPORTANT divergence from the brief's shorthand and from Salesforce's own
    Client Credentials flow: Reddit authenticates the client via HTTP Basic
    Auth (client_id as username, client_secret as password) in the request
    header, NOT by putting client_id/client_secret in the POST body. Verified
    against Reddit's own documented curl example:
    https://github.com/reddit-archive/reddit/wiki/OAuth2#application-only-oauth
    -- ``curl -X POST -d 'grant_type=client_credentials' --user
    'client_id:client_secret' https://www.reddit.com/api/v1/access_token``.
    Trusting the documentation over the brief's paraphrase here, as directed.

    A descriptive User-Agent is REQUIRED on this call (and every other Reddit
    call) -- Reddit's API rules throttle or reject generic/missing
    User-Agents. Recommended shape: ``"<platform>:<app id>:<version> (by
    /u/<reddit username>)"``.

    Raises RedditAuthError on ANY failure: network error, non-2xx response, or
    a 2xx response missing ``access_token``. Never returns a
    partially-populated token. NOT exercised against live Reddit (no OAuth
    app exists yet) -- every test mocks the HTTP layer.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                headers={"User-Agent": user_agent},
            )
    except httpx.HTTPError as exc:
        raise RedditAuthError(f"token exchange network error: {exc}") from exc

    if not resp.is_success:
        raise RedditAuthError(f"token exchange rejected ({resp.status_code}): {resp.text[:200]}")

    try:
        body: dict[str, Any] = resp.json()
    except Exception as exc:
        raise RedditAuthError(f"token exchange returned non-JSON response: {exc}") from exc

    access_token = str(body.get("access_token") or "")
    if not access_token:
        raise RedditAuthError("token exchange response missing access_token")

    expires_in_raw = body.get("expires_in")
    expires_in = int(expires_in_raw) if isinstance(expires_in_raw, int | float) else 3600
    return RedditToken(access_token=access_token, expires_in=expires_in)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class _RedditRateLimiter:
    """Proactive pacing + reactive backoff from Reddit's X-Ratelimit-* headers.

    Two layers, both "be a good citizen" per the brief rather than a promise
    Reddit documents precisely:
      1. A floor minimum interval between requests (paced under the ~100
         req/min free-tier ceiling), applied on every call regardless of
         headers.
      2. After each response, ``observe()`` reads
         ``X-Ratelimit-Remaining``/``X-Ratelimit-Reset`` (when present -- Reddit
         does not guarantee they are) and schedules extra backoff before the
         NEXT call once we're close to exhausting the current window, rather
         than waiting to be hit with a 429.
    """

    def __init__(self, min_interval: float = _DEFAULT_MIN_INTERVAL_SECONDS) -> None:
        self._min_interval = min_interval
        self._last_request: float | None = None
        self._extra_wait: float = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        floor_wait = 0.0
        if self._last_request is not None:
            floor_wait = self._min_interval - (now - self._last_request)
        wait_for = max(floor_wait, self._extra_wait)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        self._extra_wait = 0.0
        self._last_request = time.monotonic()

    def observe(self, headers: httpx.Headers) -> None:
        """Read rate-limit response headers and schedule backoff if we're close to the ceiling."""
        remaining = _parse_float(headers.get("x-ratelimit-remaining"))
        reset_seconds = _parse_float(headers.get("x-ratelimit-reset"))
        if remaining is not None and remaining <= _LOW_REMAINING_THRESHOLD:
            self._extra_wait = (
                reset_seconds if reset_seconds is not None else _DEFAULT_BACKOFF_SECONDS
            )
            logger.info(
                "reddit rate limit low (remaining=%s) -- backing off %.1fs before next call",
                remaining,
                self._extra_wait,
            )

    def observe_retry_after(self, retry_after_header: str | None) -> None:
        """A 429 response's Retry-After (seconds) always wins over guesswork."""
        seconds = _parse_float(retry_after_header)
        if seconds is not None:
            self._extra_wait = max(self._extra_wait, seconds)


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedditListingPage:
    """One page of posts (``t3`` listing children) plus Reddit's pagination cursor."""

    posts: list[dict[str, Any]]
    after: str | None


class RedditClient:
    """Read-only Reddit REST API surface: search + subreddit listing, nothing else.

    Structurally read-only, on purpose, like ``SalesforceClient``: the public
    method set is exactly ``search``, ``search_all``, ``list_subreddit_posts``,
    ``list_subreddit_posts_all``. There is no vote/comment/post/submit/message
    method anywhere in this class, and none should ever be added -- this
    module exists to WATCH public discourse, never to participate in it.

    No constructor dependency on stored credentials or on a DB session -- it
    takes an already-fetched access token and a caller-supplied User-Agent, so
    this class itself never touches config storage or the OAuth token
    endpoint. Composition (resolve_reddit_config -> fetch_access_token ->
    RedditClient, cached across a sweep) is the future caller's job. See the
    module docstring.
    """

    def __init__(
        self,
        access_token: str,
        user_agent: str,
        *,
        rate_limiter: _RedditRateLimiter | None = None,
    ) -> None:
        self._access_token = access_token
        self._user_agent = user_agent
        self._rate_limiter = rate_limiter or _RedditRateLimiter()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": self._user_agent,
        }

    async def _get(self, path: str, params: dict[str, Any], *, operation: str) -> dict[str, Any]:
        await self._rate_limiter.wait()
        url = f"{_API_BASE}{path}"
        # raw_json=1: without it Reddit HTML-entity-encodes punctuation in
        # titles/selftext (e.g. "&amp;", "&#39;") -- undocumented outside a
        # long-standing community FAQ, but consistently true in practice.
        full_params = {**params, "raw_json": 1}
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers=self._headers(), params=full_params)
        self._rate_limiter.observe(resp.headers)
        if resp.status_code == 429:
            self._rate_limiter.observe_retry_after(resp.headers.get("retry-after"))
        if not resp.is_success:
            raise RedditAPIError(operation, resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return data

    async def search(
        self,
        query: str,
        *,
        subreddit: str | None = None,
        sort: str = "new",
        time_filter: str = "all",
        limit: int = 25,
        after: str | None = None,
    ) -> RedditListingPage:
        """Search Reddit for *query* -- global, or restricted to one subreddit.

        Wraps ``GET /search`` (global) or ``GET /r/{subreddit}/search``
        (``restrict_sr=1``). *sort*: relevance | hot | top | new | comments.
        *time_filter* (Reddit's ``t`` param): hour | day | week | month | year
        | all. *limit* is clamped to Reddit's own per-request ceiling of 100.

        Raises RedditAPIError for a non-2xx response. NOT exercised against
        live Reddit -- see module docstring.
        """
        limit = max(1, min(limit, 100))
        params: dict[str, Any] = {"q": query, "sort": sort, "t": time_filter, "limit": limit}
        if after:
            params["after"] = after

        if subreddit:
            path = f"/r/{subreddit}/search"
            params["restrict_sr"] = 1
            operation = f"search:r/{subreddit}"
        else:
            path = "/search"
            operation = "search:all"

        data = await self._get(path, params, operation=operation)
        return _parse_listing(data)

    async def list_subreddit_posts(
        self,
        subreddit: str,
        *,
        sort: str = "new",
        limit: int = 25,
        after: str | None = None,
    ) -> RedditListingPage:
        """List a public subreddit's posts. Default ``sort="new"`` (most recent first).

        Wraps ``GET /r/{subreddit}/{sort}``. *sort*: new | hot | top | rising |
        controversial (any value Reddit accepts as a listing path segment).

        Raises RedditAPIError for a non-2xx response -- including a
        private/quarantined/banned/nonexistent subreddit, which the caller
        must treat as a source outage for that subreddit, not "no results".
        NOT exercised against live Reddit -- see module docstring.
        """
        limit = max(1, min(limit, 100))
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        data = await self._get(
            f"/r/{subreddit}/{sort}", params, operation=f"listing:r/{subreddit}/{sort}"
        )
        return _parse_listing(data)

    async def _collect_pages(
        self,
        fetch_page: Callable[[str | None], Awaitable[RedditListingPage]],
        *,
        max_posts: int,
        label: str,
    ) -> list[dict[str, Any]]:
        """Follow ``after`` cursors to completion. Mirrors SalesforceClient.query_all's
        role: the single-page methods above are right for a caller that pages
        itself; this is for a caller that wants everything up to a safety cap
        in one call (e.g. the brief's one-off deep initial-report scan).

        ``max_posts`` is a safety stop, not an expectation -- a runaway
        listing ends with a loud log line rather than paging forever.
        """
        out: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            page = await fetch_page(after)
            if page.posts:
                out.extend(page.posts)
            if len(out) >= max_posts:
                logger.warning(
                    "%s: stopped at max_posts=%d -- result truncated. "
                    "Narrow the query/subreddit rather than raising the cap.",
                    label,
                    max_posts,
                )
                break
            if not page.after or page.after == after:
                break
            after = page.after
        return out

    async def list_subreddit_posts_all(
        self,
        subreddit: str,
        *,
        sort: str = "new",
        max_posts: int = 500,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Page ``list_subreddit_posts`` to completion (bounded by *max_posts*)."""

        async def _fetch(after: str | None) -> RedditListingPage:
            return await self.list_subreddit_posts(
                subreddit, sort=sort, limit=page_size, after=after
            )

        return await self._collect_pages(
            _fetch, max_posts=max_posts, label=f"list_subreddit_posts_all(r/{subreddit})"
        )

    async def search_all(
        self,
        query: str,
        *,
        subreddit: str | None = None,
        sort: str = "new",
        time_filter: str = "all",
        max_posts: int = 500,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Page ``search`` to completion (bounded by *max_posts*)."""

        async def _fetch(after: str | None) -> RedditListingPage:
            return await self.search(
                query,
                subreddit=subreddit,
                sort=sort,
                time_filter=time_filter,
                limit=page_size,
                after=after,
            )

        label = f"search_all({query!r}, r/{subreddit or 'all'})"
        return await self._collect_pages(_fetch, max_posts=max_posts, label=label)


def _parse_listing(data: dict[str, Any]) -> RedditListingPage:
    """Parse a Reddit ``Listing`` response into posts + the next-page cursor.

    Never raises -- a malformed/unexpected shape yields an empty page rather
    than blowing up a caller that expects this to be fail-safe. Only ``t3``
    (link/post) children are kept; a trailing ``more`` stub carries no post
    data and is dropped.
    """
    try:
        listing_data = data.get("data") or {}
        children = listing_data.get("children") or []
        posts = [
            child["data"]
            for child in children
            if isinstance(child, dict)
            and child.get("kind") == "t3"
            and isinstance(child.get("data"), dict)
        ]
        after_raw = listing_data.get("after")
        after = str(after_raw) if after_raw else None
        return RedditListingPage(posts=posts, after=after)
    except (AttributeError, TypeError, KeyError) as exc:
        logger.warning("reddit._parse_listing: malformed listing response -- %s", exc)
        return RedditListingPage(posts=[], after=None)


# ---------------------------------------------------------------------------
# Normalization -- Reddit post -> canonical raw-finding-dict shape
# ---------------------------------------------------------------------------


def post_to_finding(post: dict[str, Any], *, state: str = NATIONAL) -> dict[str, Any] | None:
    """Normalize one Reddit post (a listing child's ``data`` dict) into the
    canonical raw-finding shape used by ``artemis.screentime.national_news.item_to_finding``
    -- see the module docstring for the exact fields matched and the two
    deliberate differences (no ``lane``, ``reasonCodes`` always empty).

    *state* is supplied by the CALLER -- whichever subreddit or search query
    produced this post (e.g. a named-state subreddit for the GA/NY/FL/NM deep
    scan, or ``NATIONAL`` for the lighter national sweep) -- the same
    provenance-not-re-derived-from-text approach ``item_to_finding`` uses for
    its ``state_abbr`` parameter. UNLIKE news headlines, a Reddit post rarely
    names a US state explicitly enough for reliable text-based resolution, so
    no content-based state re-attribution is attempted here (a KNOWN GAP --
    see this module's test file and the final report for detail).

    Returns None for a post with no usable title -- nothing worth storing.
    """
    title = str(post.get("title") or "").strip()
    if not title:
        return None

    selftext = str(post.get("selftext") or "").strip()
    if selftext in _EMPTY_BODY_MARKERS:
        selftext = ""

    subreddit = str(post.get("subreddit") or "").strip()
    permalink = str(post.get("permalink") or "").strip()
    source_url = (
        f"https://www.reddit.com{permalink}" if permalink else str(post.get("url") or "").strip()
    )
    published_at = _format_created_utc(post.get("created_utc"))

    return {
        "sourceType": _SOURCE_TYPE,
        "discoveredBy": _DISCOVERED_BY,
        "state": state,
        "title": title,
        # Theme matching (voice_recording / training_ai_on_children / etc.) is
        # the not-yet-built theme layer's job -- it reads evidence/title text
        # downstream. This source only discovers + normalizes.
        "reasonCodes": [],
        "urgency": "standard",
        "evidence": f"{title}. {selftext[:300]}".strip(". "),
        "metadata": {
            "state": state,
            "subreddit": subreddit,
            "source_url": source_url,
            "published_at": published_at,
            "source_name": f"r/{subreddit}" if subreddit else "Reddit",
            "source_type": _SOURCE_TYPE,
            # No author/username anywhere in this dict -- see module docstring
            # "No individual profiling".
        },
    }


def _format_created_utc(value: Any) -> str:
    """Best-effort ISO-8601 UTC string from Reddit's ``created_utc`` epoch float. '' on failure."""
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Fail-safe gather wrappers -- never raise into a sweep.
# ---------------------------------------------------------------------------
# Mirrors artemis.screentime.national_news's two-tier design: the client
# methods above raise typed errors for a caller that wants to distinguish
# failure modes; these wrappers catch everything (API errors, raw httpx
# network exceptions -- the client does not wrap those, matching
# SalesforceClient's contract) and degrade to [] + a log line, exactly the
# brief's "an error returns an empty list and logs; it never raises into a
# sweep" rule.


async def gather_subreddit_findings(
    client: RedditClient,
    subreddit: str,
    *,
    state: str = NATIONAL,
    sort: str = "new",
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Fetch + normalize one subreddit's recent posts. Fail-safe: [] on any error."""
    try:
        page = await client.list_subreddit_posts(subreddit, sort=sort, limit=limit)
    except Exception as exc:
        logger.warning("gather_subreddit_findings(r/%s): error -- %s", subreddit, exc)
        return []
    return [f for post in page.posts if (f := post_to_finding(post, state=state)) is not None]


async def gather_search_findings(
    client: RedditClient,
    query: str,
    *,
    subreddit: str | None = None,
    state: str = NATIONAL,
    sort: str = "new",
    time_filter: str = "all",
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search + normalize. Fail-safe: [] on any error."""
    try:
        page = await client.search(
            query, subreddit=subreddit, sort=sort, time_filter=time_filter, limit=limit
        )
    except Exception as exc:
        logger.warning("gather_search_findings(query=%r): error -- %s", query, exc)
        return []
    return [f for post in page.posts if (f := post_to_finding(post, state=state)) is not None]
