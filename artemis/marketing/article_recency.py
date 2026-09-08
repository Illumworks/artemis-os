"""Reject news articles that are too old to be a current signal.

Julie and Jamie found it by reading the channel: a Michigan article from
February, a Kansas one from March, and a Los Angeles one from **2024**, all
presented as current market signals.

Nothing was filtering them, and nothing could have been. `source_published_at`
is captured by the scout, carried the whole way through `normalize_intake_payload`,
and then simply not written at the final INSERT -- so the queue had no idea how
old anything was. The same shape as the Starbridge due date: read, used to build
the object, dropped at persist.

**Why this is narrower than "reject anything old".** A statute passed in February
is still a live fact, and an RFP posted sixty days ago can close next week. What
is never a current signal is a NEWS ARTICLE about an event that already happened
months ago, because news is the one source type whose whole claim is recency. So
the window applies to news and leaves the rest alone.

**A missing date is not a pass.** It is recorded as unknown and let through, since
refusing everything undated would empty the queue, but it is counted so the gap is
visible rather than assumed away.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

#: How old a news article may be and still count as a signal. Six weeks is
#: generous on purpose: a superintendent hired five weeks ago is genuinely still
#: a live opening, and the articles that prompted this were 6, 26 and 60+ weeks
#: old. Tighten it if the channel still feels stale, but not below a month --
#: local education reporting often surfaces late.
MAX_ARTICLE_AGE_DAYS = 42

#: Source types whose value IS their recency. Everything else (RFPs with their own
#: deadlines, board minutes, statutes) is judged on its own dates, not this one.
NEWS_SOURCE_TYPES: frozenset[str] = frozenset(
    {"news_article", "news", "regional_news", "news_api", "rss"}
)

NEWS_DISCOVERERS: frozenset[str] = frozenset(
    {"regional_news", "regional_news_scout", "news_api", "screentime_news"}
)


@dataclass(frozen=True)
class RecencyVerdict:
    """Three outcomes, and "unknown" is deliberately not "fresh"."""

    verdict: str  # "fresh" | "stale" | "unknown" | "not_news"
    age_days: int | None = None
    published: str | None = None

    @property
    def should_reject(self) -> bool:
        return self.verdict == "stale"

    def reason(self) -> str:
        if self.verdict == "stale":
            return (
                f"article published {self.published} is {self.age_days} days old, "
                f"past the {MAX_ARTICLE_AGE_DAYS}-day window for news signals"
            )
        return self.verdict


def parse_published(raw: str | None) -> date | None:
    """Parse the many shapes a publication date arrives in, or return None.

    Deliberately forgiving about format and strict about the result: a string we
    cannot read becomes None (unknown), never today's date. Defaulting an
    unparseable date to "now" would mark every stale article fresh, which is the
    failure this module exists to prevent.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None

    # ISO first, including the trailing Z that feeds and APIs both emit.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    # RFC 2822, which is what RSS pubDate uses.
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:24], fmt).date()
        except ValueError:
            continue
    logger.debug("article_recency: could not parse published date %r", raw)
    return None


def assess(
    *,
    published_at: str | None,
    source_type: str | None = None,
    discovered_by: str | None = None,
    today: date | None = None,
) -> RecencyVerdict:
    """Is this news article recent enough to be a signal?"""
    is_news = (source_type or "").strip().lower() in NEWS_SOURCE_TYPES or (
        discovered_by or ""
    ).strip().lower() in NEWS_DISCOVERERS
    if not is_news:
        return RecencyVerdict("not_news")

    published = parse_published(published_at)
    if published is None:
        return RecencyVerdict("unknown", published=published_at or None)

    age = ((today or datetime.now(UTC).date()) - published).days
    # A future date is a parsing artefact or a bad feed, not a fresh article.
    # Treated as unknown rather than fresh so it cannot sneak past on a typo.
    if age < 0:
        return RecencyVerdict("unknown", age_days=age, published=published.isoformat())
    if age > MAX_ARTICLE_AGE_DAYS:
        return RecencyVerdict("stale", age_days=age, published=published.isoformat())
    return RecencyVerdict("fresh", age_days=age, published=published.isoformat())
