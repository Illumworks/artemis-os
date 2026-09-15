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


def split_google_title(title: str) -> tuple[str, str]:
    """Split a Google News title into its headline and its publisher.

    Google News appends the outlet to every title — "Illinois State Board of
    Education issues AI guidance, written with help from AI - Capitol News
    Illinois". The suffix is not part of the article's own headline, so leaving it
    inside a quoted search actively prevents the match it is meant to make. It is
    exactly what we want as the link's LABEL, though.

    Splits on the LAST " - " so a headline containing its own dash survives.
    Returns ``(headline, publisher)``, with publisher ``""`` when there is no
    recognisable suffix — some feeds omit it, and a wrong guess here would cut a
    real headline in half.
    """
    text = (title or "").strip()
    for sep in (" - ", " – ", " — "):
        head, found, tail = text.rpartition(sep)
        if not found or not head.strip() or not tail.strip():
            continue
        # A publisher is a short name, never a sentence. Anything long is far
        # more likely to be part of the headline itself.
        if len(tail) <= 45 and tail.count(" ") <= 6:
            return head.strip(), tail.strip()
    return text, ""


def publisher_host(domain: str | None) -> str:
    """The bare host from a publisher domain, or "" — `www.` kept, scheme dropped."""
    raw = (domain or "").strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = "//" + raw
    try:
        return urllib.parse.urlparse(raw).netloc.strip().lower()
    except Exception:
        return ""


def search_url(headline: str, domain: str | None = None) -> str:
    """A search for the exact headline, narrowed to the publisher when known.

    The feed names the publisher on every item, so a search can be scoped with
    `site:` instead of hunting the whole web for a headline that a dozen outlets
    may have run. Without a domain it falls back to the headline alone.
    """
    terms = f'"{headline.strip()}"'
    host = publisher_host(domain)
    if host:
        terms = f"{terms} site:{host}"
    return "https://news.google.com/search?q=" + urllib.parse.quote_plus(terms)


def slack_link(
    url: str | None,
    label: str,
    headline: str | None = None,
    domain: str | None = None,
) -> str:
    """Slack mrkdwn for a source link, or bare text when there is nothing to link.

    `label` is what the reader sees — the publisher where we know it. `headline`
    and `domain` build the fallback search; without a headline there is nothing to
    search for, so the label is returned unlinked rather than pointing somewhere
    that does not work.
    """
    label = (label or "").strip()
    url = (url or "").strip()
    if not url:
        return label
    if is_unresolvable(url):
        if not (headline or "").strip():
            return label
        return f"<{search_url(headline or '', domain)}|{label}>"
    return f"<{url}|{label}>"
