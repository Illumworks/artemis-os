"""Per-state NEWS coverage of screen-time + AI-in-schools policy (Google News RSS).

Fills the gap LegiScan can't: bill tracking (``scout_fanout._gather_legislative``)
is national but only covers *legislation*, not news coverage of state agency
guidance, board actions, or AI-adoption stories that never became a bill. This
module is a NEW, screentime-owned gatherer — it is intentionally NOT placed
under ``artemis/scouts/state_doe/`` because that package is shared with the
marketing literacy scout (its RSS queries are literacy-scoped and feed the
campaign pipeline); mixing screen-time/AI queries into it would pollute that
beat. Everything here is additive and self-contained.

Query shape
-----------
2026-07-11 broadening (the live PoC sweep found this source effectively dead —
1 finding across 51 states): the ORIGINAL query OR'd four fully-quoted,
5-6-word phrases per state (e.g. ``"Florida schools screen time policy"``).
Google News RSS treats a quoted phrase as a near-exact match, so a bespoke
5-6-word sentence almost never appears verbatim in a real headline/body —
manual verification against the live feed confirmed 0 items for FL/TX/CA
under the old shape.

The NEW query keeps the state name + "schools" as bare (AND-ed, not quoted)
terms — the school/education scope — and OR's a group of SHORT (2-3 word),
still-quoted core phrases so any one of them matching is enough:

    <State> schools ("screen time" OR "device policy" OR "AI policy"
    OR "artificial intelligence")

Manually verified against the live Google News RSS feed (2026-07-11): FL 79
items, TX 73 items, CA 80 items (vs. 0/0/0 under the old shape) — and the
top results are genuinely on-topic (state AI-in-schools policy, device-time
tracking, etc.), not noise. Still multi-word throughout (never a bare "ai"
token — see ``artemis.screentime.topic_config`` for why that would flood the
gate); the broadened volume is filtered downstream by the existing topic
gate + dedupe, same as every other source.

Same URL shape as ``artemis/scouts/state_doe/sources.py``'s Google News usage:
``https://news.google.com/rss/search?q=...&hl=en-US&gl=US&ceid=US%3Aen``.

Findings flow through the EXISTING pipeline unchanged: fan-out → normalize →
dedupe → topic gate (screen-time OR AI-in-schools, per ``topic_config`` v3) →
real-moves filter → classify → store. Stance/AI-policy classification is
deliberately NOT done here (pending the Angela review — see
``topic_config.py``); this module only discovers + normalizes.

Load management / rotation
---------------------------
Google News RSS is lightweight (no API key, generous rate limits), so the
default behavior sweeps ALL 50 states + DC every run — see
``gather_national_policy_news(states_per_run=None)`` (the default). If a
future run ever needs to throttle (e.g. Google starts rate-limiting the
sweep), pass ``states_per_run=<n>`` to process only that many states starting
at ``cursor``; the function returns the next cursor for the caller to persist
and pass back in on the following run, cycling through the full set over
multiple runs.

Cursor persistence choice: rather than adding a new column/table, the cursor
(when used) is stored as a JSONB row in the EXISTING ``screentime_stance_config``
table (see ``artemis/screentime/models.py``) under the name
``"national_news_cursor"`` — the same table ``topic_config.py`` already uses
for tunable rules, just a different row. This is the least-invasive persistence
option: zero migration, reuses an established key/value pattern. See
``load_news_cursor`` / ``save_news_cursor`` / ``gather_national_policy_news_with_persisted_cursor``.

The DEFAULT wiring into ``scout_fanout._SCOUT_GATHERERS`` (see
``_gather_national_news``) always sweeps all states (``states_per_run=None``)
— the persisted-cursor helpers exist for an optional future cron variant and
are not required for normal operation.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import defusedxml.ElementTree as SafeET

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts._states import STATE_NAMES as _CANONICAL_STATE_NAMES
from artemis.screentime.filters import LANE_BRAND, LANE_POLICY

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State names — re-exported from the canonical table.
# ---------------------------------------------------------------------------
# Was a second hand-maintained copy. Three lists of states had drifted apart
# (this one, scout_fanout's, and the State-DoE source map) with nothing to
# detect it; see artemis/scouts/_states.py.

STATE_NAMES = _CANONICAL_STATE_NAMES


_GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

# Short, quoted, still school/AI-scoped core phrases OR'd together (2026-07-11
# broadening — see module docstring). Each is <=3 words so it actually shows
# up verbatim in real headlines/body text, unlike the old 5-6-word sentences.
_CORE_NEWS_TERMS: tuple[str, ...] = (
    "screen time",
    "device policy",
    "AI policy",
    "artificial intelligence",
)

# ── Brand lane query terms (2026-08-12) ──────────────────────────────────────
# The policy query above cannot surface a vendor-removal story: "district drops
# Amira reading program" contains none of those four phrases. That blind spot
# produced ZERO New Mexico signals during an active NM crisis, so the brand lane
# gets its own query rather than more terms bolted onto the policy one (a single
# OR-group of 12+ phrases dilutes Google News relevance ranking for both).
#
# Scoped to Amira + the Tier-1 "Closest ICP Match" competitors from Jon's
# competitor sheet: a removal at one of these is the strongest leading indicator
# that Amira is next. Tiers 2-3 are gate-only (caught if they appear in the
# policy feed) — querying every vendor per state would triple the request count
# for progressively weaker signal.
#
# Names that are ordinary English words are qualified here for the same reason
# they are in topic_config.brand_any: an unqualified "Amplify" or "Renaissance"
# query returns concerts and fairs.
_BRAND_NEWS_TERMS: tuple[str, ...] = (
    "Amira Learning",
    "i-Ready",
    "Lexia",
    "Amplify reading",
    "Renaissance Learning",
    "MagicSchool AI",
    "Brisk Teaching",
)

# Reason codes — mirrors artemis/scouts/regional_news/mapping.py's vocabulary
# so downstream provenance stays consistent across scouts (kept as local
# string constants rather than a cross-module import to avoid coupling this
# screentime-owned module to a shared scout's private internals).
_RC_POLICY_EDTECH_TIME_LIMIT = "POLICY_EDTECH_TIME_LIMIT"
_RC_POLICY_AI_IN_SCHOOLS = "POLICY_AI_IN_SCHOOLS"

_SCREEN_TIME_MARKERS: tuple[str, ...] = (
    "screen time",
    "screen-time",
    "device time",
    "device limit",
    "device-free",
    "classroom device",
)
_AI_MARKERS: tuple[str, ...] = (
    "ai policy",
    "ai guidance",
    "ai moratorium",
    "generative ai",
    "artificial intelligence",
    "chatgpt",
    "ai in school",
    "ai in the classroom",
)


def _classify_reason_codes(text: str) -> list[str]:
    """Best-effort screen-time vs AI-in-schools reason codes. Never classifies stance."""
    lower = text.lower()
    codes: list[str] = []
    if any(m in lower for m in _SCREEN_TIME_MARKERS):
        codes.append(_RC_POLICY_EDTECH_TIME_LIMIT)
    if any(m in lower for m in _AI_MARKERS):
        codes.append(_RC_POLICY_AI_IN_SCHOOLS)
    if not codes:
        # Query is school/screen-time/AI-scoped by construction, so this should
        # be rare (e.g. an item that only matched on the state name); keep a
        # deterministic fallback rather than an empty list.
        codes.append(_RC_POLICY_EDTECH_TIME_LIMIT)
    return codes


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def build_state_news_query(state_abbr: str) -> str:
    """Return the broadened, school-scoped search query for *state_abbr*.

    Shape: ``<State> schools ("screen time" OR "device policy" OR "AI policy"
    OR "artificial intelligence")`` — the state name + "schools" are bare/AND-ed
    (the school scope), and a short OR'd group of quoted 2-3-word phrases
    covers screen-time OR AI-in-schools coverage. See the module docstring for
    why this replaced the old fully-quoted 5-6-word-phrase shape (0 live hits).

    Raises ``KeyError`` for an unknown abbreviation (fail loud — a typo'd
    state code should never silently produce a garbage query).
    """
    name = STATE_NAMES[state_abbr.upper()]
    or_group = " OR ".join(f'"{t}"' for t in _CORE_NEWS_TERMS)
    return f"{name} schools ({or_group})"


def build_state_brand_query(state_abbr: str) -> str:
    """Return the BRAND-lane search query for *state_abbr*.

    Shape: ``<State> schools ("Amira Learning" OR "i-Ready" OR ...)`` — same
    school-scoping as the policy query, but the OR-group is Amira + the Tier-1
    competitors instead of policy phrases. See ``_BRAND_NEWS_TERMS``.

    Raises ``KeyError`` for an unknown abbreviation (same fail-loud contract as
    ``build_state_news_query``).
    """
    name = STATE_NAMES[state_abbr.upper()]
    or_group = " OR ".join(f'"{t}"' for t in _BRAND_NEWS_TERMS)
    return f"{name} schools ({or_group})"


def build_state_news_rss_url(state_abbr: str) -> str:
    """Build the Google News RSS URL for *state_abbr* (same shape as state_doe)."""
    query = build_state_news_query(state_abbr)
    return f"{_GOOGLE_NEWS_RSS_BASE}?q={quote(query)}&hl=en-US&gl=US&ceid=US%3Aen"


def build_state_brand_rss_url(state_abbr: str) -> str:
    """Build the Google News RSS URL for the brand lane for *state_abbr*."""
    query = build_state_brand_query(state_abbr)
    return f"{_GOOGLE_NEWS_RSS_BASE}?q={quote(query)}&hl=en-US&gl=US&ceid=US%3Aen"


# ---------------------------------------------------------------------------
# RSS parsing (self-contained; same technique as scouts/state_doe/sources.py:
# defusedxml against entity/DTD tricks in untrusted feed XML).
# ---------------------------------------------------------------------------


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def parse_news_rss(xml_text: str) -> list[dict[str, Any]]:
    """Parse a Google News RSS feed into ``{title, link, published, summary}`` dicts.

    Returns an empty list on malformed XML or an empty/absent channel — never
    raises (callers are expected to fail-safe per state regardless).
    """
    if not xml_text:
        return []
    try:
        root = SafeET.fromstring(xml_text)
    except (ET.ParseError, ValueError) as exc:
        _logger.warning("national_news.parse_news_rss: malformed XML — %s", exc)
        return []

    channel = root.find("channel")
    if channel is None:
        channel = root

    items: list[dict[str, Any]] = []
    for item_el in channel.findall("item"):
        items.append(
            {
                "title": _text(item_el.find("title")),
                "link": _text(item_el.find("link")),
                "published": _text(item_el.find("pubDate")),
                "summary": _text(item_el.find("description")),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Fetch + normalize
# ---------------------------------------------------------------------------


async def fetch_state_news_items(state_abbr: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch + parse the Google News RSS feed for one state. Fail-safe: [] on error."""
    url = build_state_news_rss_url(state_abbr)
    try:
        resp = await http.get(url)
        if resp.status_code != 200:
            _logger.warning(
                "fetch_state_news_items(%s): HTTP %d from %s", state_abbr, resp.status_code, url
            )
            return []
        return parse_news_rss(resp.text)
    except Exception as exc:
        _logger.warning("fetch_state_news_items(%s): error — %s", state_abbr, exc)
        return []


