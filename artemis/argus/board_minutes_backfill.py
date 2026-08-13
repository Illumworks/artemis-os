"""Hand-verified mapping: peer_scout's 27-district watch list -> ``districts.id``.

ARGUS-2 (2026-08-13). See ``briefs/argus-2-district-identity.md``.

``artemis/scouts/board_minutes/peer_scout.py`` (``_DEFAULT_PEER_WATCH_LIST``)
hardcodes 27 ``(district_id, state, boarddocs_url)`` triples keyed by a
hand-written id (``"TX_dallas"``) -- a different key space from
``districts.id``. Nothing joined the two, so Argus had a live, working
BoardDocs URL for these 27 districts sitting right next to it and never used
it. This module is that join, computed once by hand and reviewed here rather
than derived by any fuzzy/automatic matching at run time -- see the brief's
own warning: "A state + partial-name match will happily attach Dallas ISD's
board to Dallas County Schools."

Every mapped entry was verified against the real ``districts`` table (13,466
NCES-loaded rows in ``artemis_os``) by hand -- state-scoped ``ILIKE`` queries
followed by inspection, not a programmatic fuzzy join. ``match_basis`` records
how:

  "brief"  -- the ARGUS-2 brief itself already states the ``districts.id``
              for this district (Dallas ISD, MSD Pike Township, Prince
              George's County, Kansas City); confirmed directly against the
              row.
  "exact"  -- exactly one row in that state matches the relevant keyword, and
              its ``districts.name`` is either identical to (case aside) or an
              unambiguous formatting variant of peer_scout's own comment --
              e.g. "Fauquier County Public Schools" (identical) or "SD U-46"
              vs. peer_scout's "School District U-46" (same district, no
              other candidate exists). Where the state had a same-keyword
              decoy (Lake Dallas ISD vs. Dallas ISD; Jefferson Davis Parish
              vs. Jefferson Parish; the St. Louis COUNTY special school
              district vs. the St. Louis CITY district; East Rochester vs.
              Rochester City), the decoy names a visibly different place or
              entity type, so picking between them isn't a guess.
  Four rows (Charleston, Horry, St. Louis City, Aurora, Pinellas) carry
  ``districts.name`` values that don't read as the district's common name at
  all (South-Carolina-style "Charleston 01", "Horry 01"; "ST. LOUIS CITY";
  Aurora's pre-merger legal name; bare "PINELLAS") -- these were additionally
  cross-checked against NCES's own district-search site (nces.ed.gov) to
  confirm the NCES ID and hosted-website match peer_scout's district before
  being marked "exact"; see the ARGUS-2 report for the verification detail.

ONE entry is deliberately UNMAPPED: ``OH_cleveland``. ``districts`` has FOUR
Ohio rows containing "cleveland" (Cleveland Municipal 3904378, Cleveland
Heights-University Heights City 3904379, East Cleveland City 3904390, Miller
City-New Cleveland Local 3904936). External sources (NCES, Wikidata) suggest
"Cleveland Municipal" is Cleveland Metropolitan School District's legacy
legal name -- but that inference rests on an outside identity claim, not on
an nces_id peer_scout supplied or an exact name match, the two bases this
backfill allows. Three of the four candidates are all plausible "the
Cleveland district" without that outside lookup, unlike every decoy case
above. Left unmapped rather than guessed -- see BOARD_MINUTES_UNMAPPED below
and the ARGUS-2 report.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["BoardDocsBackfillEntry", "BOARD_MINUTES_BACKFILL", "BOARD_MINUTES_UNMAPPED"]


@dataclass(frozen=True)
class BoardDocsBackfillEntry:
    """One verified peer_scout-entry -> districts-row mapping.

    ``expected_name`` is the ``districts.name`` value observed at verification
    time (2026-08-13, against ``artemis_os``). The migration's UPDATE matches
    on ``id AND name = expected_name`` -- belt-and-suspenders: ``id`` is the
    real key, but if the row's name ever changed out from under this mapping
    (a re-import, a manual correction) the update becomes a safe no-op
    instead of silently attaching a URL to a row that is no longer the one
    verified here.
    """

    peer_scout_district_id: str
    districts_id: int
    expected_name: str
    boarddocs_url: str
    match_basis: str  # "brief" | "exact"
    note: str


# 26 of peer_scout's 27 entries, verified. Order matches
# peer_scout._DEFAULT_PEER_WATCH_LIST for easy side-by-side review.
BOARD_MINUTES_BACKFILL: tuple[BoardDocsBackfillEntry, ...] = (
    BoardDocsBackfillEntry(
        "FL_pinellas",
        2036,
        "PINELLAS",
        "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
        "exact",
        "Pinellas County Schools. Sole FL district row matching 'pinellas'; "
        "NCES ID 1201560 confirmed against nces.ed.gov (Largo, FL; "
        "pinellascountyschools website).",
    ),
    BoardDocsBackfillEntry(
        "TX_dallas",
        11331,
        "DALLAS ISD",
        "https://go.boarddocs.com/tx/disd/Board.nsf/Public",
        "brief",
        "Confirmed by the ARGUS-2 brief itself. Distinct from Lake Dallas ISD "
        "(districts.id 11601, a different TX district) -- this is the exact "
        "wrong-attachment case the brief warns about.",
    ),
    BoardDocsBackfillEntry(
        "IN_msd_pike",
        3399,
        "MSD Pike Township",
        "https://go.boarddocs.com/in/pike/Board.nsf/Public",
        "brief",
        "Confirmed by the ARGUS-2 brief itself. Distinct from Pike County "
        "School Corp (districts.id 3398, a different IN district).",
    ),
    BoardDocsBackfillEntry(
        "CA_san_diego",
        1367,
        "San Diego Unified",
        "https://go.boarddocs.com/ca/sandi/Board.nsf/Public",
        "exact",
        "San Diego Unified School District. Distinct from San Diego County "
        "Office of Education (districts.id 1585 -- a county office, not a "
        "K-12 district; different kind of entity, not a naming collision).",
    ),
    BoardDocsBackfillEntry(
        "TX_humble",
        11529,
        "HUMBLE ISD",
        "https://go.boarddocs.com/tx/hisd/Board.nsf/Public",
        "exact",
        "Sole TX district row matching 'humble'; name is an exact match.",
    ),
    BoardDocsBackfillEntry(
        "OH_columbus",
        8967,
        "Columbus City Schools District",
        "https://go.boarddocs.com/oh/columbus/Board.nsf/Public",
        "exact",
        "Columbus City Schools. Distinct from Columbus Grove Local "
        "(districts.id 9381 -- a different, small OH village district).",
    ),
    BoardDocsBackfillEntry(
        "VA_fauquier",
        12311,
        "Fauquier County Public Schools",
        "https://go.boarddocs.com/va/fcps/Board.nsf/Public",
        "exact",
        "Sole VA district row matching 'fauquier'; name is an exact match.",
    ),
    BoardDocsBackfillEntry(
        "NY_buffalo",
        8050,
        "BUFFALO CITY SCHOOL DISTRICT",
        "https://go.boarddocs.com/ny/buffalo/Board.nsf/Public",
        "exact",
        "Sole NY district row matching 'buffalo'; name is an exact match "
        "(case aside).",
    ),
    BoardDocsBackfillEntry(
        "LA_jefferson_parish",
        4303,
        "Jefferson Parish",
        "https://go.boarddocs.com/la/jppss/Board.nsf/Public",
        "exact",
        "Jefferson Parish Public School System. Distinct from Jefferson "
        "Davis Parish (districts.id 4302 -- a different LA parish, named "
        "after a different person).",
    ),
    BoardDocsBackfillEntry(
        "CO_aurora",
        1636,
        "Aurora Joint District No. 28 of the counties of Adams and A",
        "https://go.boarddocs.com/co/aurora/Board.nsf/Public",
        "exact",
        "Aurora Public Schools' pre-merger legal name (NCES ID 0802340, "
        "confirmed via nces.ed.gov: Aurora, CO / Adams-Arapahoe 28J). Sole "
        "CO district row matching 'aurora'. NOTE: districts.name for this "
        "row is itself truncated at 59 chars in the source data ('...Adams "
        "and A') -- a pre-existing NCES-import data-quality issue, unrelated "
        "to this backfill; flagged in the ARGUS-2 report, not fixed here.",
    ),
    BoardDocsBackfillEntry(
        "UT_canyons",
        12106,
        "Canyons District",
        "https://go.boarddocs.com/ut/canyons/Board.nsf/Public",
        "exact",
        "Sole UT district row matching 'canyon'; Canyons School District.",
    ),
    BoardDocsBackfillEntry(
        "SC_charleston",
        10742,
        "Charleston 01",
        "https://go.boarddocs.com/sc/charleston/Board.nsf/Public",
        "exact",
        "Charleston County School District -- SC districts are NCES-named "
        "'<County> <number>'. Sole SC row matching 'charleston'; NCES ID "
        "4501440 confirmed via nces.ed.gov (website ccsdschools.com).",
    ),
    BoardDocsBackfillEntry(
        "SC_horry",
        10762,
        "Horry 01",
        "https://go.boarddocs.com/sc/horry/Board.nsf/Public",
        "exact",
        "Horry County Schools. Sole SC row matching 'horry'; NCES ID 4502490 "
        "confirmed via nces.ed.gov (website horrycountyschools.net).",
    ),
    BoardDocsBackfillEntry(
        "MD_prince_georges",
        4612,
        "Prince George's County Public Schools",
        "https://go.boarddocs.com/mabe/pgcps/Board.nsf/Public",
        "brief",
        "Confirmed by the ARGUS-2 brief itself.",
    ),
    BoardDocsBackfillEntry(
        "MD_montgomery",
        4611,
        "Montgomery County Public Schools",
        "https://go.boarddocs.com/mabe/mcpsmd/Board.nsf/Public",
        "exact",
        "Sole MD district row matching 'montgomery'; name is an exact match.",
    ),
    BoardDocsBackfillEntry(
        "MO_st_louis",
        6389,
        "ST. LOUIS CITY",
        "https://go.boarddocs.com/mo/stlps/Board.nsf/Public",
        "exact",
        "St. Louis Public Schools (Board of Education of the City of St. "
        "Louis). Distinct from 'SPECL. SCH. DST. ST. LOUIS CO.' "
        "(districts.id 6340 -- the COUNTY special-education cooperative, a "
        "different entity serving a different area). NCES ID 2929280 "
        "confirmed via nces.ed.gov (website slps.org).",
    ),
    BoardDocsBackfillEntry(
        "MO_kansas_city",
        6165,
        "KANSAS CITY 33",
        "https://go.boarddocs.com/mo/kanscsd/Board.nsf/Public",
        "brief",
        "Confirmed by the ARGUS-2 brief itself. Distinct from North Kansas "
        "City 74 (districts.id 6270 -- a different MO district).",
    ),
    BoardDocsBackfillEntry(
        "IL_elgin_u46",
        2627,
        "SD U-46",
        "https://go.boarddocs.com/il/u46/Board.nsf/Public",
        "exact",
        "School District U-46 (Elgin, IL). Sole IL district row matching "
        "'u-46'/'elgin'.",
    ),
    BoardDocsBackfillEntry(
        "IL_rockford",
        3032,
        "Rockford SD 205",
        "https://go.boarddocs.com/il/rps205/Board.nsf/Public",
        "exact",
        "Rockford Public School District 205. Sole IL district row matching "
        "'rockford'.",
    ),
    BoardDocsBackfillEntry(
        "FL_miami_dade",
        1997,
        "MIAMI-DADE",
        "https://go.boarddocs.com/fl/sbmd/Board.nsf/Public",
        "exact",
        "Miami-Dade County Public Schools. Sole FL district row matching "
        "'dade'.",
    ),
    BoardDocsBackfillEntry(
        "IN_indianapolis",
        3317,
        "Indianapolis Public Schools",
        "https://go.boarddocs.com/in/indps/Board.nsf/Public",
        "exact",
        "Sole IN district row matching 'indianapolis'; name is an exact "
        "match.",
    ),
    # OH_cleveland is intentionally absent -- see BOARD_MINUTES_UNMAPPED.
    BoardDocsBackfillEntry(
        "NY_rochester",
        8477,
        "ROCHESTER CITY SCHOOL DISTRICT",
        "https://go.boarddocs.com/ny/rochny/Board.nsf/Public",
        "exact",
        "Rochester City School District. Distinct from East Rochester Union "
        "Free School District (districts.id 8137 -- a different, much "
        "smaller NY suburb district).",
    ),
    BoardDocsBackfillEntry(
        "NC_charlotte_mecklenburg",
        8713,
        "Charlotte-Mecklenburg Schools",
        "https://go.boarddocs.com/nc/cmsnc/Board.nsf/Public",
        "exact",
        "Sole NC district row matching 'charlotte'/'mecklenburg'; name is an "
        "exact match.",
    ),
    BoardDocsBackfillEntry(
        "NC_wake",
        8750,
        "Wake County Schools",
        "https://go.boarddocs.com/nc/wcpsnc/Board.nsf/Public",
        "exact",
        "Wake County Public School System. Sole NC district row matching "
        "'wake'.",
    ),
    BoardDocsBackfillEntry(
        "GA_gwinnett",
        2133,
        "Gwinnett County",
        "https://go.boarddocs.com/ga/gcps/Board.nsf/Public",
        "exact",
        "Gwinnett County Public Schools. Sole GA district row matching "
        "'gwinnett'.",
    ),
    BoardDocsBackfillEntry(
        "GA_fulton",
        2124,
        "Fulton County",
        "https://go.boarddocs.com/ga/fcss/Board.nsf/Public",
        "exact",
        "Fulton County Schools. Sole GA district row matching 'fulton'.",
    ),
)

# peer_scout entries deliberately left unmapped, and why. Enumerated
# separately (rather than just "27 minus len(BOARD_MINUTES_BACKFILL)") so a
# reviewer can see the reasoning without cross-referencing peer_scout.py.
BOARD_MINUTES_UNMAPPED: tuple[tuple[str, str], ...] = (
    (
        "OH_cleveland",
        "4 Ohio districts rows contain 'cleveland' (Cleveland Municipal "
        "nces=3904378, Cleveland Heights-University Heights City "
        "nces=3904379, East Cleveland City nces=3904390, Miller "
        "City-New Cleveland Local nces=3904936). peer_scout's comment says "
        "'Cleveland Metropolitan School District' (boarddocs slug 'cmsd'), "
        "which no districts.name matches exactly, and unlike every decoy "
        "elsewhere in this list (Lake Dallas, Jefferson Davis Parish, "
        "East Rochester, ...) three of the four candidates here are all "
        "plausible readings of 'the Cleveland district' without outside "
        "knowledge. NCES/Wikidata suggest 'Cleveland Municipal' is CMSD's "
        "legacy legal name, but that is an identity claim from an external "
        "source, not an nces_id peer_scout supplied or an exact name match "
        "-- the two bases this backfill allows. Left unmapped.",
    ),
)


# Sanity: every backfilled districts_id is unique -- two peer_scout entries
# must never resolve to the same district row (would silently merge two
# districts' board data on read). Asserted at import time so this can never
# regress silently; also asserted explicitly in
# artemis/argus/tests/test_argus_district_identity.py.
_seen_ids = [e.districts_id for e in BOARD_MINUTES_BACKFILL]
assert len(_seen_ids) == len(set(_seen_ids)), (
    f"BOARD_MINUTES_BACKFILL has duplicate districts_id values: {_seen_ids}"
)
