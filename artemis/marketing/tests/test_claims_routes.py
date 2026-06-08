"""Claims Register route tests."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.seed_corpus import import_writing_seed_corpus


async def _create_active_profile(
    db_session: AsyncSession, name: str = "Claims Route Profile"
) -> int:
    profile = await wr_repo.create_profile(db_session, name=name, status="active")
    await db_session.commit()
    return profile.id


async def test_seeded_claims_endpoint_returns_parsed_rows(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    result = await import_writing_seed_corpus(db_session)
    await db_session.commit()

    response = await client.get("/api/writing-studio/claims?status=approved")
    assert response.status_code == 200, response.text

    claims = response.json()
    assert len(claims) == result["claimsUpserted"] == 8

    claim_001 = next(claim for claim in claims if claim["claimCode"] == "001")
    assert claim_001["category"] == "Identity / Category"
    assert claim_001["tier"] == 1
    assert claim_001["approvedPhrasing"] == "Amira is the Learning Agent for Reading Growth."
    assert claim_001["status"] == "approved"


async def test_claim_routes_propose_approve_patch_retire_lossless(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _create_active_profile(db_session)

    create_response = await client.post(
        "/api/writing-studio/claims",
        json={
            "claimCode": "901",
            "category": "Pipeline",
            "tier": 2,
            "approvedPhrasing": "Amira helps marketing teams stay aligned.",
            "notes": "Initial proposal.",
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["status"] == "proposed"
    assert created["claimCode"] == "901"

    approve_response = await client.post(f"/api/writing-studio/claims/{created['id']}/approve")
    assert approve_response.status_code == 200, approve_response.text
    approved = approve_response.json()
    assert approved["status"] == "approved"

    patch_response = await client.patch(
        f"/api/writing-studio/claims/{created['id']}",
        json={"notes": "Approved phrasing confirmed by PMM."},
    )
    assert patch_response.status_code == 200, patch_response.text
    patched = patch_response.json()
    assert patched["notes"] == "Approved phrasing confirmed by PMM."
    assert patched["status"] == "approved"

    retire_response = await client.post(f"/api/writing-studio/claims/{created['id']}/retire")
    assert retire_response.status_code == 200, retire_response.text
    retired = retire_response.json()
    assert retired["status"] == "retired"

    get_response = await client.get(f"/api/writing-studio/claims/{created['id']}")
    assert get_response.status_code == 200, get_response.text
    persisted = get_response.json()
    assert persisted["id"] == created["id"]
    assert persisted["status"] == "retired"
    assert persisted["notes"] == "Approved phrasing confirmed by PMM."
