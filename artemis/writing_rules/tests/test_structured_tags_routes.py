"""Tests for structured draft/asset tags and registry-backed validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_content_asset,
    create_signal,
)
from artemis.writing_rules.models import WritingProfile
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


async def _make_draft_with_content(session: AsyncSession, content: str) -> CampaignDeliverable:
    deliverable = await _make_draft(session)
    meta = dict(deliverable.deliverable_metadata or {})
    meta["versions"] = [
        {
            "id": "v1",
            "version_number": 1,
            "content": content,
        }
    ]
    deliverable.deliverable_metadata = meta
    await session.commit()
    await session.refresh(deliverable)
    return deliverable


async def _create_active_profile(session: AsyncSession) -> WritingProfile:
    profile = WritingProfile(
        name="District Voice",
        status="active",
        default_model_provider="claude-code",
        default_model_id="claude-opus",
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


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


async def test_draft_tag_suggestions_return_valid_registry_values_without_persisting(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    await _create_active_profile(db_session)
    deliverable = await _make_draft_with_content(
        db_session,
        "Email draft for district leadership about a superintendent note and follow-up.",
    )

    from artemis.agent.types import Message, TextBlock, Usage

    class _FakeResult:
        messages = [
            Message(
                role="assistant",
                content=[TextBlock(text='{"audience":"superintendent","platform":"email"}')],
            )
        ]
        usage = Usage(input_tokens=10, output_tokens=5)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        mock_resolver.return_value = AsyncMock()
        mock_run_turn.return_value = _FakeResult()

        response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": {"audience": "superintendent", "platform": "email"}}

    get_response = await client.get(
        f"/api/writing-studio/drafts/{deliverable.id}/tags",
        headers=_HEADERS,
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.json() == {}


async def test_draft_tag_suggestions_drop_hallucinated_values(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    await _create_active_profile(db_session)
    deliverable = await _make_draft_with_content(
        db_session,
        "A leadership note for a district audience.",
    )

    from artemis.agent.types import Message, TextBlock, Usage

    class _FakeResult:
        messages = [
            Message(
                role="assistant",
                content=[TextBlock(text='{"audience":"governor"}')],
            )
        ]
        usage = Usage(input_tokens=10, output_tokens=5)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        mock_resolver.return_value = AsyncMock()
        mock_run_turn.return_value = _FakeResult()

        response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": {}}


async def test_draft_tag_suggestions_tolerate_non_json_model_output(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    await _create_active_profile(db_session)
    deliverable = await _make_draft_with_content(
        db_session,
        "District email draft for a superintendent audience.",
    )

    from artemis.agent.types import Message, TextBlock, Usage

    class _FakeResult:
        messages = [
            Message(
                role="assistant",
                content=[TextBlock(text="I think this fits district leadership.")],
            )
        ]
        usage = Usage(input_tokens=10, output_tokens=5)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        mock_resolver.return_value = AsyncMock()
        mock_run_turn.return_value = _FakeResult()

        response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": {}}


async def test_draft_tag_suggestions_parse_fenced_json(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    await _create_active_profile(db_session)
    deliverable = await _make_draft_with_content(
        db_session,
        "District outreach email written for a superintendent audience.",
    )

    from artemis.agent.types import Message, TextBlock, Usage

    class _FakeResult:
        messages = [
            Message(
                role="assistant",
                content=[
                    TextBlock(text='```json\n{"audience":"superintendent","platform":"email"}\n```')
                ],
            )
        ]
        usage = Usage(input_tokens=10, output_tokens=5)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        mock_resolver.return_value = AsyncMock()
        mock_run_turn.return_value = _FakeResult()

        response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": {"audience": "superintendent", "platform": "email"}}


async def test_draft_tag_suggestions_skip_model_when_draft_has_no_body(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    deliverable = await _make_draft(db_session)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": {}}
    mock_resolver.assert_not_called()
    mock_run_turn.assert_not_called()


async def test_draft_tag_suggestions_can_be_manually_applied_via_put(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry_with_audience_values(db_session)
    await _create_active_profile(db_session)
    deliverable = await _make_draft_with_content(
        db_session,
        "Email draft for a district superintendent audience.",
    )

    from artemis.agent.types import Message, TextBlock, Usage

    class _FakeResult:
        messages = [
            Message(
                role="assistant",
                content=[TextBlock(text='{"audience":"superintendent","platform":"email"}')],
            )
        ]
        usage = Usage(input_tokens=10, output_tokens=5)

    with (
        patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
        patch("artemis.agent.run_turn", new_callable=AsyncMock) as mock_run_turn,
    ):
        mock_resolver.return_value = AsyncMock()
        mock_run_turn.return_value = _FakeResult()

        suggest_response = await client.post(
            f"/api/writing-studio/drafts/{deliverable.id}/tags/suggest",
            headers=_HEADERS,
        )

    assert suggest_response.status_code == 200, suggest_response.text
    suggestions = suggest_response.json()["suggestions"]
    assert suggestions == {"audience": "superintendent", "platform": "email"}

    put_response = await client.put(
        f"/api/writing-studio/drafts/{deliverable.id}/tags",
        json={"tags": suggestions},
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
