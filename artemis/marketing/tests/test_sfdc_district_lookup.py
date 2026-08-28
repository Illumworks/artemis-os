"""District resolution for the Salesforce activity check.

Josh asked Callie for opportunity history and customer status on 2026-08-28 and
got name-match failures. Two things were wrong, and the second was worse than
the first.

The lookup was an exact ILIKE against OUR district index — which stores official
short forms. Hillsborough County (FL) is literally "HILLSBOROUGH", so every
reasonable name he tried missed.

And Callie explained the failure by asking him for the exact Salesforce account
name. That lookup never touches Salesforce, so a perfect Salesforce name would
have missed identically. Sending someone to fetch a fact that cannot help is
worse than saying "not found" — it costs their time and buys nothing.
"""

from __future__ import annotations

import pytest

from artemis.floating_artemis.tools.salesforce_tools import (
    _check_salesforce_activity,
    _resolve_district_by_name,
)
from artemis.marketing.models import District


def _factory_for(session):
    """Hand the tool the test's own session, without letting it close it."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _f():
        yield session

    return _f


async def _seed(session, rows: list[tuple[str, str]]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for name, state in rows:
        d = District(name=name, state=state)
        session.add(d)
        await session.flush()
        ids[name] = d.id
    return ids


@pytest.mark.asyncio
async def test_an_exact_name_still_resolves(db_session) -> None:
    await _seed(db_session, [("HOUSTON ISD", "TX")])

    district, candidates = await _resolve_district_by_name(db_session, "Houston ISD")

    assert district is not None and district.name == "HOUSTON ISD"
    assert candidates == []


@pytest.mark.asyncio
async def test_a_naming_convention_difference_resolves(db_session) -> None:
    """Salesforce says "Dallas Independent School District"; we store "DALLAS ISD"."""
    await _seed(db_session, [("DALLAS ISD", "TX")])

    district, candidates = await _resolve_district_by_name(
        db_session, "Dallas Independent School District"
    )

    assert district is not None and district.name == "DALLAS ISD"
    assert candidates == []


@pytest.mark.asyncio
async def test_the_hillsborough_case_offers_candidates_instead_of_guessing(db_session) -> None:
    """THE 2026-08-28 failure. Three Hillsboroughs; only one is Josh's.

    "County" is deliberately NOT a stopword in the shared normalizer — stripping
    it without "City" would merge genuinely different districts and degrade C5
    target matching. So this abstains and shows the options.
    """
    await _seed(
        db_session,
        [
            ("HILLSBOROUGH", "FL"),
            ("Hillsborough City Elementary", "CA"),
            ("Hillsborough Township Public School District", "NJ"),
        ],
    )

    district, candidates = await _resolve_district_by_name(
        db_session, "Hillsborough County Public Schools"
    )

    assert district is None, "must not pick one of three"
    names = {c.name for c in candidates}
    assert "HILLSBOROUGH" in names, "the right answer has to be offered"
    assert len(candidates) == 3


@pytest.mark.asyncio
async def test_suggestions_match_whole_words_not_prefixes(db_session) -> None:
    """A prefix match on "PRINCE" drags in Princeton and buries the real answer."""
    await _seed(
        db_session,
        [
            ("Prince George's County Public Schools", "MD"),
            ("Princeton Joint Unified", "CA"),
            ("Princeville CUSD 326", "IL"),
        ],
    )

    _district, candidates = await _resolve_district_by_name(db_session, "Prince George's")

    names = {c.name for c in candidates}
    assert "Prince George's County Public Schools" in names
    assert not any(n.startswith("Princeton") or n.startswith("Princeville") for n in names)


@pytest.mark.asyncio
async def test_a_genuinely_unknown_district_returns_nothing(db_session) -> None:
    await _seed(db_session, [("HOUSTON ISD", "TX")])

    district, candidates = await _resolve_district_by_name(db_session, "Zzyzx Unified")

    assert district is None
    assert candidates == []


# ── What Callie is told to say ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_miss_does_not_blame_salesforce_or_ask_for_its_account_name(
    db_session,
) -> None:
    """The wrong-errand guard.

    Callie asked Josh for the exact Salesforce account name. This lookup never
    reads Salesforce, so that could not have helped. The message must say where
    the gap actually is.
    """
    await _seed(db_session, [("HOUSTON ISD", "TX")])

    out = await _check_salesforce_activity(
        {"district_name": "Zzyzx Unified"}, session_factory=_factory_for(db_session)
    )

    assert "no entry for" in out, "must name OUR index as the gap"
    assert "Salesforce" in out, "the Salesforce answer stands on its own"
    assert "Salesforce account name" not in out, (
        "must not send the asker to fetch a Salesforce name that cannot help"
    )


@pytest.mark.asyncio
async def test_no_contacts_is_reported_as_unknown_not_as_clear(db_session) -> None:
    """ "Nothing to check" must never read as "nothing to worry about"."""
    await _seed(db_session, [("HOUSTON ISD", "TX")])

    out = await _check_salesforce_activity(
        {"district_name": "Houston ISD"}, session_factory=_factory_for(db_session)
    )

    assert "NOT a clean" in out
    assert "not a Salesforce failure" in out
    assert "gap in our contact data" in out


@pytest.mark.asyncio
async def test_ambiguity_is_refused_out_loud_with_ids(db_session) -> None:
    await _seed(db_session, [("HILLSBOROUGH", "FL"), ("Hillsborough City Elementary", "CA")])

    out = await _check_salesforce_activity(
        {"district_name": "Hillsborough County Public Schools"},
        session_factory=_factory_for(db_session),
    )

    assert "no exact match" in out.lower()
    assert "not going to pick one" in out
    assert "id " in out, "the id is the unambiguous handle — offer it"
