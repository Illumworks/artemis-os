"""Place-name -> state index, built from the ``districts`` table.

Why this exists
---------------
``national_news.resolve_state`` resolves a story to a state by looking for the
STATE NAME in the text. Local education reporting very often never writes it:
"Popular school program i-Ready, used in Hillsborough County, faces lawsuit over
student data" is a Florida story that resolved to NATIONAL, and Florida is one
of the states named internally as live. The same miss put a Pinellas County
story and a Charlotte-Mecklenburg contract vote in the national bucket.

``districts`` already holds 13k district names with their state, which is the
gazetteer we need. The two things that make it non-trivial are handled here:

1. **Suffix noise.** "SALAMANCA CITY SCHOOL DISTRICT" has to reduce to
   "salamanca" before it can match prose.
2. **Ambiguity.** "Hillsborough" is a district in FL, CA and NJ; "Mecklenburg"
   in NC and VA. Guessing here would manufacture confident wrong attributions,
   which is worse than the NATIONAL bucket we already have.

The disambiguation rule is ENROLLMENT DOMINANCE: a name resolves only if one
state's districts of that name enroll at least ``DOMINANCE_RATIO`` times all
rivals combined. That matches how the name is actually used -- unqualified
"Hillsborough County schools" in education coverage means the 220k-student
Florida district, not the 500-student California one -- and it abstains rather
than guesses when no candidate dominates.

Everything here is pure given the rows, so it is testable without a database:
``build_place_index`` takes an iterable of ``(name, state, enrollment)``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

# One state's districts of a given name must enroll this many times all rivals
# combined before we treat the bare name as unambiguous.
DOMINANCE_RATIO = 5.0

# Shortest place token we will index. Below this, false matches inside ordinary
# prose overwhelm the signal ("Ada", "Elk", "Lee").
MIN_TOKEN_LEN = 4

# PROMINENCE FLOOR, and the most important guard in this module.
#
# Found by testing against real headlines: "Lawsuit alleges i-Ready collected
# and shared San Diego student data" resolved to TEXAS. `districts` contains
# SAN DIEGO ISD, TX (1,453 students) and does NOT contain San Diego Unified,
# CA -- so the token looked UNAMBIGUOUS and produced a confidently wrong state.
#
# The lesson generalises: uniqueness in an incomplete reference table is not
# evidence of correctness. A bare place name in national education coverage
# refers to a district big enough to be recognised without its state, so only
# those may claim one. Districts below the floor are simply not indexed; their
# stories land in the honest NATIONAL bucket instead of a wrong state.
#
# This deliberately costs real hits -- Salamanca, NY (~1.5k students) was a
# correct resolution and is now dropped. A wrong state is worse than no state:
# it silently moves a signal into a market we are not actually being attacked in.
#
# Set to 10k rather than 25k: 25k also excluded Santa Fe Public Schools (~11k),
# and "Santa Fe Public Schools rejects state-required AI program" is the single
# most important New Mexico story in the current crisis. 10k still leaves SAN
# DIEGO ISD, TX (1,453) out, which is the case this floor exists to stop.
MIN_ENROLLMENT = 10_000

# Structural words in district names, stripped to leave the distinctive part.
# Order matters: longer phrases first so "independent school district" is gone
# before "school district" can half-match it.
_SUFFIXES: tuple[str, ...] = (
    "independent school district",
    "consolidated school district",
    "unified school district",
    "community school district",
    "central school district",
    "city school district",
    "public school district",
    "regional school district",
    "county school district",
    "metropolitan school district",
    "area school district",
    "joint school district",
    "union free school district",
    "public schools",
    "school district",
    "schools",
    "school corporation",
    "school corp",
    "district",
    "isd",
    "usd",
    "csd",
    "county",
    "parish",
    "borough",
    "township",
    "elementary",
    "secondary",
    "high school",
    "middle school",
    "academy",
    "charter",
    "cooperative",
    "co-op",
    "of education",
    "board of education",
)

# Tokens that are real district names somewhere but are also ordinary words or
# national-news furniture. Indexing them would attribute unrelated stories.
# (Ambiguity already removes most multi-state names; this covers the ones that
# happen to be dominant in one state yet common in prose.)
_STOPWORDS: frozenset[str] = frozenset(
    {
        "union",
        "central",
        "public",
        "community",
        "county",
        "city",
        "state",
        "north",
        "south",
        "east",
        "west",
        "northern",
        "southern",
        "eastern",
        "western",
        "valley",
        "river",
        "lake",
        "hill",
        "hills",
        "park",
        "grove",
        "heights",
        "ridge",
        "creek",
        "springs",
        "summit",
        "liberty",
        "freedom",
        "independence",
        "washington",
        "lincoln",
        "jefferson",
        "madison",
        "jackson",
        "franklin",
        "monroe",
        "columbus",
        "athens",
        "clayton",
        "salem",
        "auburn",
        "clinton",
        "greenville",
        "fairview",
        "riverside",
        "oakland",
        "highland",
        "mount",
        "saint",
        "santa",
        "sierra",
        "mesa",
        "canyon",
        "desert",
        "prairie",
        "plains",
        "harbor",
        "island",
        "beach",
        "center",
        "centre",
        "college",
        "university",
        "national",
        "american",
        "america",
        "united",
        "states",
        "education",
        "learning",
        "academy",
        "charter",
        "career",
        "technical",
        "virtual",
        "online",
        "global",
        "international",
        "christian",
        "catholic",
        "montessori",
        "montgomery",
        "marion",
        "lafayette",
        "warren",
        "wayne",
        "perry",
        "logan",
        "grant",
        # DOMAIN COLLISIONS. Reading, Massachusetts is a real district, and
        # "reading" is the most common word in our entire subject matter -- in
        # the first live run it attributed a Pinellas County story to MA.
        # The prominence floor happens to hide this one (Reading MA is small),
        # which is exactly why it needs its own guard: the next such collision
        # may be a large district, and then nothing would catch it.
        "reading",
        "literacy",
        "writing",
        "language",
        "science",
        "arts",
        "achievement",
        "success",
        "excellence",
        "discovery",
        "horizon",
        "pioneer",
        "frontier",
        "future",
        "bridge",
        "compass",
        "beacon",
    }
)

# Publishers emit soft hyphens and zero-width joiners inside long compound
# names -- the live feed carried "Charlotte-Meck\u00adlenburg", which split the
# token and dropped a 141k-student district. Strip them before anything else.
_INVISIBLE = re.compile(r"[\u00ad\u200b\u200c\u200d\ufeff]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s-]+")
_WS = re.compile(r"\s+")


def normalize_place(name: str) -> str:
    """Reduce a district name to its distinctive place token.

    ``"SALAMANCA CITY SCHOOL DISTRICT"`` -> ``"salamanca"``.
    Returns ``""`` when nothing distinctive survives (e.g. ``"Union Schools"``).
    """
    text = _NON_ALNUM.sub(" ", _INVISIBLE.sub("", (name or "").lower()))
    text = _WS.sub(" ", text).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            # Strip as a whole word anywhere, not just at the end: names read
            # "Hillsborough County Public Schools" and "Public Schools of X".
            pattern = re.compile(rf"\b{re.escape(suffix)}\b")
            if pattern.search(text):
                text = pattern.sub(" ", text)
                text = _WS.sub(" ", text).strip()
                changed = True
    return text


def build_place_index(
    rows: Iterable[tuple[str, str, int | None]],
) -> dict[str, str]:
    """Build ``{place_token: STATE}`` from ``(name, state, enrollment)`` rows.

    A token is included only when one state dominates it -- see the module
    docstring. Ambiguous tokens are omitted entirely, so a caller that misses
    is left with the honest NATIONAL bucket rather than a wrong state.
    """
    by_token: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for name, state, enrollment in rows:
        token = normalize_place(name)
        if len(token) < MIN_TOKEN_LEN or token in _STOPWORDS:
            continue
        st = (state or "").strip().upper()
        if len(st) != 2:
            continue
        size = float(enrollment or 0)
        if size < MIN_ENROLLMENT:
            continue  # see MIN_ENROLLMENT -- not prominent enough to claim a bare name
        by_token[token][st] += size

    index: dict[str, str] = {}
    for token, states in by_token.items():
        if len(states) == 1:
            index[token] = next(iter(states))
            continue
        ranked = sorted(states.items(), key=lambda kv: kv[1], reverse=True)
        top_state, top_weight = ranked[0]
        rivals = sum(weight for _, weight in ranked[1:])
        if rivals > 0 and top_weight >= DOMINANCE_RATIO * rivals:
            index[token] = top_state

    # CONTAINED-TOKEN AMBIGUITY. A token that is part of a longer token owned by
    # a DIFFERENT state cannot be resolved on its own.
    #
    # The case that forced this: "charlotte" is Charlotte County, FL, and
    # "charlotte-mecklenburg" is Charlotte, NC. A Charlotte Observer story about
    # the NC board -- which never writes "Mecklenburg" -- matched "charlotte"
    # and was filed under Florida. Longest-match at lookup time cannot help,
    # because the longer token is not present in that text at all. The name is
    # genuinely ambiguous in the world, so the index must say so.
    contested = {
        token
        for token in index
        for other in index
        if token != other and token in other and index[token] != index[other]
    }
    for token in contested:
        del index[token]
    return index


def find_places(text: str, index: dict[str, str]) -> set[str]:
    """Return the states named indirectly in *text* via place tokens."""
    lowered = _WS.sub(" ", _NON_ALNUM.sub(" ", _INVISIBLE.sub("", (text or "").lower())))
    matched = [t for t in index if re.search(rf"\b{re.escape(t)}\b", lowered)]
    # LONGEST MATCH WINS. "Charlotte-Mecklenburg Schools" contains "charlotte",
    # which is separately a Florida district (Charlotte County) -- so a single
    # North Carolina story resolved to both NC and FL. A token contained inside
    # a longer matched token is that token's fragment, not a second place.
    kept = [t for t in matched if not any(t != o and t in o for o in matched)]
    return {index[t] for t in kept}


async def load_place_index(session: Any) -> dict[str, str]:
    """Build the index from the live ``districts`` table."""
    from sqlalchemy import text as sql_text

    result = await session.execute(
        sql_text("SELECT name, state, enrollment FROM districts WHERE name IS NOT NULL")
    )
    return build_place_index([(r[0], r[1], r[2]) for r in result.all()])
