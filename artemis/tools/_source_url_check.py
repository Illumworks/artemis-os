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
"""

from __future__ import annotations

import logging
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
