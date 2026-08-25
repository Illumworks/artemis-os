"""Brand Signals — the daily read Callie posts for Angela's team.

What this answers, in Angela's words: what are parents saying about AI in
schools, which of it is pointed at Amira, and *where else are these outcries
growing*.

Two lanes, and the split is the whole design:

* **Vendor lane** — anything naming Amira or a named competitor. A brand match
  ALONE keeps the item; themes only classify it. This lane exists because the
  first build didn't have it: every query asked "who is complaining about
  ed-tech in state X" and none asked "who is writing about us", so a dozen
  Amira-named stories scored as zero.
* **Category lane** — per-state sweeps for the narratives themselves, which
  need a theme match AND ed-tech context to count.

Attribution is deliberately conservative. A story resolves to a state only if
it names the state, or names a district prominent enough to be recognised
without one (``screentime.gazetteer``). Everything else is reported as national
rather than guessed into a state — a wrong state moves a signal into a market
we are not actually being attacked in, which is worse than an honest "national".
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

from artemis.screentime.gazetteer import find_places, load_place_index
from artemis.screentime.national_news import (
    NATIONAL,
    STATE_NAMES,
    parse_news_rss,
    resolve_state,
)
from artemis.sentiment.themes import (
    THEME_INSTITUTIONAL_REJECTION,
    THEME_PARENT_OBJECTION,
    has_tech_context,
    is_amira_specific,
    match_themes,
)

_log = logging.getLogger(__name__)

LOOKBACK_DAYS = 120

# Most new items to list in one brief. Kept small because the brief is meant to
# be read: a day-late story is fine, an unreadable wall is not.
NEW_ITEMS_PER_BRIEF = 15
_BASE = "https://news.google.com/rss/search"

# States we sweep with both query shapes rather than one. Named internally as
# live: New Mexico (statewide), Georgia, Florida, plus New York for volume.
DEEP_STATES = ("NM", "GA", "FL", "NY")

VENDOR_QUERIES: tuple[str, ...] = (
    '"Amira Learning" when:{d}d',
    "Amira reading schools AI when:{d}d",
    '"i-Ready" OR "iReady" schools parents when:{d}d',
    "Lexia OR Amplify OR mCLASS OR DIBELS schools parents AI when:{d}d",
    '"reading assessment" AI schools parents backlash when:{d}d',
    "AI reading program school board vote when:{d}d",
)

CATEGORY_SHAPES: tuple[str, ...] = (
    '{name} schools parents ("artificial intelligence" OR "AI tutor" OR "reading app") when:{d}d',
    '{name} schools ("student data privacy" OR "voice recording" OR "recording students"'
    ' OR "AI assessment") when:{d}d',
)

# Plain-English labels. The raw theme keys are for code, not for Angela.
THEME_LABELS: dict[str, str] = {
    THEME_INSTITUTIONAL_REJECTION: "district/board pushback",
    THEME_PARENT_OBJECTION: "parent objection",
    "privacy_surveillance": "data privacy",
    "screen_time_harm": "screen time",
    "voice_recording": "voice recording",
    "training_ai_on_children": "training AI on kids",
    "is_a_chatbot": '"just a chatbot"',
}


def _published(item: dict[str, Any]) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(str(item.get("published") or ""))
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _attribute(text: str, query_state: str, place_index: dict[str, str]) -> str:
    """State for *text*, preferring an explicit state name over the gazetteer."""
    state, _confidence = resolve_state(text, query_state)
    if state != NATIONAL:
        return state
    places = find_places(text, place_index)
    return next(iter(places)) if len(places) == 1 else NATIONAL


async def _fetch(http: Any, query: str) -> list[dict[str, Any]]:
    try:
        response = await http.get(f"{_BASE}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en")
    except Exception:  # noqa: BLE001 — one dead feed must not kill the brief
        _log.warning("brand_signals: feed failed for %r", query, exc_info=True)
        return []
    if getattr(response, "status_code", 0) != 200:
        return []
    return parse_news_rss(response.text)


async def gather_brand_signals(
    http: Any, place_index: dict[str, str], *, lookback_days: int = LOOKBACK_DAYS
) -> list[dict[str, Any]]:
    """Run both lanes and return deduplicated, dated, attributed findings."""
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def keep(item: dict[str, Any], lane: str, query_state: str) -> None:
        link = str(item.get("link") or "")
        title = str(item.get("title") or "").strip()
        if not link or not title or link in seen:
            return
        when = _published(item)
        if when is None or when < cutoff:
            return
        text = f"{title} {item.get('summary', '')}"
        themes = match_themes(text)
        if lane == "vendor":
            if not is_amira_specific(text):
                return
        else:
            if not themes or not has_tech_context(text):
                return
            # Objection with no ed-tech in the HEADLINE is usually a summary
            # coincidence; the title is what a reader actually judges it by.
            if themes == {THEME_PARENT_OBJECTION} and not has_tech_context(title):
                return
        seen.add(link)
        out.append(
            {
                "lane": lane,
                "title": title,
                "link": link,
                "themes": sorted(themes),
                "amira": "amira" in text.lower(),
                "published": when,
                "state": _attribute(text, query_state, place_index),
            }
        )

    for template in VENDOR_QUERIES:
        for item in await _fetch(http, template.format(d=lookback_days)):
            keep(item, "vendor", "")

    for abbr, name in STATE_NAMES.items():
        shapes = CATEGORY_SHAPES if abbr in DEEP_STATES else CATEGORY_SHAPES[:1]
        for template in shapes:
            for item in await _fetch(http, template.format(name=name, d=lookback_days)):
                keep(item, "category", abbr)

    out.sort(key=lambda row: row["published"], reverse=True)
    return out


# Escalation vocabulary, weakest to strongest. A vendor's coverage moving DOWN
# this ladder is the thing worth briefing: parent complaints are noise until
# they reach a board agenda, and a board agenda is noise until it reaches a
# contract or a court.
_ESCALATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("litigation", ("lawsuit", "sues", "suing", "settle", "settlement", "court")),
    ("contract", ("contract", "renew", "procurement", "bid", "rfp")),
    ("board action", ("board", "trustees", "vote", "hearing", "agenda")),
)

# Competitor names worth naming in a brief. Drawn from the same brand list as
# ``is_amira_specific``, but only the ones a reader recognises as a peer product.
_PEERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("i-Ready", ("i-ready", "iready")),
    ("Lexia", ("lexia",)),
    ("Amplify", ("amplify",)),
    ("mCLASS/DIBELS", ("mclass", "dibels")),
    ("IXL", ("ixl",)),
    ("Newsela", ("newsela",)),
)


def _escalation_of(text: str) -> str | None:
    lowered = text.lower()
    for label, terms in _ESCALATION:
        if any(term in lowered for term in terms):
            return label
    return None


def _peer_of(text: str) -> str | None:
    lowered = text.lower()
    for label, terms in _PEERS:
        if any(term in lowered for term in terms):
            return label
    return None


def _peer_pattern_section(findings: list[dict[str, Any]], shown: set[str]) -> list[str]:
    """The most-covered peer, and how far its coverage has escalated.

    The predictive part of the brief rather than the descriptive part: whatever
    playbook is being run against the most-covered peer is the one most likely
    to arrive at Amira next.
    """
    tallies: Counter[str] = Counter()
    for row in findings:
        peer = _peer_of(row["title"])
        if peer:
            tallies[peer] += 1
    if not tallies:
        return []
    peer, count = tallies.most_common(1)[0]
    if count < 3:
        return []

    rows = [r for r in findings if _peer_of(r["title"]) == peer]
    stages: dict[str, dict[str, Any]] = {}
    for row in rows:
        # Prefer an exemplar the reader has not already seen. A stage whose only
        # example is printed above is dropped rather than repeated -- the ladder
        # is there to show reach, not to re-list the same links.
        if row["link"] in shown:
            continue
        stage = _escalation_of(row["title"])
        if stage and stage not in stages:
            stages[stage] = row

    lines = [
        f"\n*Pattern to watch \u2014 {peer}* ({count} stories)",
        f"_{peer} is the most-covered peer in this window, and its coverage has "
        "already reached stages New Mexico has not. This is the likely shape of "
        "what comes next._",
    ]
    for label, _terms in _ESCALATION:
        staged = stages.get(label)
        if staged is not None:
            body = _line(staged).split("\u2014 ", 1)[-1]
            lines.append(f"\u2022 *{label.title()}* \u2014 {body}")
            shown.add(staged["link"])
    return lines


def row_to_dict(row: Any) -> dict[str, Any]:
    """Adapt a ``BrandSignalFinding`` to the dict shape the composer renders.

    Keeps the composer pure and database-free, which is what lets its wording
    be pinned by tests that need no fixtures.
    """
    return {
        "id": row.id,
        "lane": row.lane,
        "title": row.title,
        "link": row.link,
        "themes": list(row.themes or []),
        "amira": bool(row.names_amira),
        "published": row.published_at,
        "state": row.state,
    }


def _line(row: dict[str, Any]) -> str:
    when = row["published"].strftime("%b %-d")
    where = "" if row["state"] == NATIONAL else f" · {row['state']}"
    labels = ", ".join(THEME_LABELS.get(t, t) for t in row["themes"])
    tail = f" _{labels}_" if labels else ""
    title = row["title"].replace("|", "-")
    return f"• *{when}*{where} — <{row['link']}|{title}>{tail}"


def compose_brand_brief(
    findings: list[dict[str, Any]],
    *,
    new_items: list[dict[str, Any]] | None = None,
    corpus_total: int | None = None,
    now: datetime | None = None,
) -> str:
    """Render the Slack brief. Pure — no I/O, so the wording is testable."""
    # Local time: the header should name the reader's day, not UTC's.
    now = now or datetime.now().astimezone()
    header = f"*Brand Signals — {now.strftime('%A, %B %-d')}*"

    # Guard on BOTH: an empty standing window with new items present must still
    # render them. Short-circuiting on `findings` alone silently discarded the
    # only part of the brief that was actually news.
    if not findings and not new_items:
        return (
            f"{header}\n"
            "_No qualifying coverage in the last "
            f"{LOOKBACK_DAYS} days._ This is a real result, not an outage: "
            "both the vendor lane and the category sweep ran and returned nothing."
        )

    named = [r for r in findings if r["amira"]]
    # Sections are mutually exclusive: a story that both names Amira and
    # describes a board action belongs in the Amira section only. Listing it
    # twice reads as padding and buries how much distinct coverage there is.
    institutional = [
        r for r in findings if THEME_INSTITUTIONAL_REJECTION in r["themes"] and not r["amira"]
    ]
    # The bottom-line count still describes ALL institutional coverage, Amira
    # included -- the dedup is presentational, not a change to the finding.
    institutional_total = sum(1 for r in findings if THEME_INSTITUTIONAL_REJECTION in r["themes"])
    states = Counter(r["state"] for r in findings if r["state"] != NATIONAL)

    parts: list[str] = [header]

    # Bottom line first — this is what gets read in a meeting.
    spread = ", ".join(f"{s} {n}" for s, n in states.most_common(6)) or "none yet"
    parts.append(
        f"*Bottom line.* {len(findings)} qualifying "
        f"{'story' if len(findings) == 1 else 'stories'} in {LOOKBACK_DAYS} days. "
        f"{len(named)} {'names' if len(named) == 1 else 'name'} Amira directly; "
        f"{institutional_total} "
        f"{'involves' if institutional_total == 1 else 'involve'} a district, "
        f"board or state acting rather than a parent complaining. "
        f"States named in the coverage: {spread}."
    )
    # A one-line scope note in the MAIN message. The full caveat sits at the
    # end, which lands in a thread reply once the brief is long -- and the
    # limits of this feed are not something a reader should have to expand a
    # thread to discover.
    parts.append(
        "_Scope: news coverage only. Facebook parent groups are closed to "
        "automated reading; Reddit and Vista Social access are pending._"
    )

    # WHAT IS NEW leads the brief. The first version re-listed the whole
    # 120-day window every morning, so by day three there was nothing to read.
    # `new_items` is the set never included in a previous brief -- see
    # `repository.unreported`. An explicit "nothing new" is a real answer and
    # is stated rather than omitted, so silence never looks like an outage.
    if new_items is not None:
        parts.append("\n*New since the last brief*")
        if new_items:
            for row in new_items[:10]:
                parts.append(_line(row))
            if len(new_items) > 10:
                parts.append(f"_\u2026and {len(new_items) - 10} more new today._")
        else:
            parts.append(
                "_Nothing new. The scan ran and found no story we have not already reported._"
            )

    shown: set[str] = set()

    if corpus_total is not None:
        parts.append(
            "\n*Standing picture* \u2014 the full window below. "
            f"{corpus_total} stories tracked since we started keeping them."
        )

    if named:
        parts.append("\n*Amira by name*")
        for row in named[:8]:
            parts.append(_line(row))
            shown.add(row["link"])

    if institutional:
        parts.append(
            "\n*Institutional action* — the commercially severe half: "
            "a parent complaint is sentiment, a board vote is a contract."
        )
        for row in institutional[:8]:
            parts.append(_line(row))
            shown.add(row["link"])

    parts.extend(_peer_pattern_section(findings, shown))

    # Anything already printed above is skipped: the same link appearing in two
    # sections reads as padding and hides how much distinct coverage there is.
    rest = [r for r in findings if r["link"] not in shown]
    if rest:
        parts.append("\n*Category backdrop*")
        for row in rest[:8]:
            parts.append(_line(row))
        if len(rest) > 8:
            parts.append(f"_…and {len(rest) - 8} more._")

    parts.append(
        "\n*What this does not cover.* Facebook parent groups are closed to any "
        "automated read. Reddit access is submitted and awaiting their review. "
        "Vista Social is pending an access request. Until those land this is "
        "news coverage only — it will under-report parent-voice chatter, which "
        "is where the specific narratives (voice recordings, training AI on "
        'children, "it\'s just a chatbot") actually live.'
    )
    return "\n".join(parts)


# Slack silently SPLITS any chat.postMessage text over ~4000 characters into
# separate messages. The first live post of this brief arrived as three
# fragments, each starting mid-list, which reads as a broken bot rather than a
# briefing. We split it ourselves instead, on section boundaries, and put the
# overflow in a THREAD so the channel shows one clean message.
SLACK_TEXT_LIMIT = 3800


def split_for_slack(text: str, *, limit: int = SLACK_TEXT_LIMIT) -> list[str]:
    """Split *text* into Slack-sized parts, preferring section boundaries.

    The first element is the main message; any others are thread replies.
    A single section longer than *limit* is emitted whole rather than cut
    mid-link -- Slack will split that one, and a broken link is worse than a
    long message.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for section in text.split("\n\n"):
        candidate = f"{current}\n\n{section}" if current else section
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = section
    if current:
        parts.append(current)
    return parts


