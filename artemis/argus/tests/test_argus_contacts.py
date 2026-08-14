"""Tests for CONTACTS-1 -- turning Argus's decision_makers findings into
district_contacts rows.

Two groups, mirroring test_argus_district_identity.py's split:

- Pure-data: ``extract_person`` against the REAL text of every one of the 14
  live ``district_research`` observations that mention "superintendent" as
  of 2026-08-14 (queried directly from ``artemis_os``, read-only, via psql --
  never touched by any test in this file). No DB involved.
- DB-backed (real Postgres, via conftest.py's ARTEMIS_TEST_DB_URL guard):
  the full pipeline -- extraction, upsert idempotency, the write-time hook
  in ``write_district_findings``, hard-delete ("wipe"), and the
  district-lookup path Callie's tool uses -- exercised against seeded
  ``districts`` rows and, for the retroactive-run test, all 14 real
  observations re-created verbatim via the real write path
  (``artemis.memory.store.write_observation``, not raw SQL).

Required-by-brief coverage:
  - a real observation naming a superintendent yields exactly one contact,
    with district and source recorded         -- test_harford_creates_one_contact*
  - an observation with no clearly identifiable person yields nothing and
    is reported, not stored speculatively      -- pure-data group + retroactive test
  - re-running extraction does not duplicate   -- test_rerun_is_idempotent*
  - deleting a contact removes the row; the referencing observation
    survives                                   -- test_wipe_*
  - the lookup answers by district, and returns nothing (not a guess) for a
    district with none                         -- test_lookup_*
  - no email or phone is ever synthesised      -- test_never_synthesises_email_or_phone
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy import text as sql_text

import artemis.db as _db
from artemis.argus.contacts import (
    extract_and_upsert,
    extract_person,
    list_contacts_for_district_id,
    resolve_district_id,
    run_retroactive_extraction,
)
from artemis.argus.drawer import (
    ARGUS_CATEGORY,
    ARGUS_SCOPE,
    ARGUS_SOURCE_QUALITY,
    Dimension,
    DistrictFinding,
    _finding_to_content,
    write_district_findings,
)
from artemis.marketing.contacts import (
    create_argus_contact,
    delete_contact,
    delete_contacts_for_district,
    get_contact,
)
from artemis.marketing.models import DistrictContact
from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Observation
from artemis.memory.store import write_observation

# asyncio_mode = "auto" (pyproject.toml) auto-detects async test functions --
# no blanket pytestmark needed, and applying one would tag the pure-data sync
# tests below with a mark pytest-asyncio warns about on non-async functions.


# ── Pure-data: extract_person against the 14 real observations ────────────────
#
# Fetched 2026-08-14 via:
#   psql -d artemis_os -c "select id, content from memory_observations
#     where category='district_research' and content ilike '%superintendent%'
#     order by id;"
# Read-only against artemis_os -- nothing in this list, or anywhere in this
# file, writes to that database. The 8 below are the decision_makers subset;
# the other 6 (district_profile / recommended_angle) are exercised in the
# retroactive-run test instead, since extract_person is never called on them
# in production (extract_and_upsert short-circuits on dimension first).

_REAL_DECISION_MAKERS_CASES: list[tuple[int, str, str]] = [
    (
        806,
        "Salem-Keizer School District",
        "Curriculum decisions are made by Salem-Keizer School Board; specific "
        "superintendent or curriculum director names not identified in available sources.",
    ),
    (
        828,
        "OR-salem-keizer",
        "Superintendent Andrea Castañeda leads district decision-making; school board "
        "voted on curriculum approval and drives procurement decisions.",
    ),
    (
        1285,
        "3470",
        "Superintendent transitioned April 2026; former superintendent retained on "
        "retainer as of July 2026, suggesting interim/split authority. Interim "
        "leadership structure and new superintendent appointment timeline not yet public.",
    ),
    (
        1290,
        "4608",
        "Dr. Dyann Mack, newly appointed superintendent (effective June 2026), is the "
        "primary district leader. She is the first Black superintendent and first HCPS "
        "graduate to lead the district, positioning her as instrumental in strategic "
        "purchasing decisions.",
    ),
    (
        1299,
        "4612",
        "New superintendent nominated as permanent leader in June 2026 after interim "
        "tenure. Leadership transition may create window for curriculum and vendor "
        "review cycles.",
    ),
    (
        1306,
        "St. Louis Public Schools",
        "Myra Berry recently installed as permanent superintendent of St. Louis Public "
        "Schools. She will be the primary curriculum and vendor decision-maker.",
    ),
    (
        1452,
        "11414",
        "Former FWISD superintendent Molinar is now CEO of REV Partnership and "
        "partnering with the district. However, with the district under state "
        "takeover, decision-making authority appears to be under state management "
        "rather than traditional district leadership.",
    ),
    (
        1487,
        "MD-PGCPS",
        "An interim superintendent leads PGCPS as of Nov 2025, 100 days into the role "
        "with unresolved long-term tenure. Leadership uncertainty may delay or "
        "accelerate curriculum adoption decisions.",
    ),
]

# The 6 real non-decision_makers observations from the same 14, for the
# retroactive-run integration test (dimension, district_key, value, source, url).
_REAL_OTHER_DIMENSION_CASES: list[tuple[int, str, str, str, str, str | None]] = [
    (
        1262,
        Dimension.DISTRICT_PROFILE,
        "11331",
        "Dallas ISD is a large urban district undergoing significant transitions for "
        "2026-27, with superintendent-led focus on academic growth and student equity. "
        "District has experienced recent enrollment decline attributed to immigration "
        "policies, and is implementing structural changes across campuses.",
        "Argus/news_api",
        "https://news.google.com/rss/articles/example-1262",
    ),
    (
        1274,
        Dimension.RECOMMENDED_ANGLE,
        "Champaign Unit 4 Schools",
        "Recommended angle for Champaign Unit 4 Schools: Current vendor: Insufficient "
        "data from available sources.. Triggered by: Champaign Unit 4 Schools approves "
        "Geovanny Ponce as next superintendent.",
        "Argus",
        None,
    ),
    (
        1284,
        Dimension.DISTRICT_PROFILE,
        "3470",
        "Valparaiso Community Schools (Indiana) faces leadership instability following "
        "superintendent resignation in April 2026, with former superintendent retained "
        "on contract as of July 2026.",
        "Argus/news_api",
        "https://news.google.com/rss/articles/example-1284",
    ),
    (
        1289,
        Dimension.DISTRICT_PROFILE,
        "4608",
        "Harford County Public Schools (Maryland) recently appointed Dr. Dyann Mack as "
        "superintendent in June 2026. The district finalized budget adjustments that "
        "eliminated cuts while adding 28 new positions.",
        "Argus/news_api",
        "https://news.google.com/rss/articles/example-1289",
    ),
    (
        1309,
        Dimension.RECOMMENDED_ANGLE,
        "St. Louis Public Schools",
        "Recommended angle for St. Louis Public Schools: Triggered by: St. Louis Public "
        "Schools installs interim superintendent Myra Berry as permanent superintendent.",
        "Argus",
        None,
    ),
    (
        1448,
        Dimension.RECOMMENDED_ANGLE,
        "11331",
        "Dallas ISD is publicly prioritizing academic growth recovery for 2026-27. "
        "Initial outreach to curriculum leadership should reference the superintendent's "
        "public academic growth commitment.",
        "Argus",
        None,
    ),
]


# Expected extract_person() verdict per real observation. This is the
# TEXT-level bar only -- 828 and 1306 DO name a confident person (Andrea
# Castañeda / Myra Berry) but never become a contact anyway, because their
# district_key is free-text and never resolves to a districts.id (the
# ARGUS-2 gap) -- see test_retroactive_extraction_over_all_14_real_observations
# for that second bar. Only 1290 (Harford County) clears BOTH bars.
_EXPECTED_TEXT_LEVEL_VERDICT: dict[int, tuple[str, str] | None] = {
    806: None,
    828: ("Andrea Castañeda", "Superintendent"),
    1285: None,
    1290: ("Dr. Dyann Mack", "Superintendent"),
    1299: None,
    1306: ("Myra Berry", "Superintendent"),
    1452: None,
    1487: None,
}


@pytest.mark.parametrize("obs_id,district_key,value", _REAL_DECISION_MAKERS_CASES)
def test_extract_person_on_real_decision_makers_text(
    obs_id: int, district_key: str, value: str
) -> None:
    """Regression-pins extract_person's verdict on every real decision_makers
    finding as of 2026-08-14 -- the pure text-level bar (see
    _EXPECTED_TEXT_LEVEL_VERDICT for why this is not the same as "becomes a
    contact")."""
    person, reason = extract_person(value)
    expected = _EXPECTED_TEXT_LEVEL_VERDICT[obs_id]
    if expected is None:
        assert person is None, (
            f"obs {obs_id} ({district_key}) should NOT extract a person, got {person} -- "
            f"this is a real production string, not a synthetic edge case"
        )
        assert reason  # every skip must explain itself
    else:
        assert person is not None, f"obs {obs_id} ({district_key}) should extract a person: {reason}"
        assert (person.name, person.title) == expected


def test_extract_person_never_returns_none_reason() -> None:
    person, reason = extract_person("")
    assert person is None
    assert reason == "empty finding value"


def test_extract_person_skips_argus_stub_text() -> None:
    person, reason = extract_person("Insufficient data from available sources for this research pass.")
    assert person is None
    assert "stub" in reason


def test_extract_person_rejects_the_wrong_person_case() -> None:
    """The FWISD case, isolated: a real name IS present, but qualified as
    'Former' -- extracting it would be exactly the wrong-person failure mode
    the brief calls out by name."""
    value = _REAL_DECISION_MAKERS_CASES[6][2]
    assert "Molinar" in value  # sanity: this is the case we think it is
    person, reason = extract_person(value)
    assert person is None
    assert "disqualifying" in reason


def test_extract_person_preserves_honorific_prefix() -> None:
    """Regression pin for the sentence-splitter bug caught during development:
    a naive split on '. ' treats 'Dr.' as a sentence end and silently drops
    the honorific from the stored name."""
    person, _ = extract_person("Dr. Jane Smith is the new superintendent.")
    assert person is not None
    assert person.name == "Dr. Jane Smith"


def test_extract_person_ambiguous_multiple_names_skips() -> None:
    person, reason = extract_person(
        "Superintendent Jane Smith and Superintendent John Doe both spoke at the meeting."
    )
    assert person is None
    assert "multiple distinct candidate names" in reason


# ── DB-backed ────────────────────────────────────────────────────────────────


@pytest.fixture
async def harford() -> AsyncIterator[int]:
    """Seed one district shaped like the real Harford County row, clean up after.

    Real ``districts.id`` in artemis_os is 4608; this test DB has no NCES
    load, so whatever id Postgres assigns here is used directly (same
    pattern as test_argus_district_identity.py's seeded_districts fixture).
    """
    async with _db.SessionLocal() as session:
        district_id = (
            await session.execute(
                sql_text(
                    "INSERT INTO districts (name, state) VALUES (:name, :state) RETURNING id"
                ),
                {"name": "Harford County Public Schools", "state": "MD"},
            )
        ).scalar_one()
        await session.commit()
    try:
        yield district_id
    finally:
        async with _db.SessionLocal() as session:
            await session.execute(
                sql_text("DELETE FROM districts WHERE id = :id"), {"id": district_id}
            )
            await session.commit()


async def _seed_real_observation(
    session,
    *,
    district_key: str,
    dimension: str,
    value: str,
    source: str = "Argus/news_api",
    url: str | None = None,
    researched_at: str = "2026-08-13",
) -> Observation:
    """Write one observation through the REAL memory write path
    (write_observation), producing the exact content shape + raw_inputs
    payload write_district_findings would have produced -- NOT raw SQL.
    Deliberately bypasses write_district_findings itself so the CONTACTS-1
    write-time hook does not fire during seeding; that hook is exercised in
    its own dedicated test (test_write_time_hook_creates_contact_going_forward).
    """
    finding = DistrictFinding(
        dimension=dimension, value=value, source=source, url=url, researched_at=researched_at
    )
    content = _finding_to_content(district_key, finding)
    obs = await write_observation(
        session,
        ARGUS_SCOPE,
        content,
        category=ARGUS_CATEGORY,
        source_quality=ARGUS_SOURCE_QUALITY,
        raw_payload={
            "agent": "argus",
            "district_key": district_key,
            "dimension": dimension,
            "source": source,
            "url": url,
            "researched_at": researched_at,
        },
        raw_source_kind="agent_run",
        raw_source_id="argus",
        raw_actor="argus",
        confidence_origin="argus",
    )
    return obs


async def test_harford_creates_one_contact_with_district_and_source(harford: int) -> None:
    """Required test: a real observation naming a superintendent yields
    exactly one contact, with the district and the source recorded."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],  # Dr. Dyann Mack text
        )
        await session.commit()

    async with _db.SessionLocal() as session:
        outcome = await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()

    assert outcome.outcome == "created"
    assert outcome.name == "Dr. Dyann Mack"
    assert outcome.title == "Superintendent"

    async with _db.SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(DistrictContact).where(DistrictContact.district_id == harford)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    contact = rows[0]
    assert contact.district_id == harford
    assert contact.source == "argus"
    assert contact.source_observation_id == obs.id  # provenance: which observation


async def test_no_name_observation_yields_nothing_and_is_reported(harford: int) -> None:
    """Required test: an observation with no clearly identifiable person
    yields nothing, and the skip is reported (not stored speculatively)."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[4][2],  # "New superintendent nominated..." (4612)
        )
        outcome = await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[4][2],
        )
        await session.commit()

    assert outcome.outcome == "skipped"
    assert outcome.reason  # a report, not silence
    assert outcome.contact_id is None

    async with _db.SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(DistrictContact).where(DistrictContact.district_id == harford)
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


