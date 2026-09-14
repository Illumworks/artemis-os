"""Turn a signal's source URL into a link a reader can actually open.

A reader in #market-signals reported on 2026-09-14 that a link did not work.
Checking the brief she was reading: **29 of its 33 links were Google News
redirects** — `news.google.com/rss/articles/CBMi…` — so this was every news item,
not one bad row, and she happened to click the Illinois one.

Those URLs cannot be resolved to the article. Both routes were tried:

- The `CBMi…` blob is no longer a base64-encoded URL; decoding it yields no
  http reference at all.
- Fetching the redirect returns 200 and stays on `news.google.com`, serving a
  581KB JavaScript interstitial with the destination nowhere in the HTML.

And the feed carries nothing better: the `<link>`, and the `<a href>` inside the
description, are both the same redirect. What it does carry reliably is the
headline and the publisher.

So the link becomes a search for the exact headline, which is a plain URL with
nothing to resolve and nothing to break. It costs the reader one click more than a
working direct link and infinitely fewer than a broken one.

**Not verified end to end, and worth saying so.** Google returned 429 to the
automated check, which is a datacentre request being rate-limited rather than
evidence about a browser. What IS established is the negative: the current link
does not reach the article, by two independent routes.
"""

from __future__ import annotations

import urllib.parse

#: Hosts whose links are redirects we cannot resolve. Matched on the host so a
#: regional Google News domain does not slip past a string check on the path.
_UNRESOLVABLE_HOSTS: frozenset[str] = frozenset({"news.google.com"})


def is_unresolvable(url: str) -> bool:
    """True when this URL will not take a reader to the article."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return host in _UNRESOLVABLE_HOSTS or host.removeprefix("www.") in _UNRESOLVABLE_HOSTS


def search_url(headline: str) -> str:
    """A search for the exact headline. Static, and resolves for a human."""
    return "https://news.google.com/search?q=" + urllib.parse.quote_plus(headline.strip())


def slack_link(url: str | None, label: str, headline: str | None = None) -> str:
    """Slack mrkdwn for a source link, or bare text when there is nothing to link.

    `label` is what the reader sees — usually the publisher. `headline` is what a
    search falls back to; without one there is nothing to search for, so the
    label is returned unlinked rather than pointing somewhere that does not work.
    """
    label = (label or "").strip()
    url = (url or "").strip()
    if not url:
        return label
    if is_unresolvable(url):
        if not (headline or "").strip():
            return label
        return f"<{search_url(headline or '')}|{label}>"
    return f"<{url}|{label}>"