# ---------------------------------------------------------------------------
# Content-based state attribution
# ---------------------------------------------------------------------------
# The per-state fan-out asks Google News one question per state, then used to
# stamp the ANSWER with the state it ASKED about. Google News does not honour
# that scope: a query naming Georgia returns Bellevue (WA) and Hillsborough (FL)
# stories, and a query naming New Mexico returned a Texas renewal. Measured
# 2026-08-21 on live data: of 23 stored rows, at least 9 were filed under a
# state the article was not about.
#
# The consequence is worse than noise. Per-state counts are what tell us whether
# we are blind somewhere, so mis-attribution inflates exactly the number we use
# to decide we have coverage.
#
# Resolution is by full state NAME only. Two-letter abbreviations are not
# matched bare -- "IN", "OR", "OK", "ME", "HI", "DE", "MS", "MD", "MT", "PA",
# "LA", "MA", "WA", "AL", "CA", "CO", "CT" are all ordinary English words or
# fragments -- but ARE matched in the ", GA" / ", Ga." place-suffix form, which
# is unambiguous.
#
# NATIONAL is the honest answer for an item with no geography at all (a vendor
# funding round, a national trade story). Those are real signals; they are just
# not evidence about any state.

NATIONAL = "US"

_STATE_NAME_RE: dict[str, re.Pattern[str]] = {
    abbr: re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
    for abbr, name in STATE_NAMES.items()
}
# ", GA" or ", Ga." — an abbreviation used as a place suffix.
_STATE_SUFFIX_RE: dict[str, re.Pattern[str]] = {
    abbr: re.compile(r",\s*" + abbr + r"\b\.?", re.IGNORECASE) for abbr in STATE_NAMES
}