async def test_rerun_is_idempotent(harford: int) -> None:
    """Required test: re-running extraction does not duplicate a contact."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()

    for _ in range(3):
        async with _db.SessionLocal() as session:
            await extract_and_upsert(
                session,
                district_key=str(harford),
                observation_id=obs.id,
                dimension=Dimension.DECISION_MAKERS,
                value=_REAL_DECISION_MAKERS_CASES[3][2],
            )
            await session.commit()

    async with _db.SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(DistrictContact).where(DistrictContact.district_id == harford)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"expected exactly one contact after 3 re-runs, got {len(rows)}"


async def test_wipe_removes_contact_but_observation_survives_untouched(harford: int) -> None:
    """Required test: deleting a contact removes the row; the observation
    that referenced it survives -- unchanged, not superseded, not edited."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        original_content = obs.content
        outcome = await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()
    assert outcome.contact_id is not None
    contact_id = outcome.contact_id
    obs_id = obs.id

    async with _db.SessionLocal() as session:
        await delete_contact(session, contact_id)
        await session.commit()

    async with _db.SessionLocal() as session:
        gone = await get_contact(session, contact_id)
        assert gone is None, "contact row must be actually gone, not soft-deactivated"

        surviving_obs = await session.get(MemoryObservation, obs_id)
        assert surviving_obs is not None, "the observation must still exist"
        assert surviving_obs.content == original_content, "prose must be byte-identical, untouched"
        assert surviving_obs.superseded_by is None, "wiping a contact must never supersede the observation"


