"""Name→email resolution against the directory_people cache.

Two entry points:

- ``resolve_people(query, session, limit, participants)`` — ranked candidate
  matches for a freeform name/email query. Returns a list of ``DirectoryMatch``
  ordered by confidence (descending), deduped by email.
- ``resolve_one(query, session, participants)`` — the email IFF there is a
  single confident, unambiguous match; otherwise ``None``. This is the safe
  entry point for automation (e.g. the post-meeting scheduler) that must not
  guess.

SECURITY: neither function is an identity-verification primitive. A name/email
*lookup* is not proof of *who is speaking*. Nothing here reads a caller's
identity, and nothing here should ever be wired into an authorization check —
see ``artemis.floating_artemis.tools.callie_dm`` for what a real identity gate
looks like (keyed on a verified Slack user id, never on a directory_people
name match).

Matching is done in Python over all ACTIVE rows (the roster is ~200 people, so
loading them all is fine). Priorities, highest first:

    1.00  exact email
    0.97  exact full_name
    0.95  "First Last"        (first name matches (see below) AND
                               last_name startswith the remaining tokens joined)
    0.90  "First L."          (first name matches AND
                               last_name startswith the last-initial)
                               → if MORE THAN ONE person matches at this tier,
                                 their confidence drops to _AMBIGUOUS_CONFIDENCE
                                 and reason is marked "ambiguous".
    0.90  first-name only     (first name matches; outranks fuzzy noise.
                               several people match → ALL of them drop to
                               _AMBIGUOUS_CONFIDENCE, reason "ambiguous" — a
                               first-name-only hit against more than one person
                               is low confidence by definition, not a coin flip
                               dressed up as 0.9)
    fuzzy difflib ratio >= 0.80 against full_name/first/last → ratio * 0.70

"first name matches" is exact OR a known nickname/formal-name equivalence
(see ``_NICKNAME_EQUIVALENTS``) — deliberately NOT a generic prefix or fuzzy
rule. "angel" and "angela" are two different given names, not a nickname pair,
and are in fact a CLOSER string match (difflib ratio ~0.91) than "josh" and
"joshua" (~0.80) — so no similarity threshold can treat the Josh/Joshua pair
as "the same name" without also merging Angel/Angela, which
``test_exact_first_name_beats_fuzzy`` deliberately guards against. Only a
short, curated table of real nickname/formal-name pairs can tell these apart.

PARTICIPANT PREFERENCE: when ``participants`` (display-name strings from the
live conversation, e.g. Slack's ``real_name``) is supplied and exactly one
candidate in an ambiguous group matches one of them, that candidate is
promoted out of the tie — "the person actually in the room" beats a
same-named stranger. If zero or more-than-one candidate matches, the group
stays ambiguous; presence is a tiebreaker, not an override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select

from artemis.directory.models import DirectoryPerson

_FUZZY_THRESHOLD = 0.80

# A first-name-only or first-name+tied match drops to this confidence when
# more than one active person plausibly matches. Comfortably below both of
# resolve_one's thresholds (0.90 strong-match floor, 0.60 unique-plausible
# floor) so a tied group never accidentally resolves, and low enough that a
# caller reading raw confidence cannot mistake it for a real answer.
_AMBIGUOUS_CONFIDENCE = 0.40

# A candidate that is confirmed present in the conversation (see
# ``participants``) and is the ONLY such candidate in an ambiguous group is
# promoted to this confidence — high enough to read as a real answer, but
# below the 0.97/1.00 exact tiers so it is visibly distinguishable from a
# genuine exact match if that ever matters to a caller.
_PARTICIPANT_RESOLVED_CONFIDENCE = 0.93

# Deliberately curated, NOT a general English-nicknames dictionary. Adding
# pairs on a guess is how you introduce NEW false ambiguity — e.g. "jon" ~
# "jonathan" would pull a third person into the same tied pool as every
# lookup of "Jon" in a workspace where that name already means the app's
# primary owner. Add a pair only when a real person here is actually known to
# go by both forms; this one exists because of the incident that prompted
# this module (2026-08-12): the workspace has both a Josh Smith and a Joshua
# Mukai who goes by "Josh", and the directory has no data field that records
# that (``display_name`` for that row is "Joshua Mukai", not "Josh").
_NICKNAME_EQUIVALENTS: dict[str, frozenset[str]] = {
    "josh": frozenset({"josh", "joshua"}),
    "joshua": frozenset({"josh", "joshua"}),
}


@dataclass
class DirectoryMatch:
    """A single ranked directory match."""

    email: str
    full_name: str
    confidence: float
    reason: str
    # True when this candidate's name matched one of the ``participants``
    # supplied to resolve_people/resolve_one — i.e. verified present in the
    # live conversation, not just present somewhere in the whole directory.
    in_conversation: bool = field(default=False)


async def _load_active_people(session: Any) -> list[DirectoryPerson]:
    result = await session.execute(
        select(DirectoryPerson).where(DirectoryPerson.is_active.is_(True))
    )
    return list(result.scalars().all())


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _first_name_matches(query_first: str, person_first: str) -> bool:
    """True if the two first-name strings plausibly name the same person.

    Exact match, or a known nickname/formal-name equivalence from
    ``_NICKNAME_EQUIVALENTS``. See the module docstring for why this is a
    curated table rather than a prefix/fuzzy heuristic.
    """
    if not query_first or not person_first:
        return False
    if query_first == person_first:
        return True
    equivalents = _NICKNAME_EQUIVALENTS.get(query_first)
    return equivalents is not None and person_first in equivalents


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

        if _first_name_matches(first_tok, first) and rest:
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

        # 0.90 — first-name only. A first-name hit must decisively outrank
        # fuzzy noise (e.g. "Angela" must beat fuzzy matches on "Angel"). When
        # several people plausibly match the first name, resolve_one still
        # declines because the matches tie within its 0.10 separation guard,
        # AND (fixed 2026-08-13) their confidence is demoted below — a
        # first-name-only hit against multiple people is low confidence by
        # definition, not just "ambiguous" in name while still reading 0.9.
        if len(tokens) == 1 and _first_name_matches(first_tok, first):
            return 0.90, "first name only"

    # Fuzzy fallback against full_name / first / last.
    best = max(_ratio(query, full_name), _ratio(query, first), _ratio(query, last))
    if best >= _FUZZY_THRESHOLD:
        return round(best * 0.70, 4), "fuzzy"

    return 0.0, ""


def _normalize_participants(participants: list[str] | None) -> list[tuple[str, str]]:
    """Split participant display-name strings into (first, last) lowercased pairs.

    ``last`` is "" when the label is a single token. Blank/whitespace-only
    entries are dropped.
    """
    out: list[tuple[str, str]] = []
    for label in participants or []:
        parts = (label or "").strip().lower().split()
        if not parts:
            continue
        out.append((parts[0], parts[-1] if len(parts) > 1 else ""))
    return out


def _matches_participant(person: DirectoryPerson, participant_names: list[tuple[str, str]]) -> bool:
    """True if ``person`` plausibly IS one of the supplied conversation participants.

    Requires the first name to match (exact or nickname-equivalent, same rule
    as query matching) AND, when the participant label carries a last name,
    the last name to match too — a first-name-only participant label is not
    enough on its own to claim a specific directory row, since that would
    just be re-introducing the same first-name-only ambiguity from the other
    direction.
    """
    person_first = (person.first_name or "").lower()
    person_last = (person.last_name or "").lower()
    if not person_first:
        return False
    for label_first, label_last in participant_names:
        if not _first_name_matches(label_first, person_first):
            continue
        if not label_last:
            continue  # first-name-only label: not enough to claim a row
        if person_last and (
            person_last == label_last
            or person_last.startswith(label_last)
            or label_last.startswith(person_last)
        ):
            return True
    return False


async def resolve_people(
    query: str,
    session: Any,
    limit: int = 5,
    participants: list[str] | None = None,
) -> list[DirectoryMatch]:
    """Return up to ``limit`` ranked matches for ``query`` (active people only).

    ``participants`` (optional): display names of people verified present in
    the current conversation. Used only as an ambiguity tiebreaker (see the
    module docstring) — never to grant a match that scoring itself did not
    already produce.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    tokens = q.split()
    people = await _load_active_people(session)
    participant_names = _normalize_participants(participants)

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
            in_conversation=_matches_participant(person, participant_names),
        )
        if reason == "first + last initial":
            initial_tier_idx.append(len(matches))
        matches.append(match)

    # If a "First L." query matched more than one person, none of them is a
    # confident pick — demote the whole tier to ambiguous.
    if len(initial_tier_idx) > 1:
        for idx in initial_tier_idx:
            matches[idx].confidence = _AMBIGUOUS_CONFIDENCE
            matches[idx].reason = "ambiguous"

    # First-name-only matches are inherently ambiguous when several people
    # plausibly share the first name (exact or nickname-equivalent) — demote
    # BOTH the label and the score, so a caller reading confidence alone still
    # sees the truth.
    first_name_matches = [m for m in matches if m.reason == "first name only"]
    if len(first_name_matches) > 1:
        for m in first_name_matches:
            m.confidence = _AMBIGUOUS_CONFIDENCE
            m.reason = "ambiguous"

    # Participant tiebreak: within a demoted-ambiguous group, if EXACTLY one
    # candidate is verified present in the conversation, that one wins — a
    # person actually in the room beats a same-named stranger. Ties involving
    # zero or multiple present candidates are left ambiguous; presence breaks
    # ties, it does not override an absence of evidence.
    ambiguous = [m for m in matches if m.reason == "ambiguous"]
    present = [m for m in ambiguous if m.in_conversation]
    if len(present) == 1:
        present[0].confidence = _PARTICIPANT_RESOLVED_CONFIDENCE
        present[0].reason = "resolved via conversation participants"

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


async def resolve_one(
    query: str,
    session: Any,
    participants: list[str] | None = None,
) -> str | None:
    """Return the email IFF a single confident, unambiguous match exists.

    Resolves in two cases:
    - a STRONG match: top confidence >= 0.90 and no other match within 0.10 of it
      (e.g. exact email, "First Last", a unique "First L.", or an ambiguous
      first-name group resolved via ``participants``).
    - a UNIQUE plausible match: exactly one candidate above the noise floor
      (>= 0.60), e.g. the only "Angela"/"Kristen" in the roster. This is safe for
      automation because the caller proposes and the operator confirms before
      anything is sent — so a lone reasonable match is worth surfacing, not dropping.

    Otherwise (several plausible people, or nothing) returns ``None``.
    """
    matches = await resolve_people(query, session, limit=5, participants=participants)
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
