"""Draft comments API for Writing Studio."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.identity.schemas import CurrentUserRead
from artemis.marketing.comments import repository as comments_repo
from artemis.marketing.comments.models import Comment
from artemis.marketing.comments.schemas import CommentCreate, CommentRead, CommentUpdate
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/writing-studio",
    tags=["writing-studio-comments"],
    dependencies=[Depends(require_token)],
)


# ── Slack DM helper ────────────────────────────────────────────────────────────


async def _dm_mentioned_users(
    *,
    mentions: list[str],
    author_name: str,
    draft_title: str,
    comment_body: str,
    draft_id: int,
) -> None:
    """Fire-and-forget DM to each mentioned user.

    Failure is non-fatal: any exception is logged and swallowed so the
    comment + mentions always persist regardless of Slack state.

    Uses ``users.lookupByEmail`` (NOT ``users.list`` + filter — pagination
    gotcha: first-page-only pagination silently misses recipients).

    Required Slack bot scopes: ``chat:write`` + ``im:write``.
    If either scope is missing Slack returns ``missing_scope``; we log it and
    continue — comment storage is unaffected.
    """
    if not mentions:
        return

    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackAPIError, SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")

        if not integrations:
            logger.info(
                "comment-dm: no active Slack integration — skipping DMs for %d mention(s)",
                len(mentions),
            )
            return

        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token_val = creds.get("bot_token") or creds.get("token") or creds.get("access_token")
        if not token_val:
            logger.warning("comment-dm: Slack credentials present but no token field found")
            return

        token = str(token_val)
        client = SlackClient(token)

        # Build the DM text.
        excerpt = comment_body[:120] + ("…" if len(comment_body) > 120 else "")
        base_url = (
            str(settings.app_base_url).rstrip("/")
            if getattr(settings, "app_base_url", None)
            else ""
        )
        link = (
            f"{base_url}/#writing-studio?draft={draft_id}"
            if base_url
            else f"/#writing-studio?draft={draft_id}"
        )
        text = f'{author_name} mentioned you on "{draft_title}": {excerpt}\nView draft: {link}'

        for email in mentions:
            try:
                slack_user_id = await client.lookup_user_by_email(email)
                if not slack_user_id:
                    logger.info("comment-dm: no Slack user found for email %r — skipping", email)
                    continue
                result = await client.post_dm(slack_user_id, text)
                logger.info(
                    "comment-dm: sent DM to %r (slack_user=%s) ts=%s",
                    email,
                    slack_user_id,
                    result.get("ts"),
                )
            except SlackAPIError as exc:
                # Specifically call out missing scope so Jon can enable it.
                if "missing_scope" in str(exc):
                    logger.warning(
                        "comment-dm: Slack bot missing required scope (chat:write / im:write). "
                        "Enable these scopes in the Slack app settings. Error: %s",
                        exc,
                    )
                else:
                    logger.warning("comment-dm: Slack DM failed for %r: %s", email, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("comment-dm: unexpected error DMing %r: %s", email, exc)

    except Exception as exc:  # noqa: BLE001
        logger.warning("comment-dm: could not send DMs (non-fatal): %s", exc)


def _serialize_user(user: User) -> CurrentUserRead:
    return CurrentUserRead(id=user.id, email=user.email, name=user.name)


def _serialize_optional_user(user: User | None) -> CurrentUserRead | None:
    if user is None:
        return None
    return _serialize_user(user)


def _serialize_comment_tree(comments: list[Comment]) -> list[CommentRead]:
    by_id: dict[int, CommentRead] = {}
    roots: list[CommentRead] = []

    for comment in comments:
        by_id[comment.id] = CommentRead(
            id=comment.id,
            draft_id=comment.draft_id,
            author_user_id=comment.author_user_id,
            parent_id=comment.parent_id,
            body=comment.body,
            anchor_start=comment.anchor_start,
            anchor_end=comment.anchor_end,
            anchored_text=comment.anchored_text,
            status=comment.status,
            mentions=list(comment.mentions or []),
            author=_serialize_user(comment.author),
            resolved_by_user_id=comment.resolved_by_user_id,
            resolved_by=_serialize_optional_user(comment.resolved_by_user),
            resolved_at=comment.resolved_at,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            replies=[],
        )

    for comment in comments:
        item = by_id[comment.id]
        if comment.parent_id is not None and comment.parent_id in by_id:
            by_id[comment.parent_id].replies.append(item)
        else:
            roots.append(item)

    return roots


async def _get_comment_or_404(session: AsyncSession, comment_id: int) -> CommentRead:
    comment = await comments_repo.get_comment(session, comment_id)
    if comment is None:
        raise not_found("comment not found", "comment_not_found")
    return _serialize_comment_tree([comment])[0]


@router.get("/drafts/{draft_id}/comments", response_model=list[CommentRead])
async def list_draft_comments(
    draft_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[CommentRead]:
    if await comments_repo.get_draft(session, draft_id) is None:
        raise not_found("draft not found", "draft_not_found")
    comments = await comments_repo.list_comments(session, draft_id)
    return _serialize_comment_tree(comments)


@router.post("/drafts/{draft_id}/comments", response_model=CommentRead, status_code=201)
async def create_draft_comment(
    draft_id: int,
    body: CommentCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> CommentRead:
    try:
        comment = await comments_repo.create_comment(
            session,
            draft_id=draft_id,
            author_user_id=current_user.id,
            body=body.body,
            anchor_start=body.anchor_start,
            anchor_end=body.anchor_end,
            anchored_text=body.anchored_text,
            parent_id=body.parent_id,
            mentions=body.mentions,
        )
    except LookupError as exc:
        if str(exc) == "parent_comment_not_found":
            raise not_found("parent comment not found", "parent_comment_not_found") from exc
        raise not_found("draft not found", "draft_not_found") from exc
    except ValueError as exc:
        raise bad_request(str(exc), "comment_invalid_parent") from exc

    await session.commit()

    # DM mentioned users via Slack — non-fatal; comment always persists.
    if body.mentions:
        draft = await comments_repo.get_draft(session, draft_id)
        draft_title = "Untitled draft"
        if draft is not None:
            meta = draft.deliverable_metadata or {}
            draft_title = str(meta.get("title") or f"Draft {draft_id}")
        author_name = current_user.name or current_user.email

        asyncio.ensure_future(
            _dm_mentioned_users(
                mentions=body.mentions,
                author_name=author_name,
                draft_title=draft_title,
                comment_body=body.body,
                draft_id=draft_id,
            )
        )

    return await _get_comment_or_404(session, comment.id)


@router.post("/comments/{comment_id}/resolve", response_model=CommentRead)
async def resolve_comment(
    comment_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> CommentRead:
    comment = await comments_repo.resolve_comment(
        session,
        comment_id=comment_id,
        resolved_by_user_id=current_user.id,
    )
    if comment is None:
        raise not_found("comment not found", "comment_not_found")
    await session.commit()
    return await _get_comment_or_404(session, comment.id)


@router.post("/comments/{comment_id}/reopen", response_model=CommentRead)
async def reopen_comment(
    comment_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CommentRead:
    comment = await comments_repo.reopen_comment(session, comment_id=comment_id)
    if comment is None:
        raise not_found("comment not found", "comment_not_found")
    await session.commit()
    return await _get_comment_or_404(session, comment.id)


@router.patch("/comments/{comment_id}", response_model=CommentRead)
async def update_comment(
    comment_id: int,
    body: CommentUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> CommentRead:
    try:
        comment = await comments_repo.update_comment(
            session,
            comment_id=comment_id,
            author_user_id=current_user.id,
            body=body.body,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": str(exc), "code": "comment_edit_forbidden"},
        ) from exc

    if comment is None:
        raise not_found("comment not found", "comment_not_found")

    await session.commit()
    return await _get_comment_or_404(session, comment.id)
