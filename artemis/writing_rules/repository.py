"""Repository helpers for Writing Studio rules + scaffolding domain.

All functions are async and accept a SQLAlchemy AsyncSession.
Callers own commit / rollback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules.models import (
    WritingExample,
    WritingFolder,
    WritingProfile,
    WritingRule,
    WritingSource,
)

# ── Profiles ──────────────────────────────────────────────────────────────────


async def list_profiles(
    session: AsyncSession, *, include_archived: bool = False
) -> list[WritingProfile]:
    q = select(WritingProfile).order_by(WritingProfile.created_at, WritingProfile.id)
    if not include_archived:
        q = q.where(WritingProfile.status != "archived")
    result = await session.execute(q)
    return list(result.scalars())


async def get_profile(session: AsyncSession, profile_id: int) -> WritingProfile | None:
    return await session.get(WritingProfile, profile_id)


async def get_active_profile(session: AsyncSession) -> WritingProfile | None:
    """Return first active profile (mirrors Node getFirstActiveProfile)."""
    result = await session.execute(
        select(WritingProfile)
        .where(WritingProfile.status == "active")
        .order_by(WritingProfile.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_profile(session: AsyncSession, **kwargs: Any) -> WritingProfile:
    profile = WritingProfile(**kwargs)
    session.add(profile)
    await session.flush()
    await session.refresh(profile)
    return profile


async def update_profile(
    session: AsyncSession, profile_id: int, **kwargs: Any
) -> WritingProfile | None:
    profile = await get_profile(session, profile_id)
    if profile is None:
        return None
    for key, value in kwargs.items():
        setattr(profile, key, value)
    profile.updated_at = datetime.now(UTC)
    await session.flush()
    return profile


# ── Folders ───────────────────────────────────────────────────────────────────


async def list_folders(session: AsyncSession, profile_id: int | None = None) -> list[WritingFolder]:
    q = select(WritingFolder).order_by(WritingFolder.created_at, WritingFolder.id)
    if profile_id is not None:
        q = q.where(WritingFolder.profile_id == profile_id)
    result = await session.execute(q)
    return list(result.scalars())


async def get_folder(session: AsyncSession, folder_id: int) -> WritingFolder | None:
    return await session.get(WritingFolder, folder_id)


async def get_folder_by_sync_id(session: AsyncSession, sync_id: str) -> WritingFolder | None:
    result = await session.execute(
        select(WritingFolder).where(WritingFolder.sync_id == sync_id).limit(1)
    )
    return result.scalar_one_or_none()


async def get_folder_by_campaign(session: AsyncSession, campaign_id: str) -> WritingFolder | None:
    """Return the first folder whose campaign_id column matches the given value.

    ``campaign_id`` here is the TEXT key stored in ``writing_folders.campaign_id``.
    For per-candidate folders this will be ``str(candidate_id)``; for legacy
    family-level folders it will be the family string (e.g. ``"obc"``).
    """
    result = await session.execute(
        select(WritingFolder)
        .where(WritingFolder.campaign_id == campaign_id)
        .order_by(WritingFolder.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_folder_by_candidate(session: AsyncSession, candidate_id: int) -> WritingFolder | None:
    """Return the per-campaign folder for a given candidate id, or None.

    Looks up ``writing_folders`` whose ``campaign_id`` column equals
    ``str(candidate_id)``.  This is the canonical lookup key for the new
    one-folder-per-campaign behaviour.
    """
    return await get_folder_by_campaign(session, str(candidate_id))


async def get_or_create_folder_by_candidate(
    session: AsyncSession,
    candidate_id: int,
    *,
    candidate_name: str | None = None,
) -> WritingFolder:
    """Return an existing per-candidate folder, or create one if none exists.

    The folder is keyed on ``str(candidate_id)`` stored in
    ``writing_folders.campaign_id``.  The folder's ``name`` is set once at
    creation time (as a snapshot) but is **always overridden at read/serialize
    time** by deriving it from the live ``CampaignCandidate.name``; callers
    should never rely on ``folder.name`` being current.

    ``candidate_name`` provides the initial snapshot name for the folder.
    When omitted the folder is named ``"Campaign {candidate_id}"``.

    The caller is responsible for flushing / committing.
    """
    folder = await get_folder_by_candidate(session, candidate_id)
    if folder is not None:
        return folder
    snapshot_name = candidate_name or f"Campaign {candidate_id}"
    folder = WritingFolder(
        name=snapshot_name,
        campaign_id=str(candidate_id),
    )
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def get_or_create_folder_by_campaign(
    session: AsyncSession,
    campaign_id: str,
    *,
    name: str | None = None,
) -> WritingFolder:
    """Deprecated shim — prefer ``get_or_create_folder_by_candidate``.

    Retained for backward compatibility with callers that use the old
    family-string keying.  New code should always call
    ``get_or_create_folder_by_candidate`` with a numeric candidate id.
    """
    folder = await get_folder_by_campaign(session, campaign_id)
    if folder is not None:
        return folder
    folder = WritingFolder(
        name=name or campaign_id,
        campaign_id=campaign_id,
    )
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def create_folder(session: AsyncSession, **kwargs: Any) -> WritingFolder:
    folder = WritingFolder(**kwargs)
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def update_folder(
    session: AsyncSession, folder_id: int, **kwargs: Any
) -> WritingFolder | None:
    folder = await get_folder(session, folder_id)
    if folder is None:
        return None
    for key, value in kwargs.items():
        setattr(folder, key, value)
    folder.updated_at = datetime.now(UTC)
    await session.flush()
    return folder


async def delete_folder(session: AsyncSession, folder_id: int) -> bool:
    folder = await session.get(WritingFolder, folder_id)
    if folder is None:
        return False
    await session.delete(folder)
    await session.flush()
    return True


# ── Rules ─────────────────────────────────────────────────────────────────────


async def list_rules(
    session: AsyncSession,
    profile_id: int | None = None,
    rule_type: str | None = None,
    *,
    include_archived: bool = False,
) -> list[WritingRule]:
    q = select(WritingRule).order_by(WritingRule.created_at, WritingRule.id)
    if profile_id is not None:
        q = q.where(WritingRule.profile_id == profile_id)
    if rule_type is not None:
        q = q.where(WritingRule.rule_type == rule_type)
    if not include_archived:
        q = q.where(WritingRule.status != "archived")
    result = await session.execute(q)
    return list(result.scalars())


async def get_rule(session: AsyncSession, rule_id: int) -> WritingRule | None:
    return await session.get(WritingRule, rule_id)


async def get_rule_by_profile_type_title(
    session: AsyncSession,
    profile_id: int | None,
    rule_type: str,
    title: str,
) -> WritingRule | None:
    """Return the active rule matching (profile_id, rule_type, title).

    Mirrors Node's getRuleByProfileTypeTitle prepared statement.
    """
    q = (
        select(WritingRule)
        .where(
            WritingRule.profile_id == profile_id,
            WritingRule.rule_type == rule_type,
            WritingRule.title == title,
            WritingRule.status != "archived",
        )
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def create_rule(session: AsyncSession, **kwargs: Any) -> WritingRule:
    rule = WritingRule(**kwargs)
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return rule


async def update_rule(session: AsyncSession, rule_id: int, **kwargs: Any) -> WritingRule | None:
    rule = await get_rule(session, rule_id)
    if rule is None:
        return None
    for key, value in kwargs.items():
        setattr(rule, key, value)
    rule.updated_at = datetime.now(UTC)
    await session.flush()
    return rule


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    rule = await session.get(WritingRule, rule_id)
    if rule is None:
        return False
    await session.delete(rule)
    await session.flush()
    return True


# ── Examples ──────────────────────────────────────────────────────────────────


async def list_examples(
    session: AsyncSession,
    profile_id: int | None = None,
    example_type: str | None = None,
) -> list[WritingExample]:
    q = select(WritingExample).order_by(WritingExample.created_at, WritingExample.id)
    if profile_id is not None:
        q = q.where(WritingExample.profile_id == profile_id)
    if example_type is not None:
        q = q.where(WritingExample.example_type == example_type)
    result = await session.execute(q)
    return list(result.scalars())


async def get_example(session: AsyncSession, example_id: int) -> WritingExample | None:
    return await session.get(WritingExample, example_id)


async def get_example_by_profile_title_type(
    session: AsyncSession,
    profile_id: int | None,
    title: str,
    example_type: str,
) -> WritingExample | None:
    result = await session.execute(
        select(WritingExample)
        .where(
            WritingExample.profile_id == profile_id,
            WritingExample.title == title,
            WritingExample.example_type == example_type,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_example(session: AsyncSession, **kwargs: Any) -> WritingExample:
    example = WritingExample(**kwargs)
    session.add(example)
    await session.flush()
    await session.refresh(example)
    return example


async def update_example(
    session: AsyncSession, example_id: int, **kwargs: Any
) -> WritingExample | None:
    example = await get_example(session, example_id)
    if example is None:
        return None
    for key, value in kwargs.items():
        setattr(example, key, value)
    example.updated_at = datetime.now(UTC)
    await session.flush()
    return example


async def delete_example(session: AsyncSession, example_id: int) -> bool:
    example = await session.get(WritingExample, example_id)
    if example is None:
        return False
    await session.delete(example)
    await session.flush()
    return True


# ── Sources ───────────────────────────────────────────────────────────────────


async def list_sources(session: AsyncSession, profile_id: int | None = None) -> list[WritingSource]:
    q = select(WritingSource).order_by(WritingSource.imported_at, WritingSource.id)
    if profile_id is not None:
        q = q.where(WritingSource.profile_id == profile_id)
    result = await session.execute(q)
    return list(result.scalars())


async def get_source(session: AsyncSession, source_id: int) -> WritingSource | None:
    return await session.get(WritingSource, source_id)


async def get_source_by_profile_key(
    session: AsyncSession, profile_id: int | None, source_key: str
) -> WritingSource | None:
    """Mirrors Node's getSourceByKey prepared statement."""
    result = await session.execute(
        select(WritingSource)
        .where(
            WritingSource.profile_id == profile_id,
            WritingSource.source_key == source_key,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_source(session: AsyncSession, **kwargs: Any) -> WritingSource:
    source = WritingSource(**kwargs)
    session.add(source)
    await session.flush()
    await session.refresh(source)
    return source


async def update_source(
    session: AsyncSession, source_id: int, **kwargs: Any
) -> WritingSource | None:
    source = await get_source(session, source_id)
    if source is None:
        return None
    for key, value in kwargs.items():
        setattr(source, key, value)
    source.updated_at = datetime.now(UTC)
    await session.flush()
    return source


async def delete_source(session: AsyncSession, source_id: int) -> bool:
    source = await session.get(WritingSource, source_id)
    if source is None:
        return False
    await session.delete(source)
    await session.flush()
    return True
