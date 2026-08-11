"""Summary review surface — Sara and Missy approve, edit, or send back AI drafts.

Owner decision (Jon, 2026-08-11): AI writes catalog summaries directly, then
Enablement reviews, and their feedback regenerates. This is the review half.

The safety property: a generated summary lands as ``ai_draft`` and Kai caveats
it as AI-drafted rather than reading it out as catalog fact. Only a human action
here can set ``enablement_verified``. Nothing in the generator can.

Three actions, matching what a reviewer actually wants to do:
  approve            the draft is right as written
  approve with edit  the reviewer rewrites it; their text is verified outright
  send back          the draft is wrong; the note regenerates it

Endpoints are read/write over catalog *metadata* only. They never delete a row
and never touch links or approval status, so the lossless rule holds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

import artemis.db as _db
from artemis.enablement.enrichment import (
    STATUS_AI_DRAFT,
    STATUS_NEEDS_REVISION,
    STATUS_VERIFIED,
    AssetFacts,
    apply_enrichment,
    generate_enrichment,
    reembed,
)
from artemis.enablement.models import EnablementAsset

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enablement/review", tags=["enablement-review"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class ReviewItem(BaseModel):
    id: int
    drive_file_id: str
    title: str | None
    asset_name: str | None
    type: str | None
    tags: list[str]
    audience: str | None
    format: str | None
    grade_range: str | None
    summary: str | None
    summary_status: str | None
    summary_feedback: str | None
    summary_reviewed_by: str | None
    drive_link: str | None
    source_sheet: str | None


class ReviewQueue(BaseModel):
    items: list[ReviewItem]
    count: int
    totals: dict[str, int]


class ApproveRequest(BaseModel):
    reviewer: str = Field(min_length=1, description="Who reviewed it (name or Slack id).")
    summary: str | None = Field(
        default=None,
        min_length=20,
        max_length=400,
        description=(
            "Optional replacement text. When present the reviewer's own wording "
            "is stored and marked verified."
        ),
    )
    audience: str | None = None
    format: str | None = None
    grade_range: str | None = None


class SendBackRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    feedback: str = Field(
        min_length=3,
        max_length=1000,
        description="What is wrong with the draft. Drives regeneration.",
    )
    regenerate: bool = Field(
        default=True,
        description="Redraft immediately using the feedback. False just parks it.",
    )


class ReviewResult(BaseModel):
    id: int
    summary_status: str | None
    summary: str | None
    regenerated: bool = False
    detail: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_item(asset: Any) -> ReviewItem:
    return ReviewItem(
        id=asset.id,
        drive_file_id=asset.drive_file_id,
        title=asset.title,
        asset_name=asset.asset_name,
        type=asset.type,
        tags=list(asset.tags or []),
        audience=asset.audience,
        format=asset.format,
        grade_range=asset.grade_range,
        summary=asset.summary,
        summary_status=asset.summary_status,
        summary_feedback=asset.summary_feedback,
        summary_reviewed_by=asset.summary_reviewed_by,
        drive_link=asset.drive_link,
        source_sheet=asset.source_sheet,
    )


async def _load(session: Any, asset_id: int) -> Any:
    asset = (
        await session.execute(select(EnablementAsset).where(EnablementAsset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")
    return asset


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=ReviewQueue)
async def list_review_queue(
    status: Annotated[
        Literal["ai_draft", "needs_revision", "enablement_verified", "all"],
        Query(description="Which bucket to show. Defaults to what needs a human."),
    ] = "ai_draft",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewQueue:
    """The queue Sara and Missy work through."""
    async with _db.SessionLocal() as session:
        stmt = select(EnablementAsset).where(
            EnablementAsset.status.is_distinct_from("archived"),
        )
        if status != "all":
            stmt = stmt.where(EnablementAsset.summary_status == status)
        stmt = stmt.order_by(EnablementAsset.title).limit(limit).offset(offset)
        rows = list((await session.execute(stmt)).scalars().all())

        totals_stmt = (
            select(EnablementAsset.summary_status, func.count())
            .where(EnablementAsset.status.is_distinct_from("archived"))
            .group_by(EnablementAsset.summary_status)
        )
        totals = {
            (key or "no_summary"): int(count)
            for key, count in (await session.execute(totals_stmt)).all()
        }

    return ReviewQueue(items=[_to_item(a) for a in rows], count=len(rows), totals=totals)


@router.post("/{asset_id}/approve", response_model=ReviewResult)
async def approve_summary(asset_id: int, body: ApproveRequest) -> ReviewResult:
    """Mark a draft reviewed. Optionally replace the text with the reviewer's own.

    Either way the result is ``enablement_verified``: a human has now read it,
    which is exactly what that status means, so Kai stops caveating it.
    """
    async with _db.SessionLocal() as session:
        asset = await _load(session, asset_id)

        if body.summary is not None:
            asset.summary = " ".join(body.summary.split())
        if not (asset.summary or "").strip():
            raise HTTPException(
                status_code=400,
                detail="cannot verify an empty summary; supply `summary` or regenerate first",
            )

        # Reviewer corrections to the facets, applied only when supplied.
        for field in ("audience", "format", "grade_range"):
            value = getattr(body, field)
            if value is not None:
                setattr(asset, field, value.strip() or None)

        asset.summary_status = STATUS_VERIFIED
        asset.summary_reviewed_by = body.reviewer.strip()
        asset.summary_reviewed_at = datetime.now(UTC)
        asset.summary_feedback = None
        # A reviewer's rewrite changes the retrieval text, so the vector must
        # follow it. Facet edits (audience) are in the embedding input too.
        await reembed(asset)
        await session.commit()

        _logger.info(
            "enablement review: asset=%s verified by %s (edited=%s)",
            asset_id,
            body.reviewer,
            body.summary is not None,
        )
        return ReviewResult(
            id=asset.id,
            summary_status=asset.summary_status,
            summary=asset.summary,
            detail="verified" + (" with reviewer's edit" if body.summary else ""),
        )


@router.post("/{asset_id}/send-back", response_model=ReviewResult)
async def send_back_summary(asset_id: int, body: SendBackRequest) -> ReviewResult:
    """Reject a draft with a note, and (by default) redraft against that note."""
    async with _db.SessionLocal() as session:
        asset = await _load(session, asset_id)
        asset.summary_status = STATUS_NEEDS_REVISION
        asset.summary_feedback = body.feedback.strip()
        asset.summary_reviewed_by = body.reviewer.strip()
        asset.summary_reviewed_at = datetime.now(UTC)
        await session.commit()

        if not body.regenerate:
            return ReviewResult(
                id=asset.id,
                summary_status=asset.summary_status,
                summary=asset.summary,
                detail="sent back; not regenerated",
            )

        facts = AssetFacts.from_row(asset)
        enrichment = await generate_enrichment(facts, feedback=body.feedback, session=session)
        if enrichment is None:
            # Leave it parked in needs_revision with the note intact. Silently
            # keeping the rejected draft as if nothing happened would be worse.
            return ReviewResult(
                id=asset.id,
                summary_status=asset.summary_status,
                summary=asset.summary,
                detail="sent back; regeneration failed, still needs revision",
            )

        apply_enrichment(asset, enrichment)
        await reembed(asset)
        # Keep the note visible on the fresh draft so the reviewer can see
        # whether it was actually addressed.
        asset.summary_feedback = body.feedback.strip()
        await session.commit()

        _logger.info("enablement review: asset=%s regenerated after feedback", asset_id)
        return ReviewResult(
            id=asset.id,
            summary_status=asset.summary_status,
            summary=asset.summary,
            regenerated=True,
            detail="regenerated from your note; back in the draft queue",
        )


@router.post("/{asset_id}/regenerate", response_model=ReviewResult)
async def regenerate_summary(asset_id: int) -> ReviewResult:
    """Redraft one asset from scratch, carrying any existing reviewer note."""
    async with _db.SessionLocal() as session:
        asset = await _load(session, asset_id)
        facts = AssetFacts.from_row(asset)
        enrichment = await generate_enrichment(
            facts, feedback=asset.summary_feedback, session=session
        )
        if enrichment is None:
            raise HTTPException(status_code=502, detail="could not generate a usable draft")
        apply_enrichment(asset, enrichment)
        await reembed(asset)
        await session.commit()
        return ReviewResult(
            id=asset.id,
            summary_status=asset.summary_status,
            summary=asset.summary,
            regenerated=True,
            detail="redrafted",
        )


@router.get("/stats")
async def review_stats() -> dict[str, Any]:
    """Coverage numbers: the thing Jon wants trending to zero."""
    async with _db.SessionLocal() as session:
        row = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(or_(EnablementAsset.summary.is_(None), EnablementAsset.summary == ""))
                    .label("no_summary"),
                    func.count()
                    .filter(EnablementAsset.summary_status == STATUS_AI_DRAFT)
                    .label("ai_draft"),
                    func.count()
                    .filter(EnablementAsset.summary_status == STATUS_VERIFIED)
                    .label("verified"),
                    func.count()
                    .filter(EnablementAsset.summary_status == STATUS_NEEDS_REVISION)
                    .label("needs_revision"),
                    func.count()
                    .filter(or_(EnablementAsset.audience.is_(None), EnablementAsset.audience == ""))
                    .label("no_audience"),
                    func.count().filter(EnablementAsset.format.is_(None)).label("no_format"),
                    func.count()
                    .filter(EnablementAsset.grade_range.is_(None))
                    .label("no_grade_range"),
                ).where(EnablementAsset.status.is_distinct_from("archived"))
            )
        ).one()

    return {
        "total": row.total,
        "no_summary": row.no_summary,
        "ai_draft": row.ai_draft,
        "enablement_verified": row.verified,
        "needs_revision": row.needs_revision,
        "no_audience": row.no_audience,
        "no_format": row.no_format,
        "no_grade_range": row.no_grade_range,
    }
