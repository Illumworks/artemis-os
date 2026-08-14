"""CONTACTS-1 -- turn Argus's decision_makers findings into district_contacts rows.

Why this module exists
-----------------------
Argus already researches people: a `decision_makers` finding names a
district's superintendent (or other decision-maker) in narrative prose,
e.g. "Dr. Dyann Mack, newly appointed superintendent (effective June 2026),
is the primary district leader." That is genuinely useful and completely
unusable -- nothing can answer "who runs Harford County", the email drafter
cannot personalise from it, and no single person could be removed on
request without editing the observation, which CLAUDE.md rule 3 forbids.

Resolution (see briefs/contacts-1-people-become-records.md): PII lives in
``district_contacts``, which is genuinely deletable (see
``artemis.marketing.contacts.delete_contact``); observations are NEVER
touched by anything in this module -- they keep exactly the prose they
always had, findable by district key exactly as before. The only new thing
is a derived, deletable record with a provenance pointer back to the
observation it was read from (``DistrictContact.source_observation_id``).

Confidence bar -- read this before changing the extractor
-----------------------------------------------------------
"A wrong person attached to a district is worse than an empty field,
because someone will write to them" (the brief, verbatim). Every design
choice below is in service of that: not clever named-entity recognition,
just a deliberately narrow pattern match that skips (and reports why)
anything it cannot be confident about. Concretely:

- Scoped to ``Dimension.DECISION_MAKERS`` only. Other dimensions
  (district_profile, recommended_angle, ...) mention "superintendent" in
  passing while narrating other facts; they are not Argus's authoritative
  answer to "who is the decision-maker", and re-extracting from them risks
  a name Argus itself only used as scene-setting. Item 2 of the brief
  ("when Argus writes a decision_makers finding...") frames the feature the
  same way.
- Any disqualifying marker (former/predecessor/outgoing/retiring/retired)
  ANYWHERE in the finding's text throws the WHOLE finding out, rather than
  trying to work out which of several names is the current one. Real
  example this bar exists for: observation 1452 names "Molinar" but as
  "Former FWISD superintendent ... now CEO of REV Partnership" while the
  district is under state takeover -- extracting Molinar as the current
  contact would be exactly the wrong-person case the brief warns about.
- Zero or more than one DISTINCT candidate name in a finding -> skip. One
  confident name is required, never a best guess among several. Real
  example: observation 1285 (Valparaiso) never names anyone at all ("former
  superintendent retained on retainer... new superintendent appointment
  timeline not yet public") -- skipped for lack of a name, independent of
  also tripping the disqualifying-marker check.
- Never extracts email or phone. There is no pattern for either in this
  module. If a future finding's text happens to contain one, it is left
  alone -- see ``artemis.marketing.contacts.create_argus_contact``, which
  always writes email=None, phone=None for rows created here.
- District attachment reuses ``artemis.argus.research._resolve_district_row``
  -- the SAME seam ARGUS-2 established for board_minutes/news lookups, so
  every consumer of a drawer key agrees on which ``districts`` row it means
  (or that it does not resolve at all). A free-text drawer key ("St. Louis
  Public Schools", "IL-U46" -- the known ARGUS-2 gap) never resolves, so a
  confidently-extracted name from such a finding is still skipped, with the
  name itself reported so nothing is silently lost.

Calibrated against the 14 real `district_research` observations mentioning
"superintendent" as of 2026-08-14: 8 are `decision_makers` findings, of
which exactly 1 (Harford County, "Dr. Dyann Mack") clears every bar above.
The other 7 are skipped for the reasons this module's tests exercise
verbatim against that real text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.argus.drawer import ARGUS_CATEGORY, ARGUS_SCOPE, Dimension
from artemis.marketing.contacts import create_argus_contact
from artemis.marketing.models import DistrictContact
from artemis.memory.models import MemoryObservation
from artemis.memory.raw_inputs import RawInput

_logger = logging.getLogger(__name__)


# ── Extraction heuristic ────────────────────────────────────────────────────

# Longest-first so "assistant superintendent" wins over a bare "superintendent"
# substring match; order here does not matter for correctness (both the
# alternation and _find_title_kw re-sort), only for readability.
_TITLE_KEYWORDS: tuple[str, ...] = (
    "assistant superintendent",
    "deputy superintendent",
    "interim superintendent",
    "superintendent",
    "school board president",
    "board president",
    "chief academic officer",
    "director of curriculum",
    "curriculum director",
)

# Any of these anywhere in a finding's text disqualifies the WHOLE finding --
# see the module docstring. Deliberately not scoped to "near the name": real
# text mixes tenses freely enough ("Superintendent transitioned...; former
# superintendent retained on retainer...") that trying to disambiguate which
# clause a marker "belongs to" would be exactly the guessing the brief warns
# against. "interim" is NOT here -- an interim superintendent is a real,
# current point of contact, not a disqualifier.
_DISQUALIFYING_MARKERS: tuple[str, ...] = (
    "former",
    "predecessor",
    "outgoing",
    "retiring",
    "retired",
    "ex-superintendent",
)

# Argus's own stub text for gaps (see artemis/argus/research.py) -- never a
# real finding, always skip before even looking for a name.
_STUB_PREFIXES: tuple[str, ...] = (
    "insufficient data",
    "no prior amira relationship data",
)

# Name = optional honorific + 2-4 capitalized tokens. \w matches Unicode
# letters under Python 3's default (str-pattern) regex mode, so accented
# names (e.g. "Andrea Castañeda") match without a special-case.
_NAME = r"(?:Dr\.|Mr\.|Ms\.|Mrs\.)?\s*[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){1,3}"

# Sentence splitter with negative lookbehinds for the same honorifics the
# name pattern recognizes -- "Dr. Dyann Mack" must not be split into "Dr."
# + "Dyann Mack" (which would silently drop the honorific from the stored
# name). Verified against all 8 real decision_makers observations before
# this module was written; see the accompanying test file.
_SENTENCE_SPLIT = re.compile(r"(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<=[.!?])\s+")


def _title_alternation() -> str:
    return "|".join(re.escape(t) for t in sorted(_TITLE_KEYWORDS, key=len, reverse=True))


# TITLE-then-NAME: "Superintendent Andrea Castañeda leads..." -- title keyword
# immediately (whitespace only) followed by a name-shaped token run.
_TITLE_THEN_NAME = re.compile(rf"(?i:\b(?:{_title_alternation()})\b)\s+(?P<name>{_NAME})")

# NAME-then-TITLE: "Myra Berry ... installed as permanent superintendent..." --
# a name-shaped token run at the START of a sentence, with a title keyword
# somewhere later in the same sentence (bounded window so it cannot reach
# into unrelated clauses).
_NAME_THEN_TITLE = re.compile(
    rf"(?P<name>{_NAME})\b.{{0,80}}?(?i:\b(?:{_title_alternation()})\b)"
)


def _find_title_kw(sentence: str) -> str:
    lowered = sentence.lower()
    for kw in sorted(_TITLE_KEYWORDS, key=len, reverse=True):
        if kw in lowered:
            return kw
    return ""


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


@dataclass(frozen=True)
class ExtractedPerson:
    name: str
    title: str | None


def extract_person(value: str) -> tuple[ExtractedPerson | None, str]:
    """Best-effort, deliberately conservative extraction of ONE named
    decision-maker + title from a `decision_makers` finding's value text.

    Returns (person_or_None, reason). ``reason`` is always populated -- on a
    skip it explains why (for the retroactive run's report); on a hit it
    names the pattern matched (for auditability).

    Never raises. Never returns email or phone -- there is no pattern for
    either here.
    """
    text = (value or "").strip()
    if not text:
        return None, "empty finding value"

    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _STUB_PREFIXES):
        return None, "stub/insufficient-data finding, not a real research result"
    if any(marker in lowered for marker in _DISQUALIFYING_MARKERS):
        return None, (
            "disqualifying transition/former-officeholder language present "
            "(former/predecessor/outgoing/retiring/retired) -- cannot confidently "
            "identify the CURRENT primary contact from this text"
        )

    candidates: dict[str, tuple[str, str]] = {}  # normalized name -> (raw_name, title_kw)
    for sentence in _SENTENCE_SPLIT.split(text):
        for match in _TITLE_THEN_NAME.finditer(sentence):
            raw = match.group("name").strip()
            candidates.setdefault(_normalize(raw), (raw, _find_title_kw(sentence)))
        name_match = _NAME_THEN_TITLE.match(sentence)
        if name_match:
            raw = name_match.group("name").strip()
            candidates.setdefault(_normalize(raw), (raw, _find_title_kw(sentence)))

    if not candidates:
        return None, "no clearly identifiable person name found in the finding text"
    if len(candidates) > 1:
        names = ", ".join(raw for raw, _ in candidates.values())
        return None, f"multiple distinct candidate names found ({names}) -- ambiguous, not stored"

    raw_name, title_kw = next(iter(candidates.values()))
    title = title_kw.title() if title_kw else None
    return (
        ExtractedPerson(name=raw_name, title=title),
        f"matched a confident name+title pattern (title={title_kw!r})",
    )


# ── District resolution ─────────────────────────────────────────────────────


async def resolve_district_id(district_key: str) -> int | None:
    """Resolve an Argus drawer district_key to a canonical districts.id.

    Delegates to ``artemis.argus.research._resolve_district_row`` -- the SAME
    seam ARGUS-2 established, so this module and board_minutes/news lookups
    always agree on which district a key means. Imported lazily (inside the
    function, not at module load) because ``research.py`` imports
    ``Dimension``/``DistrictFinding`` from ``artemis.argus.drawer`` at module
    level; importing ``research`` from THIS module at module level (drawer.py
    also imports from here — see its ``write_district_findings`` hook) would
    be circular. Deferred imports of exactly this kind are already the
    established pattern inside ``research.py`` itself.

    Returns None (never raises) when the key does not resolve -- a known,
    expected outcome for free-text drawer keys (St. Louis, Elgin, and others
    per the ARGUS-2 gap). Resolving those by name instead would risk
    attaching to the wrong same-named district and is deliberately not
    implemented, here or anywhere else.
    """
    from artemis.argus.research import _resolve_district_row

    row = await _resolve_district_row(district_key)
    return row.id if row is not None else None


# ── Orchestration ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractionResult:
    """One row of the extraction report -- always populated, success or skip."""

    observation_id: int
    district_key: str
    dimension: str
    outcome: str  # "created" | "updated" | "skipped"
    reason: str
    contact_id: int | None = None
    name: str | None = None
    title: str | None = None


async def extract_and_upsert(
    session: AsyncSession,
    *,
    district_key: str,
    observation_id: int,
    dimension: str,
    value: str,
) -> ExtractionResult:
    """Run the confidence-gated extractor on one finding and upsert
    district_contacts if (and only if) it clears every bar.

    Idempotent: calling this again for the same observation produces the
    same outcome (see ``create_argus_contact``'s dedup contract) rather than
    a duplicate row.
    """
    if dimension != Dimension.DECISION_MAKERS:
        return ExtractionResult(
            observation_id=observation_id,
            district_key=district_key,
            dimension=dimension,
            outcome="skipped",
            reason=(
                f"dimension={dimension!r} is not decision_makers -- out of scope for "
                "contact extraction (see module docstring)"
            ),
        )

    person, reason = extract_person(value)
    if person is None:
        return ExtractionResult(
            observation_id=observation_id,
            district_key=district_key,
            dimension=dimension,
            outcome="skipped",
            reason=reason,
        )

    district_id = await resolve_district_id(district_key)
    if district_id is None:
        return ExtractionResult(
            observation_id=observation_id,
            district_key=district_key,
            dimension=dimension,
            outcome="skipped",
            reason=(
                f"confident name found ({person.name!r}, title={person.title!r}) but "
                f"district_key={district_key!r} does not resolve to a canonical "
                "districts.id (free-text/non-numeric key) -- cannot attach without "
                "guessing which district row this is (the ARGUS-2 gap)"
            ),
            name=person.name,
            title=person.title,
        )

    contact, created = await create_argus_contact(
        session,
        district_id=district_id,
        name=person.name,
        title=person.title,
        source_observation_id=observation_id,
    )
    return ExtractionResult(
        observation_id=observation_id,
        district_key=district_key,
        dimension=dimension,
        outcome="created" if created else "updated",
        reason=reason,
        contact_id=contact.id,
        name=contact.name,
        title=contact.title,
    )


def _dimension_and_district_key(
    observation: MemoryObservation, raw_input: RawInput | None
) -> tuple[str, str] | None:
    """Recover (dimension, district_key) for one observation.

    Prefers the structured ``raw_inputs.payload`` (written by
    ``write_district_findings`` for every observation it produces) over
    re-parsing the content header text -- it is the same information Argus
    itself wrote down, not a re-derivation of it. Falls back to parsing the
    ``[Argus|<dimension>|<district_key>]`` header when ``raw_input_id`` is
    NULL (nullable "for backward compat" per MemoryObservation's own
    docstring, so some older rows may lack one) or the payload is missing
    either key.
    """
    if raw_input is not None and isinstance(raw_input.payload, dict):
        dimension = raw_input.payload.get("dimension")
        district_key = raw_input.payload.get("district_key")
        if isinstance(dimension, str) and isinstance(district_key, str) and dimension and district_key:
            return dimension, district_key

    header_match = re.match(r"^\[Argus\|([^|]+)\|(.+)\]", observation.content)
    if header_match is None:
        return None
    return header_match.group(1), header_match.group(2)


async def run_retroactive_extraction(session: AsyncSession) -> list[ExtractionResult]:
    """Re-derive district_contacts from every existing decision_makers finding.

    Scans all active (non-superseded) ``district_research`` observations,
    recovers each one's (dimension, district_key), and runs
    ``extract_and_upsert`` on it. Non-decision_makers observations are
    included in the scan (so the report accounts for all of them) but are
    reported as out-of-scope by ``extract_and_upsert`` itself, not silently
    dropped from the returned list.

    Idempotent -- safe to run again (CONTACTS-1 item 1: "available to run
    again"). The caller owns the transaction/commit, same as every other
    function in this module.
    """
    result = await session.execute(
        select(MemoryObservation, RawInput)
        .outerjoin(RawInput, RawInput.id == MemoryObservation.raw_input_id)
        .where(
            MemoryObservation.scope_kind == ARGUS_SCOPE.scope_kind,
            MemoryObservation.scope_id == ARGUS_SCOPE.scope_id,
            MemoryObservation.category == ARGUS_CATEGORY,
            MemoryObservation.superseded_by.is_(None),
        )
        .order_by(MemoryObservation.id)
    )
    rows = result.all()

    outcomes: list[ExtractionResult] = []
    for observation, raw_input in rows:
        parsed = _dimension_and_district_key(observation, raw_input)
        if parsed is None:
            _logger.warning(
                "run_retroactive_extraction: observation id=%s is not in Argus's "
                "[Argus|dimension|district_key] format and has no usable raw_input "
                "payload -- skipping, not ours to touch",
                observation.id,
            )
            continue
        dimension, district_key = parsed

        # The header/content is the authoritative VALUE text -- reuse the same
        # parser read_district_drawer relies on so extraction never drifts
        # from how Argus's own format is otherwise read.
        value = _extract_value_from_content(observation.content)
        outcome = await extract_and_upsert(
            session,
            district_key=district_key,
            observation_id=observation.id,
            dimension=dimension,
            value=value,
        )
        outcomes.append(outcome)
    return outcomes


def _extract_value_from_content(content: str) -> str:
    """Pull the finding's value text out of the canonical content block.

    Format: "[Argus|<dimension>|<district_key>] <value>\\nsource: ...\\n...".
    Returns "" (never raises) if the header is not in that shape -- callers
    already only reach here after ``_dimension_and_district_key`` confirmed
    the header parses, so this is a belt-and-suspenders fallback, not the
    primary guard.
    """
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    bracket_end = first_line.find("]")
    if bracket_end < 0:
        return ""
    return first_line[bracket_end + 2 :]


# ── Read-only lookup (Callie-facing; see floating_artemis/tools/marketing.py) ──


async def list_contacts_for_district_id(
    session: AsyncSession, district_id: int
) -> list[DistrictContact]:
    """Active contacts for one district, any source, sorted by id.

    Thin re-export of the district_id-keyed read path so the Callie-facing
    tool (and anything else that only has a resolved districts.id, not a
    drawer key) does not need to import artemis.marketing.contacts directly.
    Returns [] for a district with none -- never guesses, never raises.
    """
    stmt = (
        select(DistrictContact)
        .where(DistrictContact.district_id == district_id, DistrictContact.active.is_(True))
        .order_by(DistrictContact.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