async def test_wipe_all_for_district(harford: int) -> None:
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()

    async with _db.SessionLocal() as session:
        count = await delete_contacts_for_district(session, harford)
        await session.commit()
    assert count == 1

    async with _db.SessionLocal() as session:
        remaining = await list_contacts_for_district_id(session, harford)
    assert remaining == []

    # Deleting from a district with zero contacts is not an error.
    async with _db.SessionLocal() as session:
        assert await delete_contacts_for_district(session, harford) == 0


async def test_lookup_answers_who_runs_harford_county(harford: int) -> None:
    """Required test: the lookup answers by district."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()

    async with _db.SessionLocal() as session:
        contacts = await list_contacts_for_district_id(session, harford)
    assert len(contacts) == 1
    assert contacts[0].name == "Dr. Dyann Mack"
    assert contacts[0].title == "Superintendent"
    assert contacts[0].email is None
    assert contacts[0].phone is None


async def test_lookup_returns_nothing_not_a_guess_for_district_with_no_contacts(
    harford: int,
) -> None:
    """Required test: returns nothing (not a guess) for a district with no contacts."""
    async with _db.SessionLocal() as session:
        contacts = await list_contacts_for_district_id(session, harford)
    assert contacts == []


async def test_callie_tool_who_runs_harford_county(harford: int) -> None:
    """End-to-end through the actual Callie-facing tool function, by name AND
    by district_id -- the literal "who runs Harford County" question."""
    from artemis.floating_artemis.tools.marketing import _get_district_contacts

    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await extract_and_upsert(
            session,
            district_key=str(harford),
            observation_id=obs.id,
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        await session.commit()

    by_name = await _get_district_contacts({"district": "Harford County"})
    assert "Dr. Dyann Mack" in by_name
    assert "Superintendent" in by_name
    assert "argus" in by_name

    by_id = await _get_district_contacts({"district_id": harford})
    assert "Dr. Dyann Mack" in by_id

    empty = await _get_district_contacts({"district": "A District That Does Not Exist Anywhere"})
    assert "No confident district match" in empty


async def test_never_synthesises_email_or_phone(harford: int) -> None:
    """Required test: no email or phone is ever synthesised -- only stored
    when present in the source. None of the 8 real decision_makers findings
    contain one, so this asserts the structural guarantee directly."""
    async with _db.SessionLocal() as session:
        obs = await _seed_real_observation(
            session,
            district_key=str(harford),
            dimension=Dimension.DECISION_MAKERS,
            value=_REAL_DECISION_MAKERS_CASES[3][2],
        )
        contact, created = await create_argus_contact(
            session,
            district_id=harford,
            name="Dr. Dyann Mack",
            title="Superintendent",
            source_observation_id=obs.id,
        )
        await session.commit()
    assert created is True
    assert contact.email is None
    assert contact.phone is None


async def test_write_time_hook_creates_contact_going_forward(harford: int) -> None:
    """Item 2 of the brief: when Argus WRITES a decision_makers finding
    (through write_district_findings, the real production call path -- not
    the retroactive pass), the person should land in district_contacts too,
    same confidence bar."""
    finding = DistrictFinding(
        dimension=Dimension.DECISION_MAKERS,
        value=_REAL_DECISION_MAKERS_CASES[3][2],
        source="Argus/news_api",
        url=None,
        researched_at="2026-08-14",
    )
    async with _db.SessionLocal() as session:
        written_ids = await write_district_findings(session, str(harford), [finding])
        await session.commit()
    assert len(written_ids) == 1

    async with _db.SessionLocal() as session:
        contacts = await list_contacts_for_district_id(session, harford)
    assert len(contacts) == 1
    assert contacts[0].name == "Dr. Dyann Mack"
    assert contacts[0].source_observation_id == written_ids[0]


async def test_write_time_hook_skips_ambiguous_finding_without_raising(harford: int) -> None:
    """The hook must never break the observation write when extraction finds
    nothing confident -- e.g. the Valparaiso-shaped 'former ... retained on
    retainer' finding."""
    finding = DistrictFinding(
        dimension=Dimension.DECISION_MAKERS,
        value=_REAL_DECISION_MAKERS_CASES[2][2],  # the Valparaiso transition text
        source="Argus/news_api",
        url=None,
        researched_at="2026-08-14",
    )
    async with _db.SessionLocal() as session:
        written_ids = await write_district_findings(session, str(harford), [finding])
        await session.commit()
    assert len(written_ids) == 1  # observation still written

    async with _db.SessionLocal() as session:
        contacts = await list_contacts_for_district_id(session, harford)
    assert contacts == []  # but no contact -- correctly skipped


