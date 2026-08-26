"""Deciding whether a district is one Josh sells into.

The whole point of C5 is that Callie surfaced three districts and two were
existing customers. The fix is a membership test against the target list -- but
the test has three outcomes, not two, and collapsing them is how it goes wrong.

**TARGET** — matched a row in ``target_accounts``.
**NOT_TARGET** — did not match, and we are confident the name WOULD have matched
if it were there.
**UNKNOWN** — we could not tell.

UNKNOWN is not a polite NOT_TARGET, and this is the load-bearing distinction.
Salesforce account names and NCES district names diverge badly enough that a
fifth of the list does not resolve ("Sweetwater Union School District" vs
"Sweetwater Union High"). Treating those as NOT_TARGET would silently bury real
opportunities -- the same class of error as the gazetteer that confidently put
San Diego in Texas because the match was unique in an incomplete table. So a
signal we cannot classify is surfaced as unclassified, never dropped.

Normalization is deliberately mild. Every token removed here is a token that can
no longer tell two districts apart, and the live data already contains a pair
that collides: "Hempfield Area School District" and "Hempfield School District"
in PA are different districts with the same normalized form. Stripping harder
would merge more of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Words that carry no distinguishing information between districts in the same
# state. Kept SHORT on purpose -- see the module docstring. "AREA" earns its
# place only because NCES and Salesforce disagree on it constantly.
_STOPWORDS = (
    r"\b(PUBLIC|SCHOOLS|SCHOOL|DISTRICT|DISTRICTS|ISD|USD|CSD|SD|UNIFIED"
    r"|INDEPENDENT|COMMUNITY|CONSOLIDATED|AREA|PARISH|OF|THE|NO|NUMBER)\b"
)
# NCES suffixes its names with a local code: "Mesa Unified District (4235)",
# "Chandler Unified District #80 (4242)". Salesforce never carries these.
# Stripping them alone lifted the match rate from 49.5% to 80.5%.
_LOCAL_CODE = re.compile(r"\(\s*\d+\s*\)|#\s*\d+")


class Verdict(StrEnum):
    TARGET = "target"
    NOT_TARGET = "not_target"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MatchOutcome:
    """A verdict plus the evidence for it, so a person can audit the call."""

    verdict: Verdict
    target_account_id: int | None = None
    account_name: str = ""
    marketing_tier: str = ""
    reason: str = ""

    @property
    def is_target(self) -> bool:
        return self.verdict is Verdict.TARGET


def normalize_district_name(name: str) -> str:
    """Reduce a district name to its distinguishing core.

    Returns "" when nothing distinguishing survives -- e.g. "Community
    Independent School District" (a real TX account) is entirely stopwords. An
    empty result is a REFUSAL to produce a key, and callers must treat it as
    unmatchable rather than as a key that matches everything.
    """
    value = (name or "").upper().strip()
    value = _LOCAL_CODE.sub(" ", value)
    value = re.sub(r"[^A-Z0-9 ]", " ", value)
    value = re.sub(_STOPWORDS, " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class TargetIndex:
    """The target universe in memory, and the single implementation of the rules.

    Built once and reused. The alternative -- a query per signal -- is both slow
    and, worse, an invitation to write the matching rules a second time inside
    whatever SQL the caller needs. There is one set of rules and it lives here;
    ``classify_district`` below is a convenience wrapper, not a second copy.
    """

    by_exact: dict[tuple[str, str], _Target]
    by_normalized: dict[tuple[str, str], list[_Target]]
    by_district_id: dict[int, _Target]
    states: frozenset[str]

    def classify(
        self,
        *,
        district_name: str | None,
        state: str | None,
        district_id: int | None = None,
    ) -> MatchOutcome:
        """Decide whether this district is in the target universe.

        Resolution order, most reliable first: a resolved NCES id, then an exact
        ``(state, account_name)``, then a unique ``(state, normalized_name)``.
        Anything else abstains with the reason recorded.
        """
        state_code = (state or "").strip().upper()

        if district_id is not None:
            hit = self.by_district_id.get(district_id)
            if hit is not None:
                return MatchOutcome(
                    verdict=Verdict.TARGET,
                    target_account_id=hit.id,
                    account_name=hit.account_name,
                    marketing_tier=hit.marketing_tier,
                    reason="matched on resolved district id",
                )
            # Deliberately falls through: only ~80% of target rows carry a
            # district_id at all, so an absent link proves nothing either way.

        name = (district_name or "").strip()
        if not name:
            return MatchOutcome(
                verdict=Verdict.UNKNOWN,
                reason="the signal names no district, so it cannot be matched to an account",
            )
        if not state_code:
            return MatchOutcome(
                verdict=Verdict.UNKNOWN,
                reason=f"no state for {name!r}; district names are only unique within a state",
            )

        exact = self.by_exact.get((state_code, name))
        if exact is not None:
            return MatchOutcome(
                verdict=Verdict.TARGET,
                target_account_id=exact.id,
                account_name=exact.account_name,
                marketing_tier=exact.marketing_tier,
                reason="exact name and state match",
            )

        key = normalize_district_name(name)
        if not key:
            return MatchOutcome(
                verdict=Verdict.UNKNOWN,
                reason=(
                    f"{name!r} is made up entirely of generic words, so it cannot be "
                    "matched to a specific district without guessing"
                ),
            )

        candidates = self.by_normalized.get((state_code, key), [])
        if len(candidates) == 1:
            hit = candidates[0]
            return MatchOutcome(
                verdict=Verdict.TARGET,
                target_account_id=hit.id,
                account_name=hit.account_name,
                marketing_tier=hit.marketing_tier,
                reason=f"matched {hit.account_name!r} on normalized name",
            )
        if len(candidates) > 1:
            # Real case: "Hempfield Area School District" and "Hempfield School
            # District" both exist in PA. Picking either is a coin flip on a
            # live sales target.
            names = ", ".join(sorted(c.account_name for c in candidates))
            return MatchOutcome(
                verdict=Verdict.UNKNOWN,
                reason=(
                    f"{name!r} matches more than one target account in {state_code} "
                    f"({names}); cannot tell which without more detail"
                ),
            )

        return MatchOutcome(
            verdict=Verdict.NOT_TARGET,
            reason=f"{name!r} ({state_code}) is not in the target account list",
        )


@dataclass(frozen=True)
class _Target:
    """The few fields matching needs, detached from the ORM row."""

    id: int
    account_name: str
    marketing_tier: str


async def load_target_index(session: AsyncSession) -> TargetIndex:
    """Load the live target universe into an index.

    Departed accounts are excluded: a district that dropped off Josh's list has
    usually become a customer, which is the single thing he most wants kept out
    of his view.
    """
    from artemis.marketing.targets.models import TargetAccount

    rows = (
        (
            await session.execute(
                select(TargetAccount).where(TargetAccount.match_method.is_distinct_from("departed"))
            )
        )
        .scalars()
        .all()
    )

    by_exact: dict[tuple[str, str], _Target] = {}
    by_normalized: dict[tuple[str, str], list[_Target]] = {}
    by_district_id: dict[int, _Target] = {}
    states: set[str] = set()

    for row in rows:
        state = (row.state or "").upper()
        target = _Target(
            id=row.id,
            account_name=row.account_name,
            marketing_tier=row.marketing_tier or "",
        )
        states.add(state)
        by_exact[(state, row.account_name)] = target
        if row.normalized_name:
            by_normalized.setdefault((state, row.normalized_name), []).append(target)
        if row.district_id is not None:
            by_district_id[row.district_id] = target

    return TargetIndex(
        by_exact=by_exact,
        by_normalized=by_normalized,
        by_district_id=by_district_id,
        states=frozenset(states),
    )


async def classify_district(
    session: AsyncSession,
    *,
    district_name: str | None,
    state: str | None,
    district_id: int | None = None,
) -> MatchOutcome:
    """Classify ONE district. Convenience wrapper over :class:`TargetIndex`.

    Loads the whole index, so it is the wrong tool for a batch -- use
    ``load_target_index`` once and call ``.classify`` per row instead.
    """
    index = await load_target_index(session)
    return index.classify(district_name=district_name, state=state, district_id=district_id)