async def post_brand_signals_brief(session: Any) -> bool:
    """Scan, persist, compose, post as Callie. Returns True only if Slack took it.

    Ordering is deliberate and is the whole reason this persists:

    1. Upsert the scan into ``brand_signal_findings`` and COMMIT. The stories
       were genuinely seen; that fact should survive a failed post.
    2. Read back the unreported set (what is new) and the window (the standing
       picture) from the TABLE, not the feed. Reading the table is why the
       counts stop drifting between runs.
    3. Post.
    4. Only then stamp ``reported_at`` on exactly the rows we listed. Marking
       before posting would drop a story from every future brief if Slack
       failed -- the same class of bug as a tool reporting success for work it
       did not do.

    Dormant (returns False, does nothing) when ``brand_signals_channel`` is
    unset, so the feature ships off and is enabled by configuration alone.
    """
    from artemis.config import settings
    from artemis.scouts._http import ScoutHttpClient
    from artemis.screentime.reporting import _post_as_callie
    from artemis.sentiment import repository as repo

    channel = settings.brand_signals_channel
    if not channel:
        _log.info("brand_signals: no channel configured — feature off, skipping")
        return False

    place_index = await load_place_index(session)
    async with ScoutHttpClient(timeout=25.0, rate_limit=2.0) as http:
        scanned = await gather_brand_signals(http, place_index)

    inserted, refreshed = await repo.upsert_findings(session, scanned)
    await session.commit()

    new_rows = await repo.unreported(session, limit=NEW_ITEMS_PER_BRIEF)
    window_rows = await repo.window_findings(session, days=LOOKBACK_DAYS)
    corpus_total = await repo.count_all(session)

    new_items = [row_to_dict(r) for r in new_rows]
    findings = [row_to_dict(r) for r in window_rows]

    text = compose_brand_brief(findings, new_items=new_items, corpus_total=corpus_total)
    parts = split_for_slack(text)
    # unfurl=False: every link here is a Google News redirect, and Slack
    # unfurls each into the same useless "Google News" card.
    posted = await _post_as_callie(session, channel, parts[0], thread_parts=parts[1:], unfurl=False)

    if posted and new_rows:
        marked = await repo.mark_reported(session, [r.id for r in new_rows])
        await session.commit()
    else:
        marked = 0

    _log.info(
        "brand_signals: scanned=%d inserted=%d refreshed=%d new=%d marked=%d "
        "corpus=%d posted=%s channel=%s",
        len(scanned),
        inserted,
        refreshed,
        len(new_rows),
        marked,
        corpus_total,
        posted,
        channel,
    )
    return posted


