"""Tests for the migration apply mode + idempotency + conflict detection.

Test IDs:
  H-AP-01  Apply mode inserts OKR rows correctly
  H-AP-02  Apply mode inserts writing-rules rows correctly
  H-AP-03  Idempotent apply: running twice produces no duplicates
  H-AP-04  Conflict detection: pre-existing objective → skipped + reported
  H-AP-05  Conflict detection: pre-existing writing profile → skipped + reported
  H-AP-06  Conflict detection: pre-existing rule → skipped + reported
  H-AP-07  Validation error aborts apply (apply returns without inserting)
  H-AP-08  Apply sets correct timestamps (unix → datetime)
  H-AP-09  JSON bullets stored as JSONB lists (not plain strings)
  H-AP-10  source_key conflict in writing_sources → skipped + reported
  H-AP-11  Writing rules with status='archived' still inserted (not filtered)
  H-AP-12  Activity linked to KR via FK after apply
  H-AP-13  Next-up dispatch_params stored as JSONB
  H-AP-14  OKR update previews migrated when source has rows
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.migrate_okr_writing_rules import MigrationPlan, build_plan
from tests.h_prep.conftest import _make_sqlite_fixture


async def _do_apply(source_path: Path, session: AsyncSession) -> MigrationPlan:
    """Build plan + call _apply_okr and _apply_writing_rules directly using
    the test session so we can inspect results without a full engine spin-up."""
    from scripts.migrate_okr_writing_rules import _apply_okr, _apply_writing_rules

    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row
    plan = build_plan(source_path)
    try:
        await _apply_okr(conn, session, plan)
        await _apply_writing_rules(conn, session, plan)
        await session.flush()
    finally:
        conn.close()
    return plan


# ── H-AP-01: OKR rows inserted correctly ──────────────────────────────────────


@pytest.mark.asyncio
async def test_ap01_okr_rows_inserted(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrActivity, OkrKeyResult, OkrNextUp, OkrObjective

    await _do_apply(sqlite_fixture, db_session)

    objs = (await db_session.execute(select(OkrObjective))).scalars().all()
    assert len(objs) == 2
    titles = {o.title for o in objs}
    assert "Test Objective Alpha" in titles

    krs = (await db_session.execute(select(OkrKeyResult))).scalars().all()
    assert len(krs) == 1
    assert krs[0].title == "KR1 for Alpha"

    acts = (await db_session.execute(select(OkrActivity))).scalars().all()
    assert len(acts) == 1

    next_ups = (await db_session.execute(select(OkrNextUp))).scalars().all()
    assert len(next_ups) == 1


# ── H-AP-02: Writing-rules rows inserted correctly ────────────────────────────


@pytest.mark.asyncio
async def test_ap02_writing_rows_inserted(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.writing_rules.models import (
        WritingExample,
        WritingFolder,
        WritingProfile,
        WritingRule,
        WritingSource,
    )

    await _do_apply(sqlite_fixture, db_session)

    assert (await db_session.execute(select(WritingProfile))).scalars().first() is not None
    assert (await db_session.execute(select(WritingFolder))).scalars().first() is not None
    assert (await db_session.execute(select(WritingRule))).scalars().first() is not None
    assert (await db_session.execute(select(WritingExample))).scalars().first() is not None
    assert (await db_session.execute(select(WritingSource))).scalars().first() is not None


# ── H-AP-03: Idempotent apply produces no duplicates ─────────────────────────


@pytest.mark.asyncio
async def test_ap03_idempotent_apply(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrObjective
    from artemis.writing_rules.models import WritingProfile

    await _do_apply(sqlite_fixture, db_session)
    first_obj_count = len((await db_session.execute(select(OkrObjective))).scalars().all())
    first_prof_count = len((await db_session.execute(select(WritingProfile))).scalars().all())

    # Second apply — same fixture
    plan2 = await _do_apply(sqlite_fixture, db_session)

    second_obj_count = len((await db_session.execute(select(OkrObjective))).scalars().all())
    second_prof_count = len((await db_session.execute(select(WritingProfile))).scalars().all())

    assert second_obj_count == first_obj_count
    assert second_prof_count == first_prof_count

    # Conflicts should be reported on second run
    assert plan2.reports["okr_objectives"].conflict_count > 0


# ── H-AP-04: Pre-existing objective → conflict reported ───────────────────────


@pytest.mark.asyncio
async def test_ap04_conflict_objective(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrObjective

    # Pre-insert a conflicting objective
    pre = OkrObjective(title="Test Objective Alpha", cycle="Q1 2026", progress=0)
    db_session.add(pre)
    await db_session.flush()

    plan = await _do_apply(sqlite_fixture, db_session)

    r = plan.reports["okr_objectives"]
    assert r.conflict_count >= 1
    conflict_keys = [c["natural_key"]["title"] for c in r.conflicts]
    assert "Test Objective Alpha" in conflict_keys

    # Total in DB should still be 2 (the pre-existing + one new)
    all_objs = (await db_session.execute(select(OkrObjective))).scalars().all()
    assert len(all_objs) == 2


# ── H-AP-05: Pre-existing profile → conflict reported ────────────────────────


@pytest.mark.asyncio
async def test_ap05_conflict_profile(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.writing_rules.models import WritingProfile

    pre = WritingProfile(name="Test Writing Profile", status="active")
    db_session.add(pre)
    await db_session.flush()

    plan = await _do_apply(sqlite_fixture, db_session)

    r = plan.reports["writing_profiles"]
    assert r.conflict_count >= 1
    conflict_names = [c["natural_key"]["name"] for c in r.conflicts]
    assert "Test Writing Profile" in conflict_names


# ── H-AP-06: Pre-existing rule → conflict reported ───────────────────────────


@pytest.mark.asyncio
async def test_ap06_conflict_rule(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.writing_rules.models import WritingProfile, WritingRule

    # Insert profile + rule to create conflict
    prof = WritingProfile(name="Test Writing Profile", status="active")
    db_session.add(prof)
    await db_session.flush()

    rule = WritingRule(
        profile_id=prof.id,
        rule_type="voice",
        title="Be concise",
        body="Existing rule body.",
        status="active",
    )
    db_session.add(rule)
    await db_session.flush()

    plan = await _do_apply(sqlite_fixture, db_session)

    r = plan.reports["writing_rules"]
    assert r.conflict_count >= 1


# ── H-AP-07: Validation error → plan has errors, apply should abort ───────────


def test_ap07_validation_error_blocks_apply(tmp_path: Path) -> None:
    """A corrupt source row → plan.has_validation_errors = True → caller aborts."""
    path = _make_sqlite_fixture(
        objectives=[{"id": 1, "title": None}]  # NULL title fails Pydantic validation
    )
    try:
        plan = build_plan(path)
        assert plan.has_validation_errors
    finally:
        path.unlink(missing_ok=True)


# ── H-AP-08: Timestamps converted from unix-seconds to datetime ───────────────


@pytest.mark.asyncio
async def test_ap08_timestamps_converted(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrObjective

    await _do_apply(sqlite_fixture, db_session)

    obj = (
        await db_session.execute(
            select(OkrObjective).where(OkrObjective.title == "Test Objective Alpha")
        )
    ).scalar_one()

    assert isinstance(obj.created_at, datetime)
    assert obj.created_at.tzinfo is not None  # timezone-aware


# ── H-AP-09: JSON bullets stored as JSONB lists ───────────────────────────────


@pytest.mark.asyncio
async def test_ap09_json_bullets_as_list(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrKeyResult

    await _do_apply(sqlite_fixture, db_session)

    kr = (await db_session.execute(select(OkrKeyResult))).scalar_one()
    # done_bullets should be a Python list, not a raw JSON string
    assert isinstance(kr.done_bullets, list)
    assert "Done thing A" in kr.done_bullets


# ── H-AP-10: source_key conflict in writing_sources ──────────────────────────


@pytest.mark.asyncio
async def test_ap10_source_key_conflict(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.writing_rules.models import WritingProfile, WritingSource

    # Pre-insert profile + source
    prof = WritingProfile(name="Test Writing Profile", status="active")
    db_session.add(prof)
    await db_session.flush()

    src = WritingSource(
        profile_id=prof.id,
        source_key="TEST_SOURCE",
        title="Existing Source",
        source_type="reference",
        original_content="x",
        normalized_content="y",
    )
    db_session.add(src)
    await db_session.flush()

    plan = await _do_apply(sqlite_fixture, db_session)

    r = plan.reports["writing_sources"]
    assert r.conflict_count >= 1


# ── H-AP-11: Archived rules still inserted ───────────────────────────────────


@pytest.mark.asyncio
async def test_ap11_archived_rule_inserted(tmp_path: Path, db_session: AsyncSession) -> None:
    from artemis.writing_rules.models import WritingRule

    path = _make_sqlite_fixture(
        profiles=[
            {
                "id": 1,
                "name": "Arch Profile",
                "status": "active",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            }
        ],
        rules=[
            {
                "id": 1,
                "profile_id": 1,
                "rule_type": "voice",
                "title": "Old Rule",
                "body": "Body text.",
                "status": "archived",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
    )
    try:
        await _do_apply(path, db_session)
        rule = (
            await db_session.execute(select(WritingRule).where(WritingRule.title == "Old Rule"))
        ).scalar_one_or_none()
        assert rule is not None
        assert rule.status == "archived"
    finally:
        path.unlink(missing_ok=True)


# ── H-AP-12: Activity linked to KR after apply ───────────────────────────────


@pytest.mark.asyncio
async def test_ap12_activity_kr_linked(sqlite_fixture: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrActivity, OkrKeyResult

    await _do_apply(sqlite_fixture, db_session)

    act = (await db_session.execute(select(OkrActivity))).scalar_one()
    kr = (await db_session.execute(select(OkrKeyResult))).scalar_one()

    assert act.kr_id == kr.id


# ── H-AP-13: Next-up dispatch_params stored as JSONB ─────────────────────────


@pytest.mark.asyncio
async def test_ap13_dispatch_params_jsonb(tmp_path: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrNextUp

    path = _make_sqlite_fixture(
        next_up=[
            {
                "id": 1,
                "ref": "OBJ-1",
                "text": "Do something",
                "prio": "med",
                "source": "agent",
                "action_type": "dispatchable",
                "dispatch_params": json.dumps({"kr_id": 5, "type": "foo"}),
            }
        ]
    )
    try:
        await _do_apply(path, db_session)
        item = (await db_session.execute(select(OkrNextUp))).scalar_one()
        assert isinstance(item.dispatch_params, dict)
        assert item.dispatch_params["kr_id"] == 5
    finally:
        path.unlink(missing_ok=True)


# ── H-AP-14: OKR update previews migrated when source has rows ───────────────


@pytest.mark.asyncio
async def test_ap14_update_previews_migrated(tmp_path: Path, db_session: AsyncSession) -> None:
    from artemis.okr.models import OkrUpdatePreview

    path = _make_sqlite_fixture(
        update_previews=[
            {
                "id": 1,
                "raw_input": "Some text",
                "input_format": "text",
                "diff_json": json.dumps({"changes": []}),
                "created_at": 1700000000,
            }
        ]
    )
    try:
        await _do_apply(path, db_session)
        previews = (await db_session.execute(select(OkrUpdatePreview))).scalars().all()
        assert len(previews) == 1
        assert previews[0].diff_json == {"changes": []}
    finally:
        path.unlink(missing_ok=True)
