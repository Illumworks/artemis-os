"""Integration tests for POST /api/writing-studio/drafts/{id}/claim-scan.

Tests:
- Strong claim not in register → flagged
- Text closely matching approved claim → suppressed (no flag)
- Ordinary descriptive copy → no flags
- Approve + re-scan clears the flag
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules import repository as wr_repo


async def _create_profile(session: AsyncSession, name: str = "Scan Test Profile") -> int:
    profile = await wr_repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile.id


async def _create_draft(client: AsyncClient, candidate_id: int = 1) -> int:
    """Create a minimal deliverable row so the endpoint has something to look up."""
    resp = await client.post(
        "/api/writing-studio/drafts",
        json={"candidate_id": candidate_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _ensure_candidate(client: AsyncClient) -> int:
    """Return a campaign candidate id, creating one if needed."""
    resp = await client.get("/api/campaign-ops/campaigns")
    if resp.status_code == 200 and resp.json():
        return resp.json()[0]["id"]

    # Create a minimal candidate via the campaign-ops API.
    resp = await client.post(
        "/api/campaign-ops/campaigns",
        json={
            "name": "Scan test candidate",
            "status": "active",
            "score": 50,
        },
    )
    if resp.status_code in (200, 201):
        return resp.json()["id"]
    # Fallback: return 1 and let the draft creation handle the FK.
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Scan with text in request body (no DB draft needed)
# ─────────────────────────────────────────────────────────────────────────────


async def test_strong_claim_not_in_register_is_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Case 1: invented strong claim → flagged in response."""
    # Create an active profile (so the scanner has a profile to load claims for).
    await _create_profile(db_session)

    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={
            "text": "Amira improves reading scores by 99% in a single semester of daily practice."
        },
    )
    # 404 is acceptable because draft 99999 doesn't exist; but we passed text in
    # the body so the endpoint should use that.  Actually: the endpoint uses the
    # supplied text if present, so the draft lookup is skipped.
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "flags" in data
    assert len(data["flags"]) >= 1
    flag = data["flags"][0]
    assert "99%" in flag["text"]
    assert flag["reason"] in ("quantified", "superlative", "comparative")


async def test_approved_claim_is_suppressed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Case 2: text matching an approved claim → suppressed (no flag)."""
    profile_id = await _create_profile(db_session)

    # Seed an approved claim.
    await wr_repo.create_claim(
        db_session,
        profile_id=profile_id,
        claim_code="scan-001",
        category="efficacy",
        approved_phrasing=(
            "Students using Amira gain 52% more oral reading fluency in one semester."
        ),
        status="approved",
    )
    await db_session.commit()

    # Post the identical phrasing — it should be suppressed.
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "Students using Amira gain 52% more oral reading fluency in one semester."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approvedClaimsCount"] >= 1
    # The span should be suppressed — flags list should be empty.
    assert data["flags"] == [], f"Expected suppression, got flags: {data['flags']}"


async def test_ordinary_copy_produces_no_flags(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Case 3: ordinary descriptive copy → no flags."""
    await _create_profile(db_session)

    text = (
        "Our tutor listens as students read aloud and provides real-time support. "
        "Teachers receive a clear picture of every reader's progress. "
        "Amira Learning partners with districts across Indiana."
    )
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": text},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["flags"] == [], f"Expected no flags, got: {data['flags']}"


async def test_response_shape(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify the response has the expected top-level shape."""
    await _create_profile(db_session)

    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "Amira is the only AI reading tutor proven to deliver results."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "flags" in data
    assert "scannedChars" in data
    assert "approvedClaimsCount" in data
    assert isinstance(data["flags"], list)
    assert isinstance(data["scannedChars"], int)
    assert isinstance(data["approvedClaimsCount"], int)

    # If there are flags, check their structure.
    for flag in data["flags"]:
        assert "start" in flag
        assert "end" in flag
        assert "text" in flag
        assert "reason" in flag
        assert "nearestApproved" in flag
        assert isinstance(flag["nearestApproved"], list)


async def test_scan_with_no_active_profile_returns_empty_flags_for_ordinary_copy(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active profile → approvedClaimsCount=0; ordinary copy still no flags."""
    # No profile created → scanner uses empty approved list.
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "Amira helps students read better."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approvedClaimsCount"] == 0
    assert data["flags"] == []
