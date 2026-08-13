"""Argus-specific board-minutes relevance predicate.

Why this exists (ARGUS-4) -- NOT a fix to ``mapping._is_relevant``
--------------------------------------------------------------------
``artemis.argus.research._fetch_board_minutes`` used to reuse
``artemis.scouts.board_minutes.mapping._is_relevant`` to decide which
title-only BoardDocs agenda items are worth the extra HTTP call to fetch a
body (ARGUS-3). That function is deliberately broad for its own purpose --
the board-minutes SCOUT feeds a human-reviewed signal queue, so a generous
gate that occasionally lets a routine item through costs one skipped card. A
false positive there is cheap.

For Argus it is not cheap. Argus shares an 8-second body-fetch budget
(``settings.argus_board_minutes_body_budget_s``) and a 20-item cap
(``settings.argus_board_minutes_body_cap``) across FIVE research sources
in one ``asyncio.gather`` (see ``research._gather_tool_results``). Every
body fetch spent on an irrelevant item is budget the real signal (if any)
does not get.

Measured against Dallas ISD's live BoardDocs agenda on 2026-08-13 (146
agenda items, 87 unique after de-duplicating the "Board Meeting" +
"Board Briefing" agenda pairs that BoardDocs publishes for the same vote):
``mapping._is_relevant`` passed 16 raw items / 8 unique, and ALL EIGHT were
false positives for literacy-procurement purposes. They matched on:

  - the bare keyword "vendor" -- matched contract awards for building
    renovation, food-service paper products, and workers'-comp health
    care management, none of which have anything to do with literacy.
  - the bare keyword "reading" -- matched a Bible-reading-in-schools
    resolution (Texas SB 11 "Period of Prayer and Reading of the Bible").
  - the bare phrase "instructional materials" -- matched a dual-credit
    (non-literacy) instructional-materials purchase.
  - the bare keyword "adsy" (Additional Days School Year) -- matched a
    generic missed-instructional-days waiver with no literacy content in
    it at all; ADSY covers every subject, not just reading intervention.

This predicate is precision-first: every one of those four trigger classes
now requires a literacy/reading/ELA/dyslexia/phonics context word (or is
simply removed as a standalone trigger). See ``_CONTEXT_REQUIRED_PHRASES``
and ``_DIVERSIFIED_LITERACY_VENDORS`` below.

Two-stage (title filter -> LLM judges the body) was considered and
rejected for now
-----------------------------------------------------------------------
The brief that produced this module asked whether title-only matching is
even sufficient, or whether a cheap two-stage design -- broad title filter,
then have something (an LLM) judge the fetched body before it reaches
synthesis -- would do better. This was tested against real bodies, not
assumed.

Findings, fetching real ``BD-GetAgendaItem`` bodies for the ambiguous
titles found while validating this predicate (Dallas ISD, Charlotte-
Mecklenburg Schools, Wake County Schools -- all on the structured BoardDocs
AJAX API path, ``fetch_boarddocs``'s primary path):

  - For routine CONSENT/ACTION agenda items, the body the public
    ``BD-GetAgendaItem`` endpoint returns is almost always just
    ``Category`` / ``Subject`` (= the title, verbatim) / ``Type`` /
    (once decided) ``Motion & Voting`` with a trustee roll call. It does
    NOT carry a subject narrative beyond the title for the vast majority
    of items sampled.
  - Concretely: Charlotte-Mecklenburg's "Recommend Approval of Imagine
    EdgeEX & On-Demand Tutoring Curriculum Platform Contract" is genuinely
    ambiguous from the title alone (Imagine Learning sells math, ELA, and
    credit-recovery products under one brand) -- and fetching its body
    added ZERO disambiguating text. A body-judging LLM stage would have
    re-read the identical string plus boilerplate, at LLM cost, for no
    gain. This predicate deliberately treats "Imagine EdgeEX" as NOT
    relevant rather than guess.
  - The one place a body demonstrably adds value is exactly what ARGUS-3
    already built: the ``Motion & Voting`` roll call is real
    ``decision_makers`` signal that titles never carry. That is a body
    FETCH win, not a body-JUDGE win -- no extra relevance decision is
    needed once an item has already passed the title gate, because the
    downstream LLM synthesis pass (``research._run_synthesis``) already
    reads the enriched body text.

Conclusion: a tightened title-only gate, not a new body-judging stage, is
the right fix here. If a future district's BoardDocs configuration turns
out to attach richer "Recommended Action" narratives to consent items
(this was NOT observed on the three districts checked, all using the
structured API path -- boards on the PDF-fallback path were not checked
and may differ, since a scanned PDF page of real minutes text is a
different animal from this AJAX endpoint), revisit this call.

Validation
----------
This predicate was run against every unique title fetched from 11 real
districts' live BoardDocs agendas (Dallas ISD, Humble ISD, Charlotte-
Mecklenburg, Wake County, San Diego Unified, Pinellas, Gwinnett County,
Indianapolis Public Schools, Charleston 01, Canyons District, plus a
Miami-Dade fallback-path fetch) -- roughly 2,000 unique agenda items.
Zero false positives were found on manual review of every item flagged.
Two districts had genuine true positives with no procurement angle at
all (San Diego's "Dual Language Programs and Seal of Biliteracy Pathways"
committee item, Charleston's three early-literacy proficiency monitoring
reports, one naming a real early-literacy screener --
"myIGDIs Sound ID/Rhyming") -- both correctly caught by the phrase-level
checks below, without any named-vendor or procurement wording present.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Tier 1 -- phrases that are inherently about literacy curriculum, screening,
# or intervention.  No further context is required: nothing outside the
# literacy space is plausibly described by these phrases.
# ---------------------------------------------------------------------------

_LITERACY_PHRASES: tuple[str, ...] = (
    "science of reading",
    "structured literacy",
    "systematic phonics",
    "orton-gillingham",
    "foundational literacy",
    "early literacy",
    "literacy intervention",
    "reading intervention",
    "reading curriculum",
    "reading program",
    "reading diagnostic",
    "reading screener",
    "reading screening",
    "dyslexia screening",
    "dyslexia intervention",
    "dyslexia services",
    "dyslexia program",
    "reading academy",
    "reading academies",  # TX HB3-mandated K-3 teacher reading training
    "k-3 reading",
    "k3 reading",
    "tier 2 reading",
    "tier 3 reading",
    "mtss reading",
    "reading specialist",
    "phonics program",
    "phonics curriculum",
    "ela curriculum",
    "ela adoption",
    "ela instructional materials",
    "language arts curriculum",
    "language arts adoption",
    "literacy curriculum",
    "literacy adoption",
    "literacy coach",
    "biliteracy",
    "dual language literacy",
    "literacy screener",
    "literacy screening",
    "literacy diagnostic",
    "reading tutoring",
    "literacy tutoring",
)

# ---------------------------------------------------------------------------
# Tier 2 -- named literacy vendors/programs with a SINGLE product line
# (literacy). The name alone is unambiguous, so no context word is required.
# ---------------------------------------------------------------------------

_LITERACY_ONLY_VENDORS: tuple[str, ...] = (
    "lexia",
    "wilson reading",
    "fountas and pinnell",
    "fountas & pinnell",
    "dibels",
    "acadience",
    "heggerty",
    "ufli",
    "95 phonics",
    "95 percent group",
    "reading horizons",
    "really great reading",
    "core knowledge language arts",
    "ckla",
    "wit & wisdom",
    "wit and wisdom",
    "bookworms",
    "learning without tears",
    "mclass",
    "voyager sopris",
    "benchmark advance",
    "into reading",
    "readworks",
    "read works",
    "myigdis",
)

# ---------------------------------------------------------------------------
# Tier 3 -- named vendors whose product catalog spans well beyond literacy
# (math, science, credit recovery, ...).  The bare name is NOT sufficient on
# its own -- e.g. a district buying "Amplify Science" or "Renaissance STAR
# Math" under the same corporate umbrella must not register as a literacy
# hit.  Requires pairing with an explicit literacy/reading/ELA/dyslexia/
# phonics context word in the same text.
# ---------------------------------------------------------------------------

_DIVERSIFIED_LITERACY_VENDORS: tuple[str, ...] = (
    "i-ready",
    "iready",
    "amplify",
    "istation",
    "waterford",
    "renaissance",
    "imagine learning",
    "houghton mifflin harcourt",
    r"\bhmh\b",
    "wonders",
    "scholastic",
)

_LITERACY_CONTEXT_WORDS: tuple[str, ...] = (
    "reading",
    "literacy",
    r"\bela\b",
    "dyslexia",
    "phonics",
    "language arts",
)

# ---------------------------------------------------------------------------
# Tier 4 -- generic procurement/policy phrases that produced Dallas's false
# positives when used bare (see module docstring).  Same rule as Tier 3:
# only relevant when paired with a literacy context word.
# ---------------------------------------------------------------------------

_CONTEXT_REQUIRED_PHRASES: tuple[str, ...] = (
    "curriculum adoption",
    "instructional materials",
    "instructional materials adoption",
    "tutoring",
    "hb 1416",
    "hb1416",
    "adsy",
    "tutoring waiver",
    "screener",
    "screening",
    "intervention",
)


def _match(term: str, lower: str) -> bool:
    """Word-boundary regex match for short/ambiguous tokens (``\\b`` prefix),
    plain substring match otherwise."""
    if term.startswith(r"\b"):
        return re.search(term, lower) is not None
    return term in lower


def is_argus_relevant(text: str) -> bool:
    """Return True if *text* is worth Argus's HTTP body-fetch budget.

    Precision-first by design (see module docstring): a missed item costs
    one line of a dossier, a false positive costs part of a shared,
    capped, time-boxed budget. When in doubt, this returns False.
    """
    lower = text.lower()

    if any(_match(p, lower) for p in _LITERACY_PHRASES):
        return True
    if any(_match(v, lower) for v in _LITERACY_ONLY_VENDORS):
        return True

    has_context = any(_match(c, lower) for c in _LITERACY_CONTEXT_WORDS)
    if has_context and any(_match(v, lower) for v in _DIVERSIFIED_LITERACY_VENDORS):
        return True
    return has_context and any(_match(p, lower) for p in _CONTEXT_REQUIRED_PHRASES)
