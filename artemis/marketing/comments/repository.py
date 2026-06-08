"""Repository helpers for Writing Studio draft comments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from artemis.marketing.comments.models import Comment
from artemis.marketing.models import CampaignDeliverable


def _normalize_mentions(mentions: list[str] | None) -> list[str]:
    if not mentions:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in mentions:
        value = raw.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


async def get_draft(session: AsyncSession, draft_id: int) -> CampaignDeliverable | None:
    return await session.get(CampaignDeliverable, draft_id)


async def get_comment(session: AsyncSession, comment_id: int) -> Comment | None:
    result = await session.execute(
        select(Comment)
        .where(Comment.id == comment_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Comment.author),
            selectinload(Comment.resolved_by_user),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_comments(session: AsyncSession, draft_id: int) -> list[Comment]:
    result = await session.execute(
        select(Comment)
        .where(Comment.draft_id == draft_id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
        .execution_options(populate_existing=True)
        .options(
            selectinload(Comment.author),
            selectinload(Comment.resolved_by_user),
        )
    )
    return list(result.scalars().all())


async def create_comment(
    session: AsyncSession,
    *,
    draft_id: int,
    author_user_id: int,
    body: str,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
    anchored_text: str | None = None,
    parent_id: int | None = None,
    mentions: list[str] | None = None,
) -> Comment:
    if await get_draft(session, draft_id) is None:
        raise LookupError("draft_not_found")

    if parent_id is not None:
        parent = await session.get(Comment, parent_id)
        if parent is None:
            raise LookupError("parent_comment_not_found")
        if parent.draft_id != draft_id:
            raise ValueError("parent comment must belong to the same draft")

    comment = Comment(
        draft_id=draft_id,
        author_user_id=author_user_id,
        parent_id=parent_id,
        body=body.strip(),
        anchor_start=anchor_start,
        anchor_end=anchor_end,
        anchored_text=anchored_text,
        mentions=_normalize_mentions(mentions),
    )
    session.add(comment)
    await session.flush()
    return comment


async def resolve_comment(
    session: AsyncSession,
    *,
    comment_id: int,
    resolved_by_user_id: int,
) -> Comment | None:
    comment = await session.get(Comment, comment_id)
    if comment is None:
        return None
    comment.status = "resolved"
    comment.resolved_at = datetime.now(UTC)
    comment.resolved_by_user_id = resolved_by_user_id
    comment.updated_at = datetime.now(UTC)
    await session.flush()
    return comment


async def reopen_comment(session: AsyncSession, *, comment_id: int) -> Comment | None:
    comment = await session.get(Comment, comment_id)
    if comment is None:
        return None
    comment.status = "open"
    comment.resolved_at = None
    comment.resolved_by_user_id = None
    comment.updated_at = datetime.now(UTC)
    await session.flush()
    return comment


async def update_comment(
    session: AsyncSession,
    *,
    comment_id: int,
    author_user_id: int,
    body: str,
) -> Comment | None:
    comment = await session.get(Comment, comment_id)
    if comment is None:
        return None
    if comment.author_user_id != author_user_id:
        raise PermissionError("only the author can edit this comment")
    comment.body = body.strip()
    comment.updated_at = datetime.now(UTC)
    await session.flush()
    return comment