def resolve_state(text: str, query_state: str) -> tuple[str, str]:
    """Return ``(state, confidence)`` for *text* retrieved by a *query_state* feed.

    confidence is one of:
      ``confirmed``     the text names the state we searched for -- trust it.
      ``reattributed``  it names exactly one OTHER state -- file it there.
      ``national``      it names no state at all -- file under ``NATIONAL``.
      ``ambiguous``     it names several states, none of them ours -- ``NATIONAL``.

    Never raises. An unknown *query_state* is treated as having no home state,
    which degrades to national rather than to a wrong state.

    KNOWN LIMITATION, and it costs us something real. Resolution is by state
    name only, so an article that names a place but never its state resolves to
    NATIONAL: "Popular school program i-Ready, used in Hillsborough County,
    faces lawsuit over student data" is a FLORIDA signal and lands as national.
    Florida was one of three states named internally as live on 2026-08-20, so
    this is exactly the signal we most need placed.

    The fix is available and not done here: ``districts`` holds 13k district
    names with their states, which would resolve "Hillsborough County" to FL and
    "Bellevue School District" to WA. It needs a place->state index passed in
    (this module has no DB access by design) and a disambiguation rule for names
    that recur across states -- Clayton, Columbus, Athens, Jackson, Franklin,
    Union County all exist in several. Until then NATIONAL means "no state
    named", not "not about a state", and the per-state coverage alarm in
    ``artemis.ops`` is what actually protects us from believing a quiet state is
    a covered one.
    """
    mentioned = {abbr for abbr, rx in _STATE_NAME_RE.items() if rx.search(text)}
    mentioned |= {abbr for abbr, rx in _STATE_SUFFIX_RE.items() if rx.search(text)}

    home = (query_state or "").strip().upper()
    if home and home in mentioned:
        return home, "confirmed"
    if len(mentioned) == 1:
        return next(iter(mentioned)), "reattributed"
    if not mentioned:
        return NATIONAL, "national"
    return NATIONAL, "ambiguous"


