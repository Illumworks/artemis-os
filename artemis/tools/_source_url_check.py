"""Verify that a signal's source URL is a real page before we store it.

A scout is an LLM agent that emits signals through a tool. Nothing checked that
the URL it supplied corresponded to anything it had actually fetched — so when a
feed came back empty, a run could invent plausible items rather than report zero.

Between 2026-08-10 and 2026-09-02 that produced 149 fabricated signals, all from
``state_doe``, 52 of which were approved past Gate 1. One reached the Thursday
market-signals brief to Josh and Angela claiming Illinois had announced a
partnership with Amira. Julie tried to open the links and found nothing behind
them.

The tell was structural: real Google News article ids are long opaque tokens
(``/rss/articles/CBMikAFBVV95cUxPbWZk…``), while the invented ones were readable
slugs (``/rss/articles/illinois-amira-pilot``). Fetching them settles it
outright — the invented URLs return HTTP 400, the real ones 200.

**Severity split, deliberately.** A definitive 4xx rejects the write: that URL is
not a page, and a signal without a real source is worse than no signal. Anything
ambiguous — a timeout, a 5xx, a 403 from a bot filter, an unreachable network —
does NOT reject; it marks the signal unverified. Dropping real intelligence
because a site was briefly slow would be its own failure, and this check sits in
front of every scout.

**A bare homepage is rejected on shape, before any request is made.** Fetching
proves a URL resolves, not that it identifies the item the signal is about, and
`https://6abc.com` answers 200 all day. In the 45 days to 2026-09-14, **107
signals were stored with a bare-domain source URL** — `https://www.wjtv.com`,
`https://6abc.com`, `https://kval.com` — each one a homepage standing in for an
article. Three of them were links in the brief that day, so a reader clicking
them landed on a newsroom front page and had to go hunting.

They pass the network check perfectly, which is the trap: an agent holding a
Google News redirect it cannot verify can swap in the publisher's homepage and
score a clean 200. The check as written rewarded that. A signal is always about a
specific item — an article, an agenda, an RFP, a bill — so a URL with no path and
no query cannot be its source, whatever it returns. Omitting the URL entirely
stays legitimate; substituting the front door does not.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 12.0

#: Codes that prove the URL does not identify a page. 404/410 are plain; 400 is
#: here because that is exactly what Google News returns for an invented article
#: id, which is the case this exists to catch.
_DEFINITIVE_MISS = {400, 404, 410}


@dataclass(frozen=True)
class UrlVerdict:
    """The outcome of checking one URL."""

    ok: bool
    verified: bool
    reason: str = ""


async def verify_source_url(url: str) -> UrlVerdict:
    """Check that ``url`` resolves to a real page.

    Returns ``ok=False`` only on a definitive miss. Everything else returns
    ``ok=True``, with ``verified`` saying whether we actually confirmed it.
    """
    candidate = (url or "").strip()
    if not candidate:
        # No URL is a legitimate shape for some signals; absence is not a lie.
        return UrlVerdict(ok=True, verified=False, reason="no source url supplied")

    if _is_bare_domain(candidate):
        return UrlVerdict(
            ok=False,
            verified=True,
            reason=(
                "that is a site homepage, not the item — a source URL must point at the "
                "specific article, agenda, filing or bill"
            ),
        )

    try:
        from artemis.egress_guard import EgressBlockedError, async_validate_url
        from artemis.scouts._http import ScoutHttpClient

        try:
            await async_validate_url(candidate)
        except EgressBlockedError as exc:
            return UrlVerdict(ok=False, verified=True, reason=f"blocked by egress policy: {exc}")

        async with ScoutHttpClient(timeout=_TIMEOUT_SECONDS) as http:
            response = await http.get(candidate, follow_redirects=True)

        status = response.status_code
        if status in _DEFINITIVE_MISS:
            return UrlVerdict(
                ok=False,
                verified=True,
                reason=f"the URL returns HTTP {status} — it does not identify a real page",
            )
        if status >= 400:
            # 403 from a bot filter, 5xx from a struggling state site. Real pages
            # behave this way; refusing them would lose genuine intelligence.
            return UrlVerdict(ok=True, verified=False, reason=f"could not confirm (HTTP {status})")
        return UrlVerdict(ok=True, verified=True, reason=f"confirmed (HTTP {status})")
    except Exception as exc:
        logger.debug("source-url verification failed for %s", candidate, exc_info=True)
        return UrlVerdict(
            ok=True, verified=False, reason=f"could not confirm ({type(exc).__name__})"
        )


def _is_bare_domain(url: str) -> bool:
    """Whether ``url`` addresses a site rather than a page on it.

    Checked on the parsed path and query, not by counting slashes, so
    ``https://x.com``, ``https://x.com/`` and ``https://x.com/#top`` are all
    caught. A fragment is not content — the server never sees it — so it does not
    rescue a bare domain. Anything unparseable is left to the network check
    rather than rejected here; this rule should only ever fire on a URL we are
    certain about.
    """
    try:
        parts = urllib.parse.urlparse(url)
    except Exception:
        return False
    if not parts.netloc:
        return False
    return parts.path.strip("/") == "" and not parts.query
