"""Comments backend tests: repository lifecycle + HTTP endpoints."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.identity.models import User
from artemis.identity.repository import get_or_create_user
from artemis.marketing.comments import repository as comments_repo
from artemis.marketing.comments.models import Comment
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal


async def _make_deliverable(db: AsyncSession, family: str = "obc") -> CampaignDeliverable:
    sig = await create_signal(
        db,
        headline="Comments test signal",
        campaign_family=family,
        source_type="manual",
        summary="Comments test",
        discovered_by="test",
    )
    candidate: CampaignCandidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="stub-comments-test",
        campaign_id=str(candidate.id),
        status="draft_ready",
        deliverable_metadata={"title": "Comments Draft"},
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


async def _dev_user(db: AsyncSession) -> User:
    user = await get_or_create_user(db, "dev@local", "Local Dev")
    await db.commit()
    await db.refresh(user)
    return user


class TestCommentsRepository:
    async def test_comment_lifecycle_is_lossless(self, db_session: AsyncSession) -> None:
        deliverable = await _make_deliverable(db_session)
        user = await _dev_user(db_session)

        parent = await comments_repo.create_comment(
            db_session,
            draft_id=deliverable.id,
            author_user_id=user.id,
            body="Root comment",
            anchor_start=3,
            anchor_end=9,
            anchored_text="comment",
            mentions=["pm@amira.com"],
        )
        reply = await comments_repo.create_comment(
            db_session,
            draft_id=deliverable.id,
            author_user_id=user.id,
            body="Reply comment",
            parent_id=parent.id,
        )
        await db_session.commit()

        comments = await comments_repo.list_comments(db_session, deliverable.id)
        assert [comment.id for comment in comments] == [parent.id, reply.id]
        assert comments[1].parent_id == parent.id

        resolved = await comments_repo.resolve_comment(
            db_session,
            comment_id=parent.id,
            resolved_by_user_id=user.id,
        )
        assert resolved is not None
        await db_session.commit()

        persisted = await comments_repo.get_comment(db_session, parent.id)
        assert persisted is not None
        assert persisted.status == "resolved"
        assert persisted.resolved_by_user_id == user.id
        assert persisted.resolved_at is not None

        reopened = await comments_repo.reopen_comment(db_session, comment_id=parent.id)
        assert reopened is not None
        await db_session.commit()

        reopened_persisted = await comments_repo.get_comment(db_session, parent.id)
        assert reopened_persisted is not None
        assert reopened_persisted.status == "open"
        assert reopened_persisted.resolved_by_user_id is None
        assert reopened_persisted.resolved_at is None

        all_rows = (
            (
                await db_session.execute(
                    select(Comment).where(Comment.draft_id == deliverable.id).order_by(Comment.id)
                )
            )
            .scalars()
            .all()
        )
        assert [row.id for row in all_rows] == [parent.id, reply.id]


class TestCommentsRoutes:
    async def test_create_reply_resolve_reopen_and_author_is_current_user(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        deliverable = await _make_deliverable(db_session)

        create_response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/comments",
            json={
                "body": "Please tighten this opener.",
                "anchorStart": 0,
                "anchorEnd": 24,
                "anchoredText": "Please tighten this",
                "mentions": ["PMM@Amira.com", "pmm@amira.com", " "],
                "authorUserId": 999999,
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        assert created["body"] == "Please tighten this opener."
        assert created["mentions"] == ["pmm@amira.com"]
        assert created["author"]["email"] == "dev@local"
        assert created["author"]["name"] == "Local Dev"
        assert created["authorUserId"] != 999999

        current_user = await db_session.scalar(select(User).where(User.email == "dev@local"))
        assert current_user is not None
        assert created["authorUserId"] == current_user.id

        reply_response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/comments",
            json={
                "body": "Reply from the same verified user.",
                "parentId": created["id"],
            },
        )
        assert reply_response.status_code == 201, reply_response.text
        reply = reply_response.json()
        assert reply["parentId"] == created["id"]
        assert reply["author"]["email"] == "dev@local"

        list_response = await client.get(f"/api/writing-studio/drafts/{deliverable.id}/comments")
        assert list_response.status_code == 200, list_response.text
        listed = list_response.json()
        assert len(listed) == 1
        assert listed[0]["id"] == created["id"]
        assert listed[0]["replies"][0]["id"] == reply["id"]
        assert listed[0]["replies"][0]["parentId"] == created["id"]

        resolve_response = await client.post(
            f"/api/writing-studio/comments/{created['id']}/resolve"
        )
        assert resolve_response.status_code == 200, resolve_response.text
        resolved = resolve_response.json()
        assert resolved["status"] == "resolved"
        assert resolved["resolvedBy"]["email"] == "dev@local"
        assert resolved["resolvedByUserId"] == current_user.id
        assert resolved["resolvedAt"] is not None

        persisted_after_resolve = await comments_repo.get_comment(db_session, created["id"])
        assert persisted_after_resolve is not None
        assert persisted_after_resolve.status == "resolved"

        reopen_response = await client.post(f"/api/writing-studio/comments/{created['id']}/reopen")
        assert reopen_response.status_code == 200, reopen_response.text
        reopened = reopen_response.json()
        assert reopened["status"] == "open"
        assert reopened["resolvedBy"] is None
        assert reopened["resolvedByUserId"] is None
        assert reopened["resolvedAt"] is None

        persisted_after_reopen = await comments_repo.get_comment(db_session, created["id"])
        assert persisted_after_reopen is not None
        assert persisted_after_reopen.status == "open"

    async def test_patch_comment_is_author_only(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        deliverable = await _make_deliverable(db_session)
        other_user = await get_or_create_user(db_session, "other@amira.com", "Other Author")
        await db_session.commit()
        await db_session.refresh(other_user)

        comment = await comments_repo.create_comment(
            db_session,
            draft_id=deliverable.id,
            author_user_id=other_user.id,
            body="Original body",
        )
        await db_session.commit()

        response = await client.patch(
            f"/api/writing-studio/comments/{comment.id}",
            json={"body": "Dev user should not be allowed to edit this."},
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "comment_edit_forbidden"

        persisted = await comments_repo.get_comment(db_session, comment.id)
        assert persisted is not None
        assert persisted.body == "Original body"
