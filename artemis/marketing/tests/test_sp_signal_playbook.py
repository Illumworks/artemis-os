from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec
from artemis.marketing.models import SignalReasonCode
from artemis.marketing.seeds.reason_codes import seed_reason_codes


async def _insert_reason_code(
    session: AsyncSession,
    code: str = "POLICY_EDTECH_TIME_LIMIT",
    *,
    is_active: bool = True,
) -> None:
    session.add(
        SignalReasonCode(
            code=code,
            domain=code.split("_", 1)[0],
            description="District announces formal limits on ed tech time",
            what_scout_looks_for="Board minutes, policy proposals, and public commentary",
            default_urgency="standard",
            primary_scouts=["board_minutes", "regional_news"],
            campaign_families=["reading_growth"],
            is_active=is_active,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_migration_0052_columns_exist(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'signal_reason_codes' "
            "AND column_name IN ('primary_scouts', 'campaign_families')"
        )
    )
    cols = {row.column_name: row.data_type for row in result}
    assert cols == {"primary_scouts": "ARRAY", "campaign_families": "ARRAY"}


@pytest.mark.asyncio
async def test_seed_backfill_shape_matches_josh_spec(db_session: AsyncSession) -> None:
    await seed_reason_codes(db_session)
    spec_row = next(rc for rc in parse_spec().reason_codes if rc.code == "POLICY_EDTECH_TIME_LIMIT")
    result = await db_session.execute(
        text("SELECT primary_scouts FROM signal_reason_codes WHERE code = :code"),
        {"code": spec_row.code},
    )
    assert result.scalar_one() == list(spec_row.primary_scouts)


@pytest.mark.asyncio
async def test_get_reason_codes_returns_playbook_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _insert_reason_code(db_session)
    response = await client.get("/api/signal-criteria/reason-codes")
    assert response.status_code == 200
    body = response.json()[0]
    assert body["primaryScouts"] == ["board_minutes", "regional_news"]
    assert body["campaignFamilies"] == ["reading_growth"]


@pytest.mark.asyncio
async def test_post_validates_primary_scouts(client: AsyncClient) -> None:
    response = await client.post(
        "/api/signal-criteria/reason-codes",
        json={"code": "TEST_SIGNAL", "domain": "TEST", "primaryScouts": ["bogus_scout"]},
    )
    assert response.status_code == 400
    assert "board_minutes" in response.json()["error"]


@pytest.mark.asyncio
async def test_post_validates_campaign_families(client: AsyncClient) -> None:
    response = await client.post(
        "/api/signal-criteria/reason-codes",
        json={"code": "TEST_SIGNAL", "domain": "TEST", "campaignFamilies": ["bogus_family"]},
    )
    assert response.status_code == 400
    assert "reading_growth" in response.json()["error"]


@pytest.mark.asyncio
async def test_patch_updates_arrays(client: AsyncClient, db_session: AsyncSession) -> None:
    await _insert_reason_code(db_session)
    response = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_EDTECH_TIME_LIMIT",
        json={"primaryScouts": ["state_doe"], "campaignFamilies": ["OBC"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["primaryScouts"] == ["state_doe"]
    assert body["campaignFamilies"] == ["OBC"]


@pytest.mark.asyncio
async def test_code_and_domain_remain_immutable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _insert_reason_code(db_session)
    response = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_EDTECH_TIME_LIMIT",
        json={"domain": "OTHER"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_markdown_export_includes_active_codes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _insert_reason_code(db_session)
    response = await client.get("/api/signal-criteria/reason-codes/markdown-export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text_body = response.text
    assert "## POLICY_EDTECH_TIME_LIMIT" in text_body
    assert "Primary scouts: board_minutes, regional_news" in text_body


@pytest.mark.asyncio
async def test_soft_retire_flow(client: AsyncClient, db_session: AsyncSession) -> None:
    await _insert_reason_code(db_session)
    response = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_EDTECH_TIME_LIMIT",
        json={"isActive": False},
    )
    assert response.status_code == 200
    active = await client.get("/api/signal-criteria/reason-codes")
    assert active.json() == []
    all_rows = await client.get("/api/signal-criteria/reason-codes?include_inactive=true")
    assert len(all_rows.json()) == 1


@pytest.mark.asyncio
async def test_db_delete_trigger_still_blocks(db_session: AsyncSession) -> None:
    await _insert_reason_code(db_session)
    with pytest.raises(Exception, match="soft-delete"):
        await db_session.execute(
            text("DELETE FROM signal_reason_codes WHERE code = 'POLICY_EDTECH_TIME_LIMIT'")
        )
