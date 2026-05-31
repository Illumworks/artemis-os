"""Tool: district.resolve

Deterministic fuzzy name-resolver that maps a raw district name string
(+ optional state hint) to a canonical row in the ``districts`` table.

Design constraints (D-2, D-6, hallucination firewall):
- This tool does name-resolution ONLY.
- It NEVER sets enrollment or tier — those are NCES data + DIST1's pure function.
- On no confident match it returns a no-match result; the caller MUST NOT
  fabricate a district row or guess the ID.

Matching strategy (pure-deterministic layer; LLM adjudicates only ties):
1. Normalise input → casefold, strip punctuation, collapse whitespace.
2. Expand well-known abbreviations (LAUSD, NYCDOE, CPS, …) before matching.
3. Optional state filter applied before any string comparison.
4. Exact normalised match → HIGH confidence (0.95).
5. Prefix / suffix match (ignoring " School District" / " Unified" suffixes)
   → MEDIUM confidence (0.75).
6. Word-overlap Jaccard ≥ 0.8 → MEDIUM confidence (0.70).
7. Below threshold or multiple equally-scored candidates → no-match.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import District
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common abbreviation expansion map
# Keys must already be normalised (casefold + stripped).
# ---------------------------------------------------------------------------
ABBREVIATION_MAP: dict[str, str] = {
    "lausd": "los angeles unified",
    "nycdoe": "new york city department of education",
    "nyc doe": "new york city department of education",
    "cps": "chicago public schools",
    "mdcps": "miami-dade county public schools",
    "miami-dade": "miami-dade county public schools",
    "hisd": "houston independent school district",
    "disd": "dallas independent school district",
    "dallas isd": "dallas independent school district",
    "bisd": "brownsville independent school district",
    "aisd": "austin independent school district",
    "austin isd": "austin independent school district",
    "dcps": "district of columbia public schools",
    "bcps": "broward county public schools",
    "pgcps": "prince george's county public schools",
    "hcps": "hillsborough county public schools",
    "ocps": "orange county public schools",
    "mcps": "montgomery county public schools",
    "fcps": "fairfax county public schools",
    "lcps": "loudoun county public schools",
    "ccsd": "clark county school district",
    "fusd": "fresno unified school district",
    "fresno unified": "fresno unified school district",
    "lausd unified": "los angeles unified",
}

# Suffixes to strip when building "bare" names for suffix-insensitive matching.
_STRIP_SUFFIXES = (
    " school district",
    " school districts",
    " unified school district",
    " city school district",
    " public schools",
    " independent school district",
    " community school district",
    " local school district",
    " county schools",
    " county public schools",
    " county school district",
    " city schools",
    " schools",
    " unified",
    " isd",
    " usd",
    " csd",
)

# Confidence thresholds
_CONFIDENT_THRESHOLD = 0.70
_HIGH_CONF = 0.95
_MEDIUM_CONF = 0.75
_WORD_JACCARD_CONF = 0.70


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    matched: bool
    district_id: int | None
    district_name: str | None
    district_state: str | None
    confidence: float
    match_method: str  # "exact" | "prefix" | "jaccard" | "no_match"
    message: str


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Casefold, strip punctuation except hyphen/apostrophe, collapse whitespace."""
    text = text.casefold().strip()
    text = re.sub(r"[^\w\s'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _expand_abbreviation(normalised: str) -> str:
    return ABBREVIATION_MAP.get(normalised, normalised)


def _bare(normalised: str) -> str:
    """Strip common district suffixes to allow suffix-insensitive matching."""
    result = normalised
    for suffix in _STRIP_SUFFIXES:
        if result.endswith(suffix):
            result = result[: -len(suffix)].strip()
            break
    return result


def _word_set(text: str) -> frozenset[str]:
    return frozenset(w for w in text.split() if len(w) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


# ---------------------------------------------------------------------------
# Core resolver (pure — no I/O, operates on in-memory District list)
# ---------------------------------------------------------------------------


def resolve_district_from_list(
    name: str,
    state: str | None,
    districts: list[District],
) -> ResolveResult:
    """Resolve ``name`` against a pre-loaded list of District ORM rows.

    This is the pure inner function, separated from DB I/O for testability.
    """
    if not districts:
        return ResolveResult(
            matched=False,
            district_id=None,
            district_name=None,
            district_state=None,
            confidence=0.0,
            match_method="no_match",
            message="districts table is empty — no match possible",
        )

    norm_input = _normalise(name)
    expanded = _expand_abbreviation(norm_input)
    bare_input = _bare(expanded)
    words_input = _word_set(expanded)

    state_upper = state.strip().upper() if state else None

    # State-filter: if a state hint is provided, restrict candidates.
    candidates = districts
    if state_upper:
        state_filtered = [d for d in districts if (d.state or "").upper() == state_upper]
        # Only apply filter if it doesn't eliminate ALL rows (data gap protection).
        if state_filtered:
            candidates = state_filtered

    # Build scored candidate list.
    scored: list[tuple[float, str, District]] = []

    for d in candidates:
        norm_db = _normalise(d.name)
        expanded_db = _expand_abbreviation(norm_db)
        bare_db = _bare(expanded_db)
        words_db = _word_set(expanded_db)

        # 1. Exact normalised match (after abbreviation expansion)
        if expanded == expanded_db:
            scored.append((_HIGH_CONF, "exact", d))
            continue

        # 2. Bare-name match (suffix-insensitive)
        if bare_input and bare_db and bare_input == bare_db:
            scored.append((_MEDIUM_CONF, "prefix", d))
            continue

        # 3. Jaccard word overlap
        if words_input and words_db:
            j = _jaccard(words_input, words_db)
            if j >= 0.8:
                scored.append((_WORD_JACCARD_CONF, "jaccard", d))

    if not scored:
        return ResolveResult(
            matched=False,
            district_id=None,
            district_name=None,
            district_state=None,
            confidence=0.0,
            match_method="no_match",
            message=f"no district found matching {name!r}",
        )

    # Sort by confidence desc, then by district id (stable tiebreak).
    scored.sort(key=lambda t: (-t[0], t[2].id))
    top_conf, top_method, top_d = scored[0]

    # Ambiguity check: if two candidates share the same top confidence,
    # and they are different districts, leave NULL (D-2 hallucination firewall).
    if len(scored) >= 2 and scored[1][0] == top_conf and scored[1][2].id != top_d.id:
        # State hint helps disambiguate — if we already have a state filter applied
        # and there are still ties, that is a genuine data ambiguity → no-match.
        return ResolveResult(
            matched=False,
            district_id=None,
            district_name=None,
            district_state=None,
            confidence=top_conf,
            match_method="no_match",
            message=(
                f"ambiguous: {name!r} matches multiple districts at confidence {top_conf:.2f}; "
                "provide a state hint to disambiguate"
            ),
        )

    if top_conf < _CONFIDENT_THRESHOLD:
        return ResolveResult(
            matched=False,
            district_id=None,
            district_name=None,
            district_state=None,
            confidence=top_conf,
            match_method="no_match",
            message=f"best match confidence {top_conf:.2f} is below threshold {_CONFIDENT_THRESHOLD}",
        )

    return ResolveResult(
        matched=True,
        district_id=top_d.id,
        district_name=top_d.name,
        district_state=top_d.state,
        confidence=top_conf,
        match_method=top_method,
        message=f"resolved to {top_d.name!r} (id={top_d.id}) via {top_method}",
    )


async def resolve_district(
    session: AsyncSession,
    name: str,
    state: str | None = None,
) -> ResolveResult:
    """DB-backed resolver: loads candidate districts then calls the pure resolver."""
    result = await session.execute(select(District).order_by(District.id))
    districts = list(result.scalars().all())
    return resolve_district_from_list(name, state, districts)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

_MARKETING_PREFIX = "marketing."

_DEF = Tool(
    name="district.resolve",
    description=(
        "Resolve a raw district name string to a canonical districts table row. "
        "Returns the matched district_id and confidence on success; returns "
        "matched=false (no district created) when no confident match exists. "
        "This tool resolves NAMES ONLY — it never sets enrollment or tier. "
        "Only marketing agents may call this tool."
    ),
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Raw district name from the signal (e.g. 'LAUSD', "
                    "'Los Angeles Unified School District')."
                ),
            },
            "state": {
                "type": "string",
                "description": "Optional 2-letter US state code hint for disambiguation.",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot resolve districts"

        name = arguments.get("name")
        if not isinstance(name, str) or not name.strip():
            return "VALIDATION_ERROR: 'name' is required and must be a non-empty string"

        state: str | None = arguments.get("state")
        if state is not None and not isinstance(state, str):
            state = None

        result = await resolve_district(ctx.session, name.strip(), state)
        logger.info(
            "district.resolve: agent=%s name=%r state=%r matched=%s district_id=%s confidence=%.2f",
            ctx.agent_id,
            name,
            state,
            result.matched,
            result.district_id,
            result.confidence,
        )
        return json.dumps(asdict(result))

    return (_DEF, _impl)


register_tool("district.resolve", _factory)
