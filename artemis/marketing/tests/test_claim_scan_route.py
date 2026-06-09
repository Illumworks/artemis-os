"""Integration tests for POST /api/writing-studio/drafts/{id}/claim-scan
and POST /api/writing-studio/drafts/{id}/claim-dismiss.

Tests:
- Strong claim not in register → flagged
- Text closely matching approved claim → suppressed (no flag)
- Ordinary descriptive copy → no flags
- Approve + re-scan clears the flag
- Precision: questions / soft "most" / ordinal "First" → no flags
- Precision: market-claim "first" + invented stat → flagged
- Disregard: dismissed claim does not re-appear on re-scan
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules import repository as wr_repo


async def _create_profile(session: AsyncSession, name: str = "Scan Test Profile") -> int:
    profile = await wr_repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile.id


async def _create_draft(client: AsyncClient, candidate_id: int | None = None) -> int:
    """Create a draft row.  Without a candidate_id a blank draft is used (no FK dep)."""
    if candidate_id is not None:
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate_id},
        )
    else:
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"title": "Dismiss test draft"},
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


# ─────────────────────────────────────────────────────────────────────────────
# Precision: questions / soft superlatives / ordinal "First" must not flag
# ─────────────────────────────────────────────────────────────────────────────


async def test_question_not_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A sentence ending in '?' must never produce a flag."""
    await _create_profile(db_session)
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "How do we truly understand what matters most to school leaders?"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["flags"] == [], f"Question must not flag: {resp.json()['flags']}"


async def test_what_matters_most_not_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motivational 'what matters most' copy must not flag."""
    await _create_profile(db_session)
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "What matters most is the evidence behind every product we choose."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["flags"] == [], f"'what matters most' must not flag: {resp.json()['flags']}"


async def test_ordinal_first_paragraph_not_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Leading/ordinal 'First ...' must not flag."""
    await _create_profile(db_session)
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "First paragraph: Amira listens as students read aloud."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["flags"] == [], f"Ordinal 'First' must not flag: {resp.json()['flags']}"


async def test_market_claim_first_is_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """'Amira is the first reading agent proven to ...' IS a market claim — must flag."""
    await _create_profile(db_session)
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={
            "text": "Amira is the first reading agent proven to deliver measurable literacy gains."
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["flags"]) >= 1, (
        "Market-claim 'Amira is the first...' must produce a flag."
    )


async def test_invented_stat_is_flagged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An invented quantified stat must flag."""
    await _create_profile(db_session)
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-scan",
        json={"text": "Students improve scores by 99% after one semester of daily use."},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["flags"]) >= 1, "Invented '99%' stat must produce a flag."


# ─────────────────────────────────────────────────────────────────────────────
# Disregard / claim-dismiss endpoint
# ─────────────────────────────────────────────────────────────────────────────


async def test_claim_dismiss_stores_dismissal(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST claim-dismiss returns ok=true and the dismissed text in the list."""
    await _create_profile(db_session)
    # Use a blank draft — no candidate FK dependency.
    draft_id = await _create_draft(client)

    flagged_text = "Amira improves reading scores by 99% in a single semester of daily practice."

    resp = await client.post(
        f"/api/writing-studio/drafts/{draft_id}/claim-dismiss",
        json={"text": flagged_text},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert flagged_text in data["dismissedClaims"]


async def test_dismissed_claim_does_not_re_flag_on_rescan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """After dismissing a flag, a re-scan must NOT return it again.

    This is the acceptance proof for the Disregard feature: scan → dismiss → rescan → gone.
    """
    await _create_profile(db_session)
    # Use a blank draft — no candidate FK dependency.
    draft_id = await _create_draft(client)

    claim_text = "Amira improves reading scores by 99% in a single semester of daily practice."

    # ── Scan 1: the claim should flag ─────────────────────────────────────────
    resp1 = await client.post(
        f"/api/writing-studio/drafts/{draft_id}/claim-scan",
        json={"text": claim_text},
    )
    assert resp1.status_code == 200, resp1.text
    flags_before = resp1.json()["flags"]
    assert len(flags_before) >= 1, (
        f"Precondition: claim must flag before dismissal. Flags: {flags_before}"
    )
    flagged_span = flags_before[0]["text"]

    # ── Dismiss ───────────────────────────────────────────────────────────────
    dismiss_resp = await client.post(
        f"/api/writing-studio/drafts/{draft_id}/claim-dismiss",
        json={"text": flagged_span},
    )
    assert dismiss_resp.status_code == 200, dismiss_resp.text
    assert dismiss_resp.json()["ok"] is True

    # ── Scan 2: the same text must NOT re-flag ────────────────────────────────
    resp2 = await client.post(
        f"/api/writing-studio/drafts/{draft_id}/claim-scan",
        json={"text": claim_text},
    )
    assert resp2.status_code == 200, resp2.text
    flags_after = resp2.json()["flags"]
    assert flags_after == [], f"Dismissed claim must not re-appear on re-scan. Got: {flags_after}"


async def test_dismiss_404_for_nonexistent_draft(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST claim-dismiss on a draft that doesn't exist must return 404."""
    resp = await client.post(
        "/api/writing-studio/drafts/99999/claim-dismiss",
        json={"text": "Some claim text"},
    )
    assert resp.status_code == 404, resp.text


async def test_dismiss_400_when_text_missing(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST claim-dismiss without text body must return 400."""
    await _create_profile(db_session)
    draft_id = await _create_draft(client)

    resp = await client.post(
        f"/api/writing-studio/drafts/{draft_id}/claim-dismiss",
        json={},
    )
    assert resp.status_code == 400, resp.text


async def test_dismiss_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dismissing the same span twice must not create duplicate entries."""
    await _create_profile(db_session)
    draft_id = await _create_draft(client)

    flagged_text = "Amira improves reading scores by 99% in a single semester."

    for _ in range(2):
        resp = await client.post(
            f"/api/writing-studio/drafts/{draft_id}/claim-dismiss",
            json={"text": flagged_text},
        )
        assert resp.status_code == 200, resp.text

    # Only one entry should be stored.
    data = resp.json()
    assert data["dismissedClaims"].count(flagged_text) == 1, (
        "Duplicate dismissals must be deduplicated."
    )
