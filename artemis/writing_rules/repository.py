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
