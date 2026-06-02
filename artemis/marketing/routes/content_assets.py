"""Content Assets router — /api/content-assets.

Endpoints:
  GET    /                  — list assets (filtered)
  POST   /                  — create asset
  GET    /{id}              — get asset
  PATCH  /{id}              — update asset
  POST   /links             — link asset to candidate
  DELETE /links/{id}        — remove a link by link row id
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import ContentAsset, ContentAssetLink
from artemis.marketing.repository import (
    create_content_asset,
    link_content_asset_to_candidate,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/content-assets",
    tags=["content-assets"],
    dependencies=[Depends(require_token)],
)


# ── Links (must be BEFORE /{id} to avoid "links" captured as id param) ────────


@router.post("/links", status_code=201)
async def create_link(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Link a content asset to a campaign candidate."""
    candidate_id_raw = body.get("candidateId") or body.get("candidate_id")
    asset_id_raw = body.get("assetId") or body.get("asset_id")

    if candidate_id_raw is None:
        raise bad_request("candidateId is required", "campaign_asset_links_missing_candidate_id")  # noqa: B904
    if asset_id_raw is None:
        raise bad_request("assetId is required", "campaign_asset_links_missing_asset_id")  # noqa: B904

    try:
        candidate_id = int(candidate_id_raw)
        asset_id = int(asset_id_raw)
    except (TypeError, ValueError):
        raise bad_request(  # noqa: B904
            "candidateId and assetId must be integers", "campaign_asset_links_invalid_id"
        )

    # Verify asset exists
    asset = await session.get(ContentAsset, asset_id)
    if asset is None:
        raise not_found("Asset not found", "campaign_asset_links_asset_not_found")  # noqa: B904

    try:
        link = await link_content_asset_to_candidate(
            session,
            candidate_id=candidate_id,
            asset_id=asset_id,
            link_role=body.get("linkRole") or body.get("link_role"),
        )
    except ValueError:
        raise conflict(  # noqa: B904
            "Link already exists for this candidate and asset",
            code="campaign_asset_links_conflict",
        )
    await session.commit()
    return _serialize_link(link)


@router.get("/links")
async def list_links(
    campaign_id: int | None = Query(default=None, alias="campaignId"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List content-asset links, optionally filtered by campaign candidate."""
    q = select(ContentAssetLink)
    if campaign_id is not None:
        q = q.where(ContentAssetLink.candidate_id == campaign_id)
    q = q.order_by(ContentAssetLink.id.desc())
    result = await session.execute(q)
    return [_serialize_link(link) for link in result.scalars().all()]


@router.delete("/links/{campaign_id}/{asset_id}", status_code=204)
async def delete_link_by_campaign_asset(
    campaign_id: int,
    asset_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Compat delete by frontend's campaign/asset pair; missing link is already gone."""
    result = await session.execute(
        select(ContentAssetLink).where(
            ContentAssetLink.candidate_id == campaign_id,
            ContentAssetLink.asset_id == asset_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        return None
    return await delete_link(link.id, session)


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(
    link_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Remove an asset link by its row ID."""
    link = await session.get(ContentAssetLink, link_id)
    if link is None:
        raise not_found("Link not found", "campaign_asset_links_not_found")  # noqa: B904
    await session.delete(link)
    await session.commit()


# ── Content Assets CRUD ───────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_assets(
    status: str | None = Query(default=None),
    asset_type: str | None = Query(default=None, alias="assetType"),
    include_archived: bool = Query(default=False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List content assets with optional filters."""
    q = select(ContentAsset)
    if status:
        q = q.where(ContentAsset.status == status)
    elif not include_archived:
        q = q.where(ContentAsset.status != "archived")
    if asset_type:
        q = q.where(ContentAsset.asset_type == asset_type)
    q = q.order_by(ContentAsset.id.desc())
    result = await session.execute(q)
    return [_serialize_asset(a) for a in result.scalars().all()]


@router.post("/", status_code=201)
async def create_asset(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new content asset."""
    asset_type = _opt_str(body.get("assetType") or body.get("asset_type"))
    if not asset_type:
        raise bad_request("assetType is required", "content_assets_missing_type")  # noqa: B904

    asset = await create_content_asset(
        session,
        asset_type=asset_type,
        status=_opt_str(body.get("status")) or "draft",
        summary=_opt_str(body.get("summary")),
        metadata=body.get("metadata") or {},
        owner_user_id=body.get("ownerUserId") or body.get("owner_user_id"),
    )
    await session.commit()
    return _serialize_asset(asset)


@router.get("/{asset_id}")
async def get_asset(
    asset_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single content asset."""
    asset = await session.get(ContentAsset, asset_id)
    if asset is None:
        raise not_found("Asset not found", "content_assets_not_found")  # noqa: B904
    return _serialize_asset(asset)


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: int,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Partial update a content asset."""
    asset = await session.get(ContentAsset, asset_id)
    if asset is None:
        raise not_found("Asset not found", "content_assets_not_found")  # noqa: B904

    from datetime import UTC, datetime

    if "status" in body:
        asset.status = body["status"]
    if "summary" in body:
        asset.summary = body.get("summary")
    if "metadata" in body and isinstance(body["metadata"], dict):
        asset.asset_metadata = body["metadata"]
    if "assetType" in body:
        asset.asset_type = body["assetType"]
    asset.updated_at = datetime.now(tz=UTC)

    await session.flush()
    await session.commit()
    await session.refresh(asset)
    return _serialize_asset(asset)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _opt_str(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _serialize_asset(a: ContentAsset) -> dict[str, Any]:
    return {
        "id": a.id,
        "assetType": a.asset_type,
        "status": a.status,
        "summary": a.summary,
        # Wire name 'metadata' for frontend compat
        "metadata": a.asset_metadata or {},
        "ownerUserId": a.owner_user_id,
        "createdAt": a.created_at.isoformat(),
        "updatedAt": a.updated_at.isoformat(),
    }


def _serialize_link(link: ContentAssetLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "candidateId": link.candidate_id,
        "assetId": link.asset_id,
        "linkRole": link.link_role,
        "createdAt": link.created_at.isoformat(),
    }