def item_to_finding(item: dict[str, Any], state_abbr: str) -> dict[str, Any] | None:
    """Normalize one RSS item into the canonical raw-finding-dict shape.

    Returns None for an item with no usable title (nothing to store).
    Mirrors the convention used by ``scouts/regional_news/mapping.py`` so
    ``Finding.from_raw`` / ``screentime.filters.normalize_finding`` handle it
    identically to every other scout's output.
    """
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    summary = str(item.get("summary") or "").strip()
    link = str(item.get("link") or "").strip()
    combined = f"{title} {summary}"

    lane = str(item.get("lane") or LANE_POLICY).strip().lower()

    # Attribute by what the article says, not by which feed fetched it.
    resolved_state, state_confidence = resolve_state(combined, state_abbr)

    return {
        "sourceType": "national_news",
        "discoveredBy": "national_news_scout",
        "state": resolved_state,
        "title": title,
        "reasonCodes": _classify_reason_codes(combined),
        "urgency": "standard",
        "evidence": f"{title}. {summary[:300]}".strip(),
        "lane": lane,
        "metadata": {
            "state": resolved_state,
            # Kept for auditing: which feed asked, versus what the text said.
            # A run whose rows are mostly "reattributed" means the per-state
            # queries are not actually scoping, which is a scout bug, not noise.
            "query_state": state_abbr,
            "state_confidence": state_confidence,
            "source_url": link,
            "published_at": item.get("published") or "",
            "source_name": "Google News",
            "source_type": "national_news",
            # Carried so the lane survives normalize_finding → CandidateSignal
            # and lands in the stored row's raw JSON for later auditing.
            "lane": lane,
        },
    }