async def run_brand_signals() -> dict[str, Any]:
    """Cron entry point. Never raises -- a failed brief must not kill the job."""
    from artemis.db import SessionLocal

    try:
        async with SessionLocal() as session:
            posted = await post_brand_signals_brief(session)
        return {"posted": posted}
    except Exception as exc:  # pragma: no cover - cron guard
        _log.exception("brand_signals: run failed")
        return {"error": str(exc)}


def register_brand_signals_schedule(scheduler: Any) -> None:
    """Register the daily Brand Signals brief. Idempotent.

    A standalone cron is correct HERE, unlike the screentime digest: that feed
    was folded into the one combined #market-signals brief precisely to avoid
    two posts a day in one channel. #brand-signals is a dedicated channel that
    wants only this feed, which is the case
    ``screentime.runner.start_screentime_scheduler`` anticipated.

    Dormant while ``brand_signals_channel`` is unset.
    """
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    from artemis.config import settings

    # Day-of-week by NAME (mon-fri), never numeric -- this repo's APScheduler
    # cron gotcha.
    trigger = CronTrigger.from_crontab(
        settings.brand_signals_cron, timezone=settings.screentime_cron_tz
    )
    scheduler.add_job(
        run_brand_signals,
        trigger=trigger,
        id="sentiment.brand_signals.daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info(
        "brand_signals: registered cron %s (%s) -> channel %r",
        settings.brand_signals_cron,
        settings.screentime_cron_tz,
        settings.brand_signals_channel or "(unset - dormant)",
    )
