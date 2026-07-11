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
One Google News RSS query per state, OR-ing four school-scoped phrases so a
state matching ANY of them surfaces (never a bare "ai" token — see
``artemis.screentime.topic_config`` for why that would flood the gate):

    "<State> schools screen time policy" OR "<State> classroom device limits"
    OR "<State> schools AI policy" OR "<State> generative AI schools"

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
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import defusedxml.ElementTree as SafeET

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State names — abbreviation → full name, for the human-readable query text.
# Keys match ``scout_fanout.US_STATES_AND_DC`` exactly (50 states + DC).
# ---------------------------------------------------------------------------

STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

_GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

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
    """Return the OR'd, school-scoped search phrase for *state_abbr*.

    Raises ``KeyError`` for an unknown abbreviation (fail loud — a typo'd
    state code should never silently produce a garbage query).
    """
    name = STATE_NAMES[state_abbr.upper()]
    phrases = (
        f"{name} schools screen time policy",
        f"{name} classroom device limits",
        f"{name} schools AI policy",
        f"{name} generative AI schools",
    )
    return " OR ".join(f'"{p}"' for p in phrases)


def build_state_news_rss_url(state_abbr: str) -> str:
    """Build the Google News RSS URL for *state_abbr* (same shape as state_doe)."""
    query = build_state_news_query(state_abbr)
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

    return {
        "sourceType": "national_news",
        "discoveredBy": "national_news_scout",
        "state": state_abbr,
        "title": title,
        "reasonCodes": _classify_reason_codes(combined),
        "urgency": "standard",
        "evidence": f"{title}. {summary[:300]}".strip(),
        "metadata": {
            "state": state_abbr,
            "source_url": link,
            "published_at": item.get("published") or "",
            "source_name": "Google News",
            "source_type": "national_news",
        },
    }


async def gather_state_news(state_abbr: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch + normalize findings for one state. Fail-safe: [] on any error."""
    try:
        items = await fetch_state_news_items(state_abbr, http)
    except Exception as exc:  # pragma: no cover - fetch_state_news_items already guards
        _logger.warning("gather_state_news(%s): error — %s", state_abbr, exc)
        return []

    findings: list[dict[str, Any]] = []
    for item in items:
        finding = item_to_finding(item, state_abbr)
        if finding is not None:
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
                _logger.warning(
                    "gather_national_policy_news: %s failed — skipping: %s", state, exc
                )
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


async def save_news_cursor(session: Any, cursor: int, *, name: str = NEWS_CURSOR_CONFIG_NAME) -> None:
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