async def fetch_state_brand_items(state_abbr: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch + parse the BRAND-lane feed for one state. Fail-safe: [] on error.

    Every item is stamped ``lane="brand"``. That stamp is the item's provenance
    and it is what carries it through the topic gate — the retrieved headline
    frequently does not repeat the vendor name that matched the query, so the
    gate cannot re-derive brand relevance from the text. See
    ``filters.passes_topic_gate_async``.
    """
    url = build_state_brand_rss_url(state_abbr)
    try:
        resp = await http.get(url)
        if resp.status_code != 200:
            _logger.warning(
                "fetch_state_brand_items(%s): HTTP %d from %s", state_abbr, resp.status_code, url
            )
            return []
        items = parse_news_rss(resp.text)
        for item in items:
            item["lane"] = LANE_BRAND
        return items
    except Exception as exc:
        _logger.warning("fetch_state_brand_items(%s): error — %s", state_abbr, exc)
        return []


async def gather_state_news(state_abbr: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch + normalize findings for one state, across BOTH lanes.

    Two feeds per state: the policy query and the brand query. They are merged
    and de-duplicated by link here; the downstream topic gate, dedup and
    real-move filter are unchanged and handle the combined stream identically.

    Each lane is independently fail-safe, so a brand-feed outage cannot take
    policy coverage down with it.
    """
    items: list[dict[str, Any]] = []
    # BRAND FIRST, deliberately. The two lanes overlap and the link-dedup below
    # keeps the first occurrence, so whichever lane is fetched first wins the
    # lane stamp on a shared URL. Brand must win: its stamp grants the topic-gate
    # short-circuit and the brand real-move bar, both of which are strictly more
    # permissive. Fetching policy first would silently demote a crisis item that
    # happened to also match the policy query.
    try:
        items.extend(await fetch_state_brand_items(state_abbr, http))
    except Exception as exc:  # pragma: no cover - fetch_state_brand_items already guards
        _logger.warning("gather_state_news(%s): brand lane error — %s", state_abbr, exc)
    try:
        items.extend(await fetch_state_news_items(state_abbr, http))
    except Exception as exc:  # pragma: no cover - fetch_state_news_items already guards
        _logger.warning("gather_state_news(%s): policy lane error — %s", state_abbr, exc)

    findings: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    for item in items:
        finding = item_to_finding(item, state_abbr)
        if finding is None:
            continue
        # The two lanes overlap (an "AI policy" story naming a vendor hits
        # both). Dedup on link here so the same URL is not normalized twice;
        # items with no link fall through to the downstream content-hash dedup.
        link = str(item.get("link") or "").strip()
        if link:
            if link in seen_links:
                continue
            seen_links.add(link)
        findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# Rotation (states_per_run + cursor)
# ---------------------------------------------------------------------------


def _rotation_window(states: list[str], cursor: int, count: int) -> tuple[list[str], int]:
    """Return (states_to_process, next_cursor), wrapping around the list.

    Pure helper — no I/O. ``count`` is clamped to ``len(states)`` (never
    repeats a state within one window). An empty ``states`` list is a no-op.
    """
    n = len(states)
    if n == 0 or count <= 0:
        return [], 0
    start = cursor % n
    window_size = min(count, n)
    selected = [states[(start + i) % n] for i in range(window_size)]
    next_cursor = (start + window_size) % n
    return selected, next_cursor


async def gather_national_policy_news(
    states: list[str] | None = None,
    *,
    states_per_run: int | None = None,
    cursor: int = 0,
    http: ScoutHttpClient | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Sweep Google News RSS for screen-time + AI-in-schools coverage.

    Parameters
    ----------
    states:
        States to consider. Defaults to all 50 + DC
        (``scout_fanout.US_STATES_AND_DC``).
    states_per_run:
        ``None`` (default) sweeps every state in *states* each call —
        Google News RSS is lightweight, so this is the intended default.
        When set, only that many states (starting at *cursor*) are swept;
        the returned cursor lets a caller cycle through the full set across
        repeated runs.
    cursor:
        Index into *states* to start this run's window at (ignored when
        *states_per_run* is None).
    http:
        Inject a ``ScoutHttpClient`` (tests, or to share one client across a
        larger sweep). When omitted, a client is created and closed here.

    Returns
    -------
    (findings, next_cursor) — *next_cursor* is 0 when *states_per_run* is
    None (a full sweep has no meaningful "next" position).

    Failure-safe: a single state's fetch error never aborts the sweep.
    """
    from artemis.screentime.scout_fanout import US_STATES_AND_DC

    all_states = list(states) if states is not None else list(US_STATES_AND_DC)

    if states_per_run is None:
        window = all_states
        next_cursor = 0
    else:
        window, next_cursor = _rotation_window(all_states, cursor, states_per_run)

    owns_http = http is None
    client = http or ScoutHttpClient(rate_limit=2.0)
    findings: list[dict[str, Any]] = []
    try:
        for state in window:
            try:
                findings.extend(await gather_state_news(state, client))
            except Exception as exc:
                _logger.warning("gather_national_policy_news: %s failed — skipping: %s", state, exc)
    finally:
        if owns_http:
            await client.aclose()

    return findings, next_cursor


# ---------------------------------------------------------------------------
# Optional persisted-cursor helpers (screentime_stance_config row; zero
# migration). Not used by the default scout_fanout wiring — available for a
# future cron variant that wants to throttle + cycle automatically.
# ---------------------------------------------------------------------------

NEWS_CURSOR_CONFIG_NAME = "national_news_cursor"


async def load_news_cursor(session: Any, *, name: str = NEWS_CURSOR_CONFIG_NAME) -> int:
    """Read the persisted rotation cursor. Returns 0 on any error or missing row."""
    from sqlalchemy import select

    from artemis.screentime.models import ScreentimeStanceConfig

    try:
        row = (
            await session.execute(
                select(ScreentimeStanceConfig).where(ScreentimeStanceConfig.name == name)
            )
        ).scalar_one_or_none()
    except Exception:
        return 0
    if row is not None and isinstance(row.rules, dict):
        try:
            return int(row.rules.get("cursor", 0))
        except (TypeError, ValueError):
            return 0
    return 0


async def save_news_cursor(
    session: Any, cursor: int, *, name: str = NEWS_CURSOR_CONFIG_NAME
) -> None:
    """Upsert the persisted rotation cursor (same row shape as topic_config)."""
    from json import dumps

    from sqlalchemy import text as _text

    await session.execute(
        _text(
            """
            INSERT INTO screentime_stance_config (name, rules, updated_at)
            VALUES (:name, CAST(:rules AS jsonb), now())
            ON CONFLICT (name) DO UPDATE
              SET rules = EXCLUDED.rules, updated_at = now()
            """
        ),
        {"name": name, "rules": dumps({"cursor": cursor})},
    )


async def gather_national_policy_news_with_persisted_cursor(
    session: Any,
    *,
    states: list[str] | None = None,
    states_per_run: int | None = None,
    http: ScoutHttpClient | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Convenience wrapper: load cursor → sweep → persist next cursor.

    Only meaningful when *states_per_run* is set (rotation mode); with the
    default None it still works (loads/saves a cursor that stays 0) but a
    full sweep every run has no reason to use this over the plain function.
    """
    cursor = await load_news_cursor(session) if states_per_run is not None else 0
    findings, next_cursor = await gather_national_policy_news(
        states, states_per_run=states_per_run, cursor=cursor, http=http
    )
    if states_per_run is not None:
        await save_news_cursor(session, next_cursor)
    return findings, next_cursor
