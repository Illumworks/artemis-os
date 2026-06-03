"""Tests for per-campaign Writing Studio folder behaviour (ws-folders-per-campaign).

Covers:
  (a) One distinct folder is created per campaign candidate — two candidates
      in the same family get separate folders.
  (b) Renaming the candidate changes the folder's displayed name — the
      serialize helper reads the live candidate name, not the stored snapshot.
  (c) Existing drafts are re-homed into the correct per-candidate folder by
      backfill_campaign_folders.
  (d) Old family-level folders (campaign_id is not a digit string) are
      removed by the backfill.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
from artemis.db import attach_pgvector_codec

# ---------------------------------------------------------------------------
# Test-DB bootstrap (mirrors pattern used in test_f1_provider_resolvers.py)
# ---------------------------------------------------------------------------

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    """
    TRUNCATE
        writing_folders,
        campaign_deliverables,
        campaign_briefs,
        campaign_candidates,
        signal_queue
    RESTART IDENTITY CASCADE
    """
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers to create minimal DB rows without hitting validation routes.
# ---------------------------------------------------------------------------


async def _make_candidate(
    session: AsyncSession,
    *,
    name: str,
    family: str = "obc",
) -> int:
    """Insert a minimal campaign_candidates row; return its id."""
    from artemis.marketing.models import CampaignCandidate

    c = CampaignCandidate(
        campaign_family=family,
        name=name,
        stage="human_gate_1",
        decision_state="approved",
        workspace_state="pending_content",
    )
    session.add(c)
    await session.flush()
    await session.refresh(c)
    return c.id


async def _make_deliverable(
    session: AsyncSession,
    *,
    candidate_id: int,
    family: str = "obc",
    folder_id: int | None = None,
    folder_name: str | None = None,
) -> int:
    """Insert a minimal campaign_deliverables row; return its id."""
    from artemis.marketing.models import CampaignDeliverable

    meta: dict = {}
    if folder_id is not None:
        meta["folder_id"] = folder_id
    if folder_name is not None:
        meta["folder_name"] = folder_name

    d = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=f"ext-{candidate_id}",
        campaign_id=family,
        status="generating",
        deliverable_metadata=meta or None,
    )
    session.add(d)
    await session.flush()
    await session.refresh(d)
    return d.id


async def _make_family_folder(
    session: AsyncSession,
    *,
    family: str,
    name: str,
) -> int:
    """Insert an old-style family-level folder (campaign_id = family string)."""
    from artemis.writing_rules.models import WritingFolder

    f = WritingFolder(name=name, campaign_id=family)
    session.add(f)
    await session.flush()
    await session.refresh(f)
    return f.id


# ===========================================================================
# (a) One folder per candidate
# ===========================================================================


async def test_one_folder_per_candidate_different_family(db_session: AsyncSession) -> None:
    """Two candidates in different families each get their own folder."""
    from artemis.writing_rules import repository as wr_repo

    cand_a = await _make_candidate(db_session, name="OBC Spring Push", family="obc")
    cand_b = await _make_candidate(db_session, name="K12 Refresh", family="k12")

    folder_a = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand_a, candidate_name="OBC Spring Push"
    )
    folder_b = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand_b, candidate_name="K12 Refresh"
    )

    assert folder_a.id != folder_b.id
    assert folder_a.campaign_id == str(cand_a)
    assert folder_b.campaign_id == str(cand_b)


async def test_one_folder_per_candidate_same_family(db_session: AsyncSession) -> None:
    """Two candidates in the SAME family get separate folders (not collapsed)."""
    from artemis.writing_rules import repository as wr_repo

    cand_1 = await _make_candidate(db_session, name="OBC Wave 1", family="obc")
    cand_2 = await _make_candidate(db_session, name="OBC Wave 2", family="obc")

    folder_1 = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand_1, candidate_name="OBC Wave 1"
    )
    folder_2 = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand_2, candidate_name="OBC Wave 2"
    )

    assert folder_1.id != folder_2.id, "Same-family candidates must get distinct folders"
    assert folder_1.campaign_id == str(cand_1)
    assert folder_2.campaign_id == str(cand_2)


async def test_get_or_create_is_idempotent(db_session: AsyncSession) -> None:
    """Calling get_or_create twice for the same candidate returns the same folder."""
    from artemis.writing_rules import repository as wr_repo

    cand = await _make_candidate(db_session, name="Idempotent Test", family="obc")

    f1 = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand, candidate_name="Idempotent Test"
    )
    f2 = await wr_repo.get_or_create_folder_by_candidate(
        db_session, cand, candidate_name="Different Name"
    )

    assert f1.id == f2.id, "Second call must return the existing folder, not create a new one"


# ===========================================================================
# (b) Renaming the candidate changes the serialized folder name
#     These are synchronous unit tests — they use a plain namespace rather
#     than the SQLAlchemy ORM model to avoid the instrumented-attribute dance.
# ===========================================================================


class _FakeFolder:
    """Minimal stand-in for a WritingFolder ORM row (sync unit tests only)."""

    def __init__(
        self,
        *,
        id: int,
        name: str,
        campaign_id: str | None,
        parent_folder_id: int | None = None,
        description: str | None = None,
        sync_id: str | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.campaign_id = campaign_id
        self.parent_folder_id = parent_folder_id
        self.description = description
        self.sync_id = sync_id


def test_serialize_folder_uses_live_candidate_name() -> None:
    """_serialize_folder must use the live candidate name, not the stored snapshot."""
    from artemis.marketing.routes.writing_studio import _serialize_folder

    folder = _FakeFolder(id=1, name="stale-snapshot-name", campaign_id="42")

    # Simulate a rename: the live candidate name differs from the stored snapshot.
    candidate_names = {"42": "Renamed Campaign Name"}
    result = _serialize_folder(folder, candidate_names)

    assert result["name"] == "Renamed Campaign Name", (
        "Folder name must reflect the live candidate name, not the stored snapshot"
    )
    assert result["candidate_id"] == 42


def test_serialize_folder_falls_back_when_no_candidate_map() -> None:
    """Without a candidate map, _serialize_folder falls back to stored name."""
    from artemis.marketing.routes.writing_studio import _serialize_folder

    folder = _FakeFolder(id=2, name="stored-name", campaign_id="99")
    result = _serialize_folder(folder, None)

    assert result["name"] == "stored-name"
    assert result["candidate_id"] == 99


def test_serialize_folder_non_numeric_campaign_id_no_candidate_id() -> None:
    """A legacy family-level folder (non-digit campaign_id) serializes candidate_id=None."""
    from artemis.marketing.routes.writing_studio import _serialize_folder

    folder = _FakeFolder(id=3, name="obc legacy", campaign_id="obc")
    result = _serialize_folder(folder, {"obc": "should not be used"})

    # Non-digit campaign_id must NOT use the candidate-name lookup.
    assert result["name"] == "obc legacy"
    assert result["candidate_id"] is None


# ===========================================================================
# (c) + (d) backfill_campaign_folders
# ===========================================================================


async def test_backfill_assigns_per_candidate_folders(db_session: AsyncSession) -> None:
    """backfill creates one folder per candidate and updates deliverable metadata."""
    from artemis.marketing.writing_studio.invoke import backfill_campaign_folders

    cand_a = await _make_candidate(db_session, name="Camp A", family="obc")
    cand_b = await _make_candidate(db_session, name="Camp B", family="obc")  # same family!

    del_a = await _make_deliverable(db_session, candidate_id=cand_a, family="obc")
    del_b = await _make_deliverable(db_session, candidate_id=cand_b, family="obc")

    await db_session.commit()

    async with AsyncSession(
        create_async_engine(_db_url, echo=False, poolclass=NullPool), expire_on_commit=False
    ) as session2:
        result = await backfill_campaign_folders(session2)
        await session2.commit()

        from sqlalchemy import select

        from artemis.marketing.models import CampaignDeliverable
        from artemis.writing_rules.models import WritingFolder

        # Two distinct per-candidate folders must exist.
        folders_result = await session2.execute(
            select(WritingFolder).where(WritingFolder.campaign_id.in_([str(cand_a), str(cand_b)]))
        )
        per_cand_folders = list(folders_result.scalars())
        assert len(per_cand_folders) == 2, (
            f"Expected 2 per-candidate folders, got {len(per_cand_folders)}"
        )
        cids = {f.campaign_id for f in per_cand_folders}
        assert cids == {str(cand_a), str(cand_b)}

        # Each folder id must differ.
        fids = [f.id for f in per_cand_folders]
        assert fids[0] != fids[1]

        # Deliverables must point at different folders.
        da = await session2.get(CampaignDeliverable, del_a)
        db_ = await session2.get(CampaignDeliverable, del_b)
        assert da is not None and db_ is not None
        assert da.deliverable_metadata["folder_id"] != db_.deliverable_metadata["folder_id"]

    assert result.rows_examined >= 2
    assert result.rows_updated >= 2


async def test_backfill_removes_family_folders(db_session: AsyncSession) -> None:
    """backfill_campaign_folders deletes old family-level folders."""
    from artemis.marketing.writing_studio.invoke import backfill_campaign_folders

    cand = await _make_candidate(db_session, name="Camp X", family="obc")
    # Old-style family folder
    old_folder_id = await _make_family_folder(db_session, family="obc", name="obc (wrong)")
    await _make_deliverable(db_session, candidate_id=cand, family="obc")
    await db_session.commit()

    async with AsyncSession(
        create_async_engine(_db_url, echo=False, poolclass=NullPool), expire_on_commit=False
    ) as session2:
        result = await backfill_campaign_folders(session2)
        await session2.commit()

        from sqlalchemy import select

        from artemis.writing_rules.models import WritingFolder

        # Old family folder must be gone.
        gone = await session2.get(WritingFolder, old_folder_id)
        assert gone is None, "Family-level folder must have been removed by backfill"

        # Per-candidate folder must exist.
        new_folder_result = await session2.execute(
            select(WritingFolder).where(WritingFolder.campaign_id == str(cand))
        )
        new_folder = new_folder_result.scalar_one_or_none()
        assert new_folder is not None, "Per-candidate folder must be created during backfill"

    assert result.family_folders_removed >= 1


async def test_backfill_is_idempotent(db_session: AsyncSession) -> None:
    """Running backfill twice does not create duplicate folders or fail."""
    from artemis.marketing.writing_studio.invoke import backfill_campaign_folders

    cand = await _make_candidate(db_session, name="Idempotent Camp", family="k12")
    await _make_deliverable(db_session, candidate_id=cand, family="k12")
    await db_session.commit()

    engine2 = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    async with AsyncSession(engine2, expire_on_commit=False) as s2:
        await backfill_campaign_folders(s2)
        await s2.commit()

    async with AsyncSession(engine2, expire_on_commit=False) as s3:
        result2 = await backfill_campaign_folders(s3)
        await s3.commit()

    from sqlalchemy import select

    from artemis.writing_rules.models import WritingFolder

    async with AsyncSession(engine2, expire_on_commit=False) as s4:
        folder_result = await s4.execute(
            select(WritingFolder).where(WritingFolder.campaign_id == str(cand))
        )
        folders = list(folder_result.scalars())

    assert len(folders) == 1, "Idempotent run must not create duplicate folders"
    # Second run should update 0 rows (already correct).
    assert result2.rows_updated == 0
