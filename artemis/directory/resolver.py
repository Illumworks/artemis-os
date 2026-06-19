"""Name→email resolution against the directory_people cache.

Two entry points:

- ``resolve_people(query, session, limit)`` — ranked candidate matches for a
  freeform name/email query. Returns a list of ``DirectoryMatch`` ordered by
  confidence (descending), deduped by email.
- ``resolve_one(query, session)`` — the email IFF there is a single confident,
  unambiguous match; otherwise ``None``. This is the safe entry point for
  automation (e.g. the post-meeting scheduler) that must not guess.

Matching is done in Python over all ACTIVE rows (the roster is ~58 people, so
loading them all is fine). Priorities, highest first:

    1.00  exact email
    0.97  exact full_name
    0.95  "First Last"        (first_name == first token AND
                               last_name startswith the remaining tokens joined)
    0.90  "First L."          (first_name == first token AND
                               last_name startswith the last-initial)
                               → if MORE THAN ONE person matches at this tier,
                                 their confidence drops to ~0.70 and reason is
                                 marked "ambiguous".
    0.90  first-name only     (exact first-name hit; outranks fuzzy noise.
                               several people share it → resolve_one declines on
                               the 0.10 separation guard)
    fuzzy difflib ratio >= 0.80 against full_name/first/last → ratio * 0.70
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select

from artemis.directory.models import DirectoryPerson

_FUZZY_THRESHOLD = 0.80
_AMBIGUOUS_INITIAL_CONFIDENCE = 0.70


@dataclass
class DirectoryMatch:
    """A single ranked directory match."""

    email: str
    full_name: str
    confidence: float
    reason: str


async def _load_active_people(session: Any) -> list[DirectoryPerson]:
    result = await session.execute(
        select(DirectoryPerson).where(DirectoryPerson.is_active.is_(True))
    )
    return list(result.scalars().all())


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_person(query: str, tokens: list[str], person: DirectoryPerson) -> tuple[float, str]:
    """Return (confidence, reason) for one person against a normalised query.

    ``query`` is already lowercased + trimmed; ``tokens`` is query.split().
    Confidence 0.0 means "no match" (caller drops it).
    """
    email = (person.email or "").lower()
    full_name = (person.full_name or "").lower()
    first = (person.first_name or "").lower()
    last = (person.last_name or "").lower()

    # 1.00 — exact email
    if query == email:
        return 1.0, "exact email"

    # 0.97 — exact full name
    if query == full_name:
        return 0.97, "exact full name"

    if tokens:
        first_tok = tokens[0]
        rest = tokens[1:]

        if first == first_tok and rest:
            rest_joined = " ".join(rest)
            initial = rest_joined.replace(".", "").strip()
            # "First L." — a bare last-initial (e.g. "Julie K", "Julie K.").
            # This is the ambiguous-prone tier (0.90), so it takes precedence
            # over the "First Last" tier when the remainder is just an initial.
            is_initial_only = len(rest) == 1 and len(initial) == 1
            if is_initial_only and last.startswith(initial):
                return 0.90, "first + last initial"
            # 0.95 — "First Last": last name startswith the remaining tokens.
            if last and last.startswith(rest_joined):
                return 0.95, "first + last"

        # 0.90 — first-name only. An EXACT first-name hit must decisively outrank
        # fuzzy noise (e.g. "Angela" must beat fuzzy matches on "Angel"). When
        # several people share the first name, resolve_one still declines because
        # the matches tie within its 0.10 separation guard.
        if len(tokens) == 1 and first == first_tok:
            return 0.90, "first name only"

    # Fuzzy fallback against full_name / first / last.
    best = max(_ratio(query, full_name), _ratio(query, first), _ratio(query, last))
    if best >= _FUZZY_THRESHOLD:
        return round(best * 0.70, 4), "fuzzy"

    return 0.0, ""


async def resolve_people(query: str, session: Any, limit: int = 5) -> list[DirectoryMatch]:
    """Return up to ``limit`` ranked matches for ``query`` (active people only)."""
    q = (query or "").strip().lower()
    if not q:
        return []

    tokens = q.split()
    people = await _load_active_people(session)

    matches: list[DirectoryMatch] = []
    # Track which people matched at the "first + last initial" tier so we can
    # demote them to ambiguous when more than one person matches that way.
    initial_tier_idx: list[int] = []

    for person in people:
        confidence, reason = _score_person(q, tokens, person)
        if confidence <= 0.0:
            continue
        match = DirectoryMatch(
            email=(person.email or "").lower(),
            full_name=person.full_name or "",
            confidence=confidence,
            reason=reason,
        )
        if reason == "first + last initial":
            initial_tier_idx.append(len(matches))
        matches.append(match)

    # If a "First L." query matched more than one person, none of them is a
    # confident pick — demote the whole tier to ambiguous.
    if len(initial_tier_idx) > 1:
        for idx in initial_tier_idx:
            matches[idx].confidence = _AMBIGUOUS_INITIAL_CONFIDENCE
            matches[idx].reason = "ambiguous"

    # First-name-only matches are inherently ambiguous when several people share
    # the first name — mark them so callers can see it.
    first_name_matches = [m for m in matches if m.reason == "first name only"]
    if len(first_name_matches) > 1:
        for m in first_name_matches:
            m.reason = "ambiguous"

    # Dedup by email, keeping the highest-confidence row per email.
    best_by_email: dict[str, DirectoryMatch] = {}
    for m in matches:
        existing = best_by_email.get(m.email)
        if existing is None or m.confidence > existing.confidence:
            best_by_email[m.email] = m

    ranked = sorted(
        best_by_email.values(),
        key=lambda m: (m.confidence, m.full_name.lower()),
        reverse=True,
    )
    return ranked[: max(0, limit)]


async def resolve_one(query: str, session: Any) -> str | None:
    """Return the email IFF a single confident, unambiguous match exists.

    Resolves in two cases:
    - a STRONG match: top confidence >= 0.90 and no other match within 0.10 of it
      (e.g. exact email, "First Last", a unique "First L.").
    - a UNIQUE plausible match: exactly one candidate above the noise floor
      (>= 0.60), e.g. the only "Angela"/"Kristen" in the roster. This is safe for
      automation because the caller proposes and the operator confirms before
      anything is sent — so a lone reasonable match is worth surfacing, not dropping.

    Otherwise (several plausible people, or nothing) returns ``None``.
    """
    matches = await resolve_people(query, session, limit=5)
    if not matches:
        return None

    top = matches[0]

    # Strong, clearly-separated match.
    if top.confidence >= 0.90 and (
        len(matches) == 1 or top.confidence - matches[1].confidence >= 0.10
    ):
        return top.email

    # Unique plausible match — exactly one candidate above the noise floor.
    if len(matches) == 1 and top.confidence >= 0.60:
        return top.email

    return None