async def test_resolve_district_id_matches_the_argus2_seam(harford: int) -> None:
    assert await resolve_district_id(str(harford)) == harford
    assert await resolve_district_id("Harford County Public Schools") is None  # free-text, not an id
    assert await resolve_district_id("") is None


# ── Retroactive run against all 14 REAL observations, verbatim ─────────────────


@pytest.fixture
async def real_fourteen_districts() -> AsyncIterator[dict[str, int]]:
    """Seed districts for every NUMERIC district_key among the 14 real
    observations (Harford=4608, Valparaiso=3470, PGCPS-numeric=4612,
    FWISD=11414, Dallas=11331 in production). Free-text keys
    (Salem-Keizer/OR-salem-keizer/St. Louis/MD-PGCPS/Champaign) are
    deliberately NOT seeded -- in production they don't resolve either
    (the ARGUS-2 gap), and this fixture must not paper over that."""
    names = {
        "harford": ("Harford County Public Schools", "MD"),
        "valparaiso": ("Valparaiso Community Schools", "IN"),
        "pgcps": ("Prince George's County Public Schools", "MD"),
        "fwisd": ("FORT WORTH ISD", "TX"),
        "dallas": ("DALLAS ISD", "TX"),
    }
    ids: dict[str, int] = {}
    async with _db.SessionLocal() as session:
        for key, (name, state) in names.items():
            new_id = (
                await session.execute(
                    sql_text(
                        "INSERT INTO districts (name, state) VALUES (:name, :state) RETURNING id"
                    ),
                    {"name": name, "state": state},
                )
            ).scalar_one()
            ids[key] = new_id
        await session.commit()
    try:
        yield ids
    finally:
        async with _db.SessionLocal() as session:
            await session.execute(
                sql_text("DELETE FROM districts WHERE id = ANY(:ids)"),
                {"ids": list(ids.values())},
            )
            await session.commit()


