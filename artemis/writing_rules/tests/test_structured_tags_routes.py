"""Tests for structured draft/asset tags and registry-backed validation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_content_asset,
    create_signal,
)
from artemis.writing_rules.tag_registry_seed import seed_tag_registry_async

pytestmark = pytest.mark.asyncio

_HEADERS = {"X-Artemis-Token": "test-token"}


async def _seed_registry_with_audience_values(session: AsyncSession) -> None:
    await seed_tag_registry_async(session)
    await session.commit()


async def _make_draft(session: AsyncSession) -> CampaignDeliverable:
    signal = await create_signal(
        session,
        headline="District campaign",
        campaign_family="obc",
        source_type="manual",
        summary="Need draft",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        session,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="draft-1",
        campaign_id="obc",
        status="draft_ready",
        deliverable_metadata={"title": "Tagged Draft"},
    )
    session.add(deliverable)
    await session.flush()
    await session.refresh(deliverable)
    await session.commit()
    return deliverable


async def test_content_asset_tags_round_trip(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    asset = await create_content_asset(db_session, asset_type="snippet", metadata={})
    await db_session.commit()

    put_response = await client.put(
        f"/api/content-assets/{asset.id}/tags",
        json={"tags": {"audience": "superintendent", "platform": "email"}},
        headers=_HEADERS,
    )
    assert put_response.status_code == 200, put_response.text
    assert put_response.json() == {"audience": "superintendent", "platform": "email"}

    get_response = await client.get(f"/api/content-assets/{asset.id}/tags", headers=_HEADERS)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json() == {"audience": "superintendent", "platform": "email"}


async def test_draft_tags_round_trip(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    deliverable = await _make_draft(db_session)

    put_response = await client.put(
        f"/api/writing-studio/drafts/{deliverable.id}/tags",
        json={"tags": {"audience": "superintendent", "platform": "email"}},
        headers=_HEADERS,
    )
    assert put_response.status_code == 200, put_response.text
    assert put_response.json() == {"audience": "superintendent", "platform": "email"}

    get_response = await client.get(
        f"/api/writing-studio/drafts/{deliverable.id}/tags",
        headers=_HEADERS,
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.json() == {"audience": "superintendent", "platform": "email"}


async def test_draft_tags_unknown_dimension_returns_400(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    deliverable = await _make_draft(db_session)

    response = await client.put(
        f"/api/writing-studio/drafts/{deliverable.id}/tags",
        json={"tags": {"unknown_dimension": "superintendent"}},
        headers=_HEADERS,
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "draft_invalid_tags"


async def test_content_asset_tags_unknown_value_returns_400(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    asset = await create_content_asset(db_session, asset_type="snippet", metadata={})
    await db_session.commit()

    response = await client.put(
        f"/api/content-assets/{asset.id}/tags",
        json={"tags": {"audience": "assistant principal"}},
        headers=_HEADERS,
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "content_assets_invalid_tags"
