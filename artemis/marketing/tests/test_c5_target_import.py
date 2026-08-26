"""Importing a target-account list.

The list arrives as a Slack upload from Josh and will be re-posted as it
changes, so import is re-runnable and replaces rather than merges. The failure
that matters most is the wrong file: silently wiping the universe Josh sells
into is far worse than refusing to import.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from artemis.marketing.targets.ingest import (
    TargetListError,
    import_target_accounts,
    resolve_target_districts,
)
from artemis.marketing.targets.matching import Verdict, classify_district
from artemis.marketing.targets.models import TargetAccount

HEADER = (
    "Sales\tBilling State/Province\tAccount Name\tDistrict Marketing Tier\t"
    "Enrollment in District\tIs Customer\tIs Parent Account\tAmira Channel Partner"
)


def _tsv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


ROW_DALLAS = "Natasha Bullock\tTX\tDallas Independent School District\tD1\t137899\t0\t1\t"
ROW_AUSTIN = "Mack Moyer\tTX\tAustin Independent School District\tD1\t72773\t0\t1\tHMH"


@pytest.mark.asyncio
async def test_import_reads_every_column(db_session) -> None:
    report = await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))

    assert report.total_rows == 2
    assert report.inserted == 2

    austin = (
        await db_session.execute(
            select(TargetAccount).where(TargetAccount.account_name.like("Austin%"))
        )
    ).scalar_one()
    assert austin.state == "TX"
    assert austin.marketing_tier == "D1"
    assert austin.enrollment == 72773
    assert austin.sales_owner == "Mack Moyer"
    assert austin.channel_partner == "HMH"
    assert austin.is_customer is False
    assert austin.is_parent_account is True


@pytest.mark.asyncio
async def test_reimport_is_idempotent(db_session) -> None:
    """Josh will post revised lists; re-importing must not duplicate accounts."""
    await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))
    second = await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))

    assert second.inserted == 0
    assert second.updated == 2
    rows = (await db_session.execute(select(TargetAccount))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_an_account_dropped_from_the_new_list_is_marked_departed(db_session) -> None:
    """A district that leaves the list has usually become a customer.

    Marked, never deleted — but it must stop being treated as a live target,
    which is the entire point of the exercise.
    """
    await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))
    report = await import_target_accounts(db_session, _tsv(ROW_DALLAS))

    assert report.departed == 1
    austin = (
        await db_session.execute(
            select(TargetAccount).where(TargetAccount.account_name.like("Austin%"))
        )
    ).scalar_one()
    assert austin.match_method == "departed"
    assert austin.id is not None, "the row must survive; only its status changes"


@pytest.mark.asyncio
async def test_the_wrong_file_is_refused_rather_than_wiping_the_list(db_session) -> None:
    """A stray upload must never be able to empty the target universe."""
    await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))

    with pytest.raises(TargetListError) as excinfo:
        await import_target_accounts(db_session, "Region\tRevenue\nWest\t100\n")
    assert "does not look like a target-account list" in excinfo.value.reason

    rows = (await db_session.execute(select(TargetAccount))).scalars().all()
    assert len(rows) == 2, "the existing list must be untouched by a rejected import"


@pytest.mark.asyncio
async def test_a_header_only_file_is_refused(db_session) -> None:
    with pytest.raises(TargetListError) as excinfo:
        await import_target_accounts(db_session, HEADER + "\n")
    assert "no data rows" in excinfo.value.reason


@pytest.mark.asyncio
async def test_rows_missing_a_name_or_state_are_skipped_not_fatal(db_session) -> None:
    """One malformed row must not cost the other 1,286."""
    report = await import_target_accounts(
        db_session, _tsv(ROW_DALLAS, "Mack Moyer\t\t\tD1\t100\t0\t1\t")
    )
    assert report.inserted == 1
    assert len(report.skipped) == 1


@pytest.mark.asyncio
async def test_an_all_generic_name_imports_and_is_reported(db_session) -> None:
    """ "Community Independent School District" is a real TX account.

    It must import (matchable by exact name) and be surfaced in the report, not
    dropped and not given an empty normalized key.
    """
    row = "Torey Page\tTX\tCommunity Independent School District\tD3\t1200\t0\t1\t"
    report = await import_target_accounts(db_session, _tsv(row))

    assert report.inserted == 1
    assert report.unnormalizable == ["Community Independent School District (TX)"]

    stored = (await db_session.execute(select(TargetAccount))).scalar_one()
    assert stored.normalized_name is None

    out = await classify_district(
        db_session, district_name="Community Independent School District", state="TX"
    )
    assert out.verdict is Verdict.TARGET, "exact-name matching must still work"


@pytest.mark.asyncio
async def test_district_resolution_records_why_it_abstained(db_session) -> None:
    from artemis.marketing.models import District

    db_session.add(District(name="DALLAS ISD", state="TX"))
    await db_session.flush()

    await import_target_accounts(db_session, _tsv(ROW_DALLAS, ROW_AUSTIN))
    counts = await resolve_target_districts(db_session)

    assert counts.get("normalized") == 1  # Dallas matched
    assert counts.get("abstained_no_match") == 1  # Austin has no district row

    austin = (
        await db_session.execute(
            select(TargetAccount).where(TargetAccount.account_name.like("Austin%"))
        )
    ).scalar_one()
    assert austin.district_id is None
    assert austin.match_method == "abstained_no_match"