async def test_retroactive_extraction_over_all_14_real_observations(
    real_fourteen_districts: dict[str, int],
) -> None:
    """The deliverable: run extraction against (re-created, verbatim) copies
    of all 14 live district_research observations that mention
    "superintendent" as of 2026-08-14, and confirm exactly the classification
    this brief asks for -- one confident contact, everything else skipped and
    explained."""
    key_map = {
        "OR-salem-keizer": "OR-salem-keizer",
        "3470": str(real_fourteen_districts["valparaiso"]),
        "4608": str(real_fourteen_districts["harford"]),
        "4612": str(real_fourteen_districts["pgcps"]),
        "11414": str(real_fourteen_districts["fwisd"]),
        "11331": str(real_fourteen_districts["dallas"]),
    }

    # memory_observations is shared, never-cleaned-up state across this whole
    # test file (lossless by design -- there is no delete path). Other tests
    # in this module write their own district_research observations into the
    # same workspace:marketing scope and are never removed, so a scan by
    # scope+category alone would pick up their leftovers too. Track exactly
    # the ids THIS test seeds and filter the scan's output to that set --
    # this is what makes the "exactly 14, exactly 1 created" assertions below
    # correct regardless of what ran before this test in the same session.
    seeded_ids: set[int] = set()
    async with _db.SessionLocal() as session:
        for _obs_id, district_key, value in _REAL_DECISION_MAKERS_CASES:
            resolved_key = key_map.get(district_key, district_key)
            obs = await _seed_real_observation(
                session,
                district_key=resolved_key,
                dimension=Dimension.DECISION_MAKERS,
                value=value,
            )
            seeded_ids.add(obs.id)
        for _obs_id, dimension, district_key, value, source, url in _REAL_OTHER_DIMENSION_CASES:
            resolved_key = key_map.get(district_key, district_key)
            obs = await _seed_real_observation(
                session,
                district_key=resolved_key,
                dimension=dimension,
                value=value,
                source=source,
                url=url,
            )
            seeded_ids.add(obs.id)
        await session.commit()
    assert len(seeded_ids) == 14  # sanity: 14 distinct observations actually seeded

    async with _db.SessionLocal() as session:
        all_outcomes = await run_retroactive_extraction(session)
        await session.commit()
    outcomes = [o for o in all_outcomes if o.observation_id in seeded_ids]

    assert len(outcomes) == 14, f"expected all 14 real observations scanned, got {len(outcomes)}"

    created = [o for o in outcomes if o.outcome == "created"]
    skipped = [o for o in outcomes if o.outcome == "skipped"]
    assert len(created) == 1, f"expected exactly 1 created contact, got: {created}"
    assert len(skipped) == 13

    only_created = created[0]
    assert only_created.name == "Dr. Dyann Mack"
    assert only_created.title == "Superintendent"
    assert only_created.district_key == str(real_fourteen_districts["harford"])

    # Print a full, readable report -- captured verbatim with `pytest -s` as
    # the brief's required "actual contacts extracted... skipped and why".
    print("\n\n=== CONTACTS-1 retroactive extraction: all 14 live observations ===")
    for o in outcomes:
        label = f"obs={o.observation_id} dim={o.dimension} district_key={o.district_key!r}"
        if o.outcome == "created":
            print(f"CREATED  | {label} -> {o.name!r} ({o.title}) contact_id={o.contact_id}")
        else:
            print(f"skipped  | {label} -> {o.reason}")

    # Re-running must not duplicate (idempotency at the batch level).
    async with _db.SessionLocal() as session:
        second_pass_all = await run_retroactive_extraction(session)
        await session.commit()
    second_pass = [o for o in second_pass_all if o.observation_id in seeded_ids]
    second_created = [o for o in second_pass if o.outcome == "created"]
    second_updated = [o for o in second_pass if o.outcome == "updated"]
    assert second_created == [], "second run must not CREATE anything new"
    assert len(second_updated) == 1, "the Harford contact should be recognised and refreshed, not duplicated"

    async with _db.SessionLocal() as session:
        harford_contacts = await list_contacts_for_district_id(
            session, real_fourteen_districts["harford"]
        )
    assert len(harford_contacts) == 1
